/* Browser iPad main (PRD §26.3, §10.1; CONTRACTS §2, §3.3). Glues the four modules together:
 *
 *   channel.js  — the device WebSocket (hello/heartbeat/ack/dedupe/backoff/offline)
 *   media.js    — fixed clips + voice notes, face captions, playback_done (never hangs)
 *   photo.js    — capture_photo: camera or placeholder JPEG, POST /media/photo
 *   talk.js     — "Talk (browser mic)" for real LiveKit voice sessions (kept isolated)
 *
 * Face transport is the iframe bridge (face/bridge.js): postMessage({ahbo: cmd}, "*") in,
 * {ahboEvent: evt} out. Face commands are queued until the face reports `ready`; after a
 * reload the server replays the state when the ready face_event reaches it (CONTRACTS §2.4).
 * Face events map exactly as CONTRACTS §3.3: ok_tapped → touch_ok; a face tap with no session
 * → local expr wake + wake {trigger:"touch"}; everything else → face_event {event}.
 */

import { DeviceChannel } from "./channel.js";
import { ManifestCache, Player } from "./media.js";
import { captureAndUpload } from "./photo.js";
import { Talk } from "./talk.js";

const $ = id => document.getElementById(id);
const LANDSCAPE = { w: 1180, h: 820 };              // the face's real point size
const BEZEL_BORDER = 28;                            // 14px frame all round, absent in app mode

const els = {
  bezel: $("bezel"), face: $("face"), conn: $("conn"), devId: $("devId"), clipVer: $("clipVer"),
  sess: $("sess"), audioState: $("audioState"), rot: $("rot"), reloadFace: $("reloadFace"),
  sound: $("sound"), camera: $("camera"), sayForm: $("sayForm"), sayText: $("sayText"),
  sayBtn: $("sayBtn"), sayHint: $("sayHint"), okBtn: $("okBtn"), tapBtn: $("tapBtn"),
  talkBox: $("talkBox"), talk: $("talk"), log: $("log"), clearLog: $("clearLog"),
  appMode: $("appMode"), exitApp: $("exitApp"),
};

/* ---------- log + protocol trace ---------- */
function log(cls, s) {
  const t = new Date().toLocaleTimeString("en-GB", { hour12: false });
  const line = document.createElement("div");
  line.className = cls;
  line.textContent = `${t}  ${typeof s === "string" ? s : JSON.stringify(s)}`;
  els.log.append(line);
  while (els.log.children.length > 400) els.log.firstChild.remove();
  els.log.scrollTop = els.log.scrollHeight;
}

/* Ring buffer of protocol events (device frames both ways + face commands), oldest first.
 * Powers the tests (window.__simtrace) and anyone debugging in the console. */
const trace = [];
function tracePush(dir, obj) {
  trace.push({ t: Math.round(performance.now()), dir, obj });
  if (trace.length > 500) trace.shift();
}
window.__simtrace = trace;

/* ---------- face bridge ---------- */
let faceReady = false;
const faceQueue = [];

function face(cmd) {
  tracePush("face", cmd);
  if (!els.face.contentWindow) return;
  if (!faceReady) { faceQueue.push(cmd); return; }
  log("face", `face ${JSON.stringify(cmd)}`);
  els.face.contentWindow.postMessage({ ahbo: cmd }, "*");
}

function onFaceEvent(evt) {
  if (!evt || typeof evt.type !== "string") return;
  log("face", `event ${JSON.stringify(evt)}`);
  if (evt.type === "ready") {
    faceReady = true;
    const q = faceQueue.splice(0);
    q.forEach(face);                                 // flush what arrived before the face loaded
  }
  if (evt.type === "ok_tapped") { channel.send("touch_ok"); return; }
  if (evt.type === "tap" && evt.target === "face" && !sim.session) {
    face({ type: "expr", expr: "wake" });            // instant, before the server answers (§23.3)
    channel.send("wake", { trigger: "touch" });
    return;
  }
  channel.send("face_event", { event: evt });        // server replays state on `ready`
}

/* ---------- persistent bits (the iPad's config store, browser-sized) ---------- */
const store = {
  get(k, d) { try { const v = localStorage.getItem("ahbo-sim-" + k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("ahbo-sim-" + k, String(v)); } catch (e) { /* private mode */ } },
};

/* ---------- sim state ---------- */
const sim = {
  session: null,           // {session_id, reason, mode, text_only} while a session is open
  wakeEnabled: true,
  config: {},              // last config.update / welcome.config
  deviceToken: null,
};

/* ---------- media ---------- */
const manifest = new ManifestCache(s => log("note", s));
const player = new Player({
  face: cmd => face(cmd),
  log: s => log("note", s),
});

/** play_fixed / play_voice_message share this path. Returns the device playback_done fields. */
function startPlayback(p) {
  player.play(p);
}

/* ---------- device channel ---------- */
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";   // tests run on plain http
  return `${proto}://${location.host}/ws/device?sim=1`;
}

/* Test/dev knobs: /sim/?hb_ms=250&offline_ms=1200 shortens the timers (defaults are the real 10 s / 30 s). */
const qs = new URLSearchParams(location.search);

/* One stable id per browser profile (stored at startup, so every reconnect reuses it). */
const DEVICE_ID = (() => {
  let id = store.get("device_id", "");
  if (!id || !id.startsWith("sim-")) { id = "sim-" + Math.random().toString(36).slice(2, 8); store.set("device_id", id); }
  return id;
})();

const channel = new DeviceChannel({
  url: wsUrl(),
  hello: () => ({
    device_id: DEVICE_ID,
    app_version: "0.1.0-sim",
    fixed_cache_version: store.get("fixed_cache_version", ""),
    wake_engine: "none",
  }),
  heartbeat: () => ({ battery: 0.8, thermal: "nominal", in_session: !!sim.session }),
  onCommand: onServerCommand,
  onWelcome: w => {
    sim.deviceToken = w.device_token;
    sim.config = w.config || {};
    store.set("fixed_cache_version", w.fixed_cache_version || "");
    els.devId.textContent = DEVICE_ID;
    els.clipVer.textContent = w.fixed_cache_version || "–";
    manifest.ensure(w.fixed_cache_version).catch(e => log("err", "manifest: " + e.message));
    if (w.config && w.config.wake) sim.wakeEnabled = true;     // wake config arrived; wake_engine stays "none"
  },
  onState: (s, detail) => {
    els.conn.dataset.state = s;
    els.conn.textContent = s === "online" ? "online" : s + (detail ? ` (${detail})` : "");
    log("note", `channel ${s}${detail ? " " + detail : ""}`);
  },
  onOffline: on => face({ type: "offline", on }),
  onFrame: (dir, frame) => {
    tracePush(dir, frame);
    if (dir === "in") { if (frame.type !== "pong") log("in", `← ${frame.type}${frame.ui ? " " + JSON.stringify(frame.ui) : ""}`); }
    else if (dir === "out") log("out", `→ ${frame.type}`);
    else log(dir === "drop" ? "err" : "note", `${dir} ${JSON.stringify(frame)}`);
  },
  heartbeatMs: Number(qs.get("hb_ms")) || 10000,
  offlineAfterMs: Number(qs.get("offline_ms")) || 30000,
});

/* ---------- server commands (CONTRACTS §2.2) ---------- */
function onServerCommand(cmd) {
  switch (cmd.type) {
    case "ui":
      face(cmd.ui);
      return { ok: true };

    case "play_fixed": {
      const sayId = cmd.say_id;
      manifest.clip(cmd.clip_id).then(clip => {
        startPlayback({
          src: `/media/fixed/${clip.file || cmd.clip_id + ".m4a"}`,
          sayId,
          priority: cmd.priority,
          level: true,                                       // Ah Bo's voice: mouth follows
          fallbackSec: Number(clip.duration) || estimateSec(clip.text),
          faceStart: audioDur => ({
            type: "say_start", id: sayId, text: clip.text || "",
            ...(Array.isArray(clip.segments) && clip.segments.length ? { segments: clip.segments } : {}),
            ...(!clip.segments && audioDur ? { durationSec: audioDur } : {}),
          }),
          done: r => channel.send("playback_done", { clip_id: cmd.clip_id, say_id: sayId, ...(r.error ? { error: r.error } : {}) }),
        });
      }).catch(e => {
        log("err", `play_fixed ${cmd.clip_id}: ${e.message}`);
        channel.send("playback_done", { clip_id: cmd.clip_id, say_id: sayId, error: "clip_not_in_manifest" });
      });
      return { ok: true };
    }

    case "play_voice_message": {
      const sayId = cmd.say_id;
      const durS = Number(cmd.duration_s) || 0;
      startPlayback({
        src: cmd.url,
        sayId,
        priority: cmd.priority,
        level: false,                                        // her family's voice: the mouth never moves (§23.5)
        fallbackSec: durS || 4,
        faceStart: audioDur => {
          const secs = durS || audioDur || 0;
          return {
            type: "say_start", id: sayId, from: "family", fromName: cmd.from_name || "家人",
            ...(cmd.text ? { text: cmd.text } : {}),
            ...(secs ? { durationSec: secs } : (cmd.text ? {} : { durationSec: 4 })),
          };
        },
        done: r => channel.send("playback_done", { message_id: cmd.message_id, say_id: sayId, ...(r.error ? { error: r.error } : {}) }),
      });
      return { ok: true };
    }

    case "capture_photo": {
      captureAndUpload(cmd.incident_id, els.camera.checked)
        .then(({ photo_id, source }) => {
          log("note", `photo ${photo_id} (${source})`);
          channel.send("photo_uploaded", { photo_id, incident_id: cmd.incident_id });
        })
        .catch(e => {
          log("err", "photo: " + e.message);
          channel.send("error", { code: "camera_failed", detail: String(e.message || e).slice(0, 300) });
        });
      return { ok: true };
    }

    case "session.open": {
      sim.session = { session_id: cmd.session_id, reason: cmd.reason, mode: cmd.mode, text_only: !!cmd.text_only };
      renderSession();
      return { ok: true };
    }

    case "session.close": {
      sim.session = null;
      renderSession();
      return { ok: true };
    }

    case "wake.enable":
      sim.wakeEnabled = !!cmd.on;
      store.set("wake_enabled", sim.wakeEnabled ? "1" : "0");
      log("note", `wake ${sim.wakeEnabled ? "enabled" : "disabled"} (wake_engine is none: touch only)`);
      return { ok: true };

    case "config.update": {
      const { fixed_cache_version, ...rest } = cmd;
      Object.assign(sim.config, rest);
      if (typeof fixed_cache_version === "string" && fixed_cache_version !== store.get("fixed_cache_version", "")) {
        store.set("fixed_cache_version", fixed_cache_version);
        els.clipVer.textContent = fixed_cache_version;
        manifest.ensure(fixed_cache_version).catch(e => log("err", "manifest: " + e.message));
      }
      return { ok: true };
    }

    case "stop_all":
      player.stopAll();
      if (talk.joined) talk.leave().catch(e => log("err", "talk leave: " + e.message));
      face({ type: "reset" });
      return { ok: true };

    default:
      log("err", `unknown command ${cmd.type}`);
      return { ok: false, error: "unknown_command" };
  }
}

function estimateSec(text) {
  return Math.max(1.2, Array.from(String(text || "")).length / 5.2);   // ~5 chars/s (§23.5 fallback)
}

/* ---------- panel ---------- */
function renderSession() {
  const s = sim.session;
  els.sess.textContent = s ? `${s.session_id} · ${s.reason}/${s.mode}${s.text_only ? " · text" : " · voice"}` : "none";
  els.sess.classList.toggle("on", !!s);
  const textOk = !!(s && s.text_only);
  els.sayText.disabled = !textOk;
  els.sayBtn.disabled = !textOk;
  els.sayForm.classList.toggle("session", textOk);
  els.sayHint.textContent = textOk
    ? "Session open — what you send is what she says."
    : (s ? "Voice session: use “Talk (browser mic)” for real voice." : "Text is off until a session opens: tap the face to wake Ah Bo.");
  els.talkBox.hidden = !(s && !s.text_only);
  if (!(s && !s.text_only) && els.talk.checked) {
    els.talk.checked = false;
    if (talk.joined) talk.leave().catch(e => log("err", "talk leave: " + e.message));
  }
}

function fitBezel() {
  const box = els.bezel.parentElement.getBoundingClientRect();
  const portrait = els.bezel.classList.contains("portrait");
  const b = document.body.classList.contains("appmode") ? 0 : BEZEL_BORDER;
  const dims = portrait ? { w: LANDSCAPE.h + b, h: LANDSCAPE.w + b } : { w: LANDSCAPE.w + b, h: LANDSCAPE.h + b };
  const k = Math.min(box.width / dims.w, box.height / dims.h, 1);
  els.bezel.style.setProperty("--k", String(k));
}
addEventListener("resize", fitBezel);

els.rot.onclick = () => { els.bezel.classList.toggle("portrait"); fitBezel(); };

/* ---------- app mode: face only, full-screen — looks like the iPad app ---------- */
/* ⛶ App mode (panel button) or Add to Home Screen + launch (standalone). The floating ⚙
 * chip (safe-area aware) or Esc returns to the tester panel. If the browser refuses
 * requestFullscreen (iOS is picky about it), the layout still hides the panel and frame. */
function setAppMode(on) {
  document.body.classList.toggle("appmode", on);
  els.exitApp.hidden = !on;
  const root = document.documentElement;
  if (on) {
    const req = root.requestFullscreen || root.webkitRequestFullscreen;
    if (req) { try { req.call(root, { navigationUI: "hide" }); } catch (e) { log("note", "fullscreen refused: " + e.message); } }
  } else if (document.fullscreenElement && (document.exitFullscreen || document.webkitExitFullscreen)) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  }
  fitBezel();
}
els.appMode.onclick = () => setAppMode(!document.body.classList.contains("appmode"));
els.exitApp.onclick = () => setAppMode(false);
addEventListener("keydown", ev => {
  if (ev.key === "Escape" && document.body.classList.contains("appmode")) setAppMode(false);
});
const standalone = matchMedia("(display-mode: standalone)").matches || matchMedia("(display-mode: fullscreen)").matches
  || navigator.standalone === true;
if (standalone) setAppMode(true);   // launched from the Home Screen icon

els.reloadFace.onclick = () => {
  faceReady = false;
  faceQueue.length = 0;
  els.face.src = "/face/index.html";   // re-setting src reloads; the face sends `ready` again
};

els.sound.onclick = () => {
  player.unlock();
  els.audioState.textContent = player.actx && player.actx.state === "running" ? "unlocked (clips + mouth level)" : "still locked";
};

els.clearLog.onclick = () => { els.log.textContent = ""; };

els.sayForm.onsubmit = ev => {
  ev.preventDefault();
  const text = els.sayText.value.trim();
  if (!text || els.sayText.disabled) return;
  if (channel.send("utterance", { text, final: true })) els.sayText.value = "";
};

els.okBtn.onclick = () => channel.send("touch_ok");
els.tapBtn.onclick = () => onFaceEvent({ type: "tap", target: "face" });   // same path as a real face tap

els.talk.onchange = async () => {
  if (els.talk.checked) {
    try { await talk.join(sim.session || {}); }
    catch (e) {
      log("err", "talk: " + e.message);
      channel.send("error", { code: "livekit_join_failed", detail: String(e.message || e).slice(0, 300) });
      els.talk.checked = false;
    }
  } else if (talk.joined) {
    await talk.leave().catch(e => log("err", "talk leave: " + e.message));
  }
};

const talkLog = { face: cmd => face(cmd), log: s => log("note", s),
                  error: (code, detail) => channel.send("error", { code, detail }) };
const talk = new Talk(talkLog);

/* ---------- 示範 Demo controls (merged Director, 13 Nov): same-origin operator API ---------- */
console.error("SIMJS-DEBUG", document.readyState, "demoBtns:", !!document.getElementById("demoBtns"), "noInt:", !!document.getElementById("noInt"), "buttons:", document.querySelectorAll("button").length);
const demoBox = document.getElementById("demoBtns");
demoBox.addEventListener("click", async ev => {
  const b = ev.target.closest("button[data-demo]");
  if (!b) return;
  b.disabled = true;
  const post = (path, body) => fetch("/operator/api/" + path, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => { if (!r.ok) throw new Error("http_" + r.status); return r.json(); });
  try {
    const k = b.dataset.demo;
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    if (k === "reset")       await post("reset", {});
    else if (k === "s2")  { await post("clock", {anchor: "S2"}); await sleep(400);
                            await post("trigger", {kind: "utterance", text: "喂阿寶，早晨。我幾時食藥呀？"}); }
    else if (k === "s3a") { await post("clock", {anchor: "S3"}); await sleep(400);
                            await post("trigger", {kind: "utterance", text: "喂阿寶，我聽日有咩安排呀？"}); }
    else if (k === "s3b")  await post("trigger", {kind: "utterance", text: "咁我下晝好悶喎……"});
    else if (k === "s3c")  await post("trigger", {kind: "utterance", text: "好呀，幫我報名啦"});
    else if (k === "fall") await post("trigger", {kind: "fall"});
    else if (k === "play_voice") await post("trigger", {kind: "play_voice"});
    log("note", "demo ✓ " + k);
  } catch (e) {
    log("err", "demo ✗ " + ((e && e.message) || e) + " — login 過期就開返 /login");
  }
  b.disabled = false;
});

/* 🚫 No-interrupt toggle (default OFF): ON = even 「喂阿寶」 cannot cut in; applies to new sessions.
 * Document-level delegation — cannot break on a null lookup, same pattern as script.html. */
document.addEventListener("click", async ev => {
  const b = ev.target.closest("#noInt");
  if (!b) return;
  const turnOn = b.dataset.on !== "true";
  b.disabled = true;
  try {
    await fetch("/operator/api/override", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({kind: "interruption", on: !turnOn})}).then(r => { if (!r.ok) throw new Error("http_" + r.status); });
    b.dataset.on = turnOn ? "true" : "false";
    b.innerHTML = turnOn
      ? '🚫 No-interrupt: <b>ON</b>（冇人可以打斷）'
      : '🚫 No-interrupt: <b>OFF</b>（喂阿寶可以先打斷）';
    b.style.borderColor = turnOn ? "var(--red)" : "var(--green)";
    log("note", "no-interrupt " + (turnOn ? "ON — 新 session 開始先生效" : "OFF"));
  } catch (e) {
    log("err", "no-interrupt ✗ " + ((e && e.message) || e));
  }
  b.disabled = false;
});

/* ---------- go ---------- */
function start() {
  els.face.src = "/face/index.html";
  addEventListener("message", ev => {
    if (ev.source === els.face.contentWindow && ev.data && ev.data.ahboEvent) onFaceEvent(ev.data.ahboEvent);
  });
  renderSession();
  fitBezel();
  channel.start();
}

start();
