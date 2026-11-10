// AppModel.swift — the app's state and the single owner of module lifecycles (PRD §25.2).
// Native code is plumbing: logic, state, text and timing live on the server or in the face.

import AVFoundation
import Observation
import Speech
import SwiftUI
import UIKit

@MainActor
@Observable
final class AppModel {
    static var appVersion: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0" }

    // observable state (settings screen, debug overlay)
    var connection: ConnectionStatus = .notConfigured
    var inSession = false
    var wakeEngineKind: WakeEngineKind = .none
    var fixedCacheVersion = ""
    var showSettings = false
    var lastLog = ""

    // modules
    @ObservationIgnored let settings = AppSettings()
    @ObservationIgnored let face = FaceController()
    @ObservationIgnored let channel = DeviceChannel()
    @ObservationIgnored let api = APIClient()
    @ObservationIgnored let fixed = FixedClipPlayer()
    @ObservationIgnored let photo = PhotoCapture()
    @ObservationIgnored let wake = WakeWordEngine()
    @ObservationIgnored let mirror = ReminderMirror()
    @ObservationIgnored private(set) var voiceNotes: VoiceNotePlayer!
    @ObservationIgnored private(set) var voice: VoiceSession!
    @ObservationIgnored private var router: CommandRouter!
    @ObservationIgnored private var started = false
    @ObservationIgnored private var textOnly = false
    @ObservationIgnored private var wakeSessionAt: Date?
    @ObservationIgnored private var heardInSession = false
    @ObservationIgnored private var provisionalListening: Task<Void, Never>?

    init() {
        voiceNotes = VoiceNotePlayer(player: fixed, api: api)
        voice = VoiceSession(api: api)
        router = CommandRouter(app: self)
        wire()
    }

    // MARK: start-up

    func start() {
        guard !started else { return }
        started = true
        UIApplication.shared.isIdleTimerDisabled = true
        UIDevice.current.isBatteryMonitoringEnabled = true
        configureAudioSession()
        observeAudioSession()
        fixedCacheVersion = fixed.cacheVersion
        Task {
            await requestPermissions()
            await wake.prepare()
            reconnect()
        }
    }

    func reconnect() {
        api.base = settings.httpBase
        channel.start(url: settings.deviceChannelURL, secret: settings.appSecret)
    }

    private func wire() {
        face.onEvent = { [weak self] evt in self?.faceEvent(evt) }
        face.onSettingsGesture = { [weak self] in self?.showSettings = true }

        channel.helloFields = { [weak self] in
            guard let self else { return [:] }
            return ["device_id": settings.deviceId, "app_version": Self.appVersion,
                    "fixed_cache_version": fixed.cacheVersion, "wake_engine": wake.kind.rawValue]
        }
        channel.heartbeatFields = { [weak self] in
            guard let self else { return [:] }
            let level = UIDevice.current.batteryLevel
            return ["battery": level < 0 ? NSNull() : Double(level), "thermal": Self.thermal(),
                    "in_session": inSession]
        }
        channel.onStatus = { [weak self] s in self?.connection = s }
        channel.onWelcome = { [weak self] msg in self?.welcome(msg) }
        channel.onCommand = { [weak self] cmd in
            self?.router.route(cmd) ?? .init(ok: false, error: "app_gone")
        }
        channel.onOffline = { [weak self] off in self?.offlineChanged(off) }

        fixed.face = { [weak self] cmd in self?.face.apply(cmd) }
        fixed.onBusyChange = { [weak self] busy in self?.wake.setAudioBusy(busy) }

        voice.face = { [weak self] cmd in
            if (cmd["type"] as? String) == "heard" { self?.heardInSession = true }
            self?.face.apply(cmd)
        }
        voice.deviceEvent = { [weak self] type, fields in self?.channel.send(type, fields) }
        voice.onActiveChange = { active in Log.info("app", active ? "voice room active" : "voice room closed") }
        voice.restoreAudioSession = { [weak self] in self?.configureAudioSession() }

        wake.onKindChange = { [weak self] k in self?.wakeEngineKind = k }
        wake.onHit = { [weak self] in self?.wakeHit() }
        wake.onWake = { [weak self] remainder in self?.wakeConfirmed(remainder: remainder) }

        mirror.face = { [weak self] cmd in self?.face.apply(cmd) }
        mirror.playClip = { [weak self] clipId, sayId in
            _ = self?.fixed.playFixed(clipId: clipId, sayId: sayId, priority: 1) { _ in }
        }
    }

    // MARK: device channel

    private func welcome(_ msg: [String: Any]) {
        api.deviceToken = msg["device_token"] as? String
        textOnly = (msg["text_sessions"] as? Bool) ?? false
        if let cfg = msg["config"] as? [String: Any] { applyConfig(cfg) }
        if let v = msg["fixed_cache_version"] as? String { refreshFixed(v) }
        for c in mirror.drainConfirmations() {
            channel.send("face_event", ["event": ["type": "reminder_confirmed", "slot_id": c.slot_id, "at": c.at,
                                                  "source": "touch_offline"]])
        }
        if settings.interruptionOn == false { sendInterruptionSetting(false) }
    }

    func applyConfig(_ cfg: [String: Any]) {
        if let w = cfg["wake"] as? [String: Any] { wake.applyServerConfig(w) }
        if let m = cfg["reminder_mirror"] { mirror.update(m) }
        if let i = cfg["interruption_enabled"] as? Bool { settings.interruptionOn = i }
        if let v = cfg["fixed_cache_version"] as? String { refreshFixed(v) }
        // quiet_hours: the server sets the sleepy mood; nothing to do natively.
    }

    private func refreshFixed(_ version: String) {
        Task {
            await fixed.refresh(to: version, api: api)
            fixedCacheVersion = fixed.cacheVersion
        }
    }

    private func offlineChanged(_ off: Bool) {
        face.apply(["type": "offline", "on": off])
        mirror.setOffline(off)
    }

    /// The owner's interruption toggle goes to the server (requested contract addition: `settings` event).
    func sendInterruptionSetting(_ on: Bool) {
        channel.send("settings", ["interruption_enabled": on])
    }

    // MARK: face events (CONTRACTS §3.3)

    private func faceEvent(_ evt: [String: Any]) {
        let type = evt["type"] as? String ?? ""
        switch type {
        case "ok_tapped":
            channel.send("touch_ok")
        case "tap" where (evt["target"] as? String) == "face":
            if channel.isOffline {
                if !mirror.tapWhileOffline() { playOfflineLine() }
                return
            }
            let idle = (face.cache.mood == "idle" || face.cache.mood == "sleepy") && !inSession && !face.cache.okButtonShown
            if idle {
                face.applyTransient(["type": "expr", "expr": "wake"])
                channel.send("wake", ["trigger": "touch"])
            } else {
                channel.send("face_event", ["event": evt])
            }
        case "tap":
            if channel.isOffline { _ = mirror.tapWhileOffline() }
            channel.send("face_event", ["event": evt])
        default:
            if type == "error" { Log.error("face", evt["message"] as? String ?? "?") }
            channel.send("face_event", ["event": evt])
        }
    }

    private func playOfflineLine() {
        fixed.playFixed(clipId: "G2", sayId: "say_local_G2_\(FrameID.make())", priority: 2) { _ in }
    }

    // MARK: wake word

    private func wakeHit() {
        guard !channel.isOffline else { return }
        face.applyTransient(["type": "mood", "mood": "listening"])     // provisional; the server confirms
        provisionalListening?.cancel()
        provisionalListening = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard let self, !Task.isCancelled, !self.inSession else { return }
            self.face.applyTransient(["type": "mood", "mood": self.face.cache.mood])   // no session: undo
        }
    }

    private func wakeConfirmed(remainder: String) {
        if channel.isOffline { playOfflineLine(); return }
        var f: [String: Any] = ["trigger": "wakeword"]
        if !remainder.isEmpty { f["s2_text"] = remainder }
        wakeSessionAt = Date()
        channel.send("wake", f)
    }

    func refreshWake() { wake.enabledBySettings = settings.wakeOn }

    // MARK: sessions

    func sessionOpened(reason: String, mode: String, sessionId: String?, textOnly: Bool) {
        inSession = true
        heardInSession = false
        provisionalListening?.cancel()
        let join = !textOnly && !self.textOnly && settings.liveKitOn
        if !textOnly && !self.textOnly && !settings.liveKitOn {
            channel.send("error", ["code": "livekit_disabled", "detail": "LiveKit switched off in settings"])
        }
        Task {
            await wake.pauseForSession()                         // the wake tap releases the mic first
            guard join else { return }                           // text session (fake LiveKit): nothing to join
            await voice.open(reason: reason, mode: mode, sessionId: sessionId)
        }
    }

    func sessionClosed(why: String?) {
        let rejected = why == "wake_rejected" || why == "rejected_s3"
            || (wakeSessionAt.map { Date().timeIntervalSince($0) < 4 } ?? false && !heardInSession)
        if rejected { wake.noteRejected() }
        wakeSessionAt = nil
        inSession = false
        Task {
            await voice.close()
            wake.inSession = false
        }
    }

    func stopAll() {
        fixed.stopAll()
        Task { await voice.close() }
        face.apply(["type": "reset"])
    }

    // MARK: camera

    func capturePhoto(incidentId: String) {
        Task {
            do {
                let jpeg = try await photo.capture()
                let res = try await api.postMultipart("/media/photo", fields: ["incident_id": incidentId], fileField: "file",
                                                      fileName: "\(incidentId).jpg", mime: "image/jpeg", file: jpeg)
                guard let pid = res["photo_id"] as? String else { throw APIError.badResponse }
                channel.send("photo_uploaded", ["photo_id": pid, "incident_id": incidentId])
                Log.info("photo", "uploaded \(pid)")
            } catch {
                Log.error("photo", error.localizedDescription)
                channel.send("error", ["code": "camera_failed", "detail": String(error.localizedDescription.prefix(300))])
            }
        }
    }

    // MARK: audio session (§25.4)

    func configureAudioSession() {
        let s = AVAudioSession.sharedInstance()
        do {
            try s.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothA2DP])
            try s.setActive(true)
        } catch {
            Log.error("audio", "audio session: \(error.localizedDescription)")
        }
    }

    private func observeAudioSession() {
        let nc = NotificationCenter.default
        for name in [AVAudioSession.routeChangeNotification, AVAudioSession.interruptionNotification,
                     AVAudioSession.mediaServicesWereResetNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] note in
                let n = note.name.rawValue
                MainActor.assumeIsolated {
                    guard let self, !self.voice.isActive else { return }    // LiveKit owns audio during a session
                    Log.info("audio", n)
                    if n == AVAudioSession.mediaServicesWereResetNotification.rawValue { self.configureAudioSession() }
                    self.wake.restart(n)
                }
            }
        }
    }

    private func requestPermissions() async {
        let mic = await AVAudioApplication.requestRecordPermission()
        if !mic { channel.send("error", ["code": "mic_denied", "detail": "microphone permission denied"]) }
        wake.micAllowed = mic
        let speech = await Self.speechAuthorization()
        if speech != .authorized { Log.info("wake", "speech recognition authorization: \(speech.rawValue)") }
        wake.enabledBySettings = settings.wakeOn
    }

    /// Nonisolated on purpose: the callback arrives on a background queue (a main-actor closure would trap).
    nonisolated private static func speechAuthorization() async -> SFSpeechRecognizerAuthorizationStatus {
        await withCheckedContinuation { c in
            SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) }
        }
    }

    private static func thermal() -> String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: "nominal"
        case .fair: "fair"
        case .serious: "serious"
        case .critical: "critical"
        @unknown default: "nominal"
        }
    }
}
