// Live face mirror (§12, CONTRACTS §3.3): an iframe of /face/index.html driven with postMessage({ahbo: cmd}).
// It starts from the snapshot's `ui` (rebuilt into reset, mood, cards, steps, ok_button, offline) and then
// replays every `ui` SSE event. Fixed clips and voice notes are captioned by the iPad itself from
// `play_fixed` / `play_voice_message` commands, so the mirror synthesizes the same say_start/say_end from
// the `command` stream (de-duplicated by say id). The mirror is view-only: its face events are never sent
// to the server (it is not a device).

const FACE_TYPES = new Set(["mood", "expr", "heard", "say_start", "say_update", "say_end", "level", "card",
  "card_update", "card_clear", "steps", "step", "steps_clear", "ok_button", "countdown", "flash", "birth",
  "offline", "reset"]);

/** Snapshot `ui` → face commands that rebuild the face from blank (same order as kernel replay_commands). */
export function snapshotToCommands(ui) {
  const out = [{ type: "reset" }];
  if (!ui) return out;
  if (ui.mood && ui.mood !== "idle") out.push({ type: "mood", mood: ui.mood });
  const cards = Array.isArray(ui.cards) ? ui.cards : Object.values(ui.cards || {});
  for (const card of cards) if (card && card.id) out.push({ type: "card", card: stripInternal(card) });
  const groups = Array.isArray(ui.steps) ? ui.steps
    : Object.entries(ui.steps || ui.groups || {}).map(([id, g]) => ({ id, ...g }));
  for (const g of groups) {
    if (!g || !g.id) continue;
    const stepsIn = Array.isArray(g.steps) ? g.steps
      : Object.entries(g.steps || {}).map(([id, s]) => ({ id, ...s }));
    const group = { id: g.id, steps: stepsIn.map(s => {
      const o = { id: s.id, label: s.label || "", state: s.state || "pending" };
      if (s.doneLabel) o.doneLabel = s.doneLabel;
      return o;
    }) };
    if (g.title) group.title = g.title;
    out.push({ type: "steps", group });
  }
  if (ui.ok_button === true || (ui.ok_button && ui.ok_button.show)) {
    const c = { type: "ok_button", show: true };
    if (ui.ok_button && ui.ok_button.label) c.label = ui.ok_button.label;
    out.push(c);
  }
  if (ui.offline) out.push({ type: "offline", on: true });
  return out;
}

function stripInternal(card) {
  const c = {};
  for (const [k, v] of Object.entries(card)) if (k !== "when_candidates" && v != null) c[k] = v;
  return c;
}

/** Accepts a bare face command, `{ui: cmd}` or a device.ui Command `{payload:{ui}}`. */
export function unwrapUi(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.type === "string" && FACE_TYPES.has(data.type)) return data;
  if (data.ui && typeof data.ui === "object") return unwrapUi(data.ui);
  if (data.payload && data.payload.ui) return unwrapUi(data.payload.ui);
  return null;
}

export class Mirror {
  constructor(iframe, { onFaceReady, onStatus } = {}) {
    this.iframe = iframe;
    this.onFaceReady = onFaceReady || (() => {});
    this.onStatus = onStatus || (() => {});
    this.faceReady = false;
    this.holding = true;          // until the snapshot has been replayed
    this.buffer = [];
    this.log = [];                // last commands applied (debug / tests)
    this.sayIds = new Map();      // say id -> "ui" | "synth"
    this.sayTimers = new Map();
    this.clips = {};              // fixed clips {id: {text, duration}}
    this.version = null;
    addEventListener("message", ev => this._onMessage(ev));
    iframe.addEventListener("load", () => this._hookKeys());
    this._fit();
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => this._fit()).observe(iframe.parentElement);
    addEventListener("resize", () => this._fit());
    this._probeFace();     // the iframe may beat this module to `load`/`ready` (fast local load)
  }

  setClips(clips) { this.clips = clips || {}; }

  _fit() {
    const wrap = this.iframe.parentElement;
    const s = wrap.clientWidth / 1180;
    if (s > 0) this.iframe.style.transform = `scale(${s})`;
  }

  // Esc must reach the console even if the iframe somehow has focus.
  _hookKeys() {
    if (this._keysHooked) return;
    this._keysHooked = true;
    try {
      const w = this.iframe.contentWindow;
      w.addEventListener("keydown", e => {
        if (e.key === "Escape") dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      });
    } catch { /* cross-origin: ignore */ }
  }

  /** The face posts `ready` once, possibly before this module ran and registered its message
   *  listener — then the message is lost forever. Poll for `window.AhBo` (face.js) as the fallback. */
  _probeFace() {
    if (this._probe) clearInterval(this._probe);
    let tries = 0;
    this._probe = setInterval(() => {
      let w = null;
      try { w = this.iframe.contentWindow; } catch { /* cross-origin */ }
      if (w && w.AhBo) {
        clearInterval(this._probe); this._probe = null;
        this._hookKeys();
        this._markReady(w.AhBo.version || null);
      } else if (++tries > 100) clearInterval(this._probe);
    }, 100);
  }

  _markReady(version) {
    if (this.faceReady) return;
    this.faceReady = true;
    if (version) this.version = version;
    this.holding = true;            // resync: rebuild from the pending snapshot
    this.onStatus(`face ${this.version || ""} ready`);
    this.onFaceReady();
  }

  _onMessage(ev) {
    if (ev.source !== this.iframe.contentWindow) return;
    const evt = ev.data && ev.data.ahboEvent;
    if (!evt) return;
    if (evt.type === "ready") {
      this._markReady(evt.version);
    } else if (evt.type === "error") {
      this.onStatus("face error: " + evt.message);
    }
    // all other face events are ignored: the mirror is view-only
  }

  _post(cmd) {
    this.log.push(cmd);
    if (this.log.length > 300) this.log.shift();
    try { this.iframe.contentWindow.postMessage({ ahbo: cmd }, "*"); } catch { /* frame gone */ }
  }

  /** Hold live commands while the snapshot is fetched. */
  hold() { this.holding = true; }

  /** Apply a snapshot (rebuild from blank), then flush whatever arrived meanwhile. */
  replaySnapshot(ui) {
    for (const t of this.sayTimers.values()) clearTimeout(t);
    this.sayTimers.clear(); this.sayIds.clear();
    for (const c of snapshotToCommands(ui)) this._post(c);
    this.holding = false;
    const buf = this.buffer; this.buffer = [];
    for (const [c, src] of buf) this._apply(c, src);
    this.onStatus("live");
  }

  /** A `ui` SSE event (src "ui"), or a synthesized caption from a play_* command (src "synth"). */
  ui(data, src = "ui") {
    const cmd = unwrapUi(data);
    if (!cmd) return;
    if (this.holding || !this.faceReady) { this.buffer.push([cmd, src]); if (this.buffer.length > 500) this.buffer.shift(); return; }
    this._apply(cmd, src);
  }

  _apply(cmd, src = "ui") {
    if (cmd.type === "say_start" && cmd.id) {
      if (src === "ui" && this.sayIds.get(cmd.id) === "synth") return;   // already captioned from play_* command
      this.sayIds.set(cmd.id, src);
    }
    if (cmd.type === "say_end" && cmd.id) this._clearSayTimer(cmd.id);
    if (cmd.type === "reset") { for (const t of this.sayTimers.values()) clearTimeout(t); this.sayTimers.clear(); }
    this._post(cmd);
  }

  _clearSayTimer(id) {
    const t = this.sayTimers.get(id);
    if (t) { clearTimeout(t); this.sayTimers.delete(id); }
  }

  _synthSay(cmd, durationSec) {
    if (!cmd.id || this.sayIds.has(cmd.id)) return;
    this.ui(cmd, "synth");
    const ms = Math.max(800, (Number(durationSec) || cmd.text.length * 0.19) * 1000 + 150);
    this.sayTimers.set(cmd.id, setTimeout(() => { this.sayTimers.delete(cmd.id); this.ui({ type: "say_end", id: cmd.id }); }, ms));
  }

  /** A `command` SSE event (Kernel Command). Only device commands that the iPad turns into face changes matter. */
  command(c) {
    if (!c || typeof c !== "object") return;
    const p = c.payload || c;
    switch (c.type) {
      case "device.ui": this.ui(p); break;
      case "device.play_fixed": {
        const clip = this.clips[p.clip_id] || {};
        if (!p.say_id) break;
        const text = clip.text || p.text || "";
        const cmd = { type: "say_start", id: p.say_id, text };
        if (Array.isArray(clip.segments) && clip.segments.length) cmd.segments = clip.segments;
        else cmd.durationSec = Number(clip.duration) || text.length * 0.19;
        this._synthSay(cmd, clip.duration);
        break;
      }
      case "device.play_voice_message": {
        if (!p.say_id) break;
        const text = p.text || "";
        const dur = Number(p.duration_s) || Math.max(2, text.length * 0.19);
        this._synthSay({ type: "say_start", id: p.say_id, from: "family", fromName: p.from_name || "家人",
          text, durationSec: dur }, dur);
        break;
      }
      case "device.stop_all": this.ui({ type: "reset" }); break;
      default: break;
    }
  }

  /** A `kernel_event`: playback_done ends a synthesized caption on time. */
  kernelEvent(ev) {
    if (!ev || ev.type !== "device.playback_done") return;
    const id = ev.payload && ev.payload.say_id;
    if (id && this.sayTimers.has(id)) { this._clearSayTimer(id); this.ui({ type: "say_end", id }); }
  }
}
