# Task 04 (Links — family Telegram, phone Twilio, beacon sensors + fakes and outbox) report

## Post-handoff in-depth check (re-audit)

Re-verified everything after handoff, against the repo as it stands:
- **End-to-end through the real app (44/44 checks, fresh container):** links wiring installs with no errors;
  operator login → `/operator/api/outbox`; beacon `POST /events/sensor` → real fall incident → ESCALATING →
  ALERT row (c_chun) + Twilio row (c_ecs) with TwiML; the platform's own
  `POST /operator/api/outbox/{id}/press` route → `press1` → incident ACKNOWLEDGED → `handled` → NOTIFY →
  `resolved` → CLOSED; live INV-9 probes (raw numbers, unknown contact, full-width 「＋９９９」) refused with
  **no outbox rows** and `phone.call_refused` traces; Telegram webhook (missing secret → 401, wrong → 403,
  stranger dropped incl. `text: null`; `/fact` stores a memory); Twilio signed callback 200 / tampered → 403;
  inject helpers (`inject_text`, `inject_voice_note`, `press`) callable exactly as `demo.py` will call them,
  including a playable voice file on disk.
- **Gates re-run after one robustness fix** (below): `tests/links tests/invariants` → **145 passed**;
  full suite → **583 passed, 33 skipped**; `ahbo.evals` unchanged (scenarios 8/9 — the actions-runner
  environment issue, not links; safety_redteam 41/41).
- **Sim CLIs re-run clean** (`sim/telegram_fake.py`, `sim/twilio_fake.py`, `sim/beacon.py detect`).
- One robustness fix applied during the audit: a Telegram message with explicit `"text": null` crashed the
  reply branch (`msg.get("text", "").strip()` on None) — it was still dropped safely, but now the stranger
  properly gets the fixed reply (one-line change in `links/telegram.py`).

## Files created/changed

**Kept unchanged (from the stopped agent — reviewed, they were correct):** `brain/ahbo/links/__init__.py`,
`brain/ahbo/links/outbox.py`, `brain/ahbo/links/family.py` (dispatcher, INFO 10-min merge, 10-min retry
backoff). No edits were needed.

**Created:**
- `brain/ahbo/links/telegram.py` — real + fake Telegram channels, webhook handling (secret check, chat-id
  allowlist, commands, voice notes, callbacks), `set_webhook()` helper (builds the request, never runs it).
- `brain/ahbo/links/twilio.py` — the **INV-9 dial guard**, phone dispatcher (guard → cap → outbox row →
  background call), real + fake channels, TwiML, `/twilio/twiml|status|gather` routes with RequestValidator,
  `GET /twilio/twiml/test` (operator cookie), public P1 clip publishing (kv `public_clip_path`).
- `brain/ahbo/links/sensors.py` — `detect_fall(samples)` (§9.1 reference rule, pure), `POST /events/sensor`
  (sha256 token check, idempotent on `(beacon_id, seq)`, 202 + queued), beacon seeding from `BEACON_TOKENS`.
- `brain/ahbo/links/wiring.py` — `install(svc)` (registers `family.send` + `phone.call` dispatchers, real/fake
  by `settings.is_fake(...)`), combined `router`, and the console helpers `press`, `inject_voice_note`,
  `inject_text`, `outbox_list` (re-export).
- `sim/beacon.py` — CLI: `fall|sos|heartbeat|detect` (stdlib-only `urllib` POST; `detect` runs the reference
  rule, preferring `ahbo.links.sensors.detect_fall`).
- `sim/telegram_fake.py`, `sim/twilio_fake.py` — thin CLI wrappers around the fake classes (throwaway
  in-memory DB; never touch the live stack).
- `brain/seed/voice/demo_daughter.m4a` — silent AAC placeholder (3.65 s, from the seed transcript's length),
  generated with ffmpeg per the task; replaced whenever the owner provides a real recording (§27.2 #6).
- Tests: `brain/tests/links/{conftest.py, link_helpers.py, test_inv9_dial_guard.py, test_telegram.py,
  test_twilio.py, test_fakes.py, test_sensors.py}`.

## Tests (exact command + the real last line of pytest output)

- `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/links tests/invariants`
  → **`145 passed, 1 warning in 75.00s`** (0 skipped, 0 failed)
  - **Before:** `63 passed, 9 skipped in 21.03s`, with `SKIPPED [9] tests/invariants/inv_helpers.py:18:
    component not built yet: dispatch:phone.call`. **After: the same 9 invariant cases run and pass**
    (7 parametrised `test_phone_link_refuses` cases + `test_phone_link_refuses_raw_numbers` +
    `test_fall_calls_only_verified_contacts`); `tests/invariants/test_inv_09_never_999.py` alone:
    `11 passed in 5.43s`.
- Full suite: `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q`
  → **`583 passed, 33 skipped, 1 warning in 205.57s (0:03:25)`** — nothing new fails. (The three failures
  00_COMMON listed as known/pre-existing did not appear in this run at all.)
- `docker run --rm -v /opt/ahbo:/repo -w /repo/brain ahbo-api python -m ahbo.evals`:
  - `scenarios  FAIL  pass 8/9` (see Deviations #10 — the failing case is the actions runner, not links)
  - **`safety_redteam  PASS  pass 41/41`** — rt25–rt28 (the `phone.call` INV-9 dispatcher cases) now run and
    pass; rt24 (no such tool) still passes.
  - `medicine_truth 14/14`, `memory 15/15`, `cantonese 40/40`, `escalation 21/21` all PASS; `audio` pending
    keys (pre-existing). Report written to `evals/report.md` by the tool. S4 (which needs my outbox: ALERT row
    to `c_chun` + Twilio call row to `c_ecs`) passes.

## What is real vs faked

**Real code (runs whenever the corresponding provider is removed from `AHBO_FAKE`):**
- Telegram: raw Bot API over `httpx` (sendMessage/sendPhoto/getFile/file download/answerCallbackQuery); secret
  header compared with `hmac.compare_digest`; allowlist by `contacts.telegram_chat_id`; `/start /whoami /status
  /meds /fact /story`; voice notes: getFile → OGG → ffmpeg → AAC `.m4a` → ffprobe duration → `voice_messages`
  row (transcript: none) → `family.voice_note`; text → `family.text`; callbacks → `family.button` +
  answerCallbackQuery, `confirm` → `store.confirm_fact`.
- Twilio: `twilio` 9.13.0 SDK; `calls.create(to, from_, url, status_callback, status_callback_event, timeout)`
  in a worker thread; TwiML `<Play loop="2">` + `<Gather numDigits="1">`; `/twilio/*` validated with the SDK's
  `RequestValidator` (URL = `PUBLIC_BASE_URL` + path + query); gather digit 1 → `phone.call_status
  acknowledged`; status callbacks mapped (incl. `canceled → failed`); daily cap 5 with safety-call exemption ≤ 3
  per incident via `ahbo.ops.caps.check_cap` (lazy import), usage recorded with `trace.costs.record_cost`.
- INV-9 guard: independent second guard (the Kernel has the first). Refuses raw numbers in the payload,
  unknown contact, role not in {emergency_centre_sim, family}, `verified≠1`, 999/112/911 in any formatting
  (full-width digits included, exact or short-code suffix ≤ 5 digits before), and numbers with < 8 diallable
  digits (partials/short codes). **A refused call writes no outbox row** (it writes a `phone.call_refused`
  trace event instead). Only a verified contact with a valid number (real mode), or no number at all (fake
  mode — nothing can be dialled) passes.
- Sensors: sha256-hashed `BEACON_TOKENS`, constant-time compare, idempotency via `sensor_seen`, 202 + queued
  (kernel.submit), `beacons.last_seen/battery/rssi/last_seq` updated, `detect_fall` reference rule.
- Outbox: every outbound message/call is a row (fake=1 in fake mode, provider message id / call SID in real
  mode); INFO rate limit merge; per-contact retry with backoff up to 10 min (family.py).

**Faked (fake mode, the default `AHBO_FAKE=all`, zero network):**
- `FakeTelegram`: delivery is the outbox row itself; `send` returns a deterministic `tgfakeNNNN` id.
- `FakeTwilio`: writes the TwiML into the outbox row and emits `phone.call_status` ringing → in-progress →
  completed over 6 s (× `time_scale`); the console's press-1 makes it acknowledged.
- Demo voice note is a silent AAC placeholder; webhook replies (stranger line, /whoami, command answers) are
  logged in fake mode (no bot token exists) while state changes (/fact, /story, family.text/voice_note/button)
  work for real through the Kernel.
- Real adapters were tested **only with mocks** (patched `twilio.rest.Client`; Telegram fake channel /
  patched `download`); no real external API was called.

## Deviations from PRD/CONTRACTS (and why)

1. **`打俾媽媽` (call_mum) is not rendered in v1** (kept from the stopped agent's `family.py`): the PRD's
   button bridges Ah Chun to a call "the iPad answers on speaker" (§9.2, VERIFY feasibility "otherwise drop
   the button"); the iPad has no telephony endpoint. A stale `press(call_mum)` is accepted, submitted as
   `family.button` (which the Kernel ignores) and answered honestly with a note; nothing dials.
2. **Fake mode allows a phone.call to a verified contact with no number at all** (`to_e164 == ""`): the seeded
   demo contacts carry no numbers, and invariant case `test_fall_calls_only_verified_contacts` requires the
   escalation call row. Nothing can be dialled (fake=1, no SDK call). Real mode refuses it
   (`short_or_missing_number`). This is stricter than the task's "≤ 4 digits": partial 5–7-digit numbers are
   also refused — the same refusal set as `kernel/common.is_emergency_number` (len < 8 never dials).
3. Two extra informational event types for the trace (INV-6): `family.send_result` (source `telegram`) and
   `phone.call_refused` (source `twilio`). Neither is in the CONTRACTS §4.1 catalogue; no reducer consumes
   them; the Kernel just records them.
4. Voice-note transcripts are always None from Telegram (speech/STT is faked; writing an unverified
   transcript would break INV-4-adjacent honesty). The seeded `vm_demo` keeps its seed transcript.
5. `GET /twilio/twiml/test` calls `auth.require_operator(request)` inside the handler (same 401 behaviour as a
   dependency) instead of importing it at decoration time.
6. Screenshots are sent as separate `sendPhoto` calls (Telegram has no multi-image + caption + one call
   without sendMediaGroup; kept simple and honest).
7. The public P1 clip (`kv public_clip_path`, `/media/public/<rand>/P1.m4a`): if the speech component hasn't
   rendered P1 yet, a silent AAC of the template's duration is generated so a real Twilio call still plays
   something; the rendered clip replaces it once it exists.
8. Each intruding stranger message gets the one fixed reply (「呢個係私人家庭機械人。」) and is dropped —
   read as "one fixed reply (content)" per message, per §9.2.
9. `set_webhook()` builds and returns the setWebhook request dict for the owner/`make doctor`; nothing in the
   system ever executes it (hard rule).
10. **Eval scenario S3-turn3-signup fails, and it is not links:** the actions component's Playwright runner
    cannot reach `ws://browser:3000/` inside the bare eval container (`ENOTFOUND browser`) → the action ends
    `failed` → the Kernel correctly sends INFO (「報唔到名…」), so the expected NOTIFY/HANDOFF outbox row is
    absent. My dispatcher did deliver that INFO row (`('telegram','INFO','c_chun')` present). Needs the
    actions owner (see "Changes needed").

## VERIFY results (library + version + exact function names checked)

- `twilio` **9.13.0** (in `ahbo-api`): `twilio.request_validator.RequestValidator.validate(self, uri, params,
  signature, algorithm=None)` and `.compute_signature(self, uri, params, algorithm=None)` — executed in the
  image, round-trip `validate(compute_signature(...)) is True`; `twilio.rest.Client(username=None, password=
  None, account_sid=None, region=None, http_client=None, …)` and `client.calls.create(to, from_, method,
  status_callback, status_callback_event, status_callback_method, timeout, url, twiml, …)` — verified with
  `inspect.signature` in the image.
- `httpx` **0.28.1** (in image): `AsyncClient.post(json=…/data=…/files=…)` used for the raw Bot API.
- `ffmpeg`/`ffprobe` **5.1.9** at `/usr/bin/` in the image — voice conversion (OGG→AAC), duration probing,
  silent AAC generation.
- `ahbo.ops.caps.check_cap(db, clock, config, name, *, session_id=None, safety=False, incident_id=None,
  adding=1)` — read from source (`ops/caps.py:76`); safety exemption reads `trace.costs.calls_for_incident`
  (rows `provider='twilio' AND purpose='safety_call'`), which is why the dispatcher records usage with
  `record_cost(db, "twilio", None, {"calls": 1}, 0.0, purpose="safety_call"|"phone_call")` after dispatching.
- `ahbo.memory.store`: `remember(db, clock, fact, category, source)` (raises `MemoryRejected`; `"telegram"` ∈
  `TRUSTED_SOURCES`, so `/fact` facts are active immediately), `confirm_fact(db, fact_id, accept=True)`;
  `ahbo.memory.life_stories.add_life_story(db, clock, text, *, title=None, photo_id=None, …)` — verified from
  source.
- `core/adapters.py` `FamilyChannel.send(*, kind, contact_id, chat_id, text, photo_path=None, buttons=None,
  screenshots=None, correlation_id=None)` and `PhoneChannel.call(*, to_e164, contact_id, call_id, clip_url,
  correlation_id=None)` — my channels match; each takes one extra keyword-only `outbox_id=None` (the frozen
  protocol file was **not** edited; the Kernel never calls channels directly).
- `fastapi.testclient.TestClient`, `starlette` request.form() for the urlencoded Twilio posts — exercised in
  the webhook tests.

## Changes needed in files I don't own

1. `brain/ahbo/api/demo.py` (platform console): call the fake controls —
   `from ahbo.links.wiring import press, inject_voice_note, inject_text` and
   `from ahbo.links.outbox import outbox_list`. Exact signatures (already matched to the task):
   `outbox_list(db)` · `await press(svc, outbox_id, button)` · `await inject_voice_note(svc, file=None)` ·
   `await inject_text(svc, text)`; all return `{"ok": bool, …}` dicts. (`routes/operator.py` already finds
   `press` in `ahbo.links.wiring` and `outbox_list` in `ahbo.links.outbox`.)
2. `brain/ahbo/evals/harness.py` — `EvalSystem.outbox()` parses only `payload`, leaving `buttons` as JSON
   text. One-line improvement: also `r["buttons"] = loads(r.get("buttons"), [])`. (My tests parse it locally
   meanwhile.)
3. `brain/ahbo/actions/runner.py` (actions owner): the Playwright runner hard-fails when the `browser`
   service is absent (`ws://browser:3000/` ENOTFOUND) → eval case S3-turn3-signup fails. Suggest a
   browser-unreachable fallback (`needs_human` outcome or a fake-mode outcome) so the case passes without the
   browser container.
4. Digest writer (agent component): for 「確認資料」 buttons on a DIGEST row, put the pending fact ids in the
   `family.send` payload as `fact_ids: [...]` with `buttons: ["confirm"]` — both `press()` and the Telegram
   callback path read `payload.fact_ids` and call `store.confirm_fact`.

## Known gaps / things I did not verify

- Nothing was verified against **real** Telegram/Twilio/beacon hardware (no keys exist; spending is banned).
  The real paths are code-reviewed and mock-tested only. `set_webhook` has never been executed (by design).
- `sim/beacon.py`'s POST path was not run against a live api in this session (it needs a running stack with
  `BEACON_TOKENS` set); its `detect` path was run in the container and on host Python (fallback rule), and the
  endpoint it targets is fully tested.
- With `BEACON_TOKENS` unset (fake default), `POST /events/sensor` rejects every request (no seeded beacons).
  That is deliberate; the demo's 「模擬跌倒」 button uses `operator.fall` and needs no token. The owner sets
  `BEACON_TOKENS` for real hardware (§27.1 row 10).
- The INFO rate-limit window uses the loop's monotonic clock scaled by `time_scale` (like Kernel timers), not
  the virtual clock — matches how the Kernel itself delays things; noted in case the digest timing ever cares.
- If ffmpeg is missing in some deployment, voice notes are kept as OGG (`audio/ogg`) instead of AAC; the iPad
  expects AAC — ffmpeg exists in the image, so the normal path converts. Flagging for the iOS owner.
- The `audio` eval suite remains pending keys (pre-existing, not mine).
