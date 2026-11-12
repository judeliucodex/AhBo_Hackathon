# QA 26 report — Operations: doctor, clips, caps, wipe/export, backups, traces

## Summary

- **6 defects: 3 × S2, 3 × S3.** No S1 found.
- **Overall verdict: usable with issues.** Doctor, fixed clips, backups/restore and retention are solid; the
  cap system has two caps that don't actually guard anything (`sessions_per_day`, `llm_usd` on the chat path),
  and `ahbo wipe --yes` leaves the public phone clip (her address + fall details) on disk, still served
  unauthenticated.
- All 7 checks from `26_qa_ops.md` were executed against a throwaway fake-mode server
  (`ahbo-qa26-api` + `ahbo-qa26-browser` on network `ahbo-qa26`, `AHBO_FAKE=all`, data dir `/tmp/qa` in the
  container) and throwaway probe containers. Everything cleaned up afterwards. The live stack, host ports
  80/443/8000, `.env` and every real provider were never touched.

## Defects

### D1 [S2] `ahbo wipe --yes` leaves the public phone clip on disk — still served, unauthenticated
- Where: `brain/ahbo/memory/retention.py:116-124` (`wipe` unlinks only `media.path`,
  `voice_messages.file_path`, `sessions.transcript_ref` — never `data/media/public/`);
  `brain/ahbo/api/routes/media.py:120-124` (`/media/public/{rand}/{name}` serves any file under
  `data/media/public/<rand>/` with no auth and no check against the current `public_clip_rand` kv).
- Spec: PRD §11 (export-and-wipe is the PDPO erasure path); `ops/wipe.py` docstring "delete all personal
  data + media files"; the P1 clip contains her name, address and the fall notice
  (`config/fixed_lines.yaml` P1_TEMPLATE); CONTRACTS §14.3 puts it under `data/media/public/<random>/`, and
  its URL is necessarily disclosed to Twilio when a call is placed.
- Repro:
  ```bash
  # on a throwaway fake server (AHBO_DATA_DIR=/tmp/qa):
  # 1. trigger a fall (renders + copies P1 into data/media/public/<rand>/, sets kv public_clip_rand)
  # 2. python -m ahbo.ops.wipe --yes          → "wiped: 250 rows across 28 tables, 0 media files"
  # 3. curl http://127.0.0.1:8000/media/public/<old-rand>/P1.m4a    (no cookie, no token)
  ```
- Actual: **HTTP 200, 3065 bytes** — the clip survives the wipe, an api restart *and* a Reset demo
  (evidence `docs/qa/26/04c_public_clip_survives.txt`; the full wipe log is `docs/qa/26/04_wipe.txt`).
  The kv rows pointing at it are gone, so nothing re-mints or overwrites the old dir either.
- Notes for the fix: delete `data/media/public/` in `wipe` (or at least the dir named by `public_clip_rand`
  before the tables are cleared), and/or have the route verify `rand` equals the current `public_clip_rand`.
  Borderline S1: "stranger gets access" needs the 26-char dir name, but that URL was already shared with a
  third party (Twilio) at call time, and the owner believes the data destroyed after a wipe.

### D2 [S2] `sessions_per_day` cap (120) is configured and displayed but never enforced anywhere
- Where: `config/caps.yaml` (`sessions_per_day: 120`); `brain/ahbo/ops/caps.py:29-31` lists it;
  `brain/ahbo/api/snapshot.py` / `ops.caps.all_caps` show it on the console — but there is **no**
  `check_cap(db, clock, config, "sessions_per_day")` call site in `brain/ahbo/` (grep in evidence).
- Spec: PRD §11: "exceeding a cap disables that feature with a console warning" (sessions_per_day is a repo
  addition beyond the PRD's four caps — `ops/caps.py` calls it "a runaway guard, not money" — but it is
  configured, displayed, and dead).
- Repro: `docs/qa/26/probes/test_check3_caps.py::test_sessions_per_day_cap_never_enforced` — insert 120
  session rows opened today, then `harness.say("阿寶，今日幾號呀？")`.
- Actual: the 121st session opens normally; no refusal, no `system.cap_exceeded`, sessions count 121.
  (`check_cap` itself correctly refuses when called — the mechanics work; no feature calls it.)
- Evidence: `docs/qa/26/03_caps.txt`.

### D3 [S2] `llm_usd` spend cap ($3/day) never disables the conversation feature
- Where: `brain/ahbo/agent/wiring.py:266-273` — `_cap_ok` gates only the deep path, and returns `True`
  immediately when `settings.is_fake("models")`; no `check_cap("llm_usd")` anywhere on the Flash/chat path
  (`models/flash.py`, `agent/runner.py`, `agent/text_loop.py` have none).
- Spec: PRD §11: "exceeding a cap disables that feature with a console warning" — the LLM spend cap is
  exactly the owner's money guard.
- Repro: `docs/qa/26/probes/test_check3_caps.py::test_llm_usd_cap_does_not_stop_conversation` — record
  4 × $1.00 `deepseek` costs today (cap 3.0) → `check_cap` refuses and warns (mechanics fine) →
  `harness.say("阿寶，我幾時食藥呀？")` still gets the full medicine answer.
- Actual: the conversation keeps running over the cap. In real mode the same code path means the chat keeps
  billing past $3; the only gated feature is deep tasks, and even that gate is skipped whenever models are
  faked (`_cap_ok` line 268).
- Evidence: `docs/qa/26/03_caps.txt`.

### D4 [S3] `deep_tasks` cap is counted from two different sources; the kernel path never warns
- Where: `brain/ahbo/kernel/reducers/action.py:244-252` counts `deep_tasks` **table** rows started today;
  `brain/ahbo/ops/caps.py:87` + `brain/ahbo/trace/costs.py:47-48` count `costs` rows with
  `purpose='deep_task'`. Only the ops path emits `system.cap_exceeded`; the kernel path returns
  `{ok:false, status:"busy", reason:"daily_cap"}` silently.
- Repro: 15 fake `deep_tasks` rows → kernel refuses with no warning while `ops.caps` sees used=0; 15 fake
  `costs` rows → the reverse. The two gates can disagree about the same day.
- Actual: verified both directions in `docs/qa/26/03_caps.txt`. Behaviour-visible effect is minor (the tool
  reply is the CONTRACTS-specified `daily_cap`), but the console warning and the gate can disagree, and the
  kernel's refusal is never surfaced as a cap warning.

### D5 [S3] The origin events of a fall and a session carry no `correlation_id` — `/operator/api/trace` starts its story one event late
- Where: `brain/ahbo/kernel/core.py:137-157` — `_process_locked` records the event **before** any
  correlation default; only emitted *commands* inherit `ev.correlation_id or ev.id` (INV-6 for commands,
  line 156-157). `Kernel.note()` defaults the id (line 166-167) but `_process_locked` does not. The incident
  id is created inside the fall reducer (`kernel/reducers/fall.py:299-300` back-fills commands only), and
  `links/sensors.py` maps the beacon payload without a correlation id.
- Spec: INV-6 "All inputs … are appended to an event log with a `correlation_id`. Any demo moment can be
  replayed and explained"; CONTRACTS §4.1's `sensor.fall_suspected` example carries
  `"correlation_id":"inc_01J…"`; task 26: "`/operator/api/trace?correlation_id=…` returns the whole story".
- Repro: trigger a fall (operator 模擬跌倒 or beacon), then
  `GET /operator/api/trace?correlation_id=<incident id>` → the returned events start at `timer.fired`; the
  `sensor.fall_suspected` row has `correlation_id IS NULL` (same for `device.wake` vs its session).
  Reproduced twice, including on a fresh incident after Reset demo.
- Actual: every **command** has `event_id` + `correlation_id` (73/73 — the task's INV-6 check passes), and
  the console's incident timeline still tells the whole story from `sensor.fall_suspected` (mitigation), but
  the trace endpoint's story is missing its first input event. 53 `schedule.tick` events/day are also
  uncorrelated (noise, arguably fine).
- Evidence: `docs/qa/26/07_traces.txt`.

### D6 [S3] §15 observability is empty on a fake-mode server: 0 spans, `doctor --latency` can never have data
- Where: the only span writers are `agent/worker.py:415-417` (TurnTimer — LiveKit voice worker),
  `api/routes/internal.py:69-71` (turn.latency reported by that worker) and `models/runtime.py:52-53`
  (real-LLM calls). No spans are written for tools, device acks, Telegram or Twilio (§15 lists them), and
  the fake text loop writes none.
- Spec: PRD §15 — spans for STT final / LLM / each tool / TTS / device ack / Telegram / Twilio, shown in
  the console waterfall; p50/p95 from `ahbo doctor --latency`.
- Repro: on the fake server, run a conversation, a full fall (with fake Telegram alert + fake Twilio call),
  then `SELECT COUNT(*) FROM spans` → 0, and `python -m ahbo.ops.doctor --latency` → "no turns recorded
  yet" even though turns exist in `turns`.
- Actual: the required check ("`--latency` works with no data") passes; the deeper issue is that on a
  fake-mode (i.e. demo-day) server the waterfall and latency stats have **no data source at all** — only the
  real voice worker would ever populate them.
- Evidence: `docs/qa/26/07_traces.txt`, `docs/qa/26/01b_doctor_latency_empty.txt`.

## Passed checks

- **Doctor (check 1)**: 41 checks; all 10 faked providers show `fake (needs owner)` (4 model routes,
  3 ElevenLabs, LiveKit, Telegram webhook, Twilio) — PRD §16.3 A5 satisfied in fake mode; the only red rows
  are the honest ones (no iPad online, no beacon heartbeat) and each says what to do with a SETUP.md
  pointer; every secret shows at most its last 4 chars (`mask()` verified: `…wxyz`); exit code 1 when red
  (BUILD_LOG D-37); `--latency` works with an empty spans table ("no turns recorded yet"); Playwright green
  against the throwaway browser container; Fixed cache / Clock / Disk / Database green.
- **Fixed clips (check 2)**: 76/76 assertions — manifest has every `fixed_lines.yaml` clip (F1 F2 F3 F4 F4p
  F5 A1 G1 G2) with exact texts, one `R_<slot>` per active dose slot with med names joined exactly like the
  agent says them (「…血壓藥，同埋糖尿藥。」), `P1` + `public` section under a random 26-char dir with the kv
  keys in the links format; every clip's `duration` matches `ffprobe` within 0.1 s; segments are monotonic,
  non-overlapping, cover every UTF-16 offset, start at 0 and end at the text's UTF-16 length; F4/F4p are the
  owner's 「我已經打電話俾阿珍…」 wording with no ambulance claim; `version` = `f-<sha1(texts+voice+mode)>`;
  no `fallback` flag. Changing slot s_2000's meds and re-rendering changed the version
  (f-dcfc2e6081a0 → f-8b039266378c), rewrote `R_s_2000.m4a` and updated its text.
- **Caps (check 3)**: `twilio_calls` (5/day) — a regular `phone.call` through the real dispatcher is refused
  (`phone.call_refused {reason:"cap_reached"}`), writes **no** outbox row, records the `system.cap_exceeded`
  note event (console-visible via the kernel_event stream) and a `quality` row `cap_exceeded`; the refused
  attempt is not double-counted. **Safety exemption**: with the cap already exceeded, 3 incident calls to
  阿珍 are allowed in sequence (3 outbox rows), the 4th for the same incident is refused `cap_reached`.
  `playwright_actions` (10/day) — a confirmed signup action fails with `action.status failed {reason:
  "daily_cap", used:10, cap:10}` plus the warning event + quality row. `deep_tasks` (15/day) — the kernel
  answers `deep.start_request` with `{ok:false, status:"busy", reason:"daily_cap"}` and adds no row
  (CONTRACTS §6). `llm_usd` / `sessions_per_day` — `check_cap` mechanics refuse and warn correctly; the
  feature-level gaps are D2/D3.
- **Wipe/export (check 4)**: `ahbo wipe` without `--yes` exits 2 with a clear refusal and deletes nothing,
  writes no export; with `--yes` the export is written first (mode 600, 29 tables, contains the pre-wipe
  fall event), then every personal table is emptied (250 rows across 28 tables) with only `meta` kept; FTS
  indexes are rebuilt and return **no** matches for wiped content (`MATCH '血壓'` → 0 rows); the fixed-clip
  cache is kept by design; the api restarts cleanly on the wiped data dir; Reset demo re-seeds (3 dose
  slots) and a conversation + a fall both work afterwards. (The public-clip survivor is D1.)
- **Backup/restore (check 5)**: `ops.backup.backup()` taken while the server runs; `PRAGMA
  integrity_check` = ok on the copy; row counts match the live DB (equal while idle); mode 600; 8 backups
  → exactly the newest 7 kept, oldest pruned; a backup taken while another connection commits contains the
  committed row. Restoring a backup into a fresh data dir and starting a new server on it: healthz ok,
  login ok, `/operator/api/state` ok (3 doses), the pre-backup marker row present, and conversation works
  (after resolving the incident that legitimately travelled inside the backup).
- **Retention (check 6)**: 14/14 — photo >7 days deleted (row + file); photo at exactly 7 days kept;
  photo with past `expires_at` deleted; life-story photo exempt (BUILD_LOG D-15); voice note >30 days
  deleted, ≤30 kept; turn row >30 days deleted, newer kept; old session's transcript file deleted and
  `transcript_ref` nulled; a row pointing at a real repo seed file (`/repo/brain/seed/fall_placeholder.jpg`)
  removes the row but **never the file**; `memory/wiring.py` runs this once per HKT day.
- **Traces (check 7)**: 73/73 commands have non-null `event_id` and `correlation_id` (INV-6 for commands);
  `/operator/api/trace?correlation_id=…` (cookie auth; 401 without) returns the incident's F1–F4 clips,
  `phone.call`, `family.send`, `family.send_result`, `phone.call_status` and the session's
  `user.utterance → agent.steps_plan → agent.tool_call/tool_result → agent.reply_start/reply_end`; an
  unknown correlation id returns an empty story with 200. (Origin-event gap = D5; empty spans = D6.)

## Not tested / blocked

- **Real-mode doctor checks** (tiny live model/TTS/Telegram/Twilio/LiveKit probes): forbidden by the QA
  rules — nothing real was ever called. Fake-mode rows only.
- **`session_minutes` cap**: not in the task's list; mechanics covered by repo unit tests, not feature-verified.
- **Beacon-path sensor ingest** (`POST /events/sensor`): `BEACON_TOKENS` unset on my server rejects every
  request by design; the correlation finding (D5) was verified via the operator fall trigger (same reducer)
  plus code reading of `links/sensors.py`.
- **Telegram-visible cap warnings**: Telegram is faked; warnings verified as console-visible
  (`system.cap_exceeded` kernel event + quality row). Whether 阿珍 should also see them is the documented
  open item in FULL_GUIDE §5d ("silent failures"), not re-litigated here.
- **iOS bundled-clip copy** (`ios/AhBo/Resources/fixed/`): out of scope; the copy step fails on the
  read-only repo mount with the documented expected noise line.
- **Wipe on the live stack**: forbidden; all wipe/backup/retention work ran in the throwaway container.
- Minor note, not a defect: the doctor's red-row hints point at "SETUP.md §27.x" sections — SETUP.md does
  not exist yet (BUILD_LOG §5d), so the pointer dangles until that file is generated; the actionable part
  of each hint (e.g. "run `ahbo render-fixed`") is present.

## Evidence index (`docs/qa/26/`)

| File | Content |
|---|---|
| `01_doctor.txt`, `01b_doctor_latency_empty.txt` | full doctor table (41 checks) + `--latency` on empty data |
| `02_clips_manifest.txt`, `02b_clips_rerender.txt` | 76 manifest assertions; dose-slot change → re-render |
| `03_caps.txt` | all cap probes (29 PASS lines incl. the D2/D3/D4 findings) |
| `04_wipe.txt`, `04b_wipe_restart_reset.txt`, `04c_public_clip_survives.txt` | wipe refusal/export/erasure; restart + Reset demo; the D1 proof |
| `05_backup.txt`, `05b_restore_server.txt` | backup checks; conversation on the restored server |
| `06_retention.txt` | 14 retention assertions |
| `07_traces.txt` | INV-6 + trace-endpoint output with the D5/D6 findings |
| `probes/` | the probe scripts (run recipes inside each file; pytest harness recipe per `20_QA_COMMON.md` §3) |
