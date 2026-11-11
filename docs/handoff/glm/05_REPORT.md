# GLM task 05 (Browser iPad simulator, `sim/`) report

## Files created/changed

**Changed / created in `sim/` (owned):**
- `sim/sim.js` — **created**. The glue module the stopped agent's `index.html` referenced but that was
  missing (the page was dead without it). Wires `channel.js` + `media.js` + `photo.js` + `talk.js` to the
  panel and the face iframe: hello/heartbeat/ack/dedupe/reconnect via `DeviceChannel`; `ui` → face
  `postMessage`; `play_fixed` / `play_voice_message` → manifest + player + `say_*` + `playback_done`;
  `capture_photo` → placeholder/camera + `POST /media/photo` + `photo_uploaded`; `session.open/close`
  → text-box gate + talk toggle; `wake.enable`/`config.update` → stored; `stop_all` → stop audio +
  face `reset`; face events mapped per CONTRACTS §3.3 (`ok_tapped`→`touch_ok`, face tap while idle →
  local `expr wake` + `wake {trigger:"touch"}`, `ready` and everything else → `face_event {event}`).
  Face commands are queued until the face reports `ready`; a stable `device_id` is kept in
  `localStorage` so reconnects reuse it. Also adds a 500-entry protocol ring buffer
  `window.__simtrace` (tests + console debugging) and optional `?hb_ms=` / `?offline_ms=` query
  knobs (defaults stay the real 10 s / 30 s).
- `sim/index.html` — one line changed: the text-box hint now matches the actual gating ("Text is off
  until a text session opens: tap the face to wake Ah Bo.").
- `sim/channel.js`, `sim/media.js`, `sim/photo.js`, `sim/talk.js`, `sim/sim.css` — reviewed from the
  stopped agent, kept unchanged (all correct against CONTRACTS §2/§3, PRD §10/§23/§26.3).

**Tests (owned): `brain/tests/sim/`**
- `stub_server.py` — stdlib-only stand-in HTTP + WebSocket (hand-rolled RFC 6455) server speaking
  CONTRACTS §2: serves `/sim/`, `/face/`, a fallback manifest (one playable clip, one with a missing
  file), `POST /media/photo`; plays the device hub (hello → welcome + replay, frames recorded,
  `send_command`, `send_command_twice`, `drop`, `reject_ws`). The ahbo-browser image ships no web
  framework, hence no third-party server library.
- `sim_harness.py` — wait/assert helpers + a generated PCM `tone.wav`.
- `conftest.py` — fixtures: Chromium from `AHBO_BROWSER_WS` (an ahbo-browser container's
  `playwright run-server`, the same endpoint the Action Runner uses) or local launch, else **skip**;
  stub server per test; `sim_page`.
- `test_sim_channel.py` — 13 protocol tests.
- `test_sim_real_app.py` — real-app smoke (skips unless `AHBO_SMOKE_URL` is set).

**Screenshots:** `docs/screens/sim/{00_idle,S2_01_reply_cards,S4_01_alarm_countdown,S4_02_escalation}.png`.

## Tests (exact command + the real last line of pytest output)

Private network + throwaway browser container (removed afterwards; no host ports):

```
docker network create ahbo-t-sim
docker run -d --name ahbo-sim-browser --network ahbo-t-sim --network-alias browser ahbo-browser
docker run --rm -v /opt/ahbo:/repo -w /repo/brain --network ahbo-t-sim --network-alias simapi \
  -e AHBO_BROWSER_WS=ws://browser:3000/ -e AHBO_STUB_BIND=0.0.0.0 -e AHBO_STUB_HOST=simapi \
  ahbo-api python -u -m pytest -q tests/sim
```

Last line: **`13 passed in 58.65s`**

- Without a browser (plain ahbo-api, i.e. what the orchestrator's full-suite run sees):
  `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/sim` → **`14 skipped in 9.17s`**
  (13 protocol tests + 1 smoke test; clean skips, no failures).
- Real-app smoke (api container `ahbo-sim-api` on the same network, fresh `AHBO_DATA_DIR=/tmp/ahbo-sim-smoke`,
  throwaway generated APP_SECRET/SESSION_SECRET/INTERNAL_TOKEN + bcrypt OPERATOR_PASSWORD_HASH for test
  password `sim-smoke-2026`; the real `.env` was never opened):
  `docker exec -e AHBO_BROWSER_WS=ws://browser:3000/ -e AHBO_SMOKE_URL=http://smokeapi:8000
   -e AHBO_SMOKE_PW=sim-smoke-2026 ahbo-sim-api python -u -m pytest -q /repo/brain/tests/sim/test_sim_real_app.py`
  → **`1 passed in 29.51s`** (passed 3× across iterations: 27.33s, 29.51s final).
- Ad-hoc UI check (rotate → `.portrait` + rescale, Reload face → second `ready`, talk box hidden,
  zero page errors): `rotate: portrait=1 scale 0.9437… -> 0.7864…; reload→ready events=2; talkBox
  hidden=True; pageerrors=[]`.

## Full-suite run (last line)

`docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q` →
**`583 passed, 33 skipped, 1 warning in 200.39s (0:03:20)`**

Note: a first full-suite run ~30 min earlier showed `5 failed` in `tests/links/` and
`tests/trace/` — another agent was editing `brain/ahbo/links/` and `brain/ahbo/trace/` mid-run
(their containers were active at that moment). Re-running those two directories alone right after
gave `86 passed`, and the clean full-suite re-run above has zero failures. None of those files are
mine; my change set cannot affect them (tests/sim imports no ahbo module).

## What is real vs faked

- **Real:** the whole device-channel protocol — the smoke test talks to the real `DeviceHub`
  (`?sim=1` + operator cookie, hello → welcome → replay), real login flow, real
  `POST /operator/api/scenario/S2|S4`, real `session.open` (text box gating asserted), real
  photo upload route, real face kit in the iframe (untouched `face/`). In the stub tests the hub is
  my stand-in (asserted against `device_hub.py` semantics).
- **Faked (per the system's fake mode):** LiveKit (text sessions; `text_only: true`), fixed clips are
  silent fallback entries — the sim drives captions from the manifest's `duration`/`segments` with a
  timer and still sends `say_end` + `playback_done` (asserted). No external API, call or message was
  made; no keys exist on the box; no money spent.
- **Not exercised:** `getUserMedia` camera path (no camera in a headless container; the "Camera"
  checkbox stays off, so the drawn-placeholder path runs — upload flow identical below that branch),
  LiveKit voice (`talk.js` cannot join without keys — the toggle surfaces the server's
  `text_only` answer instead of importing the CDN module).

## Deviations from PRD/CONTRACTS (and why)

1. **`sim/index.html` vs the PRD's `sim/ipad.html`** — the "web entry files are index.html" rule wins
   (the server's `/sim/` route already expects `index.html` first). Mentioned here per task.
2. **`ws://` on plain-http origins** — the task text says `new WebSocket("wss://" + location.host +
   "/ws/device?sim=1")`; the sim picks the scheme from `location.protocol` (`wss:` behind the real
   https host, `ws:` for http tests/local runs). Production traffic is unchanged (`wss://`).
3. **Placeholder photo canvas is 1280×720** (quality 0.7), not the task's "640×480": PRD §10.1 sets
   the constraint (JPEG ≤ 1280 px longest side) and the earlier agent's implementation satisfies it;
   kept.
4. **Test knobs on the tester page** (`?hb_ms=`, `?offline_ms=`; defaults are the real 10 s/30 s) and
   the `window.__simtrace` ring buffer — additive tester/dev affordances, nothing Po-po-facing.
5. **Text box gating** follows the task (`session.open text_only` → enabled, `session.close` →
   disabled) rather than PRD §26.3's "a text box sends what she says" alone; the first utterance
   therefore needs a wake (face tap), which is also how the native app behaves. The hint text says so.
6. **`play_fixed` with a clip missing from the manifest** → `ack ok` + `playback_done
   {clip_id, say_id, error:"clip_not_in_manifest"}` with no face caption (the Kernel only sends clips
   that exist; INV-3 path never hangs). The task's required case — file missing/autoplay-blocked —
   still captions from the manifest and finishes (tested).
7. The stub manifest marks clips F1 playable / F2 missing to test both paths; production's manifest
   (`fallback: true`, no files on disk) is the same situation the sim must survive — verified live.

## VERIFY results (library + version + exact function names checked)

- **livekit-client (CDN pin used by `talk.js`)** — `https://data.jsdelivr.com/v1/packages/npm/livekit-client`
  lists `2.22.4` (latest `2.23.0`); `…/npm/livekit-client@2.22.4/entrypoints` reports UMD as the
  package entrypoint, and the exact file `talk.js` imports,
  `https://cdn.jsdelivr.net/npm/livekit-client@2.22.4/dist/livekit-client.esm.mjs`, returns **HTTP 200**
  (checked via `curl -I`, and via the jsdelivr data API). Symbols used (`Room`, `RoomEvent.TrackSubscribed/
  ParticipantAttributesChanged/Disconnected`, `Track.Kind.Audio`, `registerTextStreamHandler`,
  `setMicrophoneEnabled`, `startAudio`, `audioLevel`) are those documented in that version's typings
  (header comment in `talk.js`); **not runtime-verified** — impossible without LiveKit keys.
- **Playwright 1.64.1** in both images; remote pattern `chromium.connect(ws_endpoint, timeout=…)`
  mirrors the repo's own verified usage in `brain/ahbo/actions/playwright_form.py:122`
  (`browser_ws` / `AHBO_BROWSER_WS`).
- **Face transport** — read `face/bridge.js` + `face/face.js` (never edited): iframe mode uses
  `parent.postMessage({ahboEvent: evt}, "*")` and accepts `{ahbo: cmd}`; commands/events match
  CONTRACTS §3 (`ok_tapped`, `tap {target}`, `ready {version}`, `say_done`, `countdown_done`, `error`).
- **Server truth** — `device_hub.py` (ack within 2 s, dedupe by id, replay on hello/`face_event ready`,
  P0 resend, `playback_done` dedup by `say_id`), `routes/device.py` (`POST /session` →
  `{text_only: true}` in fake mode), `routes/media.py` (`POST /media/photo` JPEG/PNG magic check),
  `routes/public.py` (`/sim/` served from repo `sim/` behind the operator cookie, index candidates
  `("index.html", "ipad.html")`), `api/auth.py` (cookie `ahbo_session`, `secure` only when
  `PUBLIC_BASE_URL` is https) — all read and matched by the sim/tests.
- **Fallback manifest on disk** — `data/media/fixed/manifest.json`: `version "f-606b2ab5b1ce"`,
  `fallback: true`, 9 clips, no audio files; a fresh `AHBO_DATA_DIR` gets the same shape auto-written
  by `services._fallback_manifest`, so the smoke test exercised exactly the fallback situation.
- **RFC 6455 stub** — verified against Chromium's real WebSocket in 13 passing tests (handshake,
  masked client frames, text frames, close 4401 path, drop/reconnect).

## Changes needed in files I don't own

None requested. Every server piece the sim needs (`/sim/` serving, hub auth/replay, `POST /session`,
`/media/*`, scenario endpoints) existed and behaved per contract.

## Known gaps / things I did not verify

- `talk.js` voice path: only the CDN URL/pin is verified; join, `lk.transcription` handling and
  barge-in remain unexercised until real LiveKit keys exist. Kept isolated in `talk.js` as tasked.
- Camera (`getUserMedia`) branch is untested at runtime (no camera in the container); tested path is
  the drawn-placeholder upload, which is what a headless/demo browser will do anyway.
- Mouth `level` (~20 Hz) is not asserted: the WebAudio analyser needs a running AudioContext, which a
  headless container without audio hardware may keep suspended; `media.js` then sends no levels and
  the face uses its own random flap (per PRD §23.5 fallback). Code path reviewed, not proven live.
- The five `docs/screens/sim/` images (4 saved) differ from the kit frames only in the captured
  moment, since the sim's face IS the same untouched kit: S2_01 was shot mid-reply (caption partially
  revealed, her question still in the heard line) vs `kit/S2_03_say_end_s2a.png` at `say_end` (happy
  squint, last sentence 「到時我會提你㗎。」); S4_02 shows F4's caption mid-reveal while
  `kit/S4_09_step_fall_tell_done.png` caught the caption already faded. Composition, colours, cards
  (⏰ 12:00 食藥 + four pill chips, 跌倒處理 steps ✓/✕, incident card, 緊急中心/已通知阿珍 cards),
  我冇事 button and ⏳ countdown match the kit frames (S4_01 ≈ kit S4_07, modulo countdown digit).
- The smoke test's idle screenshot needed an `operator/api/trigger reset` first because an earlier
  failed attempt left the (real) Kernel state with S2 cards — worth knowing: kernel turn cards do
  survive into the replay until a reset clears them.
- Other agents were working concurrently; `sim/*.py` (`beacon.py`, `telegram_fake.py`,
  `twilio_fake.py`) appeared in `sim/` mid-task — per the task those are not mine and I did not touch
  them; my page does not reference them.

## Post-handoff re-verification (in-depth check, later the same day)

Everything re-run from scratch after the handoff and after the sibling tasks landed their reports:

- **Static audit:** my `sim/` JS/HTML/CSS, `brain/tests/sim/`, the screenshots and this report are
  byte-identical to the handoff state (mtimes unchanged); `face/` untouched (02:17, predates all
  work); every server file the sim depends on (`device_hub.py`, `routes/{device,media,public,operator}.py`,
  `services.py`, `auth.py`, `demo.py`, `settings.py`, manifest) unchanged since my reads (06:23–06:39).
  The sibling task's `sim/*.py` files were updated again (12:01–12:02) — my page references only
  `sim.css`/`sim.js`/`/face/*`, and no module outside `brain/tests/sim/` imports anything of mine
  (grep clean).
- **Protocol suite re-run:** `13 passed, 1 skipped in 42.79s`, then coverage extended.
- **Coverage gaps closed** (found by this audit; implemented-but-untested commands now locked in):
  `wake.enable` (ack + stored), `config.update` (new `fixed_cache_version` → UI updates + manifest
  refetch, counter-asserted), voice-session `session.open` (Talk toggle visible, text box locked;
  hidden again on `session.close`), unknown command type (negative ack `unknown_command`), face
  `error` events forwarded as `face_event` (`mood:"nope"` → "unknown mood"), and failed photo upload
  (`/media/photo` 500 → device `error {code:"camera_failed"}`, no `photo_uploaded`). New files:
  +`stub_server.py` (`fail_photo` flag, `manifest_requests` counter), +3 tests in
  `test_sim_channel.py`. Suite now: **`16 passed, 1 skipped in 94.18s`** (the skip is the smoke test,
  which needs `AHBO_SMOKE_URL`).
- **Rotate/reload/page-error check re-run:** identical pass (`portrait=1, scale 0.9437→0.7864,
  reload→ready events=2, talkBox hidden=True, pageerrors=[]`).
- **Real-app smoke re-run on a fresh app** (new container, new throwaway secrets, empty data dir):
  **`1 passed in 32.26s`**; all four screenshots regenerated (15:51) and re-inspected — S2 and S4
  visuals unchanged and still matching the kit frames.
- **Real `POST /session` (cookie auth) on the live smoke app** returns
  `{'text_only': True, 'session_id': 'sess_…'}` — exactly the shape `talk.join` handles gracefully in
  fake mode (the Talk toggle never appears; her side stays text).
- **CDN re-check:** `livekit-client@2.22.4/dist/livekit-client.esm.mjs` still HTTP 200.
- **Full suite, final clean line** (the earlier mid-edit transient failures in
  `tests/actions/test_serving.py` / `tests/ops/test_render_fixed.py` pass in isolation — their owner
  edited those files while my run was in flight): **`586 passed, 37 skipped, 1 warning in 385.07s`** —
  zero failures.
- All throwaway containers/networks removed again; no git commands run; no secrets printed.
