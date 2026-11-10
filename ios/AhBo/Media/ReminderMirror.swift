// ReminderMirror.swift — the local reminder mirror (PRD §10.4, §25.2).
//
// Holds the last `reminder_mirror` from welcome / config.update:
//   {"date":"2026-11-20","slots":[{"slot_id","time":"12:00","clip_id":"R_s_1200","cards":[{…med card…}]}]}
// While the device channel has been offline for more than 30 s, a 15 s timer plays each due R-slot clip once
// (due = its HKT time has passed by at most 30 min) and shows that slot's meds cards locally. A tap on the
// face or on a card while a local reminder is open counts as her touch confirmation: it is queued (and
// persisted) and sent on reconnect as `face_event {event:{type:"reminder_confirmed", slot_id, at,
// source:"touch_offline"}}` (requested contract addition; see the final report).

import Foundation

@MainActor
final class ReminderMirror {
    struct Slot: Codable, Sendable {
        let slot_id: String
        let time: String
        let clip_id: String
        let cards: [[String: JSONValue]]?
    }
    struct Mirror: Codable, Sendable { let date: String; let slots: [Slot] }
    struct Confirmation: Codable, Sendable { let slot_id: String; let at: String }

    var face: (([String: Any]) -> Void)?
    var playClip: ((_ clipId: String, _ sayId: String) -> Void)?

    private var mirror: Mirror?
    private var played: Set<String> = []              // "<date>/<slot_id>"
    private var openSlot: (id: String, until: Date)?
    private var pending: [Confirmation] = []
    private var timer: Task<Void, Never>?
    private let d = UserDefaults.standard

    static let tz = TimeZone(identifier: "Asia/Hong_Kong") ?? .current
    static let dueWindow: TimeInterval = 30 * 60

    init() {
        if let data = d.data(forKey: "reminderMirror") { mirror = try? JSONDecoder().decode(Mirror.self, from: data) }
        played = Set(d.stringArray(forKey: "reminderPlayed") ?? [])
        if let data = d.data(forKey: "reminderPending") {
            pending = (try? JSONDecoder().decode([Confirmation].self, from: data)) ?? []
        }
    }

    /// Persist the server's mirror (any JSON object).
    func update(_ raw: Any) {
        guard JSONSerialization.isValidJSONObject(raw),
              let data = try? JSONSerialization.data(withJSONObject: raw),
              let m = try? JSONDecoder().decode(Mirror.self, from: data) else { return }
        mirror = m
        d.set(data, forKey: "reminderMirror")
        Log.info("mirror", "reminder mirror \(m.date): \(m.slots.count) slots")
    }

    /// Called with the device channel's offline state (true after 30 s without the server).
    func setOffline(_ offline: Bool) {
        timer?.cancel(); timer = nil
        guard offline else { openSlot = nil; return }
        timer = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                self?.tick()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    private func tick(now: Date = Date()) {
        guard let m = mirror else { return }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = Self.tz
        let today = Self.dayString(now)
        for slot in m.slots {
            let key = "\(today)/\(slot.slot_id)"
            guard !played.contains(key) else { continue }
            let hm = slot.time.split(separator: ":").compactMap { Int($0) }
            guard hm.count == 2, let due = cal.date(bySettingHour: hm[0], minute: hm[1], second: 0, of: now) else { continue }
            let late = now.timeIntervalSince(due)
            guard late >= 0, late <= Self.dueWindow else { continue }
            played.insert(key)
            d.set(Array(played.suffix(200)), forKey: "reminderPlayed")
            openSlot = (slot.slot_id, now.addingTimeInterval(Self.dueWindow))
            Log.info("mirror", "offline reminder \(slot.slot_id)")
            for card in slot.cards ?? [] {
                face?(["type": "card", "card": card.mapValues(\.any)])
            }
            playClip?(slot.clip_id, "say_local_\(slot.slot_id)_\(today)")
            return
        }
    }

    /// A tap while offline. Returns true when it confirmed an open local reminder.
    func tapWhileOffline(now: Date = Date()) -> Bool {
        guard let open = openSlot, now < open.until else { return false }
        openSlot = nil
        pending.append(Confirmation(slot_id: open.id, at: ISO8601DateFormatter.hkt.string(from: now)))
        if let data = try? JSONEncoder().encode(pending) { d.set(data, forKey: "reminderPending") }
        face?(["type": "expr", "expr": "happy"])
        Log.info("mirror", "offline touch confirmation queued for \(open.id)")
        return true
    }

    /// Queued confirmations, cleared once handed over (sent on reconnect).
    func drainConfirmations() -> [Confirmation] {
        let out = pending
        pending.removeAll()
        d.removeObject(forKey: "reminderPending")
        return out
    }

    static func dayString(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = tz
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
}

extension ISO8601DateFormatter {
    /// HKT (+08:00) timestamps (CONTRACTS §1).
    nonisolated(unsafe) static let hkt: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.timeZone = TimeZone(identifier: "Asia/Hong_Kong")
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}

/// Minimal Codable JSON value (to persist the server's card objects unchanged).
enum JSONValue: Codable, Sendable {
    case string(String), number(Double), bool(Bool), array([JSONValue]), object([String: JSONValue]), null

    init(from decoder: any Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        case .null: try c.encodeNil()
        }
    }

    var any: Any {
        switch self {
        case .string(let s): s
        case .number(let n): n
        case .bool(let b): b
        case .array(let a): a.map(\.any)
        case .object(let o): o.mapValues(\.any)
        case .null: NSNull()
        }
    }
}
