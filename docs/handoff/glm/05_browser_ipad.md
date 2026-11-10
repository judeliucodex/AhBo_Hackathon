# GLM task 05: Browser iPad simulator (`sim/`)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/05_REPORT.md`.

**PRD sections:** §10 (device contract), §23 (the face), §25.2 (what the native app does — the sim mirrors it), §26.3
("The browser iPad" paragraph), §26.4. **CONTRACTS §2 and §3 exactly.** Read `face/README.md`, `face/face.js`,
`face/bridge.js`, `face/dev.html` (never edit or copy them).

**You own:** `sim/index.html` (the entry; the PRD calls it `sim/ipad.html` — the rule "web entry files are index.html" wins,
mention it in your report), `sim/*.js`, `sim/*.css`, `brain/tests/sim/**`. NOT `sim/*.py` (another task).

**Already on disk from a stopped agent (review, keep what is right, finish):** `sim/index.html`, `sim/channel.js`,
`sim/talk.js`, `sim/sim.css`.

**The real server is live and committed** — read it, it is the truth: `brain/ahbo/links/device_hub.py` (the WebSocket hub),
`brain/ahbo/api/routes/{device,media,public}.py`. It serves `/sim/` from the repo's `sim/` folder behind the operator login
cookie; the device channel is `wss://<host>/ws/device?sim=1` with that cookie; LiveKit is faked, so sessions are **text
sessions** (`welcome.text_sessions: true`, `session.open.text_only: true`). The fixed-clip manifest at
`/media/fixed/manifest.json` may be a text-only fallback (`"fallback": true`, no audio files) until another task renders
clips — the sim must then drive the face from the manifest's `duration`/`segments` and still finish (never hang).

## Build: a faithful stand-in for the native app, speaking exactly the same protocol
1. **Layout:** an iPad-sized frame 1180×820 (rotate button → 820×1180) holding an iframe of `/face/index.html`; a small side
   panel for the tester: connection status, session indicator, 「王婆婆講：」 text box + send, a 「我冇事」 button, event log.
   The panel may use a dark operator style; the face itself is untouched.
2. **Device channel:** `new WebSocket("wss://" + location.host + "/ws/device?sim=1")`; first frame `hello` (device_id
   `sim-<random>`, app_version, fixed_cache_version, wake_engine `"none"`); `heartbeat` every 10 s (battery, thermal
   `"nominal"`, in_session); `ack {ref, ok}` for **every** command within 2 s; de-duplicate commands by `id`; reconnect with
   backoff 0.5, 1, 2, 4, 8 s cap + jitter; after 30 s disconnected send the face `offline {on:true}`, clear it on reconnect.
   After `welcome` the server replays the face state itself — just apply what arrives.
3. **Commands (CONTRACTS §2.2):**
   - `ui` → `iframe.contentWindow.postMessage({ahbo: frame.ui}, "*")`.
   - `play_fixed {clip_id, say_id}` → look up the clip in the manifest (fetch once per version, cookie auth); start the
     `<audio>` and **in the same tick** send the face `say_start {id: say_id, text, segments}`; when it ends send
     `say_end {id: say_id}`, then `playback_done {clip_id, say_id}`. If the file is missing/silent/blocked by autoplay,
     use a timer of `duration` seconds instead — still send say_end and playback_done.
   - `play_voice_message {message_id, url, say_id, from_name, text?, duration_s?}` → play `url`; face
     `say_start {id: say_id, from:"family", fromName: from_name, text, durationSec: duration_s}`; at the end `say_end`, then
     `playback_done {message_id, say_id}` (same fallback timer rule).
   - Send face `level {v}` ~20 Hz from a WebAudio analyser on the playing element when possible (else nothing — the face
     flaps by itself).
   - `capture_photo {incident_id}` → `getUserMedia` front camera if granted, else draw a dark 640×480 canvas JPEG; `POST
     /media/photo` multipart (`file`, `incident_id`) → then `photo_uploaded {photo_id, incident_id}` with the returned id.
   - `session.open` (text_only) → enable the text box; `session.close` → disable it; `wake.enable` / `config.update` → store;
     `stop_all` → stop audio, face `reset`.
   - When a session is **not** text_only, show a "Talk (browser mic)" toggle that loads `livekit-client` from
     `cdn.jsdelivr.net` at an exact version you VERIFY exists (`https://data.jsdelivr.com/v1/packages/npm/livekit-client`),
     calls `POST /session`, joins the room, and handles `lk.transcription` as PRD §23.5 says. It can't be tried without keys —
     say so; keep it isolated in `talk.js`.
4. **Her side:** the text box sends `utterance {text, final:true}`; the 「我冇事」 button sends `touch_ok`.
5. **Face events (`{ahboEvent}` from the iframe):** `ok_tapped` → `touch_ok`; `tap {target:"face"}` while no session is
   open → send the face `expr {expr:"wake"}` locally, then `wake {trigger:"touch"}`; `ready` → `face_event {event}` (the
   server replays state on it); everything else → `face_event {event}`.
6. **Tests (`brain/tests/sim/`):** Playwright (`ahbo-browser` image) against a small Python stand-in WebSocket/HTTP server
   written in the test (speaking CONTRACTS §2): assert hello/heartbeat/ack shapes, `ui` reaches the iframe (check `.face`
   classes / card DOM), play_fixed → say_start with segments → say_end → playback_done (with a missing audio file too),
   utterance frames, touch_ok, reconnect + offline dot. Then a **smoke test against the real app** on a private Docker
   network: run `ahbo-api` with `AHBO_DATA_DIR=/tmp/<x>`, throwaway generated APP_SECRET/SESSION_SECRET/INTERNAL_TOKEN and a
   bcrypt OPERATOR_PASSWORD_HASH for a test password (never read or copy the real `.env`); log in, open `/sim/`, then
   `POST /operator/api/scenario/S2` and `.../S4` with the same cookie, and screenshot the frame. Save screenshots to
   `docs/screens/sim/`. Compare them by eye with `docs/screens/kit/S2_03_say_end_s2a.png` and
   `docs/screens/kit/S4_09_step_fall_tell_done.png` and describe any visual difference in your report.

## Done when
- The stand-in-server tests pass; the real-app smoke run shows S2 (⏰ 12:00 食藥 + four pill chips, step ✓) and S4 (amber
  eyes, 跌倒處理 steps, 我冇事 button/countdown, then the escalation cards) in the sim frame, with screenshots saved.
- Full suite: nothing new fails.
