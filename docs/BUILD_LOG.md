# Ah Bo build log

Decisions, deviations from the PRD (with reasons), VERIFY results and open flags.
Newest entries are appended at the end of each section.

## 1. Server recon (10 Nov 2026, before any change)

| Item | Finding |
|---|---|
| OS | Ubuntu 24.04.5 LTS (noble), kernel 6.8.0-149 |
| CPU / RAM / disk | 2 vCPU · 7.8 GiB RAM (no swap) · 96 GB disk, 94 GB free |
| Docker | Engine 29.9.3 · Compose v5.7.0 |
| Containers | none (`docker ps -a` empty) |
| Listening ports | 22 (sshd), 53 (systemd-resolved, loopback), 127.0.0.1:65529 (monarx-agent, the host's security agent). **80 and 443 free.** |
| ufw | active; default deny in; allow 22, 80, 443 (v4+v6). Not changed. |
| Host Python | 3.12.3, no `venv`/`ensurepip` → all Python runs in containers. No node, no uv on the host. |
| DNS | `srv2135273.hstgr.cloud` → 72.60.232.111 publicly (= this host). Locally `/etc/hosts` maps the hostname to 127.0.1.1. |
| Git | 2.43; repo initialised in place at `/opt/ahbo`, branch `main`, local identity `Ah Bo Builder <builder@ahbo.invalid>` (no owner noreply address given). No remote. |

Nothing on the host's security configuration (sshd, ufw, fail2ban, users) is touched by this build.

## 2. Kit

- `ui-reference/face/*` copied byte for byte into `face/` (`diff -r` clean). `ui-reference/` stays read-only.
- Storyboards run headlessly (Playwright 1.64.1, Chromium, 1600×1000 viewport) by `evals/visual/kit_capture.py`:
  - golden traces → `brain/tests/golden/kit/{S1,S2,S3,S4,S4ok}.json` (21/19/36/25/26 commands);
  - keyframe screenshots → `docs/screens/kit/` (45 PNGs). These are the visual bar.
- The headless Chromium shows the kit's own font fallback (M PLUS Rounded 1c lacks some Han glyphs, so a few characters render in the fallback font). That is the kit as-is; not "fixed".

## 3. Decisions and deviations

| # | Decision | Why |
|---|---|---|
| D-1 | Shared contracts live in `brain/ahbo/core/` (events, db schema, settings, config loader, virtual clock, ids, Cantonese number words, adapter protocols). `kernel/clock.py` re-exports the clock. | One frozen place for what every component imports; lets components be built in parallel without editing each other's files. |
| D-2 | The repo is bind-mounted into the `api` container (`/repo`) instead of baking code into the image. | The PRD's "one environment on the VPS" (§26.1): code changes need a restart, not a rebuild. Dependencies are baked into the image. |
| D-3 | `/face/`, `/sim/`, `/operator/` and `/mock-centre/` static files are served by `api` (behind Caddy), not by Caddy's file_server. | One place for the auth rules (§26.4); Caddy stays a pure TLS proxy that blocks `/internal/*`. |
| D-4 | Initial operator password: `ahbo init-secrets` generates a random one, stores only its bcrypt hash in `.env`, and writes the plaintext once to `data/secrets/operator_password.initial` (mode 600, git-ignored) so the owner can read it over SSH (`make show-initial-password`) and replace it with `make set-password`, which deletes that file. | M0 requires the owner to be able to log in before they have run anything; PRD §26.2 says the plaintext is never stored — this is the one bounded exception, removed by `set-password`. Headless checks read the same file. |
| D-5 | `play_voice_message` has no entry in `ui_steps.yaml`. | The kit's S1 storyboard (the golden trace, which wins on visuals) shows no playback step; the playing waveform card is the visual. |
| D-6 | Mood `listening` = a session was just opened by wake/touch or **she is speaking**. While the agent merely waits after a reply the face is `idle`. | The kit storyboards return to `idle` after every reply and only show `listening` while she speaks (§23.3 row 6 refined to match the kit). |
| D-7 | Turn cards are flushed when the reply starts (mood → cards → caption), not when each tool returns. | Matches the kit order (step ✓ → mood → cards → caption) in S1, S2, S3. |
| D-8 | Fall: `ok_button` + `countdown` are sent after F3 finishes (playback_done or its max duration), and the Kernel's N-second timer starts then. | Matches the kit S4 order (F3 caption, then button and countdown). |
| D-10 | INV-10's "at most 2 sentences" counts sentences longer than 8 characters; shorter ones (「有呀！」「想唔想去？」「到時我會提你㗎。」) are tags and don't count. | The owner-confirmed S2 and S3 scripts (§1.3) have three sentence marks each; this keeps them verbatim while still cutting long rambles. |
| D-9 | `activities.yaml` gains `venue_short` (e.g. 長者中心) and the calendar seed is used as-is; card labels use 12-hour `h:mm` (3:00, 9:30, 12:30). | The kit cards read 「3:00 粵曲班」/「長者中心 · 可代報名」. Additive only. |
| D-11 | The host has no `make`: `deploy/make.sh <target>` runs the Makefile inside a small `ahbo-make` container (Docker socket + repo mounted at the same path). | Nothing is installed on the host (§26.1); `make <target>` in the PRD = `deploy/make.sh <target>`. |
| D-12 | `/ws/device` auth failure: accept the socket, then close with **4401**. | A close before accept becomes an HTTP 403 and the client never sees 4401 (CONTRACTS §2.1). |
| D-13 | Memory: the post-session summary accepts 1–5 lines (the prompt still asks for 3–5). | Short sessions have little to summarise; rejecting them would lose the episode. |
| D-14 | Memory: health and family-relationship facts heard by voice go to `pending_review` (confirmed by 阿珍 in the digest); anything about medicine is never auto-written. Telegram `/fact` is a trusted source (active at once). | INV-4 and §7 writing policy; voice is unverified, the family is. |
| D-15 | Memory retention: photos a life story uses are exempt from the 7-day photo deletion. | Story cards show them on the iPad; deleting them would break reminiscence. |
| D-16 | Fall check: an answer like 「冇跌親」 escalates — when OK and help words both match, help wins. | Safety bias (INV-2): a mixed answer is never read as "OK". |
| D-17 | The `kernel_listen` session opens when F3 starts, not after it ends. | She often answers while F3 is still playing. |
| D-18 | The fall `photo` step waits at most 0.8 s for the vision note before moving on. | Vision is advisory only (INV-3); the flow never waits on it. |
| D-19 | If `playback_done` never arrives, a clip counts as finished after max(1, 0.19 × chars) + 0.3 s. | Escalation must not depend on the iPad (§2.4). |
| D-20 | `RESOLVED_OK` closes the incident by itself (no operator step). | Her OK is final; the console only watches. |
| D-21 | Escalation calls: 中心 → 阿珍 → 中心, at most 3 calls per incident (also the Twilio safety-call cap exemption). | §4.3.1 order with a hard bound; caps never block a safety call. |
| D-22 | The Kernel re-validates every `deep.result` (schema + spoken filter) before using it, even though the deep worker validated it. | Deep output is untrusted (INV-7). |
| D-23 | Informational events are recorded with `Kernel.note()` (trace + console, no reducers): `arbiter.decision`, `checkin.skipped`, `memory.repetition(_refresh)`, `phone.call_refused`, `system.cap_exceeded`. | INV-6/INV-8 visibility without feeding them back into state. CONTRACTS §4.1 additions. |
| D-24 | Scheduler jump rule: items in a virtual-clock jump > 120 s are skipped, not fired in a burst. | Setting the clock for a scenario must not fire stale reminders; the console has dose/check-in/plan-now buttons. CONTRACTS §14.2. |
| D-25 | Spoken filter: OpenCC s2t runs only when a Simplified-only character is present, with a shield list so HK colloquial characters (晒吓搵郁) are never "corrected" (曬嚇揾鬱). | Eval defect (d); INV-10. |
| D-26 | 「打九九九」 alone gets a clarifying question (Ah Bo cannot phone anyone itself); symptoms raise concern. | INV-9: no dial path exists; the concern path escalates to people. |
| D-27 | LiveKit worker: no Haiku fallback (livekit-agents 1.8.11 has no Anthropic plugin installed) and the Sonnet-over-Opper route is not wired. | The voice image carries only the verified plugin set (azure, openai, silero, elevenlabs). Open item. |
| D-28 | `send_message_to_family` only asks the Kernel via the `family.message_request` request event; the Kernel decides and emits `family.send`. | A tool never sends to a person itself (INV-1). Kernel handler pending (§5). |
| D-29 | New device event `settings {interruption_enabled}` (iOS hidden settings toggle). | Requested by the iOS component; Device Hub handler pending (§5). |
| D-30 | `face_event {type:"reminder_confirmed", slot_id, at, source:"touch_offline"}` confirms a dose she tapped while the iPad was offline. | §10.4 local reminder mirror; synced on reconnect. |
| D-31 | `session.closed` carries `why` (`agent`, `wake_rejected`, `panic`). | Trace and console show why a session ended. |
| D-32 | Extra endpoints: `POST /operator/api/reset`, `POST /operator/api/panic` (Esc), `POST /internal/reset` (`make demo-fake`), `POST /internal/clock` (`ahbo clock`). | One-click Reset/Panic (§13.2, §12.2); CLI access without a cookie. |
| D-33 | Incident cards per kind: SOS 「求救掣」, emergency concern 「緊急關注」 with step-group title 「緊急處理」; console-triggered incidents say 「操作員觸發」. | Honest card text per cause (INV-8). CONTRACTS §14.1. |
| D-34 | Fixed-clip manifest gains `clips.P1` and a top-level `"public"` section; P1 is rendered once and copied to `data/media/public/<random>/`. | The Links copy path needs `clips.P1.file`; additive JSON (GLM 01). |
| D-35 | kv `public_clip_path` = URL path `/media/public/<rand>/P1.m4a`, `public_clip_rand` = dir name (the Links format). | The two components fought over the key; the money path's format won (GLM 01). |
| D-36 | Reminder clips join med names with the shared `join_names` (「…、膽固醇藥，同埋薄血丸。」). | Clip and fake agent say names identically (one source of truth). |
| D-37 | `ahbo doctor` exits 1 when any row is red (fake rows don't fail it). | Lets `make doctor` gate. |
| D-38 | Countdown ticks are not rendered (the face beeps itself); F4 and F4p are both always rendered; `seed/voice/demo_daughter.m4a` is generated only if missing. | Kit behaviour; stable manifest across profiles; owner's real recording is never overwritten (§27.2 #6). |
| D-39 | Action timeout → `failed {reason: timeout}` (runner budget 110 s; the Kernel's 120 s guard is authoritative), not `needs_human`. | PRD §4.3.5 maps timeout → FAILED (GLM 02). |
| D-40 | Real-mode LLM field-map proposals apply to optional fields only; a required field must match the deterministic rules or the action is `needs_human`. | Stops the model filling an unknown required field (e.g. 緊急聯絡人) with her name. |
| D-41 | Cascading selects (時段 after 活動) are deferred and re-read at fill time; still `needs_human` if no allowed option appears. | Otherwise the demo form can never be filled. |
| D-42 | The mock centre API also refuses the `hkid/captcha/login/payment` variants server-side; it serves no statics (the platform does) and adds `GET /mock-centre/api/activities`. | Defence in depth; one source of truth for activities. |
| D-43 | Telegram-message actions submit `action.message_send`; the Kernel must emit the `family.send`. `action.status.screenshots` are absolute paths under `data/media/actions/<action_id>/`. | INV-1; the Family Link resolves absolute paths. Kernel handler pending (§5). |
| D-44 | `mock_registrations` is created at install (`CREATE TABLE IF NOT EXISTS`), outside `core/db.py` and the Reset-demo tables. | Demo-site data is not care data. CONTRACTS §14.5. |
| D-45 | Console wake panel is partial: it shows the connected device's engine/battery/ack and an honest 「not available yet」 for Stage 1/2 scores, counters and sliders. | No backend endpoint for wake stats yet (GLM 03). |
| D-46 | Console override bodies: `force_tool_result {kind, tool, result}`, `provider {kind, which: stt\|llm\|tts, to: "fallback"}`; dose-now with no slot sends `slot_id: null` (= next). | `demo.py` passes them through unvalidated; only two call sites to change if the agent side differs. |
| D-47 | The console mirror rebuilds from the snapshot only on load, SSE reconnect, face `ready` and the resync button, not on every `state` push. | A rebuild would wipe an in-flight caption. |
| D-48 | The 「打俾媽媽」 (`call_mum`) button is not rendered in v1; a stale press is accepted and does nothing. | The iPad has no telephony endpoint (§9.2 "otherwise drop the button"). |
| D-49 | Fake mode allows `phone.call` to a verified contact with no number (nothing can be dialled); real mode refuses it. Numbers with < 8 diallable digits are refused (stricter than "≤ 4"). | Seeded demo contacts have no numbers; same refusal set as `kernel/common.is_emergency_number`. |
| D-50 | Links info events `family.send_result` (submitted) and `phone.call_refused` (note); a refused call writes no outbox row. | INV-6 trace of every outbound attempt. |
| D-51 | Telegram: voice-note transcripts are always None; screenshots go as separate `sendPhoto` calls; each stranger message gets one fixed reply and is dropped; `set_webhook()` only builds the request; `GET /twilio/twiml/test` checks the cookie inside the handler. A silent P1 stand-in is generated if P1 is not rendered yet. | Honesty (no unverified transcript), simplicity, hard rule "never call out unasked". |
| D-52 | Browser iPad entry is `sim/index.html` (PRD says `sim/ipad.html`); WebSocket scheme follows `location.protocol`; placeholder photo is 1280×720. | Web entry files are `index.html`; `ws:` for http test runs; PRD §10.1 allows ≤ 1280 px. |
| D-53 | Sim tester affordances: `?hb_ms=`, `?offline_ms=` (defaults 10 s / 30 s) and a 500-entry `window.__simtrace`; the text box is enabled only by `session.open text_only` (wake by tapping the face first). | Tests and debugging; matches the native app's wake-first behaviour. |
| D-54 | Sim: `play_fixed` for a clip missing from the manifest → `ack ok` + `playback_done {error:"clip_not_in_manifest"}`, no caption. | INV-3 path never hangs. |

## 4. VERIFY results

| Item | Result | How verified |
|---|---|---|
| Playwright Python | 1.64.1 used for both the client (api) and the `browser` image `mcr.microsoft.com/playwright/python:v1.64.1-noble` (latest image tag on MCR; PyPI has 1.65.0 but client/server must match). The image ships browsers but **not** the Python package; it is pip-installed in `Dockerfile.browser`. | MCR tag list, PyPI JSON, running the capture |
| livekit-api (Python) | 1.2.2: `AccessToken(key, secret).with_identity().with_name().with_grants(VideoGrants(room_join=True, room=…)).to_jwt()`; `VideoGrants` is a dataclass with `room_join`, `room`. | Read in the installed package (platform; GLM 01 re-checked) |
| Caddy | `caddy:2.11.9`, local image digest `sha256:0c2243efcf437ab7f7a978f1027fcf0d4f9be8a75df27b1b3fbc5dbe0ffa8cf0`. | `docker image inspect` on the host |
| LiveKit Swift SDK | 2.18.0, commit `93a7251` (tag 2026-10-16), pinned exactly; every symbol in `Voice/VoiceSession.swift` read in the tag's source (also present in 2.19.0, not used because it moved data streams to a new Rust core). | `git clone --branch 2.18.0`; `ios/API_VERIFICATION.md` |
| Apple frameworks (iPadOS 26) | `SpeechAnalyzer`, `SpeechTranscriber`, `DictationTranscriber`, `AssetInventory`, AVFAudio, AVFoundation, WebKit, UIKit symbols read from Apple's documentation JSON; runtime items (zh_HK/yue availability on the device, Web Audio autoplay) marked **N / check on device**. | `ios/API_VERIFICATION.md` |
| Swift pure parts | Swift 6.2 (`swift:6.2-noble`): `JyutpingMatcher` 8/8 XCTests pass on the real wake YAML/JSON; `UIStateCache`, `ReminderMirror`, `DeviceChannel` compile with no warnings. XcodeGen 2.46.0 spec keys checked. | Built and run in Docker on Linux |
| PyCantonese | 5.0.0: `characters_to_jyutping([ch])` gives 阿 → `o1` only; the HKCanCor readings (`pycantonese.hkcancor()`) add 阿 → `aa3` (330 of 330 tokens) to the generated homophone table; 喂寶寶 rejected by the reduplication guard (寶寶 = "baby"); pre-roll fallback when no utterance-start time is available. | `brain/ahbo/wake/table.py`, `matcher.py`, `tests/wake` |
| azure-cognitiveservices-speech | 1.53.0: `SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm` (no AAC output format exists → WAV + ffmpeg), `PropertyId.SpeechServiceResponse_RequestWordBoundary`, word-boundary args (`audio_offset` ticks, `duration` timedelta, `text_offset`, `word_length`, `boundary_type`), `SpeechSynthesizer(speech_config, audio_config=None)`, `speak_ssml_async`; STT `PushAudioInputStream(stream_format=AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1))` (no `create_wave_format`). | site-packages source + `dir()` in the image (GLM 01) |
| Playwright (Python) | 1.64.1 in `ahbo-api` and `ahbo-browser`: `BrowserType.connect(endpoint, *, expose_network, headers, slow_mo, timeout)` (async API has no `connect_over_ws`); `new_context(accept_downloads=…)`, `goto`, `wait_for_selector`, `screenshot`, `Locator.fill/select_option`; `playwright run-server --port --host`. | `inspect.signature` in both images (GLM 01, 02, 03) |
| twilio | 9.13.0: `RequestValidator.validate(uri, params, signature)` / `compute_signature` (round-trip checked), `Client(...)`, `calls.create(to, from_, url, status_callback, status_callback_event, timeout, …)`. | `inspect.signature` in the image (GLM 04) |
| httpx, Pillow, ffmpeg | httpx 0.28.1 `AsyncClient.post(json/data/files)`; Pillow 13.0.0 JPEG save; ffmpeg/ffprobe 5.1.9 (`anullsrc`, AAC, `-show_entries`). | In the image (GLM 01, 04) |
| Pydantic, FastAPI, ruff, pytest | Pydantic 2.14.3 ignores extra keys by default; FastAPI 0.162.2 wraps included routers in `_IncludedRouter` (no `.path`); ruff 0.17.4 `--select F,E4,E7,E9` clean on actions; pytest 9.2.0 `--confcutdir`. | In the image (GLM 02, 03) |
| livekit-client (JS) | 2.22.4 pinned in `sim/talk.js`; `…/livekit-client@2.22.4/dist/livekit-client.esm.mjs` returns HTTP 200; symbols per that version's typings. **Not runtime-verified** (no LiveKit keys). | jsDelivr data API + `curl -I` (GLM 05) |
| Face kit protocol | `{ahbo: cmd}` in / `{ahboEvent: evt}` out via `postMessage`; `ready {version}` on load; `window.AhBo.apply`; mood class on `#face`; chips `#cards .chip[data-id]`. | Read in `face/face.js`, `face/bridge.js`, asserted in Playwright (GLM 03, 05) |
| Model prices | `models/pricing.py` per-MTok prices and `WEB_SEARCH_USD` are **placeholders, not verified** against provider price pages. They only steer caps. | Open flag (§5) |

## 5. Open flags

Status as of 11 Nov 2026 (`f09b3b7`; GLM components committed **pending review**).

- **No real provider has ever been called.** Every real adapter (DeepSeek, Anthropic, Azure, LiveKit, Telegram,
  Twilio, the doctor's tiny checks) is mock-tested only. First `deploy/make.sh doctor` after a key may surface
  surprises (Twilio JSON shapes, `getWebhookInfo` fields, E.164 formatting of verified numbers).
- **LiveKit worker never ran** (imports only; no keys). Browser `talk.js` voice path likewise unverified.
- **iOS app never compiled with Xcode / the iOS SDK.** Pure-Swift parts compiled on Linux only; device-only checks
  (Cantonese speech model, Web Audio autoplay, audio levels) are open.
- `models/pricing.py` prices and `WEB_SEARCH_USD` are placeholders (VERIFY before trusting cost numbers/caps).
- Kernel does not yet handle `family.message_request` (→ `send_message_to_family` returns unavailable) or
  `action.message_send` (telegram-message actions are traced but nothing is sent). The action `done` NOTIFY text
  says 「報名」 even for `kind="message"`.
- Device Hub ignores the iOS `settings {interruption_enabled}` event (the console toggle works).
- Eval `scenarios` 8/9: S3-turn3-signup fails when the Playwright `browser` container is absent (`ENOTFOUND
  browser` → action `failed` → INFO instead of NOTIFY). Passes only with the browser service up. `audio` suite
  and 52 Sonnet-judge checks are pending keys.
- `ahbo doctor --latency` / `ahbo wipe --yes` via the CLI fail (delegated subcommands take no flags); use
  `python -m ahbo.ops.doctor --latency` / `python -m ahbo.ops.wipe --yes`.
- `api/backup.py` duplicates `ops/backup.backup` (two copies of the nightly backup logic).
- In real mode `doctor.rows()` runs on every console health refresh (~30 s) → repeated tiny billable model calls.
- No wake-stats endpoint (console wake panel partial); `GET /operator/api/fixed` has no `segments` (mirror
  captions reveal by duration).
- `BEACON_TOKENS` unset → `POST /events/sensor` rejects every request (by design; the 模擬跌倒 button still works).
- The demo voice note `seed/voice/demo_daughter.m4a` is a silent placeholder (§27.2 #6).
- `mock_registrations` references (`DEMO-%04d`) could collide after ~9 578 rows (demo only).
- Fixed clips are silent fake renders until Azure is configured. The live `data/media/fixed/manifest.json` is
  still the platform's text-only fallback (`f-606b2ab5b1ce`, 9 clips, `fallback: true`) until the api restarts
  with the renderer installed; doctor shows it red.

## 6. Build organisation (owner decision, 10 Nov)

Because of credit limits, the remaining component work is split. Claude Code builds the safety-critical and tightly coupled
components (Kernel flows, Agent + models + deep, Links (Telegram/Twilio/INV-9), Browser iPad). GLM 5.3 Flash in Zcode builds
three self-contained ones from hand-off prompts in `docs/handoff/glm/`: Ops/speech/trace, Actions + mock centre, Operator
console. Every GLM deliverable is reviewed, tested and committed by Claude before it counts toward a gate.

## 7. Model route: Opper for everything (owner decision, 11 Nov 2026)

- `config/models.yaml`: Flash = `opper` / `deepseek-v4.1-flash`; Flash fallback = `opper` / `claude-haiku-5-5`;
  Sonnet = `opper` / `claude-sonnet-5-5`; vision = `opper` / `claude-haiku-5-5`. One key: `OPPER_API_KEY` (a project
  key; base URL `https://api.opper.ai/v3/compat`, from the owner's Opper "View code" snippets). Direct DeepSeek /
  Anthropic routes remain supported by changing `provider` back.
- Consequences (PRD §20.2 says to mark fields that don't pass through Opper): the deep agent runs `deep/compat_loop.py`
  (OpenAI chat-completions tool loop, same JSON schema, limits and validation) with **no server-side web search/fetch,
  no adaptive-thinking field, no `fallbacks`/`output_config`**: research uses the seeded activities, HKO weather,
  calendar and profile only. Vision sends the photo as an `image_url` data URL.
- DeepSeek's `thinking` field is still sent through Opper; `flash.send_thinking: false` turns it off if the route
  rejects it (`make doctor` reports it).
- VERIFY when the key arrives: the Haiku id `claude-haiku-5-5` in Opper's catalogue (`GET /v3/models`), thinking
  field accepted, image input accepted, tool calling on all three models.
- VERIFY (11 Nov, free model-list call `GET /v3/compat/models`, 1,540 models): exact ids `deepseek-v4.1-flash`,
  `claude-sonnet-5-5` and `claude-haiku-5-5` are all present on the owner's Opper account; the key is a project key
  (accepted). Not yet verified (needs a paid call, owner go-ahead): thinking field, tool calling, image input.

## 8. Caregiver model changes (owner decisions, 11 Nov 2026)

**Fall escalation (supersedes PRD §4.3.1 escalation targets and the F4 text):**
- 「我冇事」/button **and** the camera shows no problem (`fine` or `unclear`) → RESOLVED_OK → **Telegram INFO to
  阿珍 only** (with a 「打俾媽媽」 button; no photo, D15 kept).
- 「我冇事」 **but** the camera shows a fall (`problem`, same fixed template as the photo step label) → escalate
  (`why: camera`): ALERT + **Twilio call to 阿珍**. If the photo check is still running when she says OK, the face
  resolves at once and the family message waits ≤ 6 s for it (`decide` timer); a late `problem` still escalates.
  The camera can only add a call, never remove one (INV-2 unchanged).
- No answer / help word → ALERT + **call 阿珍**, up to 3 calls. **The simulated emergency centre is no longer called**
  (`kernel/common.call_order` = role family only; INV-9 guard unchanged).
- Wording: F4/F4p now 「我已經打電話俾阿珍…」 (no ambulance claim); P1 phone clip addressed to 阿珍; escalation card
  `esc_call` 「致電阿珍 · 已致電」; fall step `call` 「致電阿珍 / 已致電阿珍」. Golden S4/S4ok compare against the
  kit with a documented label map (`tests/golden.py OWNER_LABELS`); kinds and order unchanged.
- Fake camera: kv `fake_vision_note` lets the S4-OK scenario show her fine (「見到婆婆坐喺度，睇落冇事」); default
  stays the PRD's fall note.

**Telegram replaces the web operator console (built 11 Nov, Claude agent; reviewed):** 阿珍's chat (8925008896) is the caregiver and
admin chat: alerts, voice notes, viewing and editing the medicine schedule, calendar, quiet hours, check-in times and
contacts/phone numbers, plus demo controls (reset, panic, simulate fall, S1–S4, clock, mute, status, health).
`operator-ui/` is dropped. The operator HTTP API stays (used by the scenario runner, /sim/ and tests).

## 9. Removed (owner decision, 12 Nov 2026)
- **No 07:00 day-plan precompute and no 20:00 family digest.** The scheduler no longer emits `plan.due`/`digest.due`;
  Telegram loses `/plan`, the "即刻做 07:00 計劃" button and the digest time setting; `checkins.yaml` drops `digest`.
  `get_day_plan` still answers from the seeded plan (S3 is unaffected). Morning/afternoon/evening check-ins stay.

## 10. Speech on ElevenLabs (owner decision 11 Nov; built + reviewed 12 Nov)
- `models.yaml speech:` STT ElevenLabs `scribe_v3_live` (live, `yue` forced, via `livekit-plugins-elevenlabs` 1.8.11 with
  `model_id` overridden; every partial/final converted s2hk before captions/Kernel/LLM), `scribe_v2` for Telegram voice
  notes; TTS voice CY (`SPRXwABrRtJGzRFBpO1H`), `live_model: eleven_v4_turbo` (TTFB ≈ 0.11 s warm, 0.20 s through the
  plugin; only fast model that read the medicine names right), `fixed_model: eleven_v4` (with-timestamps → caption
  segments). Azure kept as an optional provider/fallback; no Azure key needed.
- Keyterms exclude every OK phrase and anything with 食 (a biased "OK" is the dangerous direction); test-enforced.
- Matchers accept formal/Simplified OK forms (沒事, 我沒事, 沒問題) and more help forms (起唔到, 跌倒, 摔倒, 救護車…).
- Telegram voice notes now get a Traditional transcript (supersedes the earlier "transcript None" note).
- Not yet verified: a full LiveKit session end to end; Cantonese by ear (owner to compare evals/audio/tts_eleven_v4.mp3
  with tts_eleven_v3.mp3); ElevenLabs request logging left at the plugin default.
