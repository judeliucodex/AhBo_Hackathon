// VoiceSession.swift — the per-session LiveKit room (PRD §3.2, §10.2, §23.5, §24.2). LiveKit Swift SDK 2.18.0.
//
// POST /session {reason, mode} (device token) → {livekit_url, token, room, expires_in, session_id} (or
// {text_only:true} when LiveKit is faked) → Room.connect → publish the mic. The agent's audio plays through
// LiveKit's own audio engine (AudioManager owns the AVAudioSession while connected, §25.4).
// Captions (all LiveKit-only symbols are in this file, so an SDK drift is a local compile error):
//   * text streams on topic `lk.transcription`; attributes `lk.segment_id`, `lk.transcribed_track_id`,
//     `lk.transcription_final` (names verified in the SDK's AttributeTypings.swift and livekit-agents 1.8.11)
//   * her words (transcribed track = our mic track, or sent by us) → face `heard {text}` … `heard {text, final}`
//   * Ah Bo's words → `say_start {id: segment}` (no text: streaming mode), `say_update {id, text, revealed}`
//     with `revealed` in UTF-16 code units, `say_end {id}` when the stream ends or the agent state leaves
//     `speaking` (barge-in: say_end at once)
//   * agent state from the participant attribute `lk.agent.state` (idle|initializing|listening|thinking|speaking)
//   * mouth: the agent participant's `audioLevel` sampled at 20 Hz while it speaks → face `level {v}`

import Foundation
import LiveKit

/// Room delegate on a plain (non-isolated) object; it hops to the main actor with plain values only.
final class RoomEvents: NSObject, RoomDelegate, @unchecked Sendable {
    weak var owner: VoiceSession?

    func room(_ room: Room, participant: Participant, didUpdateAttributes attributes: [String: String]) {
        let state = participant.attributes["lk.agent.state"]
        let isAgent = participant.kind == .agent || state != nil
        guard isAgent, let state else { return }
        Task { @MainActor [weak self] in self?.owner?.agentStateChanged(state) }
    }

    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        let msg = error.map { "\($0)" }
        Task { @MainActor [weak self] in self?.owner?.roomDisconnected(msg) }
    }
}

@MainActor
final class VoiceSession {
    var face: (([String: Any]) -> Void)?
    var deviceEvent: ((_ type: String, _ fields: [String: Any]) -> Void)?
    /// true once the room is joined, false when it is left (wake engine, audio tap, heartbeat).
    var onActiveChange: ((Bool) -> Void)?
    /// Re-apply our own AVAudioSession configuration after LiveKit lets go of it.
    var restoreAudioSession: (() -> Void)?

    private let api: APIClient
    private var room: Room?
    private let events = RoomEvents()
    private var micTrackSid: String?
    private var localIdentity: String?
    private var segment: String?                 // the agent line currently captioned
    private var agentState = "idle"
    private var levelTask: Task<Void, Never>?
    private var joining = false
    private(set) var sessionId: String?

    var isActive: Bool { room != nil }

    init(api: APIClient) {
        self.api = api
        events.owner = self
        // Our clips keep playing between sessions: don't let LiveKit deactivate the shared audio session.
        AudioManager.shared.audioSession.isAutomaticDeactivationEnabled = false
    }

    func open(reason: String, mode: String, sessionId: String?) async {
        guard room == nil, !joining else { return }
        joining = true
        defer { joining = false }
        self.sessionId = sessionId
        do {
            let s = try await api.postJSON("/session", ["reason": reason, "mode": mode])
            if (s["text_only"] as? Bool) == true {
                Log.info("voice", "server says text_only (LiveKit faked): no room")
                return
            }
            guard let url = s["livekit_url"] as? String, let token = s["token"] as? String else {
                throw APIError.badResponse
            }
            let room = Room()
            room.add(delegate: events)
            try await room.registerTextStreamHandler(for: "lk.transcription") { [weak self] reader, identity in
                await self?.transcription(reader: reader, from: identity.stringValue)
            }
            try await room.connect(url: url, token: token)
            self.room = room
            localIdentity = room.localParticipant.identity?.stringValue
            let pub = try await room.localParticipant.setMicrophone(enabled: true)
            micTrackSid = pub?.sid.stringValue
            onActiveChange?(true)
            startLevels()
            Log.info("voice", "joined room \(s["room"] as? String ?? "?")")
        } catch {
            Log.error("voice", "LiveKit join failed: \(error.localizedDescription)")
            deviceEvent?("error", ["code": "livekit_join_failed", "detail": String(error.localizedDescription.prefix(300))])
            await teardown()
        }
    }

    func close() async {
        guard room != nil else { return }
        Log.info("voice", "leaving room")
        await teardown()
    }

    private func teardown() async {
        let r = room
        room = nil
        levelTask?.cancel(); levelTask = nil
        endSegment()
        micTrackSid = nil
        if let r {
            await r.unregisterTextStreamHandler(for: "lk.transcription")
            await r.disconnect()
            onActiveChange?(false)
        }
        restoreAudioSession?()
    }

    // MARK: delegate hops

    fileprivate func agentStateChanged(_ state: String) {
        agentState = state
        if state != "speaking" { endSegment() }          // done speaking, or barge-in (→ listening)
    }

    fileprivate func roomDisconnected(_ error: String?) {
        guard room != nil else { return }
        Log.info("voice", "room disconnected\(error.map { ": \($0)" } ?? "")")
        Task { await self.teardown() }
    }

    // MARK: transcriptions

    private func transcription(reader: TextStreamReader, from identity: String) async {
        let attrs = reader.info.attributes
        let segId = attrs["lk.segment_id"] ?? reader.info.id
        let track = attrs["lk.transcribed_track_id"]
        let isHer = (track != nil && track == micTrackSid) || identity == localIdentity
        var text = ""
        do {
            if isHer {
                for try await chunk in reader {
                    text += chunk
                    face?(["type": "heard", "text": text])
                }
                face?(["type": "heard", "text": text, "final": attrs["lk.transcription_final"] == "true"])
                return
            }
            if segment != segId {
                endSegment()
                segment = segId
                face?(["type": "say_start", "id": segId])
            }
            for try await chunk in reader {
                guard segment == segId else { return }        // cut by barge-in
                text += chunk
                face?(["type": "say_update", "id": segId, "text": text, "revealed": text.utf16.count])
            }
            if segment == segId { endSegment() }
        } catch {
            Log.error("voice", "transcription stream failed: \(error.localizedDescription)")
            if segment == segId { endSegment() }
        }
    }

    private func endSegment() {
        guard let id = segment else { return }
        segment = nil
        face?(["type": "say_end", "id": id])
    }

    // MARK: mouth level

    private func startLevels() {
        levelTask?.cancel()
        levelTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(50))
                guard let self, let room = self.room else { return }
                guard self.agentState == "speaking" else { continue }
                let agent = room.remoteParticipants.values.first { $0.kind == .agent || $0.attributes["lk.agent.state"] != nil }
                if let a = agent {
                    let v = min(1, max(0, Double(a.audioLevel)))
                    self.face?(["type": "level", "v": (v * 100).rounded() / 100])
                }
            }
        }
    }
}
