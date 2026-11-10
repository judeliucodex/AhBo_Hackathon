// VoiceNotePlayer.swift — family voice notes (PRD §10.1 play_voice_message, §23.5; CONTRACTS §2.2).
//
// Downloads the AAC with the device token (cached under Caches/voice/<message_id>.m4a), plays it through the
// FixedClipPlayer queue (one audio output at a time, same audio session → echo cancellation hears it), and
// drives the family caption: `say_start {id: say_id, from:"family", fromName, text?, durationSec}`. The mouth
// doesn't move (it's her family's voice, not Ah Bo's), so no `level` is sent. The waveform card's `playing`
// flag is the Kernel's (it sends card_update). The caller replies `playback_done {message_id, say_id}`.

import Foundation

@MainActor
final class VoiceNotePlayer {
    private let player: FixedClipPlayer
    private let api: APIClient

    init(player: FixedClipPlayer, api: APIClient) {
        self.player = player
        self.api = api
    }

    private static var cacheDir: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("voice", isDirectory: true)
    }

    func play(messageId: String, url: String, sayId: String, fromName: String, text: String?, durationS: Double?,
              priority: Int, done: @escaping (String?) -> Void) {
        Task { @MainActor in
            let file = await self.fetch(messageId: messageId, url: url)
            var start: [String: Any] = ["type": "say_start", "id": sayId, "from": "family", "fromName": fromName,
                                        "durationSec": durationS ?? 4.0]
            if let text, !text.isEmpty { start["text"] = text }
            self.player.enqueue(.init(clipId: nil, sayId: sayId, priority: priority, fileURL: file, faceStart: start,
                                      fallbackDuration: durationS ?? 4.0, sendsLevel: false, done: done))
        }
    }

    private func fetch(messageId: String, url: String) async -> URL? {
        let safe = messageId.filter { $0.isLetter || $0.isNumber || $0 == "_" || $0 == "-" }
        let file = Self.cacheDir.appendingPathComponent("\(safe.isEmpty ? "note" : safe).m4a")
        if FileManager.default.fileExists(atPath: file.path) { return file }
        do {
            let data = try await api.get(url)
            try FileManager.default.createDirectory(at: Self.cacheDir, withIntermediateDirectories: true)
            try data.write(to: file)
            return file
        } catch {
            Log.error("voice_note", "download failed: \(error.localizedDescription)")
            return nil
        }
    }

    func stop() { player.stopAll() }
}
