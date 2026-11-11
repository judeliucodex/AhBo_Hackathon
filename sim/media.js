/* Fixed clips and voice notes (PRD §10.3, §23.5, §25.2 FixedClipPlayer / VoiceNotePlayer; CONTRACTS §2.2).
 *
 * One <audio> element plays everything. The face's say_start is sent in the same tick that playback starts
 * (the play() promise resolving). If the audio cannot play (autoplay blocked, undecodable or silent fake
 * file, network error, stall), the face is driven by the manifest/command duration instead, and say_end +
 * playback_done are still sent: playback never hangs. Mouth level (~20 Hz) comes from a WebAudio analyser
 * on the element when an unlocked AudioContext exists and the clip actually carries sound.
 */

const START_TIMEOUT_MS = 3000;   // play() neither started nor failed by then → drive by duration
const OVERRUN_S = 3;             // audio still "playing" this long past its duration → force the end

export class ManifestCache {
  constructor(log) { this.manifest = null; this.loading = null; this.log = log; }
  get version() { return this.manifest ? this.manifest.version : null; }

  /** Fetch /media/fixed/manifest.json unless the cached one already has `wantVersion`. */
  async ensure(wantVersion) {
    if (this.manifest && (!wantVersion || wantVersion === this.manifest.version)) return this.manifest;
    if (!this.loading) {
      this.loading = fetch("/media/fixed/manifest.json", { credentials: "same-origin", cache: "no-cache" })
        .then(r => { if (!r.ok) throw new Error(`manifest HTTP ${r.status}`); return r.json(); })
        .then(m => { this.manifest = m; this.log(`manifest ${m.version} (${Object.keys(m.clips || {}).length} clips)`); return m; })
        .finally(() => { this.loading = null; });
    }
    return this.loading;
  }

  async clip(clipId) {
    let m = await this.ensure();
    if (!m.clips || !m.clips[clipId]) { this.manifest = null; m = await this.ensure(); }   // stale cache
    const c = m.clips && m.clips[clipId];
    if (!c) throw new Error(`clip ${clipId} not in manifest`);
    return c;
  }
}

export class Player {
  /**
   * @param {object} o
   * @param {(cmd: object) => void} o.face     send a face command
   * @param {(s: string) => void} o.log
   */
  constructor(o) {
    Object.assign(this, o);
    this.audio = new Audio();
    this.audio.preload = "auto";
    this.cur = null;          // the playback in progress
    this.waiting = [];        // lower-priority playbacks queued behind it
    this.actx = null; this.analyser = null; this.srcNode = null;
  }

  /** Called from a user gesture: creates/resumes the AudioContext (autoplay unlock) and wires the analyser. */
  unlock() {
    try {
      if (!this.actx) this.actx = new (window.AudioContext || window.webkitAudioContext)();
      if (this.actx.state !== "running") this.actx.resume();
      if (!this.srcNode) {
        this.srcNode = this.actx.createMediaElementSource(this.audio);
        this.analyser = this.actx.createAnalyser();
        this.analyser.fftSize = 512;
        this.srcNode.connect(this.analyser).connect(this.actx.destination);
      }
      // A muted, empty play inside the gesture also unlocks HTMLMediaElement autoplay in Safari.
    } catch (e) { this.log("audio unlock failed: " + e.message); }
  }

  get playing() { return !!this.cur; }

  /**
   * @param {object} p
   * @param {string} p.src              audio URL
   * @param {string} p.sayId
   * @param {number} p.priority         0..3 (lower = more important)
   * @param {(audioDurationSec: number|null) => object} p.faceStart  face say_start for this line
   * @param {number} p.fallbackSec      how long to drive the face if the audio can't play
   * @param {boolean} p.level           send mouth level (Ah Bo's voice only)
   * @param {(result: {mode: string, error?: string}) => void} p.done   after say_end
   */
  play(p) {
    if (this.cur && p.priority > this.cur.priority) { this.waiting.push(p); return "queued"; }
    if (this.cur) this._finish(this.cur, "interrupted");
    this._start(p);
    return "started";
  }

  /** stop_all: end everything now (say_end + done with error "stopped"). */
  stopAll() {
    const q = this.waiting; this.waiting = [];
    if (this.cur) this._finish(this.cur, "stopped");
    q.forEach(p => p.done({ mode: "skipped", error: "stopped" }));
  }

  _start(p) {
    const a = this.audio;
    this.cur = p;
    p.mode = "pending";
    p.timers = [];
    const after = (ms, fn) => p.timers.push(setTimeout(fn, ms));

    const driveByTimer = err => {           // audio failed: the face still gets its line, then we end
      if (this.cur !== p || p.mode === "timer") return;
      const wasAudio = p.mode === "audio";
      p.mode = "timer"; p.error = err;
      try { a.pause(); } catch (e) { /* ignore */ }
      if (!wasAudio) { p.t0 = performance.now(); this.face(p.faceStart(null)); }
      const left = Math.max(0.2, p.fallbackSec - (performance.now() - p.t0) / 1000);
      this.log(`audio unavailable (${err}); driving the face for ${left.toFixed(1)} s`);
      after(left * 1000, () => this._finish(p, err));
    };

    a.onended = () => { if (this.cur === p && p.mode === "audio") this._finish(p, null); };
    a.onerror = () => {
      const code = a.error ? a.error.code : 0;
      driveByTimer(code === 4 ? "decode_failed" : code === 2 ? "network_error" : "audio_error");
    };
    a.src = p.src;
    a.currentTime = 0;
    let pr;
    try { pr = a.play(); } catch (e) { pr = Promise.reject(e); }
    Promise.resolve(pr).then(() => {
      if (this.cur !== p || p.mode !== "pending") return;
      p.mode = "audio"; p.t0 = performance.now();
      const d = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : null;
      this.face(p.faceStart(d));                                // same tick as playback start
      if (p.level) this._levels(p);
      after(((d || p.fallbackSec) + OVERRUN_S) * 1000, () => this._finish(p, null));   // watchdog
    }).catch(e => {
      const name = e && e.name;
      driveByTimer(name === "NotAllowedError" ? "autoplay_blocked" : name === "NotSupportedError" ? "decode_failed" : "play_failed");
    });
    after(START_TIMEOUT_MS, () => { if (this.cur === p && p.mode === "pending") driveByTimer("start_timeout"); });
  }

  _levels(p) {
    if (!this.analyser || !this.actx || this.actx.state !== "running") return;   // face uses its random flap
    const buf = new Float32Array(this.analyser.fftSize);
    let heard = false;
    const id = setInterval(() => {
      if (this.cur !== p || p.mode !== "audio") { clearInterval(id); return; }
      this.analyser.getFloatTimeDomainData(buf);
      let s = 0;
      for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
      const v = Math.min(1, Math.sqrt(s / buf.length) * 4);
      if (v > 0.02) heard = true;
      if (heard) this.face({ type: "level", v: Math.round(v * 100) / 100 });   // silent clip: never send 0s
    }, 50);
    p.timers.push(id);
  }

  _finish(p, error) {
    if (p.finished) return;
    p.finished = true;
    p.timers.forEach(t => { clearTimeout(t); clearInterval(t); });
    if (this.cur === p) {
      this.cur = null;
      try { this.audio.pause(); } catch (e) { /* ignore */ }
      this.audio.onended = this.audio.onerror = null;
    }
    if (p.mode === "pending") {          // ended before it ever started (stop_all / interrupt): no caption
      p.mode = "skipped";
    } else {
      this.face({ type: "say_end", id: p.sayId });
    }
    p.done({ mode: p.mode, ...(error ? { error } : {}) });
    const next = this.waiting.sort((x, y) => x.priority - y.priority).shift();
    if (next && !this.cur) this._start(next);
  }
}
