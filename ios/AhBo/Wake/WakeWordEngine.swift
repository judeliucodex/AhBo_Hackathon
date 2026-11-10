// WakeWordEngine.swift — always-on 「喂阿寶」 (PRD §20.4.0 v1 = Stage 2 only, §20.4.5, §25.2). Cantonese ONLY.
//
// Recognizer (behind the WakeRecognizer protocol so an SDK drift stays local to this file):
//   1. SpeechAnalyzer + SpeechTranscriber, locale zh_HK, else yue (iPadOS 26; model installed via AssetInventory)
//   2. else SpeechAnalyzer + DictationTranscriber, zh-HK (else yue)
//   3. else none (touch only)
//   Never a Mandarin locale: every candidate must pass `isCantonese` (language yue, or zh with region HK).
// Rules: start of utterance (≥ 300 ms silence before it, from the AudioTap energy onsets) · 「喂」 required
// (JyutpingMatcher) · the phrase in 2 consecutive updates of the same utterance or in its final result ·
// 5 s cooldown after a Stage-3 reject · no hits while a clip plays + 0.5 s (self-wake) · paused during
// sessions and when the server says wake.enable {on:false} (AWAITING_OK) · watchdog restarts (error, route
// change, interruption, 60 s without results while the mic shows speech, every 30 min).
// Privacy: ambient transcripts are never logged or stored; only "wake hit (rule)" is logged.

@preconcurrency import AVFoundation
import CoreMedia
import Foundation
import Speech

enum WakeEngineKind: String, Sendable {
    case speechTranscriber = "speech_transcriber"
    case dictation
    case none
}

struct RecognizedText: Sendable {
    let text: String
    let isFinal: Bool
    let startSec: Double?        // start of the result on the analyzer's audio timeline, if known
}

/// The only surface the engine needs from Apple's Speech framework.
protocol WakeRecognizer: AnyObject, Sendable {
    var kind: WakeEngineKind { get }
    var localeID: String { get }
    func start(tap: AudioTap, onResult: @escaping @Sendable (RecognizedText) -> Void,
               onError: @escaping @Sendable (String) -> Void) async throws
    func stop() async
}

// MARK: - SpeechAnalyzer implementation (iPadOS 26)

final class AnalyzerRecognizer: WakeRecognizer, @unchecked Sendable {
    let kind: WakeEngineKind
    let locale: Locale
    var localeID: String { locale.identifier }
    private var analyzer: SpeechAnalyzer?
    private var input: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private weak var tap: AudioTap?

    init(kind: WakeEngineKind, locale: Locale) {
        self.kind = kind
        self.locale = locale
    }

    static func isCantonese(_ l: Locale) -> Bool {
        let lang = l.language.languageCode?.identifier
        let region = l.region?.identifier ?? l.language.region?.identifier
        return lang == "yue" || (lang == "zh" && region == "HK")
    }

    /// PRD order: zh_HK, else yue (HK first; yue_CN last because it writes Simplified characters).
    static func pick(from supported: [Locale]) -> Locale? {
        let cantonese = supported.filter(isCantonese)
        func norm(_ l: Locale) -> String { l.identifier.replacingOccurrences(of: "-", with: "_").lowercased() }
        for want in ["zh_hk", "zh_hant_hk", "yue_hk", "yue_hant_hk", "yue", "yue_hant", "yue_cn", "yue_hans_cn"] {
            if let l = cantonese.first(where: { norm($0) == want }) { return l }
        }
        return cantonese.first
    }

    /// Choose the engine for this device, or nil (touch only).
    static func choose() async -> AnalyzerRecognizer? {
        if SpeechTranscriber.isAvailable, let l = pick(from: await SpeechTranscriber.supportedLocales) {
            return AnalyzerRecognizer(kind: .speechTranscriber, locale: l)
        }
        if let l = pick(from: await DictationTranscriber.supportedLocales) {
            return AnalyzerRecognizer(kind: .dictation, locale: l)
        }
        return nil
    }

    private func makeModule(onResult: @escaping @Sendable (RecognizedText) -> Void,
                            onError: @escaping @Sendable (String) -> Void) -> any SpeechModule {
        switch kind {
        case .dictation:
            let t = DictationTranscriber(locale: locale, contentHints: [.shortForm, .farField], transcriptionOptions: [],
                                         reportingOptions: [.volatileResults, .frequentFinalization],
                                         attributeOptions: [.audioTimeRange])
            resultsTask = Task {
                do {
                    for try await r in t.results {
                        onResult(RecognizedText(text: String(r.text.characters), isFinal: r.isFinal,
                                                startSec: Self.seconds(r.range.start)))
                    }
                } catch { onError("dictation results: \(error.localizedDescription)") }
            }
            return t
        default:
            let t = SpeechTranscriber(locale: locale, transcriptionOptions: [],
                                      reportingOptions: [.volatileResults, .fastResults],
                                      attributeOptions: [.audioTimeRange])
            resultsTask = Task {
                do {
                    for try await r in t.results {
                        onResult(RecognizedText(text: String(r.text.characters), isFinal: r.isFinal,
                                                startSec: Self.seconds(r.range.start)))
                    }
                } catch { onError("transcriber results: \(error.localizedDescription)") }
            }
            return t
        }
    }

    private static func seconds(_ t: CMTime) -> Double? {
        t.isValid && !t.isIndefinite ? t.seconds : nil
    }

    func start(tap: AudioTap, onResult: @escaping @Sendable (RecognizedText) -> Void,
               onError: @escaping @Sendable (String) -> Void) async throws {
        let module = makeModule(onResult: onResult, onError: onError)
        // Model assets (§20.4.0 day-1 check): download/install the Cantonese model if needed.
        if let req = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
            Log.info("wake", "downloading speech model for \(localeID)")
            try await req.downloadAndInstall()
        }
        _ = try? await AssetInventory.reserve(locale: locale)
        let analyzer = SpeechAnalyzer(modules: [module])
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [module]) else {
            throw NSError(domain: "AhBo.Wake", code: 1, userInfo: [NSLocalizedDescriptionKey: "no compatible audio format"])
        }
        let (stream, cont) = AsyncStream.makeStream(of: AnalyzerInput.self)
        try await analyzer.start(inputSequence: stream)
        self.analyzer = analyzer
        self.input = cont
        self.tap = tap
        try tap.start(format: format, voiceProcessing: true) { buffer in
            cont.yield(AnalyzerInput(buffer: buffer))
        }
    }

    func stop() async {
        tap?.stop()
        input?.finish()
        input = nil
        resultsTask?.cancel()
        resultsTask = nil
        if let a = analyzer { await a.cancelAndFinishNow() }
        analyzer = nil
    }
}

// MARK: - Engine

@MainActor
final class WakeWordEngine {
    private(set) var kind: WakeEngineKind = .none
    var config = WakeMatchConfig() { didSet { matcher?.config = config; tap.silenceMs = config.utteranceStartSilenceMs } }
    /// remainder = her words after the wake phrase (sent as `wake {s2_text}`, §20.4.5 rule 2 fallback).
    var onHit: (() -> Void)?
    var onWake: ((_ remainder: String) -> Void)?
    var onKindChange: ((WakeEngineKind) -> Void)?

    // switches (all must allow it)
    var enabledBySettings = true { didSet { refresh() } }
    var enabledByServer = true { didSet { refresh() } }
    var inSession = false { didSet { refresh() } }
    var micAllowed = false { didSet { refresh() } }

    private let tap = AudioTap()
    private var matcher: JyutpingMatcher?
    private var recognizer: (any WakeRecognizer)?
    private var chosen = false
    private var running = false
    private var transitioning = false
    private var cooldownUntil = Date.distantPast
    private var suppressUntil = Date.distantPast
    private var audioBusy = false
    private var streak: (key: String, count: Int)?
    private var firedKey: String?
    private var pending: (key: String, remainder: String, deadline: Date)?
    private var pendingTask: Task<Void, Never>?
    private var pausedAfterWakeUntil = Date.distantPast
    private var lastResultAt = Date()
    private var startedAt = Date()
    private var watchdog: Task<Void, Never>?

    init() {
        // Defaults from the bundled brain/config/wake_match.yaml (observers don't run inside init, so set
        // everything explicitly here); the server's `welcome.config.wake` overrides later.
        var cfg = WakeMatchConfig()
        if let url = Bundle.main.url(forResource: "wake_match", withExtension: "yaml"),
           let text = try? String(contentsOf: url, encoding: .utf8) {
            cfg = WakeMatchConfig.parseYAML(text)
        }
        config = cfg
        tap.silenceMs = cfg.utteranceStartSilenceMs
        matcher = JyutpingMatcher.load(config: cfg)
        if matcher == nil { Log.error("wake", "wake_homophones.json missing: wake word off") }
    }

    /// Server `wake` config (welcome / config.update).
    func applyServerConfig(_ d: [String: Any]) { config = config.merged(d) }

    /// FixedClipPlayer busy flag (self-wake suppression: while playing + 0.5 s).
    func setAudioBusy(_ busy: Bool) {
        audioBusy = busy
        if !busy { suppressUntil = Date().addingTimeInterval(0.5) }
    }

    /// Stage 3 rejected the last wake (or the server closed the session as a reject): 5 s cooldown.
    func noteRejected() {
        cooldownUntil = Date().addingTimeInterval(config.cooldownAfterRejectS)
        Log.info("wake", "stage-3 reject: cooldown \(config.cooldownAfterRejectS) s")
    }

    /// Pick the engine once (reported in `hello.wake_engine`).
    func prepare() async {
        guard !chosen else { return }
        chosen = true
        if let r = await AnalyzerRecognizer.choose() {
            recognizer = r
            kind = r.kind
            Log.info("wake", "engine \(r.kind.rawValue), locale \(r.localeID)")
        } else {
            kind = .none
            Log.info("wake", "no Cantonese on-device recognizer: touch only (wake_engine none)")
        }
        onKindChange?(kind)
        refresh()
    }

    private var shouldRun: Bool {
        enabledBySettings && enabledByServer && !inSession && micAllowed && recognizer != nil && matcher != nil
            && Date() >= pausedAfterWakeUntil
    }

    func refresh() {
        guard !transitioning else { return }
        if shouldRun && !running { Task { await self.startRecognizer() } }
        if !shouldRun && running { Task { await self.stopRecognizer() } }
    }

    /// A session is opening: release the mic now (LiveKit takes it), before joining the room.
    func pauseForSession() async {
        inSession = true
        if running { await stopRecognizer() }
    }

    /// Watchdog / route change / interruption: restart if it should be running.
    func restart(_ why: String) {
        guard running else { refresh(); return }
        Log.info("wake", "restart (\(why))")
        Task {
            await self.stopRecognizer()
            try? await Task.sleep(for: .milliseconds(300))
            self.refresh()
        }
    }

    private func startRecognizer() async {
        guard let r = recognizer, !running, !transitioning else { return }
        transitioning = true
        defer { transitioning = false }
        do {
            tap.silenceMs = config.utteranceStartSilenceMs
            try await r.start(tap: tap, onResult: { [weak self] res in
                Task { @MainActor in self?.handle(res) }
            }, onError: { [weak self] msg in
                Task { @MainActor in self?.recognizerFailed(msg) }
            })
            running = true
            startedAt = Date()
            lastResultAt = Date()
            startWatchdog()
            Log.info("wake", "listening for 喂阿寶")
        } catch {
            Log.error("wake", "start failed: \(error.localizedDescription)")
            await r.stop()
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(10))
                self?.refresh()
            }
        }
        if !shouldRun { await stopRecognizer() }
    }

    private func stopRecognizer() async {
        guard running, let r = recognizer else { return }
        transitioning = true
        running = false
        watchdog?.cancel(); watchdog = nil
        await r.stop()
        streak = nil
        transitioning = false
        Log.info("wake", "paused")
        if shouldRun { refresh() }
    }

    private func recognizerFailed(_ msg: String) {
        guard running else { return }
        Log.error("wake", msg)
        restart("error")
    }

    private func startWatchdog() {
        watchdog?.cancel()
        watchdog = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(15))
                guard let self, self.running else { return }
                if Date().timeIntervalSince(self.startedAt) > 30 * 60 { self.restart("30 min refresh"); return }
                if Date().timeIntervalSince(self.lastResultAt) > 60, self.tap.secondsSinceSpeech < 5 {
                    self.restart("no results for 60 s while the mic hears speech"); return
                }
            }
        }
    }

    // MARK: matching

    private func handle(_ r: RecognizedText) {
        guard running, let matcher else { return }
        lastResultAt = Date()
        let key = r.startSec.map { String(format: "%.1f", $0) } ?? "wall"
        if let p = pending, p.key == key {               // after the hit: collect the chained command
            let m = matcher.match(r.text)
            if m.ok { pending?.remainder = m.remainder }
            if r.isFinal { finishPending() }
            return
        }
        let now = Date()
        if now < cooldownUntil || audioBusy || now < suppressUntil { streak = nil; return }
        if key == firedKey { return }
        let m = matcher.match(r.text)
        guard m.ok else { if streak?.key == key { streak = nil }; return }
        // Utterance start: the result must begin at an energy onset that followed ≥ 300 ms of silence.
        let atStart: Bool
        if let s = r.startSec { atStart = tap.hasOnset(near: s) } else { atStart = !tap.recentOnsets(within: 4).isEmpty }
        guard atStart else { return }
        // Stability: two consecutive updates of this utterance, or its final result.
        var confirmed = r.isFinal || !config.requireTwoUpdates
        if !confirmed {
            if let s = streak, s.key == key { confirmed = s.count + 1 >= 2 } else { streak = (key, 1) }
        }
        guard confirmed else { return }
        streak = nil
        firedKey = key
        Log.info("wake", "wake hit (\(m.rule))")
        onHit?()                                          // face → listening now (≤ 700 ms budget)
        pending = (key, m.remainder, Date().addingTimeInterval(5))
        if r.isFinal { finishPending(); return }
        pendingTask?.cancel()
        pendingTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(150))
                guard let self, let p = self.pending else { return }
                if Date() >= p.deadline || self.tap.secondsSinceSpeech >= 0.8 { self.finishPending(); return }
            }
        }
    }

    private func finishPending() {
        pendingTask?.cancel(); pendingTask = nil
        guard let p = pending else { return }
        pending = nil
        // Free the mic for LiveKit; resume after the session (or after 8 s if none opens).
        pausedAfterWakeUntil = Date().addingTimeInterval(8)
        refresh()
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(8.2))
            self?.refresh()
        }
        onWake?(p.remainder)
    }
}
