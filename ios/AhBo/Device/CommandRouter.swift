// CommandRouter.swift — dispatches every server command (PRD §10.1, CONTRACTS §2.2) to the face, the players,
// the camera, the voice session and the wake engine. `route` returns the ack at once; slow work runs in Tasks
// and reports back with device events (playback_done, photo_uploaded, error).

import Foundation

@MainActor
final class CommandRouter {
    private unowned let app: AppModel

    init(app: AppModel) { self.app = app }

    func route(_ cmd: [String: Any]) -> DeviceChannel.AckResult {
        guard let type = cmd["type"] as? String else { return .init(ok: false, error: "no_type") }
        let priority = cmd["priority"] as? Int ?? 2
        switch type {
        case "ui":
            guard let ui = cmd["ui"] as? [String: Any], ui["type"] is String else { return .init(ok: false, error: "bad_ui") }
            app.face.apply(ui)
            return .init(ok: true)

        case "play_fixed":
            guard let clipId = cmd["clip_id"] as? String, let sayId = cmd["say_id"] as? String else {
                return .init(ok: false, error: "bad_fields")
            }
            if let mood = cmd["mood"] as? String { app.face.apply(["type": "mood", "mood": mood]) }
            let known = app.fixed.playFixed(clipId: clipId, sayId: sayId, priority: priority) { [weak app] error in
                var f: [String: Any] = ["clip_id": clipId, "say_id": sayId]
                if let error { f["error"] = error }
                app?.channel.send("playback_done", f)
            }
            return known ? .init(ok: true) : .init(ok: false, error: "unknown_clip")

        case "play_voice_message":
            guard let mid = (cmd["message_id"] as? String) ?? (cmd["id_"] as? String),
                  let url = cmd["url"] as? String, let sayId = cmd["say_id"] as? String else {
                return .init(ok: false, error: "bad_fields")
            }
            app.voiceNotes.play(messageId: mid, url: url, sayId: sayId, fromName: cmd["from_name"] as? String ?? "家人",
                                text: cmd["text"] as? String, durationS: (cmd["duration_s"] as? NSNumber)?.doubleValue,
                                priority: priority) { [weak app] error in
                var f: [String: Any] = ["message_id": mid, "say_id": sayId]
                if let error { f["error"] = error }
                app?.channel.send("playback_done", f)
            }
            return .init(ok: true)

        case "capture_photo":
            guard let incident = cmd["incident_id"] as? String else { return .init(ok: false, error: "bad_fields") }
            guard app.settings.cameraOn else {
                app.channel.send("error", ["code": "camera_disabled", "detail": "camera switched off in settings"])
                return .init(ok: false, error: "camera_disabled")
            }
            app.capturePhoto(incidentId: incident)
            return .init(ok: true)

        case "session.open":
            app.sessionOpened(reason: cmd["reason"] as? String ?? "kernel", mode: cmd["mode"] as? String ?? "companion",
                              sessionId: cmd["session_id"] as? String, textOnly: (cmd["text_only"] as? Bool) ?? false)
            return .init(ok: true)

        case "session.close":
            app.sessionClosed(why: cmd["why"] as? String)
            return .init(ok: true)

        case "wake.enable":
            app.wake.enabledByServer = (cmd["on"] as? Bool) ?? true
            return .init(ok: true)

        case "config.update":
            app.applyConfig(cmd)
            return .init(ok: true)

        case "stop_all":
            app.stopAll()
            return .init(ok: true)

        default:
            Log.error("router", "unknown command \(type)")
            return .init(ok: false, error: "unknown_type")
        }
    }
}
