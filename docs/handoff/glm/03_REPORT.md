# GLM task 03: Operator console UI (`operator-ui/`) report

## Files created/changed

**Changed (kept what the stopped agent had right — api.js, dom.js, state_panel.js, transcript.js, canned.js,
console.css were correct and are untouched):**
- `operator-ui/js/main.js` — **new** (referenced by index.html but missing): the whole wiring. SSE
  (`/operator/stream`: state, kernel_event, command, ui, turn, outbox, health), top bar (HKT clock chip with
  local ticking between snapshots, profile + fake-mode badges, incident badge), panels for scenarios
  (S1/S2/S3/S4/S4ok + Prepare anchors), triggers (模擬跌倒, SOS, dose now with slot select, check-in,
  inject voice note / family text labelled 「fake mode · 示範用 Demo」, run 07:00 plan, Reset demo, Warm-up,
  Birth), virtual clock (set HH:MM / offset / back to now), overrides (incident ok/escalate/resolve, force
  tool result with per-tool canned variants + editable JSON, play fixed clip, mute/unmute, say-a-line with
  visible 「operator」 label, provider fallback, interruption toggle), outbox (SSE-updated rows, photo,
  screenshots, pressable Telegram buttons + Twilio press-1), model knobs (Flash off/low/high/max, Sonnet
  off/low/medium/high/xhigh/max, applied from the response echo + snapshot), wake panel (honest), health
  table, trace view per correlation id (also fed by id-links from the state panel), action-results log, and
  **Esc = Panic** (`POST /operator/api/panic`, hint in the header, toast).
- `operator-ui/js/mirror.js` — three bug fixes (see "Bugs found in the existing code").
- `operator-ui/index.html` — added a mirror "resync" button (rebuilds the face from the state snapshot) and
  replaced the wake panel's 「placeholder」 badge with an honest one.

**Created:**
- `brain/tests/operator_ui/conftest.py` — Playwright fixtures; aborts fonts.googleapis.com/gstatic requests so
  the pages render offline/deterministically; skips cleanly when Chromium is absent.
- `brain/tests/operator_ui/standin.py` — the §9 stand-in server (stdlib only, threaded): canned snapshot,
  SSE stream with test-controlled event injection, request recording, `/face/*` served from the REAL kit,
  the console's own static files, and `hide` support to make endpoints 404.
- `brain/tests/operator_ui/test_console.py` — 12 Playwright tests (see Tests).
- `brain/tests/operator_ui/test_smoke_real_app.py` — two real-app smokes (skip without env vars): S2 cards in
  the mirror, and S4 end-to-end (alarm mood, INCIDENT badge, live fake Telegram ALERT + Twilio call with
  TwiML in the Outbox, both buttons pressed through the real press wiring, call row → acknowledged, then
  Esc = Panic resetting the face).
- `brain/tests/operator_ui/smoke_real_app.sh` — host-side orchestration: private docker network
  `ahbo-t-glm03`, throwaway `ahbo-api` with GENERATED secrets (temp env file, mode 600, deleted on exit;
  the real `.env` is never read), runs the smoke pytest in `ahbo-browser`, tears everything down.
- `docs/screens/operator/smoke_S2_console.png`, `smoke_S2_mirror.png`, `smoke_S4_console.png`,
  `smoke_S4_outbox.png`.

## Tests (exact command + the real last line of pytest output)

Stand-in suite (ahbo-browser; pytest is pip-installed at container start because the image doesn't bake it;
`--confcutdir` stops pytest from loading `tests/conftest.py`, which imports the `ahbo` package that the
browser image doesn't have):
```
docker run --rm -v /opt/ahbo:/repo --workdir /repo/brain ahbo-browser sh -c \
  "pip install --break-system-packages -q pytest==9.2.0 && \
   python -m pytest -q tests/operator_ui --confcutdir=/repo/brain/tests/operator_ui"
→ last line: 12 passed, 1 skipped, 2 warnings in 11.63s
```
(12 stand-in tests + the smoke test skipping for lack of AHBO_SMOKE_* env. Ran twice — stable. The tests
cover: panels render incl. state/doses/devices ack+battery/transcript STT/outbox buttons/health/knobs/wake
honesty; a live `state` push marks an unacked-P0 device row and the incident amber; every trigger/scenario/
clock/override button POSTs the right body (asserted race-free via `expect_request`); the mirror applies `ui`
events (mood class on `.face`, cards `12:00 食藥` + med chips, ok_button, steps group, reset), captions a
`device.play_fixed` command with the clip text from `/operator/api/fixed`; outbox presses post
`{"button":"handled"}` / `{"button":"press1"}`; Esc and the PANIC button both POST `/operator/api/panic`; the
trace view renders events/commands/spans for a correlation id; hidden 404 endpoints read "not available yet"
and the console stays usable.)

Real-app smoke (both tests, re-run twice after hand-off — stable):
```
sh brain/tests/operator_ui/smoke_real_app.sh
→ last line: 2 passed, 2 warnings in 23.74s
```
Test 1 logs in, opens `/operator/`, clicks **S2**, waits in the mirror iframe for `[data-id='next_dose']`
(「12:00 食藥」) and `med_bp/med_dm/med_chol/med_asp`, asserts 血壓藥/薄血丸 labels and her line in the
transcript, screenshots console + mirror. Test 2 clicks **S4**: alarm class on the mirror's `.face`,
INCIDENT badge, incident box, then the Outbox panel receives the live fake Telegram ALERT (我處理緊/已解決
buttons) and the fake Twilio call row with its TwiML (`<Response>…<Gather numDigits="1">…`) — both buttons
are pressed through the real press wiring (`family.button` / `phone.call_status acknowledged`), the call row
repaints to `acknowledged` via SSE, then **Esc** fires the real panic (`stop_all`, session closed, face
reset) and screenshots are saved.

Same tests in the api image (no Chromium) for the full-suite run:
```
docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/operator_ui
→ last line: 14 skipped in 1.64s
```

## Full-suite run (last line)
```
docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q
→ 586 passed, 37 skipped, 1 warning in 428.12s (0:07:08)
```
Fully green. (At hand-off time the suite had 4 failures in `tests/links`/`tests/trace` — the parallel
agent's then-in-progress code, incl. a `NameError` in `brain/ahbo/trace/wiring.py`; those components have
since landed and everything passes. My 14 tests are inside the 37 skipped.) A post-hand-off re-check also
confirmed my delivered files were untouched by anyone else (mtimes) and that the newly-landed
`links/twilio.py` + `links/wiring.py` match what the console renders/posts (see Deviations 3).

## What is real vs faked
- **Real:** everything the console does is real HTTP/SSE against the real API; the mirror is the real face
  kit (`face/` served unmodified) driven by the real `ui` command stream; the smoke test runs the real
  FastAPI app, Kernel, FakeFlash, seed and scenario runner (fake mode `AHBO_FAKE=all`, no keys, no spend).
- **Faked (in tests only):** the stand-in server's canned §9 data (it is a test double, clearly a test
  file); the smoke test's LLM/STT/TTS/LiveKit/Telegram/Twilio are the system's own fakes.
- Wake-word statistics are shown honestly as "not available yet" (see Deviations).

## Deviations from PRD/CONTRACTS (and why)
1. **Wake panel (§20.4.5 rule 6) is partial by necessity.** Stage 1/2 live scores, wake/reject counters, T1
   & strictness sliders and the "missed wake" button have no backend endpoint to talk to. The panel shows
   the connected iPad's wake engine/battery/ack when one is online and — per the task's own instruction —
   an honest 「no iPad connected」 otherwise, plus an explicit "not available yet" line for the stats.
2. **Body shapes assumed for two overrides** (backend `demo.py` passes them through as `operator.*` event
   payloads without validating keys): `force_tool_result` → `{kind, tool, result}`; `provider` →
   `{kind, which:"stt"|"llm"|"tts", to:"fallback"}`. If the agent side expects other keys, only these two
   call sites in `main.js` need changing.
3. **Twilio outbox rendering** renders `payload.twiml` when present plus an always-available
   「press 1 (答咗)」 button posting `{"button":"press1"}` (CONTRACTS §9). **Post-hand-off confirmation:** the
   since-landed `links/twilio.py` patches exactly that key (`payload_patch={"twiml": call_twiml(…)}` in the
   fake channel), and `links/wiring.py press()` accepts my button values (`handled…` for Telegram,
   `press1` for calls). Verified live in the S4 smoke. Telegram rows use the real shape from
   `links/family.py` (`payload.text/photo_id/screenshots`, `buttons[].id/text`).
4. Reset demo / Warm-up / Birth live in the Triggers panel (the task lists them there); Scenarios panel has
   the S-buttons + Prepare anchors per §12.1-5/§27.5.
5. The mirror replays the snapshot on load, on SSE reconnect, on face reload (`ready`) and via the resync
   button — not on every `state` event (a rebuild would wipe an in-flight caption; live `ui` events keep it
   in sync exactly like the device).
6. Dose-now sends `slot_id: null` when no slot is selected (backend `operator.dose_now` treats it as "next").

## VERIFY results (library + version + exact function names checked)
- **face kit protocol** (read from `face/bridge.js` / `face/face.js`, then asserted live in the iframe):
  in: `addEventListener("message", ev => { if (ev.data && ev.data.ahbo) apply(ev.data.ahbo); })` (face.js:474);
  out: `{ahboEvent: evt}` via `window.parent.postMessage` (bridge.js:20); `ready {version}` on load
  (face.js:477); `window.AhBo = {apply, version…}` (face.js:476); mood → class on `#face` (face.js:104–107);
  cards → `#cards .chip[data-id]` with `.lbl` (face.js:328–331); caption → `#caption` (face.js:42,177);
  steps → `#steps .stepgroup[data-id]` (face.js:375); `tone()` is try/catch-guarded → safe headless.
- **Playwright** `1.64.1` (Dockerfile.browser pin on `mcr.microsoft.com/playwright/python:v1.64.1-noble`):
  `sync_playwright`, `browser.new_context(viewport)`, `context.route(pattern, handler)`,
  `page.expect_request(predicate)` + `request.post_data`, `page.frame_locator`/`page.frames`,
  `frame.wait_for_function(expr, arg)`, `locator(sel, has_text=)`, `locator(selector, has_text=)` counting,
  `page.on("pageerror")` — all exercised by the passing tests (never guessed; failures surfaced them).
- **pytest** `9.2.0` (matches `brain/pyproject.toml` dev pin); `--confcutdir` verified against the nested
  conftest problem; `pytest.importorskip` + `pytest.skip` for the Chromium guard.
- **EventSource**: named listeners per CONTRACTS §9 event names; reconnect via `es.readyState` — verified in
  the browser tests (conn chip state transitions asserted).
- **Backend routes** verified by reading `brain/ahbo/api/routes/operator.py`, `api/demo.py`, `api/snapshot.py`
  (they follow CONTRACTS §9 plus `POST /operator/api/reset` and `POST /operator/api/panic`, both used).
  Kernel SSE payload shapes verified in `kernel/core.py` (`_notify("kernel_event"|"command"|"ui", …)`).

## Bugs found in the existing code (fixed, since I own these files)
1. `mirror.js` dropped every synthesized caption: `_synthSay` set `sayIds` to `"synth"` **before** routing
   through `_apply`, whose guard (`sayIds.get(id) === "synth" → return`) then discarded its own `say_start`.
   Fixed by tagging the source (`ui` vs `synth`) and only de-duplicating cross-source.
2. `mirror.js` lost the face's one-time `ready` postMessage when the iframe beat the deferred module (fast
   local load) → the mirror stayed "holding" forever. Fixed with a `window.AhBo` probe started from the
   constructor (covers both lost `ready` and missed `load`), idempotent `_markReady`, and a `_keysHooked`
   guard so the Esc-iframe hook can't double-register.
3. Duplicate DOM ids: `#clock-now` (display `<b>` vs "back to now" button) and `#clock-offset` (display span
   vs apply button) — buttons renamed (`clock-back-now`); display span renamed (`clock-offset-label`).

## Changes needed in files I don't own
1. **Wake stats endpoint (feature request, blocks §20.4.5 rule 6 fully):** add e.g. `GET /operator/api/wake`
   (live Stage 1/2 scores, wake/reject counters; per device) and `POST /operator/api/wake/config`
   (T1, match strictness; persisted via `device.config_update`) + a "missed wake" tag endpoint. Once it
   exists I will wire the panel (it already renders the honest "not available yet" line).
2. None for the existing backend — every §9 endpoint the console uses already works as contracted, and the
   post-hand-off links/trace components slot in unchanged.

## Known gaps / things I did not verify
- Real-audio behaviour: the mirror shows captions, but actual clip audio playback can only be verified with
  the browser iPad (`/sim/`) or the compiled app — out of scope here.
- Wake panel with a REAL iPad connected (wake_engine ≠ none): only the sim path was exercised; the connected
  branch renders device fields from the same snapshot shape, unverified against a real heartbeat.
- Google Fonts: the console loads M PLUS Rounded 1c from fonts.googleapis.com like the face; in an offline
  deployment it falls back to Hiragino/PingFang/Noto (checked the font stack in console.css). Tests abort
  the font requests for determinism.
- Fixed-clip `segments` (audio-clock caption reveal) are not exposed by `GET /operator/api/fixed`
  (text + duration only), so mirror captions reveal by duration, slightly less precise than the iPad's
  segment-driven reveal. A `segments` field on that endpoint would make the mirror byte-exact with the app.
- Post-hand-off re-check (fresh evidence): stand-in suite re-run green (`12 passed, 2 skipped`), deepened
  real-app smoke re-run green twice (`2 passed`, incl. S4 outbox presses + Esc panic against the real
  backend), full suite fully green (`586 passed, 37 skipped`), my files untouched by others (mtimes), and
  the tablet-width layout still overflows nothing at 800/1024 px.
