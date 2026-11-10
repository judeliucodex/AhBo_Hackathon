// DeviceChannel.swift — the always-open device channel (PRD §3.2, §10.1; CONTRACTS §2).
//
//  * URLSessionWebSocketTask to wss://<host>/ws/device with `Authorization: Bearer <APP_SECRET>`
//  * first frame `hello`; the server answers `welcome {device_token, server_time, fixed_cache_version,
//    text_sessions, config}` and replays the face state as `ui` commands
//  * `heartbeat {battery, thermal, in_session}` every 10 s (plus a WebSocket ping to catch half-open sockets)
//  * every command is answered with `ack {ref, ok, error?}` at once (≤ 2 s); de-duplicated by `id`
//    (a resent command is acked again with the first result, never run twice)
//  * reconnect with backoff 0.5, 1, 2, 4, 8 s (cap) ± 20 % jitter; after 30 s without a welcome → offline
// Also holds APIClient: the HTTP calls that use the device token (POST /session, /media/*).

import Foundation
import UIKit

/// Frame ids: a ULID-shaped string (48-bit ms time + 80 random bits, Crockford base32).
enum FrameID {
    private static let alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    static func make() -> String {
        var ms = UInt64(Date().timeIntervalSince1970 * 1000)
        var time = [Character](repeating: "0", count: 10)
        for i in (0..<10).reversed() { time[i] = alphabet[Int(ms & 31)]; ms >>= 5 }
        var rnd = SystemRandomNumberGenerator()
        let tail = (0..<16).map { _ in alphabet[Int(rnd.next() & 31)] }
        return String(time) + String(tail)
    }
}

enum ConnectionStatus: String, Sendable {
    case notConfigured = "未設定", connecting = "連線中", online = "在線", reconnecting = "重新連線中", offline = "離線"
}

@MainActor
final class DeviceChannel {
    struct AckResult { var ok: Bool; var error: String? = nil }

    var onStatus: ((ConnectionStatus) -> Void)?
    var onOffline: ((Bool) -> Void)?
    var onWelcome: (([String: Any]) -> Void)?
    /// Runs a server command and returns its ack result. Must return fast (start slow work in a Task).
    var onCommand: (([String: Any]) -> AckResult)?
    var helloFields: (() -> [String: Any])?
    var heartbeatFields: (() -> [String: Any])?

    private(set) var status: ConnectionStatus = .notConfigured { didSet { onStatus?(status) } }
    private(set) var isOffline = false
    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private var generation = 0
    private var attempt = 0
    private var welcomed = false
    private var disconnectedSince: Date?
    private var seen: [String: AckResult] = [:]
    private var seenOrder: [String] = []
    private var heartbeatTask: Task<Void, Never>?
    private var offlineTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private var url: URL?
    private var secret = ""

    static let heartbeatSeconds: Double = 10
    static let offlineAfterSeconds: Double = 30
    private static let backoff: [Double] = [0.5, 1, 2, 4, 8]

    func start(url: URL?, secret: String) {
        stop()
        guard let url, !secret.isEmpty else {
            status = .notConfigured
            Log.info("channel", "not configured (host or app secret missing)")
            markDisconnected()
            return
        }
        self.url = url
        self.secret = secret
        attempt = 0
        connect()
    }

    func stop() {
        generation += 1
        reconnectTask?.cancel(); reconnectTask = nil
        heartbeatTask?.cancel(); heartbeatTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
        welcomed = false
    }

    /// Send a device event `{id, type, ...fields}`. Dropped silently when not connected (events need no ack).
    func send(_ type: String, _ fields: [String: Any] = [:]) {
        var frame = fields
        frame["id"] = FrameID.make()
        frame["type"] = type
        sendFrame(frame)
    }

    var isOnline: Bool { welcomed }

    // MARK: connection

    private func connect() {
        guard let url else { return }
        generation += 1
        let gen = generation
        status = attempt == 0 ? .connecting : .reconnecting
        var req = URLRequest(url: url)
        req.setValue("Bearer \(secret)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 15
        let s = URLSession(configuration: .default)
        let t = s.webSocketTask(with: req)
        t.maximumMessageSize = 4 * 1024 * 1024
        session = s
        task = t
        t.resume()
        Log.info("channel", "connecting to \(url.host() ?? "?") (attempt \(attempt + 1))")
        var hello: [String: Any] = helloFields?() ?? [:]
        hello["id"] = FrameID.make()
        hello["type"] = "hello"
        sendFrame(hello)
        Task { @MainActor [weak self] in await self?.receiveLoop(task: t, gen: gen) }
    }

    private func receiveLoop(task t: URLSessionWebSocketTask, gen: Int) async {
        while gen == generation {
            do {
                let msg = try await t.receive()
                guard gen == generation else { return }
                switch msg {
                case .string(let s): handle(text: s)
                case .data(let d): handle(text: String(decoding: d, as: UTF8.self))
                @unknown default: break
                }
            } catch {
                guard gen == generation else { return }
                let code = t.closeCode.rawValue
                Log.error("channel", "socket closed (\(code)): \(error.localizedDescription)")
                if code == 4401 { Log.error("channel", "server rejected the app secret (4401)") }
                dropped()
                return
            }
        }
    }

    private func dropped() {
        heartbeatTask?.cancel(); heartbeatTask = nil
        task?.cancel(with: .abnormalClosure, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
        welcomed = false
        generation += 1
        markDisconnected()
        let base = Self.backoff[min(attempt, Self.backoff.count - 1)]
        let delay = base * Double.random(in: 0.8...1.2)
        attempt += 1
        status = .reconnecting
        reconnectTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            self?.connect()
        }
    }

    private func markDisconnected() {
        if disconnectedSince == nil { disconnectedSince = Date() }
        guard !isOffline, offlineTask == nil else { return }
        let left = max(0, Self.offlineAfterSeconds - Date().timeIntervalSince(disconnectedSince ?? Date()))
        offlineTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(left))
            guard let self, !Task.isCancelled else { return }
            self.offlineTask = nil
            if !self.welcomed && !self.isOffline {
                self.isOffline = true
                self.status = .offline
                Log.info("channel", "offline (no server for \(Int(Self.offlineAfterSeconds)) s)")
                self.onOffline?(true)
            }
        }
    }

    private func handle(text: String) {
        guard let data = text.data(using: .utf8),
              let msg = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let type = msg["type"] as? String else { return }
        if type == "welcome" {
            welcomed = true
            attempt = 0
            disconnectedSince = nil
            offlineTask?.cancel(); offlineTask = nil
            status = .online
            if isOffline { isOffline = false; onOffline?(false) }
            Log.info("channel", "welcome")
            startHeartbeat()
            onWelcome?(msg)
            return
        }
        guard let id = msg["id"] as? String else { return }
        if let prior = seen[id] {                       // a resend: ack again, never run twice
            sendAck(id, prior)
            return
        }
        let result = onCommand?(msg) ?? AckResult(ok: false, error: "no_router")
        seen[id] = result
        seenOrder.append(id)
        if seenOrder.count > 500 { seen.removeValue(forKey: seenOrder.removeFirst()) }
        sendAck(id, result)
    }

    private func sendAck(_ ref: String, _ r: AckResult) {
        var f: [String: Any] = ["ref": ref, "ok": r.ok]
        if let e = r.error { f["error"] = e }
        send("ack", f)
    }

    private func startHeartbeat() {
        heartbeatTask?.cancel()
        let gen = generation
        heartbeatTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self, gen == self.generation else { return }
                self.send("heartbeat", self.heartbeatFields?() ?? [:])
                self.task?.sendPing { [weak self] error in
                    guard let error else { return }
                    let msg = error.localizedDescription
                    Task { @MainActor [weak self] in
                        guard let self, gen == self.generation else { return }
                        Log.error("channel", "ping failed: \(msg)")
                        self.dropped()
                    }
                }
                try? await Task.sleep(for: .seconds(Self.heartbeatSeconds))
            }
        }
    }

    private func sendFrame(_ frame: [String: Any]) {
        guard let t = task, JSONSerialization.isValidJSONObject(frame),
              let data = try? JSONSerialization.data(withJSONObject: frame),
              let s = String(data: data, encoding: .utf8) else { return }
        t.send(.string(s)) { error in
            if let error { Log.error("channel", "send failed: \(error.localizedDescription)") }
        }
    }
}

// MARK: - HTTP (device token)

enum APIError: Error, LocalizedError {
    case notConfigured, http(Int), badResponse
    var errorDescription: String? {
        switch self {
        case .notConfigured: "server not configured or no device token yet"
        case .http(let c): "HTTP \(c)"
        case .badResponse: "bad response"
        }
    }
}

@MainActor
final class APIClient {
    var base: URL?
    var deviceToken: String?

    func url(_ pathOrURL: String) -> URL? {
        if pathOrURL.hasPrefix("http://") || pathOrURL.hasPrefix("https://") { return URL(string: pathOrURL) }
        guard let base else { return nil }
        return URL(string: pathOrURL, relativeTo: base)?.absoluteURL
    }

    private func request(_ pathOrURL: String) throws -> URLRequest {
        guard let u = url(pathOrURL), let token = deviceToken else { throw APIError.notConfigured }
        var r = URLRequest(url: u)
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.timeoutInterval = 20
        return r
    }

    func postJSON(_ path: String, _ body: [String: Any]) async throws -> [String: Any] {
        var r = try request(path)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: r)
        try check(resp)
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { throw APIError.badResponse }
        return obj
    }

    func get(_ pathOrURL: String) async throws -> Data {
        let r = try request(pathOrURL)
        let (data, resp) = try await URLSession.shared.data(for: r)
        try check(resp)
        return data
    }

    /// multipart/form-data POST with one file part and plain fields.
    func postMultipart(_ path: String, fields: [String: String], fileField: String, fileName: String,
                       mime: String, file: Data) async throws -> [String: Any] {
        var r = try request(path)
        r.httpMethod = "POST"
        let boundary = "AhBo-\(UUID().uuidString)"
        r.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        for (k, v) in fields {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(k)\"\r\n\r\n\(v)\r\n".utf8))
        }
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(fileName)\"\r\nContent-Type: \(mime)\r\n\r\n".utf8))
        body.append(file)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let (data, resp) = try await URLSession.shared.upload(for: r, from: body)
        try check(resp)
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { throw APIError.badResponse }
        return obj
    }

    private func check(_ resp: URLResponse) throws {
        guard let h = resp as? HTTPURLResponse else { throw APIError.badResponse }
        guard (200..<300).contains(h.statusCode) else { throw APIError.http(h.statusCode) }
    }
}
