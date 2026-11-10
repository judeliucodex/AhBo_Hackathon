// UIStateCache.swift — mirrors what the face should show (PRD §25.2, CONTRACTS §2.4), updated from every
// face command, so a reloaded web view is never blank: replay = reset, mood, persistent cards, open step
// groups (with each step's current state), ok_button if shown, offline if set.

import Foundation

@MainActor
final class UIStateCache {
    private(set) var mood: String = "idle"
    private var cards: [(id: String, card: [String: Any])] = []           // persistent cards, in order
    private var groups: [(id: String, group: [String: Any])] = []         // open step groups, in order
    private(set) var okButton: [String: Any]? = nil
    private(set) var offline = false

    var okButtonShown: Bool { okButton != nil }

    /// Update from one face command (host → face). Transient commands (level, say_*, heard, expr, flash,
    /// countdown, birth) don't change the cache.
    func update(_ cmd: [String: Any]) {
        guard let type = cmd["type"] as? String else { return }
        switch type {
        case "mood":
            if let m = cmd["mood"] as? String { mood = m }
        case "card":
            guard let card = cmd["card"] as? [String: Any], let id = card["id"] as? String else { return }
            cards.removeAll { $0.id == id }
            if (card["persistent"] as? Bool) == true { cards.append((id, card)) }
        case "card_update":
            guard let id = cmd["id"] as? String, let patch = cmd["patch"] as? [String: Any],
                  let i = cards.firstIndex(where: { $0.id == id }) else { return }
            var card = cards[i].card
            for (k, v) in patch where k != "playing" { card[k] = v }
            cards[i].card = card
        case "card_clear":
            if let id = cmd["id"] as? String { cards.removeAll { $0.id == id } }
            // no id: clears non-persistent cards only, which the cache doesn't hold
        case "steps":
            guard let g = cmd["group"] as? [String: Any], let id = g["id"] as? String else { return }
            groups.removeAll { $0.id == id }
            groups.append((id, g))
        case "step":
            guard let gid = cmd["group"] as? String, let sid = cmd["id"] as? String,
                  let gi = groups.firstIndex(where: { $0.id == gid }) else { return }
            var g = groups[gi].group
            var steps = g["steps"] as? [[String: Any]] ?? []
            if let si = steps.firstIndex(where: { ($0["id"] as? String) == sid }) {
                if let st = cmd["state"] { steps[si]["state"] = st }
                if let label = cmd["label"] { steps[si]["label"] = label }
            }
            g["steps"] = steps
            groups[gi].group = g
        case "steps_clear":
            if let gid = cmd["group"] as? String { groups.removeAll { $0.id == gid } } else { groups.removeAll() }
        case "ok_button":
            okButton = (cmd["show"] as? Bool) == true ? cmd : nil
        case "offline":
            offline = (cmd["on"] as? Bool) == true
        case "reset":
            mood = "idle"; cards.removeAll(); groups.removeAll(); okButton = nil
            // `offline` is the host's own state: a server reset doesn't make the network come back
        default:
            break
        }
    }

    /// The commands that rebuild the face from scratch.
    func replayCommands() -> [[String: Any]] {
        var out: [[String: Any]] = [["type": "reset"], ["type": "mood", "mood": mood]]
        out += cards.map { ["type": "card", "card": $0.card] }
        out += groups.map { ["type": "steps", "group": $0.group] }
        if let ok = okButton { out.append(ok) }
        if offline { out.append(["type": "offline", "on": true]) }
        return out
    }
}
