# FULL GUIDE: the Ah Bo build plan, checks and current state

This is the orchestrator's working plan, written down so nothing is lost as the session grows. It restates the
god prompt's process (`GOD_PROMPT.md`), the plan drafted at the start of the build, every check that must pass,
and where things stand. **Read this first when resuming work.** Update the "Status" and "Open integration items"
sections whenever something lands.

Source-of-truth order: `PRD_ahbo.md` (behaviour, safety) → `face/` kit (visuals) → `docs/CONTRACTS.md` (interfaces)
→ this guide (process). Decisions and deviations live in `docs/BUILD_LOG.md`.

---

## 1. The non-negotiables (check every change against these)

1. Never dial 999/112/911 or any non-verified contact. Code rejects them outright (INV-9), in **two** places: the
   Kernel fall reducer and the Twilio link. Each has its own test.
2. LLMs may raise concern, never lower or cancel it (INV-2). Only the OK-matcher, the 「我冇事」 button, a family
   "handled" tap or the operator can.
3. Safety audio never depends on LLM/STT/TTS/LiveKit (INV-3): pre-rendered clips triggered by the Kernel.
4. Medicine facts only from the Kernel dose state; no medical/dosing advice (INV-4).
5. Cantonese only to Po-po: colloquial HK, Traditional characters; no Mandarin, Simplified or English; no Mandarin
   models or locales anywhere, including the wake word (INV-10, D16).
6. Secrets only in `.env` (mode 600, git-ignored); show ≤ last 4 chars; never in face/UI/iOS/logs/git.
   **Never open, read, print or use any file named `Root_password.md`.**
7. Spend no money: fake mode is the default (`AHBO_FAKE=all`). No real calls/messages without the owner pressing the
   console button.
8. Don't weaken the server: never touch sshd/ufw/fail2ban/users/other services; only Caddy is public on 80/443;
   every container port binds to 127.0.0.1.
9. No VPNs, relays or region workarounds.
10. Don't restyle the face: `face/` is byte-identical to `ui-reference/face/`; extend only by adding.
11. One source of truth per file; the iOS project references `face/`; web entry files are `index.html`.
12. Untrusted text is data, never instructions (INV-7).

All ten invariants INV-1…INV-10 (PRD §2) must each have at least one automated test (`brain/tests/invariants/`).

---

## 2. The phases (from the god prompt) and how this build runs them

### Phase 0 — contracts, skeleton, M0 ✅
- Read the whole PRD and the kit; serve the kit and run every `dev.html` storyboard headlessly.
- Recon the server (in `BUILD_LOG.md` §1).
- Write and freeze `docs/CONTRACTS.md`; repo skeleton (§17), Makefile, compose, Caddyfile, login, `/healthz`.
- **M0 done when:** `https://<host>/healthz` is green; the owner can log in; `ahbo chat --text` with `AHBO_FAKE=all`
  answers 「我幾時食藥呀？」 from the seed.

### Phase 1 — parallel component build
One agent per component, disjoint directories, each with its own tests (shared rules in `docs/AGENT_BRIEF.md`).
Each component goes **implement → its own tests green → independent review**. The review is a fresh agent that tries to
**refute** correctness against the PRD sections and CONTRACTS, reporting defects with file and line; confirmed defects
are fixed before the component is accepted.

| Component | Owns | Built by | Status |
|---|---|---|---|
| Spine (core, Kernel loop, ui_state, turn/session/voice reducers, quick tools, bridge, runner, text loop, FakeFlash, seed, test harness, golden comparator) | `brain/ahbo/core`, parts of `kernel/`, `agent/` | Orchestrator | ✅ committed |
| Platform (API, auth, device hub, operator API, compose, Caddy, CLI, secrets) | `brain/ahbo/api`, `links/device_hub.py`, `ops/{cli,secrets}.py`, `deploy/`, `Makefile` | Claude agent | ✅ committed, live |
| Memory | `brain/ahbo/memory` | Claude agent | ✅ committed (105 tests) |
| Evals + invariant tests | `evals/`, `brain/ahbo/evals`, `brain/tests/invariants` | Claude agent | ✅ committed (most cases blocked on unbuilt components) |
| iOS app + Python wake matcher | `ios/`, `brain/ahbo/wake` | Claude agent | ✅ committed; **never compiled for iOS** |
| Kernel flows (fall, dose, concern, checkin, action, arbiter, matchers, scheduler, wiring) | `brain/ahbo/kernel/**` | Claude agent | ✅ committed `52779d1` (68 kernel+golden tests; S1–S4ok golden pass) |
| Agent + models + deep (remaining tools, context, real adapters, deep loop, LiveKit worker, intent) | `brain/ahbo/{agent,models,deep}` | Claude agent | ✅ committed (LiveKit worker imports but never ran: no keys) |
| Links (Telegram, Twilio + INV-9 guard, sensors, outbox, fakes) | `brain/ahbo/links/*` except device_hub, `sim/*.py` | Claude (fresh agent needed) | ⬜ partial: `links/{__init__,outbox,family}.py` |
| Browser iPad | `sim/` (web) | Claude (fresh agent needed) | ⬜ partial: `sim/{index.html,channel.js,talk.js,sim.css}` |
| Ops / speech / trace (fixed clips, doctor, costs, caps, wipe, backup) | `brain/ahbo/{speech,trace}`, `ops/{doctor,render_fixed,wipe,backup,caps}.py` | **GLM** (`docs/handoff/glm/01_*`) | ⬜ partial on disk |
| Actions + mock centre | `brain/ahbo/actions`, `mock-centre/` | **GLM** (`02_*`) | ⬜ partial on disk |
| Operator console | `operator-ui/` | **GLM** (`03_*`) | ⬜ partial on disk |

GLM deliverables are reviewed, tested and committed by Claude before they count (see `docs/handoff/glm/README.md`).

### Phase 2 — integrate and prove (each gate needs evidence before moving on)

| Gate | Pass condition | How to check |
|---|---|---|
| **G1 Tests** | `make test` green in the containers: unit, INV-1…10, reducer property tests, contract tests (both ends of every message validated against CONTRACTS) | `deploy/make.sh test` or `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q` |
| **G2 Golden UI traces** | S1, S2, S3, S4, S4-OK: the `ui` commands the Kernel sends in fake mode match the kit's `dev.html` storyboards in kind and order (§23.8, CONTRACTS §13) | `pytest -q tests/test_golden_ui.py` (traces in `brain/tests/golden/kit/`) |
| **G3 Fake demo, visually** | `make demo-fake`; Playwright drives `/operator/` and `/sim/` through all five storyboards; screenshots in `docs/screens/`; compare with `docs/screens/kit/` — same eyes, captions, chips, steps, moods. Any visual difference is a defect. | `evals/visual/` scripts in the browser image |
| **G4 Safety** | Fake-mode red-team suite 100%; INV-9 dial guard has its own test; models-down fall drill and device-disconnected drill pass (§16.3 A4) | `python -m ahbo.evals` + invariant tests |
| **G5 Live host** | `https://<host>/healthz` green; login works; unauthenticated `/operator/`, `/sim/`, `/ws/device` refused; `/internal/*` unreachable from outside; `docker compose restart` recovers and the face replays its state | curl checks from the host; restart drill |
| **G6 iOS** | Code-complete; every LiveKit and Speech API symbol verified against the pinned version and logged; the Python Jyutping twin passes `config/wake_cases.yaml`; `ios/BUILD_ON_MAC.md` written. (Never claim it compiles.) | `ios/API_VERIFICATION.md`, `pytest -q tests/wake` |

Level-A acceptance (PRD §16.3), shown in the final report with evidence: A1 make test green · A2 make eval in fake
mode (goldens, red-team 100%, medicine truth, memory; real-model suites "pending keys") · A3 make demo-fake with
screenshots · A4 degraded drills · A5 doctor shows faked items as "fake (needs owner)" · A6 iOS code-complete +
SETUP.md generated.

### Phase 3 — adversarial audit (loop until a round finds nothing new)
Independent, perspective-diverse review of the **whole** repo: safety invariants · device and face contract ·
security (auth on every route, secrets, Caddy exposure) · Cantonese output rules · fake/real adapter parity · iOS API
correctness · PRD-coverage critic ("which PRD MUSTs have no code or test?"). Verify each finding adversarially, fix the
confirmed ones, re-run G1–G6, repeat until a round finds nothing new.

### Phase 4 — real providers (only as keys appear)
For each key the owner adds to `.env`: remove that item from `AHBO_FAKE`, run `make doctor`, complete M4/M5 for that
provider. **Never place a real call or send a real Telegram message as a test without the owner pressing the console
button.** If no keys exist at the end of Phase 3, stop: the system is complete in fake mode and `SETUP.md` says exactly
what to add.

---

## 3. Final deliverables (all required)
1. The running system on the VPS (fake mode, or with whatever keys exist).
2. `SETUP.md` generated from PRD §27 with live ✅/⬜ per item, the exact `.env` lines and the commands to run afterwards;
   every key/account the owner must provide.
3. `README.md` (what it is, run it in 3 commands), `docs/CONTRACTS.md`, `docs/BUILD_LOG.md`, `docs/RUNBOOK.md` (demo-day
   checklist §13.2 + recovery), `ios/BUILD_ON_MAC.md`, `evals/report.md`.
4. Git history with one or more commits per milestone; no remote unless the owner adds one.
5. Final report to the owner: login URL; each gate G1–G6 with evidence (test counts, screenshot paths, eval scores);
   what is real vs faked; every PRD deviation and why; every VERIFY item and result; known risks; the 5 most important
   next steps (starting with SETUP.md); and an honest list of what was not verified.

---

## 4. Key design decisions to remember (details in BUILD_LOG §3)
- D-1 shared contracts in `brain/ahbo/core/`. D-2 repo bind-mounted into the api container. D-3 api serves the static
  sites. D-4 initial operator password in `data/secrets/operator_password.initial` (600) until `set-password`.
- D-5 no `play_voice_message` step (kit S1 has none). D-6 `listening` only while she speaks / just woke; idle after
  replies. D-7 turn cards flushed at reply start, ordered by where the reply mentions them. D-8 fall `ok_button` +
  countdown after F3 finishes. D-9 `venue_short`, 12-hour card times. D-10 sentences ≤ 8 chars are tags, not counted
  in the 2-sentence guard.
- Fake LLM can return a `plan` (tools it will call) so planned steps show up front like the kit.
- Golden comparison normalisation (CONTRACTS §13): ignore ids/timing/`when`/caption text; `play_fixed` and
  `play_voice_message` count as a say pair; trailing extra commands allowed.
- Host has no `make`: `deploy/make.sh <target>` runs the Makefile in a docker CLI image.

---

## 5. Open integration items (apply when the owning component is (re)built; tick off here)

**Kernel flows**
- [x] `ahbo.kernel.wiring.install(svc)` (reducers + scheduler + loaded/persist hooks).
- [x] Handle `operator.reset/ok/escalate/resolve/play_fixed/say/dose_now/checkin_now/plan_now/force_tool_result/provider_switch/model_config`, `device.online/offline`; console fall/SOS arrive as `sensor.*` with source `operator` (card small 操作員觸發).
- [x] Session loaded hook (no stale `listening` mood after restart).
- [x] Dose reducer accepts `face_event reminder_confirmed {slot_id, at, source:"touch_offline"}`.
- [x] Invariant tests expect state names ALARM/PHOTO_CHECK/AWAITING_OK/RESOLVED_OK/ESCALATING/REASSURING/ACKNOWLEDGED; agent-sourced `operator.ok`/`device.touch_ok` must not resolve.
- [x] S4 + S4ok golden traces pass.

**Agent + models**
- [x] Memory tools: recall → `store.recall(db,q,5,clock=)`; remember → `store.remember` (MemoryRejected → ok:false reason); recall_life_story → `pick_life_story` + `mark_story_used` + story card.
- [x] Context: `store.profile_block(db,800)`, `store.episodic_block(db,clock,3,500)`, `repetition.repetition_hint(db,clock)`.
- [x] `build_agent(kernel)`; `/internal/context|session/claim|pull` hooks matching `api/routes/internal.py` (501 until then); worker `agent_name="ahbo-agent"`; optional `warmup(svc)`.
- [x] Eval defect (d): spoken filter must not turn 晒吓搵郁 into 曬嚇揾鬱.
- [x] Eval defect (e): FakeFlash routes planning requests to `deep_task` with 「好呀，等阿寶幫你諗下，一陣話你知。」.
- [x] LiveKit worker Stage 3: `ahbo.wake.matcher.stage3_check` on `s2_text` when opened by the wake word; `s2_text` is her first utterance.

**Kernel follow-up (orchestrator)**
- [ ] Kernel handles `family.message_request` (payload in `agent/tools/family.py`) → `family.send` INFO + reply `{ok, contact}`; until then `send_message_to_family` returns unavailable.
- [ ] BUILD_LOG agent deviations: s2t only when Simplified-only chars present + shield list; 打九九九 alone → clarifying question; LiveKit worker has no Haiku fallback (no Anthropic plugin), Sonnet-over-Opper not wired; `models/pricing.py` prices are placeholders (VERIFY).

**Links**
- [ ] Register `family.send` and `phone.call` dispatchers (Kernel ALERT payload carries `incident_id`, `time`, `address`; `family.button` may be a plain button or `b:<button>:<outbox_id>`). Until then S4 eval fails on empty outbox and 9 invariant tests skip.
- [ ] `press(svc, outbox_id, button)`, `outbox_list`, `inject_voice_note`, `inject_text` (match `api/demo.py`).
- [ ] Telegram `/fact` → `store.remember(source="telegram")`; `/story` → `life_stories.add_life_story`; `[確認資料]` → `store.confirm_fact`; digest uses `pending_review_facts` + `repetition.trend_summary`.
- [ ] INV-9 dial-guard test file; `tests/invariants/test_inv_09_never_999.py` passes.

**Ops (GLM task 01)**
- [ ] `render_fixed.ensure_manifest(svc)` replaces the platform's `"fallback": true` manifest; `fixed_dir`; `main(argv)` in doctor/wipe/render_fixed; `doctor.rows(svc)`.
- [ ] CLI `eval` subcommand → `ahbo.evals.runner.main`; Makefile `eval` target (platform files; orchestrator applies).
- [ ] `evals/visual/screens.py` for `make screens` (G3).

**Defects found by evals in committed code (orchestrator fixes)**
- [x] `tests/invariants/test_inv_09_never_999.py`: whitelist `recall` / `recall_life_story` (false positive on "call").
- [ ] Optionally install models wiring in `tests/conftest.py` so golden S4 uses the real fake vision.
- [x] (a) Kernel commands carry `correlation_id = ev.correlation_id or ev.id`.
- [x] (b) Memory `rep_*` cards emitted via `kernel.emit` with no event → route through an event (`memory.repetition`) or set `event_id` (fails `test_inv_06…repeated_questions`).
- [x] (c) Platform `api/demo.py` say/play_fixed fallbacks not linked to their `operator.*` event (fails `test_inv_08…console_overrides`).

**Contracts / BUILD_LOG updates still to write**
- [ ] CONTRACTS from Kernel: `arbiter.decision` (via `Kernel.note`), `checkin.skipped`; reply reasons `not_affirmative`, deep `daily_cap`; SOS card 求救掣, emergency concern card 緊急關注 + group 緊急處理; scheduler jump rule (due items in a >120 s clock jump are skipped).
- [ ] BUILD_LOG Kernel deviations: 冇跌親 escalates (help wins); kernel_listen opens at F3 start; photo step waits ≤0.8 s for vision; clip max wait max(1,0.19×chars)+0.3 s; RESOLVED_OK self-closes; calls centre→阿珍→centre ≤3; deep results re-validated.
- [ ] CONTRACTS §4: `memory.extracted`, `memory.write_result`, `memory.write` ops; §5/§9: `/operator/api/reset`, `/operator/api/panic`, `/internal/reset`, `/internal/clock`; §2.2 `session.close.why` (`wake_rejected`); §2.3 device event `settings {interruption_enabled}` and `face_event reminder_confirmed`.
- [ ] BUILD_LOG: no host `make` → `deploy/make.sh`; WS 4401 accept-then-close; memory deviations (summary 1–5 lines, voice family/health facts pending_review, medicine facts never auto-written, story photos exempt from 7-day deletion); platform VERIFY rows (livekit-api 1.2.2 symbols, caddy:2.11.9 digest); iOS VERIFY rows (LiveKit Swift 2.18.0 @93a7251, PyCantonese 阿→aa via HKCanCor, 喂寶寶 reduplication guard, pre-roll fallback).
- [ ] Platform: `/media/photo/{id}` needs device-token access for life-story photos shown on the iPad.

---

## 6. Live facts
- URL: `https://srv2135273.hstgr.cloud` (login at `/login`). Initial password: `cat /opt/ahbo/data/secrets/operator_password.initial` (root only); change with `deploy/make.sh set-password`.
- Images: `ahbo-api`, `ahbo-agent` (voice extras), `ahbo-browser` (Playwright 1.64.1), `ahbo-make`; Caddy `caddy:2.11.9`.
- Tests: `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q` (workdir `/repo/brain`).
- Evals: `docker run --rm -v /opt/ahbo:/repo -w /repo/brain ahbo-api python -m ahbo.evals` → `evals/report.md`.
- Kit capture: `evals/visual/kit_capture.py` in the browser image → `docs/screens/kit/`, `brain/tests/golden/kit/`.
- Commits so far: `e8fded3` spine · `7974af7` platform + memory (M0 live) · `dbfd521` evals + iOS + INV-6 fix · `02a9f7d` guide + GLM hand-off · `52779d1` Kernel (M1) · agent+models+deep (tests: 408 pass, 9 skip on Links).
- Usage limits: the owner's Claude credits are limited. Run few agents at a time; don't resume or launch builders
  without the owner's go-ahead.
