// Log.swift — os.Logger plus a small ring buffer for the hidden settings screen (PRD §25.2).
// Rules: never log secrets (app secret, device token, LiveKit token) and never log ambient transcripts
// (§20.4.1 privacy). Callers pass only short, non-sensitive text.

import Foundation
import os

/// Thread-safe ring buffer of the last log lines (any thread may log).
final class LogRing: @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [String] = []
    private let capacity: Int

    init(capacity: Int) { self.capacity = capacity }

    func append(_ line: String) {
        lock.lock(); defer { lock.unlock() }
        lines.append(line)
        if lines.count > capacity { lines.removeFirst(lines.count - capacity) }
    }

    func last(_ n: Int) -> [String] {
        lock.lock(); defer { lock.unlock() }
        return Array(lines.suffix(n))
    }
}

enum Log {
    private static let logger = Logger(subsystem: "hk.ahbo.app", category: "ahbo")
    static let ring = LogRing(capacity: 200)

    private static func stamp() -> String {
        Date().formatted(date: .omitted, time: .standard)     // device clock, local (HK) time
    }

    static func info(_ area: String, _ message: String) {
        logger.info("[\(area, privacy: .public)] \(message, privacy: .public)")
        ring.append("\(stamp()) \(area): \(message)")
    }

    static func error(_ area: String, _ message: String) {
        logger.error("[\(area, privacy: .public)] \(message, privacy: .public)")
        ring.append("\(stamp()) \(area) ERROR: \(message)")
    }

    /// Last `n` lines for the settings screen.
    static func recent(_ n: Int = 20) -> [String] { ring.last(n) }

    /// Shows at most the last 4 characters of a secret (PRD §11).
    static func mask(_ secret: String) -> String {
        secret.count > 8 ? "…" + String(secret.suffix(4)) : (secret.isEmpty ? "(unset)" : "…set")
    }
}
