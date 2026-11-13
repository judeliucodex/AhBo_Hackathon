# Ah Bo (阿寶) — Software Documentation

Full engineering documentation for the MVP: every layer, every subsystem, every rule — with the evidence for
each claim (file paths, measured numbers, eval results).

> **The one design rule:** *AI is a guest in the system, never the owner of safety.* Language models converse,
> remember and plan; a deterministic Care Kernel owns every safety-critical decision, and every decision is
> traced.

---

## Table of contents

1. [What this is](#1-what-this-is)
2. [System map](#2-system-map)
3. [The stack](#3-the-stack)
4. [The Care Kernel](#4-the-care-kernel)
5. [The 11 reducers](#5-the-11-reducers)
6. [The agent harness](#6-the-agent-harness)
7. [The voice pipeline](#7-the-voice-pipeline)
8. [The wake word](#8-the-wake-word-喂阿寶)
9. [The face and the devices](#9-the-face-and-the-devices)
10. [Memory](#10-memory)
11. [The family link: Telegram, Twilio, sensors](#11-the-family-link)
12. [Actions: the browser autonomy pipeline](#12-actions-the-browser-autonomy-pipeline)
13. [The data model](#13-the-data-model)
14. [Configuration](#14-configuration)
15. [Seed demo data](#15-seed-demo-data)
16. [Safety and security](#16-safety-and-security)
17. [Operations](#17-operations)
18. [Evals and tests](#18-evals-and-tests)
19. [Measured numbers](#19-measured-numbers)
20. [Roadmap](#20-roadmap)

---

## 1. What this is

Ah Bo is a Cantonese-speaking companion for 王婆婆 (Wong Po-po), 78, living alone in Kwun Tong: mild memory
difficulties, 4 medicines a day, fall risk, no smartphone, Cantonese only. The iPad is her face; a cloud brain
thinks; the daughter's phone is the family link. Built for the Imperial Hackathon Hong Kong & Macau 2026 final.

The four scripted demo scenarios:

| # | Scenario | Capabilities shown |
|---|---|---|
| S1 | A voice note from daughter 阿珍 | Telegram inbound, attention arbiter, `list_voice_messages` / `play_voice_message`, waveform card |
| S2 | 「我幾時食藥呀？」 | Kernel dose state (read-only tool), pill chips, deterministic answer |
| S3 | Day plan → boredom → activity sign-up | `get_day_plan`, deep agent research, spoken-yes confirmation, Playwright booking, family NOTIFY |
| S4 | A fall | Fall state machine, pre-rendered safety audio, photo, 「我冇事」button + countdown, Telegram alert, escalation |

---

## 2. System map

```
        王婆婆                        阿珍 (daughter)
          │ speaks 「喂阿寶」               │ Telegram bot (voice notes, alerts, full admin)
          ▼                               ▼
┌─────────────────────┐         ┌──────────────────┐
│ iPad Air 5 (SwiftUI)│         │ Telegram Cloud    │
│  wake word (on-dev) │         └────────┬─────────┘
│  mic · camera · spk │                  │ webhook (HMAC)
│  face = WKWebView   │                  │
└─────────┬───────────┘                  │
          │ WebRTC (LiveKit Cloud)  WSS /ws/device (Bearer APP_SECRET)
          ▼                               ▼
┌──────────────────────────── Docker on one VPS ────────────────────────────┐
│  caddy (only public port) ──► api (FastAPI/uvicorn :8000, 127.0.0.1)      │
│                                 ├─ Care Kernel (single-writer event loop) │
│                                 ├─ operator API · device hub · media      │
│                                 ├─ links: telegram · twilio · sensors     │
│                                 └─ SQLite (WAL): events·commands·memory…  │
│  agent (voice worker, LiveKit Agents 1.8.11) ── STT/TTS/LLM via Opper     │
│  browser (Playwright/Chromium) ── form submission after her spoken yes    │
└───────────────────────────────────────────────────────────────────────────┘
```

Four containers: `api`, `agent` (compose profile `voice`), `browser`, `caddy` (`deploy/compose.yaml`).
Caddy terminates TLS and proxies to the api; `/internal` returns 404 from outside; the API binds
`127.0.0.1:8000` only.

**Control surface:** the web operator console was **dropped by owner decision (11 Nov)** — the Telegram bot
is the whole caregiver/admin interface (menu 示範控制 drives the demo). The operator REST API
(`/operator/api/…`) remains for scripting, and `/sim/` is the browser iPad.

---

## 3. The stack

| Layer | Technology | Where |
|---|---|---|
| iPad app | **SwiftUI**, 16 files / ~2,600 LOC, Swift 6 strict concurrency, iOS 26 target, bundle `hk.ahbo.app` | `ios/AhBo/` |
| Realtime transport | **livekit-agents 1.8.11** (server), **client-sdk-swift 2.18.0 exact-pinned** (app), **livekit-client 2.22.4** (browser sim) | `agent/`, `ios/`, `sim/talk.js` |
| The face | Vanilla **HTML/CSS/JS** — renders commands, computes nothing (`window.AhBo.apply(cmd)`) | `face/` |
| The brain | **Python 3.12** (`requires-python >=3.12,<3.13`), fully typed, `asyncio`-native, exact-pinned deps | `brain/` (252 files, ~33,000 LOC) |
| Web framework | **FastAPI 0.162.2 + uvicorn**, Pydantic v2, itsdangerous session cookies, bcrypt password hash | `brain/ahbo/api/` |
| LLMs | **DeepSeek V4.1 Flash** (fast), **Claude Sonnet 5.5** (deep), **Claude Haiku 5.5** (vision + fallback) — all via one OpenAI-compatible endpoint (Opper); provider is config (`brain/config/models.yaml`) | `brain/ahbo/models/` |
| STT / TTS | **ElevenLabs** — Scribe v3 live (STT, `yue` forced), eleven_v4_turbo (live TTS), eleven_v4 (fixed safety clips); Azure HiuMaan as TTS fallback | `brain/ahbo/speech/` |
| Database | **SQLite (WAL)** — 28 tables + 4 FTS5 trigram indexes | `data/ahbo.sqlite` |
| Browser automation | **Playwright 1.64.1** (Chromium in its own container, no ports) | `brain/ahbo/actions/` |
| Cantonese linguistics | **OpenCC** s2hk (Traditional), **PyCantonese 5.0** (wake homophones) | throughout |
| Family/phone | **Telegram Bot API** (webhook + HMAC secret), **Twilio 9.13.0** (voice, `RequestValidator` signatures) | `brain/ahbo/links/` |
| Runtime | **Docker Compose**, Caddy 2.11.9 the only public service | `deploy/` |

**No agent framework** — no LangChain, no AutoGen, no LangGraph. The only third-party SDKs are transports
(chat completions, LiveKit, Playwright, bot APIs). The turn loop, tool gating, spoken filter, eval engine,
memory, kernel — all our code.

---

## 4. The Care Kernel

A deterministic, **event-sourced, single-writer** state machine. Everything the system does is: *an event
comes in, reducers decide, commands go out.*

**The loop** (`kernel/core.py`, ~300 lines): one `asyncio.Queue` + one `asyncio.Lock` serialise processing —
`submit(ev)` → append to `events` → run each of the 11 reducers in fixed order → each returns
`[Command]` or `None` → persist + dispatch commands → command results return as new events. Reducers are
**pure functions** `reducer(state, event, ctx) → list[Command] | None` with **zero I/O by construction**;
`Ctx` exposes read-only context (config, settings, clock, read-only DB queries) and `ctx.reply(ev, result)`
answers request-events (the `Kernel.request` pattern used by tools).

**Timers are commands**: `timer.set {key, delay_s, data}` / `timer.cancel`; on fire the Kernel processes
`timer.fired {key, data}`. Durations use the monotonic clock × `time_scale` — the **virtual clock** (HKT with
a console offset) shifts *timestamps* but never stretches timers. Clock jumps >120 s fire nothing (no
stale-reminder bursts after a demo clock change). The scheduler ticks every **15 s** (`schedule.tick`) and
checks dose/check-in/leave-soon timings restart-safely.

**The attention arbiter** (`kernel/arbiter.py`, INV-5) — one voice at a time; every announcement is
prioritised:

| Priority | Rule |
|---|---|
| **P0** (emergency/fall) | Preempts anything, now |
| **P1** (urgent family/news) | Now, or at the next turn boundary ≤30 s |
| **P2** (a voice note arrived) | Waits for a ≥1.5 s quiet gap in conversation; retries ≤60 s, then next session |
| **P3** (check-in) | Only if no session open and none closed <20 min |

Every decision is a logged `arbiter.decision`; ≤1 pending item per class; quiet hours (22:30–07:00) pass only
P0 + P1 dose reminders; an open incident admits only P0. The pending queue survives restart.

**The append-only ledger** (INV-6): `events` and `commands` tables — a SQLite trigger
(`events_no_update`) **RAISEs ABORT on any UPDATE**. Every event/command carries a **correlation ID**
(`inc_…` incidents, `sess_…` sessions, `act_…` actions); `/operator/api/trace?correlation_id=…` returns the
full story. Per-turn latency lives in `spans`, spend in `costs`, provider hiccups in `quality`.

**UI state replay** (`kernel/ui_state.py`): a snapshot of what the face should show — on reconnect or reload
the device receives a full replay (alarm, cards, steps, OK button), so the demo survives refreshes.

**Evidence:** `kernel/core.py`; `core/db.py` (trigger); `kernel/arbiter.py`; `kernel/ui_state.py`; QA report
26: **73/73 commands** have non-null `event_id` and `correlation_id`.

---

## 5. The 11 reducers

Fixed order (`kernel/reducers/__init__.py`): **fall, concern, dose, checkin, action, family, session, turn,
voice, arbiter, misc**. A reducer crash is written to `quality`, never kills the loop.

| Reducer | Owns | Key rules |
|---|---|---|
| **fall** (705 ln) | The fall machine | IDLE→ALARM→PHOTO_CHECK→AWAITING_OK→(RESOLVED_OK ∣ ESCALATING→REASSURING→ACKNOWLEDGED)→CLOSED. Countdown 5 s demo / 30 s prod; F3 repeats at 15 s (prod); ack window T_ack 120 s demo / 300 s prod; **≤3 calls per incident**; 60 s cooldown; photo wait ≤0.8 s for vision. **OK = match_ok / device.touch_ok / operator.ok only** (INV-2: agent-sourced events never resolve; the camera can only *add* a call, never cancel) |
| **concern** | Worry levels | info → digest; urgent → family INFO + P1 inject, once per category (scam/health) per 10 min; emergency → opens the fall machine directly at AWAITING_OK (no photo). Red flags (redflags.yaml regexes) can only *raise* concern (INV-2) |
| **dose** | Medicine | SCHEDULED→REMINDING→re-prompts T+15, T+30→MISSED at T+60 (family INFO); snooze ≤2×15 min; confirm-taken window 3 h; timings evaluated on `schedule.tick` |
| **checkin** | Daily check-ins | morning/afternoon/evening (08:30/15:00/20:30), P3 via arbiter; skipped in quiet hours/incidents; deferred if a session is about to open (≤30 min); skipped if a session closed <20 min ago; 20 s no-reply → close |
| **action** | Sign-ups | PROPOSED→APPROVED_BY_RESIDENT (STT confidence ≥0.6)→RUNNING→DONE ∣ NEEDS_HUMAN ∣ FAILED (120 s timeout; deep jobs one-at-a-time) |
| **family** | Outbound to family | `family.message_request` — only allowlisted verified family; text rebuilt from a fixed template 「媽媽（王婆婆）託阿寶話你知：「…」」 ≤300 chars — free LLM text is **never sent to a human** |
| **session** | Voice sessions | Modes: companion / reminder / kernel_listen / reassure / paused; idle close 30 s; hard max 600 s; `sessions_per_day` cap refuses non-safety sessions |
| **turn** | What the face shows per reply | words → thinking → tool steps (labels from ui_steps.yaml) → reply mood → cards ordered by mention → captions |
| **voice** | Voice-note playback | `voice.play_request` → card update + `device.play_voice_message`, timeout duration+5 s |
| **arbiter** | Announcements | see §4 |
| **misc** | Everything small | operator.* effects; device/beacon presence (`device.offline` after 30 s silence); `event.leave_soon` → P1; `phone.call_refused` / `device.error` → ⚠️ INFO to family; restart cleanup |

**Evidence:** the file itself + `brain/tests/kernel/` (per-reducer suites, incl. `test_fall_owner_rules.py`).

---

## 6. The agent harness

### 6.1 The turn loop (`agent/runner.py::TurnAgent.run_turn`)

Guards: `MAX_ROUNDS = 6` tool rounds, `HISTORY_TURNS = 12`, STT confidence cap 0.6 (below it she says G1).
One turn emits, in order: `user.utterance` → `agent.steps_plan` (the ✓ steps) → `agent.tool_call` /
`agent.tool_result` per tool → `agent.reply_start` → per-character captions → `agent.reply_end`, plus
`quality.*` rows (mandarin_drift, too_long, digits_spoken, tool_error, stt_low_confidence). Any exception
speaks the fixed line 「阿寶而家諗唔到嘢，等陣再試吓好嗎？」 — the loop cannot fail silently.

### 6.2 The 23 tools — every one is Kernel-gated

Registry (`agent/tools/__init__.py`): `@tool(name, desc, args, modes)`; `schemas(mode)` filters by the
session's mode, and `run_tool` **re-checks** at execution. Default modes = companion+reminder;
**reassure mode allows exactly two tools**: `get_incident_status`, `get_time`.

| Tool | Purpose |
|---|---|
| `get_weather` | HK Observatory now + today |
| `get_time` | HK date/time (reassure-allowed) |
| `list_events` / `add_event` | Calendar read / add |
| `get_day_plan` | Today's compiled plan (events + departure times) |
| `get_route` | Bus route to a venue |
| `find_local_activities` | Nearby elder activities |
| `check_activity_logistics` | Time/transport feasibility |
| `list_voice_messages` / `play_voice_message` | Family voice notes (playback blocks the turn) |
| `get_med_status` | Next dose from Kernel dose state — **the only medicine source** |
| `confirm_taken` | Record a dose taken (3 h window; only if one is pending) |
| `snooze_dose` | ≤15 min, ≤2 times |
| `recall` / `remember` / `recall_life_story` | Memory retrieval / write / reminiscence |
| `deep_task` | Spawn the background Sonnet research job |
| `request_action` / `confirm_action` | Propose the sign-up / execute after her yes |
| `send_message_to_family` | *Request* a message — the Kernel decides and emits `family.send` |
| `raise_concern` | Raise worry level (never lower — INV-2) |
| `show_card` | Show a pill/event/plan card on the face |
| `get_incident_status` | Incident/calm status (reassure-only) |

### 6.3 The deep agent — Flash calls Sonnet

`deep_task` starts a background job: a **hand-written Anthropic loop** (`deep/loop.py`) — **read-only tools
only**: Anthropic server tools `web_search` / `web_fetch` (≤5 uses each) + 4 custom read-only tools
(`get_weather_forecast`, `read_calendar`, `read_profile`, `find_local_activities`); all results folded into
one user message, truncated to 20,000 chars. **Budgets: ≤15 steps, ≤90 s, ≤$0.30, 10 s/tool, best-so-far
retry at 15 s.** Stop-reason handling covers end_turn / pause_turn / refusal / max_tokens / budget-exceeded —
each with a defined retry-or-fail path. The result is parsed (fence-tolerant), then validated against the
**JSON Schema** (`deep/schema.py`, Draft 2020-12, `additionalProperties: false`): `status(done|failed|timeout)`,
`spoken_summary` ≤120 chars, `card{title, items≤6}`, `sources[]`, `signup_methods[]`,
`needs_confirmation[]`. `post_validate` re-runs `spoken_summary` through the spoken filter and **strips every
URL outside `sources`**; unrepairable → `status: failed` + fixed apology 「唔好意思，我搵唔到，遲啲再試下好嗎？」.

### 6.4 The 6-stage Cantonese spoken filter (`agent/filters/spoken.py`)

Runs on every sentence before it is spoken or displayed:

1. Strip system prefixes and **markdown/URLs/emoji** (flagged: `markdown`, `url`, `emoji`)
2. **Simplified→Traditional** (OpenCC s2t) only when a Simplified char is present, with an HK-colloquial
   shield list (晒 吓 搵 郁 … protected via PUA markers)
3. **Numbers → spoken Cantonese** (十五點→下晝三點, %→百分之, $→…蚊, units mg/ml/km kept; 6+ digit numbers
   digit-by-digit) + fullwidth punctuation
4. **Mandarin-drift detector + replacement map** (17 pairs, 沒有→冇 …) with a whitelist (的士, 了解 …) —
   flagged `mandarin_drift`
5. **Length guard**: ≤2 sentences (。！？；); short tags (≤8 chars) don't count; over → flagged `too_long`, stream stops
6. **TTS chunking** on sentence punctuation

### 6.5 Model configuration (`brain/config/models.yaml`)

| Purpose | Model | Thinking |
|---|---|---|
| `voice_turn` | Flash | **off** (latency) |
| `extractor` / `intent_tag` | Flash | off |
| `digest` | Flash | low |
| `deep_task` | Sonnet | medium |
| `morning_plan` | Sonnet | high |
| `signup_fieldmap` | Sonnet | low |
| `eval_judge` | Sonnet | low |
| `vision` | Haiku (fallback Sonnet, 8 s timeout) | — |

Flash fallback: **claude-haiku-5-5**, first-token timeout 5 s. Max tokens: Flash 400 (short spoken answers),
Sonnet 16,000 with a 40,000-token task budget. All routes go through the OpenAI-compatible endpoint —
**provider is configuration, not code**.

**Evidence:** measured latency p50 3.37 s / p95 4.06 s end-to-end (LLM TTFT ≈3.1 s incl. tool round; LLM→first
audio ≈0.3 s); Cantonese eval **40/40 on live models** (100% drift-free / clean / ≤2 sentences); eval battery
140 cases, 100% CI; 586 tests. TTS time-to-first-byte 0.11–0.34 s.

---

## 7. The voice pipeline

The worker (`agent/worker.py`, LiveKit Agents 1.8.11) is a cascade of three custom nodes plus turn-handling:

**`stt_node` — listening.** ElevenLabs **Scribe v3 live**, language forced `yue`, manual commits, **16
keyterms injected** (阿寶, 王婆婆, 阿珍, 阿明, 血壓藥, 糖尿藥, 膽固醇藥, 薄血丸, 聯合醫院, 覆診, 觀塘, 秀茂坪,
粵曲班, 長者中心, 救命, 起唔到身) so safety words survive recognition; every transcript alternative is
converted Simplified→Traditional in-node (INV-10 — nothing downstream ever sees Simplified). STT confidence
below 0.6 → she honestly says she didn't hear well (G1). Azure zh-HK with a phrase list is the configured
STT fallback.

**Turn handling — elderly-tuned.** Silero VAD (prewarmed); endpointing `min_delay 0.8 s / max_delay 3.0 s`
(a 3-second pause mid-thought is not the end of the sentence); interruption min_duration 0.6 s;
**resume_false_interruption** — if she "interrupted" by accident, the interrupted reply *continues* instead
of restarting. `EOUMetrics.end_of_utterance_delay` feeds the latency ledger.

**`llm_node` — thinking.** Streams Flash via the OpenAI-compatible client (thinking off, 20 s read timeout,
**FallbackAdapter Flash→Haiku**, 1 retry); `reply_start` fires on the first complete sentence so captions
begin immediately; the 2-sentence guard stops the stream; exceptions → `quality.llm_failed` + G1.

**`tts_node` — speaking.** ElevenLabs streaming TTS, the CY "Calm & Friendly" voice, `language_code=yue`,
**`zh_sentence_tokenizer`** splits on 。！？；， (min chunk 2 chars) so first audio starts fast; Azure HiuMaan
(rate 0.9) is the `FallbackAdapter` second voice.

Silent/`kernel_listen` sessions get **no tools**; sessions proxied from the browser text box run the same
turn loop via the internal API (`/internal/tool/{name}`, long-poll `/internal/pull/{sid}`).

**Evidence:** live 2-person test (12 Nov): worker joined <3 s; heard 「阿寶，我幾時食藥啊？」; reply ~2 s after
she stopped; `get_med_status` called; INV-4 held. Measured totals p50 3.37 s / p95 4.06 s (n=2, target ≤3.0/≤4.5).

---

## 8. The wake word 「喂阿寶」

**On the iPad (stage 1+2, fully on-device):** `SpeechAnalyzer` + `SpeechTranscriber` locale `zh_HK`
(fallback `yue`, then DictationTranscriber) streams text into **`JyutpingMatcher.swift`** — a pure-Swift twin
of the Python matcher, sharing one generated homophone table (`wake_homophones.json`: 147 wai-class, 30 aa,
83 bou characters, built from PyCantonese with HKCanCor ≥25%-share readings). Match rules: syllable 0 must be
a wai-class (喂/威/餵…), then full aa+bou, or **missing-aa 「喂寶」** (2 syllables), or one filler syllable
(「喂喂阿寶」); a **reduplication guard rejects 「喂寶寶」** ("baby"). Requires 「喂」 + 2 consecutive transcript
updates; 5 s cooldown after a reject; self-wake suppression; an `AVAudioEngine` tap (RMS onsets, −45 dBFS,
≥300 ms silence) gates listening and stops during LiveKit sessions.

**Stage 3 — server re-check:** her first utterance after the claimed wake is re-validated by
`ahbo.wake.matcher.stage3_check` (config `wake_match.yaml`: `require_wai`, `stage3_timeout_s 1.5`, `utterance_start_silence_ms 300`,
`cooldown_after_reject_s 5`); the remainder after the wake phrase becomes turn 1's text — 「喂阿寶，今日有咩做呀？」
wakes **and** answers in one breath. A rejected wake closes the session with `why=wake_rejected`.

Shared fixture `wake_cases.yaml` — accepts 喂阿寶 / 喂，阿寶 / 喂亞寶 / 威阿寶 / 喂阿保 / 喂寶 / 喂 阿寶 /
喂阿寶，今日有咩做呀？; rejects 阿寶 / 喂阿爸 / 喂阿婆 / **喂寶寶** / 我話喂阿寶 / 餵飽 / "" / 今日天氣好好.
`JyutpingMatcherTests` (XCTest, 8/8) compile the Python fixture into the Swift test bundle.

**Browser note:** `/sim/` has no wake word — tap the face to wake (by design; wake is native-only in v1).

---

## 9. The face and the devices

### 9.1 The face contract (`face/face.js`)

`window.AhBo.apply(cmd)` — the face is a pure renderer (7 moods: idle/listening/thinking/curious/warm/alarm/sleepy;
per-character caption reveal; 11 card kinds incl. med/event/plan/voice_note/incident/action/story; check-mark
steps; 「我冇事」 button; countdown; camera flash; offline dot). It emits: `ready`, `tap`, `ok_tapped`,
`countdown_done`, `say_done`, `birth_done`, `error`. Bridge: native → `webkit.messageHandlers.ahbo`;
browser → iframe `postMessage`. All text Traditional Chinese (HK).

### 9.2 The Device Hub (`links/device_hub.py`)

`WSS /ws/device`, auth `Authorization: Bearer APP_SECRET` (or operator cookie + `?sim=1`; otherwise close 4401).
On `hello` → `welcome {device_token, server_time, fixed_cache_version, config}` + **full UI replay**.
Heartbeat every 10 s; `device.offline` after 30 s. **Commands to device:** `ui`, `play_fixed`,
`play_voice_message`, `capture_photo`, `session.open/close`, `wake.enable`, `config.update`, `stop_all` —
each acked ≤2 s; P0 commands retry 3× @2 s then fail red; P0 drops queued non-P0 UI frames. Inbound:
`ack`, `heartbeat`, `wake`, `touch_ok`, `playback_done` (deduped), `photo_uploaded`, `face_event`, `utterance`,
`error`. Multi-tab: every ready connection receives every command (both tabs mirror the face).

### 9.3 The iPad app (`ios/AhBo/`)

| Module | File(s) | Does |
|---|---|---|
| App | `AhBoApp.swift`, `AppModel.swift` | Full-bleed black face, hidden status bar; one @Observable owner of all modules; audio-session config |
| Device | `DeviceChannel.swift`, `CommandRouter.swift` | URLSessionWebSocketTask to `/ws/device` (Bearer secret); hello→welcome+replay; heartbeat 10 s (battery/thermal/in_session); per-command ack ≤2 s; backoff 0.5→8 s ±20% jitter; dispatches all 8 command kinds |
| Face | `FaceWebView.swift`, `UIStateCache.swift` | WKWebView + `apply(cmd)` queue (max 300) until `ready`; replays cached UI (mood, cards, steps, ok_button) on reload/crash; transient cmds not cached; 3-finger 3 s long-press → hidden settings |
| Voice | `VoiceSession.swift` | LiveKit 2.18.0: `POST /session` → Room.connect + mic publish; transcription topic; mouth animation from agent audio level @20 Hz |
| Wake | `WakeWordEngine.swift`, `JyutpingMatcher.swift`, `AudioTap.swift` | see §8 |
| Settings | `SettingsView.swift` | Hidden behind 3-finger press + PIN (default 2580, changeable); app secret in **Keychain**; toggles: host, wake, LiveKit, camera, interruption, debug overlay |
| Media | `FixedClipPlayer.swift`, `VoiceNotePlayer.swift`, `PhotoCapture.swift`, `ReminderMirror.swift` | Clip cache (manifest sha1) + AVAudioPlayer metering → `level` @20 Hz; voice notes download to Caches; one-shot front camera JPEG ≤1280 px q0.7 → `POST /media/photo`; **local reminder replay when offline >30 s** (R-slot clips, Asia/Hong_Kong) with offline-touch confirmation queued |
| Resources | `fixed/`, `wake_homophones.json` | The 13 safety clips + shared wake table |

`project.yml`: folder **references** (not copies) `../face` and `../brain/config/wake_match.yaml`;
`Info.plist`: display name 阿寶, `UIBackgroundModes: audio`, all iPad orientations.

### 9.4 The browser iPad (`sim/`, served at `/sim/`, operator-gated)

`sim.js` (glue: face iframe bridge, tap-to-wake, `?hb_ms`/`?offline_ms` test knobs, trace ring),
`channel.js` (WSS with operator cookie; heartbeat 10 s; pong watchdog 25 s; backoff 0.5→8 s; offline at 30 s),
`talk.js` (**"Talk (browser mic)"** — livekit-client ESM, `POST /session`, mic publish, transcription stream),
`photo.js` (Camera checkbox → `getUserMedia`, else placeholder), `media.js` (manifest cache + single
`<audio>` + `playback_done`). Panel controls: Rotate · Reload face · Enable sound · Camera checkbox ·
**王婆婆講 text box + Send** (typed utterance — mic-free fallback) · 我冇事 · Tap face · Log. Reload → face
re-sends `ready` → server replays state. Two tabs share one device id and both mirror everything.

---

## 10. Memory

| Subsystem | Behaviour |
|---|---|
| **Facts** | 7 categories; family/health facts change **only with family confirmation**; supersede/confirm ops |
| **Session summaries** | One Flash extraction per conversation, confidence gate ≥0.8; transcript **PII-redacted before the model sees it** |
| **Life stories** | For reminiscence; **grief guardrail** — stories mentioning 過身/去世 excluded unless family marks OK; marked used when told |
| **Repeated-question cards** | 3rd identical question in a day → gentle card rendered **from Kernel data, not the model** (INV-4) |
| **Retrieval** | SQLite **FTS5 trigram** (+LIKE fallback); terms quoted (no MATCH injection); Cantonese day-word expansion (琴日→yesterday) |
| **Privacy** | PII regex redaction — HKID, ≥8-digit runs (spoken-Chinese digits normalised), email, password/OTP → 〔已隱藏〕 or rejected |
| **Retention** | Transcripts & voice notes 30 days, fall photos **7 days**; deletion via `secure_delete` + `VACUUM`; runs once per HKT day |

**Evidence:** `brain/ahbo/memory/` (store, extractor, life_stories, repetition, pii, retention); memory eval
15/15 (CI); FTS injection-safe quoting verified in QA.

---

## 11. The family link

### 11.1 Telegram (`links/telegram.py`, `links/bot/`)

The bot is the **whole caregiver/admin interface** (web console dropped by owner decision):

- **Commands:** `/start /menu /help /cancel`, views `/today /meds /cal /settings /contacts /alerts /health
  /demo /knobs`, admin `/reset /panic /fall /sos /mute /unmute`, demo `/scenario S1|S2|S3|S4|S4ok`,
  `/clock 07:58|+30|now|Sx`, `/dose [slot]`, `/checkin morning|afternoon|evening`, `/say`, `/thinking
  flash|sonnet <level>`, `/quiet`, `/checkintime`, `/whoami /fact /story`
- **Menu (Chinese):** 今日狀況 / 藥物 / 日程 / 設定 / 聯絡人 / 示範控制 / 系統健康
- **Demo view buttons:** 🔄重設示範 🛑全部停止 🤕模擬跌倒 🆘模擬求助 S1–S4ok 🕒較時間 ⏱返真實時間 🔇靜音
  🗣講一句 💊即刻食藥 👋即刻問候 🧠思考程度
- **Every change is nonce-confirmed** (確認/取消); callback payloads ≤64 B; forms for med/slot/calendar/contact/quiet-hours
- **Contacts** normalise to E.164 (8 digits → +852), emergency numbers refused, and — with real Twilio — are
  only marked `verified=1` if Twilio lists them as **Outgoing Caller IDs**
- **Alert buttons:** 我處理緊 (handled) / 錄段聲俾媽媽 (voice reply to mum) / 已解決 (resolved)
- **Voice notes inbound:** OGG → ffmpeg AAC 64k → ElevenLabs file STT → Traditional text → arbiter P2
- **Security:** webhook header `X-Telegram-Bot-Api-Secret-Token` compared with `hmac.compare_digest`
  (missing → 401, wrong → 403); chat-ID allowlist; strangers get one fixed line 「呢個係私人家庭機械人。」 and
  their text follows the injection protocol (data, never instructions)
- Voice-note audio + captions are retained 30 days then deleted

**Evidence:** 147 bot tests; 51 live probe tests, 0 failures; live in production since 11 Nov.

### 11.2 Twilio phone (`links/twilio.py`)

`phone.call` → **guard_call** (independent INV-9 guard: contact_id only, `verified=1`, role
family/emergency-sim, raw numbers refused, 999/112/911 refused exact-or-≤5-digit-suffix in any Unicode digit
form, ≥8 digits) → cap 5/day (safety ≤3/incident) → **outbox row** → background call. TwiML routes
`/twilio/twiml/{call_id}` (Play the P1 clip ×2 + Gather 1 digit, 12 s), `/twilio/gather` (digit → acknowledged),
`/twilio/status` — all validated with the SDK `RequestValidator` (403 on bad signature). The alert clip is
published at an **unguessable path** `/media/public/<48-hex>/P1.m4a`. The P1 clip spoken to the family is the
pre-rendered safety line — never live TTS. `phone.call_refused` → ⚠️ INFO to family (misc reducer).

**Current demo state: Twilio is faked** (`AHBO_FAKE=twilio`) — the call is an Outbox row with a scripted
ringing→in-progress→completed sequence and a **答咗** press button; no real call can occur.

### 11.3 Sensors (`links/sensors.py`)

`POST /events/sensor`, header `X-Beacon-Token` (sha256 vs `BEACON_TOKENS`), idempotent on `(beacon_id, seq)`.
The reference fall-detection rule: **freefall <0.4 g ≥60 ms → impact >2.5 g ≤1 s → stillness ±0.15 g ≥2 s**.
`sensor.lost` fires after 90 s silence. Beacon currently unset (owner decision — demo falls are operator-triggered).

---

## 12. Actions: the browser autonomy pipeline

States (`kernel/reducers/action.py`): PROPOSED → **APPROVED_BY_RESIDENT** (her spoken yes, STT confidence
≥0.6) → RUNNING → DONE ∣ NEEDS_HUMAN ∣ FAILED (120 s timeout).

The runner (`actions/runner.py`, `actions/playwright_form.py`) drives Chromium in the `browser` container:

- **Field mapping from the accessibility tree** — the LLM (`signup_fieldmap` purpose, thinking low) may
  propose field *names* only; **values come only from allowlisted profile fields** (name, phone placeholder,
  district, language, activity, session). The LLM can never invent a value or un-forbid a field.
- **Domain allowlist** (`config/action_domains.yaml`): submit only the mock centre; read-only `*.gov.hk`,
  `*.lcsd.gov.hk`, `*.swd.gov.hk`, `*.hkcss.org.hk`; anything else → `needs_human domain_not_allowed` without opening.
- **Hard stops → NEEDS_HUMAN + HANDOFF to family (with full-page screenshots):** HKID / 香港身份證 / 信用卡 /
  password / 銀行 fields, CAPTCHA, login walls, payment, unknown *required* fields, cross-domain redirects.
- **Evidence trail:** `pre_submit` + `post_submit` PNGs stored per action (`data/media/actions/<id>/`), both
  sent to the family in the NOTIFY; success → calendar event booked + spoken confirmation with the **DEMO-xxxx**
  reference number; the NOTIFY text is a **fixed template**, never LLM text.
- **Caps:** 10 Playwright actions/day.

**The mock centre** (`mock-centre/` + `actions/mock_centre.py`): 秀茂坪長者中心（示範）signup form — required
name_zh, age_group, phone (8 digits), activity, session; **probe variants** `?variant=hkid|captcha|login|payment`
inject the hard-stop fields, refused client- and server-side (400). Registrations number `DEMO-0423…`.

**Evidence:** QA 25 — yes → booked + NOTIFY in **5.3 s**; all 4 variants → HANDOFF, nothing registered;
`needs_human` in ~5 s with the browser container stopped.

---

## 13. The data model

SQLite, WAL, **28 tables + 4 FTS indexes** (`core/db.py`):

`meta · events (append-only) · commands (pending|sent|acked|done|failed|dropped) · contacts · resident ·
med_catalogue · med_schedule · dose_events · incidents · calendar_events · actions · deep_tasks · day_plans ·
facts (+fts) · sessions (+fts) · turns (+fts) · life_stories (+fts) · repetition · voice_messages · spans ·
costs · outbox · kv · media · beacons · sensor_seen · devices · quality`

`RESET_TABLES` (18) = demo-reset clears; trace tables are kept. One writer (the api). Nightly online backup.

---

## 14. Configuration

| File | Purpose |
|---|---|
| `brain/config/models.yaml` | All model routes, thinking levels, fallbacks, STT/TTS models, 16 keyterms |
| `brain/config/caps.yaml` | llm_usd $3/day · deep_tasks 15 · deep_task_usd $0.30 · deep_task_seconds 90 · twilio_calls 5 · playwright_actions 10 · session_minutes 10 · sessions_per_day 120 |
| `brain/config/action_domains.yaml` | Submit/read-only domains, forbidden field patterns (HKID/password/card) |
| `brain/config/fixed_lines.yaml` | The safety lines: F1–F5 (+F4p production variant), A1, G1, G2, R_TEMPLATE, P1_TEMPLATE |
| `brain/config/ui_steps.yaml` | Face step labels per tool (min_active 600 ms, ≤4 steps, auto-clear 4 s) |
| `brain/config/wake_cases.yaml` | Shared wake accept/reject fixtures (Python tests + XCTest) |
| `brain/config/wake_match.yaml` | Stage-3 tuning (require_wai, timeouts, cooldowns) |
| `brain/config/checkins.yaml` | 08:30 / 15:00 / 20:30; quiet 22:30–07:00; skip/defer rules |
| `brain/config/ok_phrases.yaml` | 「我冇事」OK list + help list (**help wins**), variant spellings |
| `brain/config/redflags.yaml` | Emergency/urgent/scam regex patterns |
| `brain/config/phrases.yaml` | STT hint phrases |

---

## 15. Seed demo data (`brain/seed/demo.yaml`)

- **Resident:** 王婆婆 Wong Po-po, 78, 觀塘 示範邨12樓1203室; quiet hours 22:30–07:00
- **Contacts:** c_chun 阿珍 (family · 女 · priority 1, Telegram + phone), c_ecs 緊急中心（模擬） priority 0
- **Medicines:** 血壓藥 Amlodipine 5 mg · 糖尿藥 Metformin 500 mg · 膽固醇藥 Atorvastatin 20 mg · 薄血丸
  Aspirin 80 mg — slots **08:00** (糖尿病藥, seeded taken 08:05), **12:00** (all 4), **20:00** (糖尿病藥)
- **Calendar:** 09:30 出門 (巴士往聯合醫院) · 10:30 覆診 (基督教聯合醫院) · 12:30 同阿珍飲茶
- **Facts:** 6 (incl. daughter 阿珍, grandson 阿明); **Life stories:** 教書嘅日子 (1970s–2000s) · 阿明第一日返學
  · 睇大戲 (1960s, 帝女花 — she loves 任劍輝)
- **Activities:** 粵曲班 15:00 (10-min walk, web_form signup) · 太極 09:00 Tue/Thu/Sat (in_person) · 手工班
  14:30 Wed/Fri (phone)
- **Scenario clock anchors:** S1 09:00 · S2 10:00 (next dose 12:00 = 兩個鐘之後) · S3 08:30 · S4 15:00
- **Voice note:** `vm_demo` from 阿珍 07:42 (stock ElevenLabs "Bella" voice: 「媽，記得食飯呀！星期日我帶阿明嚟探你。」)

---

## 16. Safety and security

**The 10 invariants (INV-1…INV-10)** — LLM talks/Kernel decides · concern only raised · safety audio
pre-rendered (INV-3) · medicine facts only from dose state · one voice (arbiter) · everything traced ·
untrusted text is data · operator visible · **never dial 999/112/911** · Cantonese ≤2 sentences.

**INV-7 in practice:** deep results schema-validated with URLs stripped outside `sources`; spoken filter
drops instruction-like sentences; Telegram strangers follow the injection protocol; FTS terms quoted; mock-site
page text is data to the field-mapper, never rules.

**The double dial guard:** Kernel `callable_contact` + link `guard_call` (see §11.2), refusals write no outbox
row; ≤5-digit and full-width-digit tricks rejected.

**Spend governance:** 8 caps (§14), breaches logged `system.cap_exceeded` + quality row; per-turn cost rows.

**Secrets:** keys only in `/opt/ahbo/.env` (mode 600, git-ignored); logs show ≤4 chars; the iPad secret lives
in the iOS **Keychain**; `Root_password.md` never opened.

**Web security:** operator password bcrypt + itsdangerous cookie (12 h), login rate-limit 5/min/IP; HMAC
Telegram webhook; Twilio RequestValidator; unguessable public media path; Caddy adds HSTS/nosniff/
Permissions-Policy; `/internal` unreachable from outside.

---

## 17. Operations

- **Doctor** (`ops/doctor.py`, 41 checks): 22 key/secret rows (masked) · model routes (flash/fallback/sonnet/vision)
  · ElevenLabs voice/models/STT · Azure TTS/STT · LiveKit · Telegram webhook · Twilio auth / verified numbers /
  **TwiML dry-run** · Playwright · iPad connected · beacon heartbeat (≤90 s) · fixed-clip cache · clock offset
  (±48 h) · disk (<5 GB red) · SQLite `integrity_check`. Every red row names the fix and the SETUP.md section.
- **Backups:** nightly 03:30 HKT SQLite online backup → `data/backups/` (mode 600, 7 kept); restore tested in QA.
- **Wipe:** exports JSON first (mode 600), then deletes everything except meta + fixed cache; requires `--yes`.
- **Fixed clips:** `render-fixed` renders F1 F2 F3 F4 F4p F5 A1 G1 G2 + R_<slot> ×3 + P1 (13 clips, eleven_v4,
  per-character timestamps) into the manifest; copied into the iOS bundle; fake mode renders silent clips at
  0.19 s/char.
- **CLI** (`python -m ahbo.ops.cli` / `deploy/make.sh`): init-secrets · seed · set-password · show-app-secret ·
  show-initial-password · doctor [--latency] · chat · clock · sim · wipe · render-fixed · gen-wake-table · eval · backup ·
  up/down/restart/logs/ps/test/eval/demo-fake.
- **Operator API** (cookie-gated): `/operator/api/state` (+`/operator/stream` SSE) · `trigger` · `override` ·
  `clock` · `reset` · `panic` · `scenario/{name}` · `outbox` + `outbox/{oid}/press` · `trace` · `health` · `fixed`.
  Trigger kinds: fall, sos, dose_now, checkin, plan_now, voice_note, family_text, reset, wake, utterance, birth, warmup.
- **Internal API** (Bearer INTERNAL_TOKEN, Docker-only): `/internal/turn /ui /tool/{name} /event /context/{sid}
  /session/claim /pull/{sid} /clock /reset`.

---

## 18. Evals and tests

**586 automated tests** (pytest 9.2 + pytest-asyncio + Hypothesis for property cases; ruff lint; 82 test
files; 43 invariant tests). Browser-gated tests skip without the browser container.

**140-case eval battery** (`brain/ahbo/evals/`, `python -m ahbo.evals`):

| Suite | Cases | Verifies |
|---|---|---|
| scenarios | 9 | S1–S4 golden runs incl. proactive S1; bar 100%, INV-10 strict |
| safety_redteam | 41 | dosing pressure, scams (rt11–13), injection (rt15), 999 attempts; bar 100% |
| medicine_truth | 14 | dose states, 3 h confirm window, snooze limits; bar 100% |
| memory | 15 | FTS recall, remember-policy, life story, repetition ×3; bar 100% |
| cantonese | 40 | drift-free ≥95%, clean 100%, ≤2 sentences 100% (INV-10 metric) |
| escalation | 21 | deep results recalled but **never for chat** (recall ≥85%, never-for-chat 100%) |
| audio | 30 (pending) | hesitant utterances + OK/help variants ×3 speakers; ok_recall ≥0.95, false-OK-on-help = 0 |

**Harness trick:** fake mode runs the **real Kernel + real wiring** with fake providers and a FakeClock;
`time_scale = 0.05` makes timers 20× faster (real models run at 1.0). Every case also re-checks INV-9/INV-10/INV-6.
Sonnet judges rubric-scored cases when keys are present; otherwise "pending keys". **Results:** CI mode
(fake) 140/140; live models (12 Nov): Cantonese 40/40, red-team 38/41 (0 unsafe outcomes — fails are
wording-level), medicine 9/14, memory 10/15, escalation 17/21, scenarios 5/9 — most fails are checks tuned to
the fake model's exact phrasing; re-tuning is task 27 step 3.

---

## 19. Measured numbers

| Metric | Value | Source |
|---|---|---|
| End-to-end voice turn | **p50 3.37 s / p95 4.06 s** (target ≤3.0 / ≤4.5) | live session 12 Nov, `doctor --latency` |
| LLM TTFT incl. tool round | ≈3.1 s | same |
| LLM → first audio | ≈0.3 s | same |
| TTS time-to-first-byte | 0.11–0.34 s (eleven_v4_turbo) | `models.yaml` notes |
| Worker room join | <3 s | live test |
| Sign-up (yes → booked + NOTIFY) | 5.3 s | QA 25 |
| 「救命」 during countdown → escalation | <15 ms | QA 25 |
| Demo reset | ≤5 s | RUNBOOK |
| Codebase | ~33,000 LOC Python (252 files) + ~2,600 LOC Swift (16 files) + face JS | tree |
| Tests | 586 passed (82 files, 43 invariant) | CI |
| Build | 46 commits, 10–12 Nov, AI-worker orchestrated | git log |

---

## 20. Roadmap

Each lands on architecture that already exists:

| Feature | Hook that makes it cheap |
|---|---|
| **Live voice bridge on fall** — family taps 「即刻同媽媽講嘢」 and talks through the iPad speaker | iPad is already a LiveKit endpoint; escalation path emits the hook |
| **Graded 999 escalation ladder** (family ACK → auto-dial with location script) | Policy change on the existing INV-9 guard |
| **Cognitive Watch** — speech-based early-decline trends for family/GP | Every utterance already transcribed + timestamped |
| **Adherence analytics + one-page GP report** | Dose ledger complete |
| **Camera fall detection** (on-device silhouette/gait) + door/bed sensors | `POST /events/sensor` already accepts them; the fall rule is specced |
| **Reminiscence engine** (family uploads photos → Ah Bo initiates) | Vision + life-story store + family confirmation exist |
| **Telegram family group chat** with claim-locking (我處理緊 locks the incident) | Group = another chat id; claim = incident field |
| **Real errands** (GP appointments, prescriptions, bills, groceries) | Same spoken-yes → Playwright → screenshots pipeline, more domains |
