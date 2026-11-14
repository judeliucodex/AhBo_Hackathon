// AudioTap.swift — AVAudioEngine input tap feeding the wake recognizer while no session is open (PRD §25.2).
//
//  * converts the mic buffers to the analyzer's format (AVAudioConverter) and hands them to `sink`
//  * computes the RMS energy of every buffer for the utterance-start rule (§20.4.0 rule 2): an "onset" is
//    recorded when speech begins after ≥ `silenceMs` of silence; onsets are kept on the analyzer's audio
//    timeline (seconds of audio fed since start) so they can be compared with result time ranges
//  * stops when a LiveKit session opens (LiveKit owns the mic) and restarts when it closes
// Not actor-isolated: the tap block runs on the audio render thread, so state is behind a lock.

@preconcurrency import AVFoundation
import Foundation

final class AudioTap: @unchecked Sendable {
    struct Onset: Sendable { let audioTime: Double; let wallTime: Date }

    private let lock = NSLock()
    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private var targetFormat: AVAudioFormat?
    private var fedSeconds: Double = 0
    private var silentFor: Double = 10           // seconds of continuous silence so far
    private var inSpeech = false
    private var onsets: [Onset] = []
    private var lastSpeechWall = Date.distantPast
    private var lastLevel: Float = 0

    /// dBFS below which a buffer counts as silence. 13 Nov: raised -45 → -30 — the wake word needs a
    /// clearly audible voice; ambient room noise and far-away speech no longer arm the wake engine.
    var silenceThresholdDB: Float = -30
    var silenceMs: Int = 300

    /// Start tapping the mic. `sink` gets buffers in `format` (the analyzer's best format).
    func start(format: AVAudioFormat, voiceProcessing: Bool, sink: @escaping @Sendable (AVAudioPCMBuffer) -> Void) throws {
        stop()
        let e = AVAudioEngine()
        let input = e.inputNode
        if voiceProcessing {
            do { try input.setVoiceProcessingEnabled(true) } catch { Log.error("tap", "voice processing unavailable: \(error.localizedDescription)") }
        }
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else { throw NSError(domain: "AhBo.AudioTap", code: 1) }
        guard let conv = AVAudioConverter(from: inFormat, to: format) else { throw NSError(domain: "AhBo.AudioTap", code: 2) }
        lock.lock()
        converter = conv
        targetFormat = format
        fedSeconds = 0
        silentFor = 10
        inSpeech = false
        onsets.removeAll()
        lock.unlock()
        input.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buffer, _ in
            self?.process(buffer, sink: sink)
        }
        e.prepare()
        try e.start()
        lock.lock(); engine = e; lock.unlock()
        Log.info("tap", "mic tap started (\(Int(inFormat.sampleRate)) Hz → \(Int(format.sampleRate)) Hz)")
    }

    func stop() {
        lock.lock()
        let e = engine
        engine = nil
        converter = nil
        lock.unlock()
        guard let e else { return }
        e.inputNode.removeTap(onBus: 0)
        e.stop()
        Log.info("tap", "mic tap stopped")
    }

    var isRunning: Bool { lock.lock(); defer { lock.unlock() }; return engine != nil }

    /// Speech onsets (after ≥ silenceMs of silence) within the last `seconds` of wall time.
    func recentOnsets(within seconds: Double) -> [Onset] {
        lock.lock(); defer { lock.unlock() }
        let cutoff = Date().addingTimeInterval(-seconds)
        return onsets.filter { $0.wallTime >= cutoff }
    }

    /// True if an onset lies within `tolerance` s of `audioTime` (the start of a recognizer result).
    func hasOnset(near audioTime: Double, tolerance: Double = 0.6) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return onsets.contains { abs($0.audioTime - audioTime) <= tolerance }
    }

    /// Seconds since the mic last carried speech (watchdog: "no results for 60 s while the mic shows speech").
    var secondsSinceSpeech: Double { lock.lock(); defer { lock.unlock() }; return Date().timeIntervalSince(lastSpeechWall) }

    private func process(_ buffer: AVAudioPCMBuffer, sink: @Sendable (AVAudioPCMBuffer) -> Void) {
        let db = Self.rmsDB(buffer)
        let dur = Double(buffer.frameLength) / buffer.format.sampleRate
        lock.lock()
        let isSpeech = db > silenceThresholdDB
        if isSpeech {
            if !inSpeech, silentFor * 1000 >= Double(silenceMs) {
                onsets.append(Onset(audioTime: fedSeconds, wallTime: Date()))
                if onsets.count > 64 { onsets.removeFirst(onsets.count - 64) }
            }
            inSpeech = true
            silentFor = 0
            lastSpeechWall = Date()
        } else {
            silentFor += dur
            if silentFor * 1000 >= Double(silenceMs) { inSpeech = false }
        }
        fedSeconds += dur
        let conv = converter
        let target = targetFormat
        lock.unlock()
        guard let conv, let target else { return }
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 64)
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var fed = false
        var error: NSError?
        let status = conv.convert(to: out, error: &error) { _, inputStatus in
            if fed { inputStatus.pointee = .noDataNow; return nil }
            fed = true
            inputStatus.pointee = .haveData
            return buffer
        }
        if status == .error || out.frameLength == 0 { return }
        sink(out)
    }

    static func rmsDB(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let ch = buffer.floatChannelData, buffer.frameLength > 0 else { return -160 }
        let n = Int(buffer.frameLength)
        var sum: Float = 0
        let p = ch[0]
        for i in 0..<n { sum += p[i] * p[i] }
        let rms = (sum / Float(n)).squareRoot()
        return rms > 0 ? 20 * log10(rms) : -160
    }
}
