# GLM task 04: Links — family Telegram, phone Twilio, beacon sensors (+ fakes and outbox)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/04_REPORT.md`.

**This component is safety-critical** (who gets messaged and phoned). Claude reviews the dial guard, the allowlist and the
webhook verification line by line before it is accepted. Keep that code small and obvious.

**PRD sections:** §2 (INV-7, INV-9), §4.3.1 (ALERT content and buttons), §9.1, §9.2, §9.3, §10.4 (Telegram/Twilio degraded),
§11, §26.2–26.4, §26.3 (fake Telegram/Twilio + outbox), §27.1 rows 6–7, §27.2.

**You own:** `brain/ahbo/links/{__init__,outbox,family,telegram,twilio,sensors,wiring}.py` (NOT `device_hub.py`),
`sim/beacon.py`, `sim/telegram_fake.py`, `sim/twilio_fake.py`, `brain/tests/links/**`.

**Already on disk from a stopped agent (review, keep what is right, finish):** `links/__init__.py`, `links/outbox.py`,
`links/family.py`. No tests yet; no telegram/twilio/sensors/wiring yet.

**How it plugs in (read these, they are committed):** the platform calls `ahbo.links.wiring.install(svc)` and includes
`ahbo.links.wiring.router` (`brain/ahbo/api/services.py`). The console calls your helpers from `brain/ahbo/api/demo.py`
(read it and match the names/signatures exactly): `outbox_list(db)`, `press(svc, outbox_id, button)`,
`inject_voice_note(svc, file=None)`, `inject_text(svc, text)`. The Kernel (`brain/ahbo/kernel/reducers/fall.py`, `dose.py`,
`concern.py`, `action.py`, `kernel/common.py`) emits the commands you dispatch — read them for the exact payloads:
- `family.send` {kind: ALERT|NOTIFY|INFO|DIGEST|HANDOFF, contact_ids, text, photo_id?, buttons?, screenshots?, incident_id?,
  time?, address?}. ALERT buttons: handled 「我處理緊」, call_mum 「打俾媽媽」, resolved 「已解決」; confirm 「確認資料」 for facts.
- `phone.call` {contact_id, clip, incident_id}. The Kernel already refuses 999/112/911 and unverified contacts
  (`kernel/common.py`); **you add a second, independent guard here**.
Results go back as Kernel events: `phone.call_status {call_id, status, incident_id}` (status ∈ queued ringing in-progress
completed no-answer busy failed acknowledged), `family.button {contact_id, button, outbox_id?, via?}` (the Kernel accepts
a plain button or `b:<button>:<outbox_id>` callback data), `family.voice_note {contact_id, voice_id}`, `family.text {contact_id, text}`.
Submit with `await svc.kernel.submit(Event(...))`.

## Build (real + fake for each, chosen by `settings.is_fake("telegram")` / `is_fake("twilio")`)
1. **Dispatcher `family.send`:** contact ids → chat ids from `contacts.telegram_chat_id`; at most 1 INFO per 10 min per
   contact (merge the rest into the next one; ALERT exempt); retry with backoff for up to 10 min (§10.4); attach photo
   (`data/media/incidents/<id>.jpg`) / screenshots; inline buttons. Every outbound message is a row in `outbox`
   (`fake=1` in fake mode, else the provider message id). Dispatchers return fast; do the work with `svc.kernel.spawn(...)`.
2. **Telegram real** (`links/telegram.py`, raw Bot API over `httpx` — no extra SDK): router `POST /telegram/webhook`, check
   `X-Telegram-Bot-Api-Secret-Token` with `hmac.compare_digest`; **allowlist by chat id**: anyone else gets exactly one fixed
   reply 「呢個係私人家庭機械人。」 and is dropped — never forwarded to the Kernel/agent (INV-7), except `/whoami` (anyone;
   reply with their chat id only). Voice notes: getFile → download OGG → `ffmpeg` to AAC `.m4a` → `ffprobe` duration →
   `voice_messages` row (transcript: none unless speech is real) → `family.voice_note`. Text → `family.text` (quoted, never
   instructions). Commands: `/start`, `/status` (today's meds from `dose_events`, open incident, last interaction),
   `/fact <text>` → `ahbo.memory.store.remember(db, clock, text, category="other", source="telegram")` (catch
   `MemoryRejected` → polite refusal), `/story <text>` (+photo) → `ahbo.memory.life_stories.add_life_story(...)`, `/meds`
   (read-only schedule). Callback buttons → `family.button`, then `answerCallbackQuery`; `confirm` → `store.confirm_fact`.
   `set_webhook(svc)` helper (setWebhook with `secret_token`) for the owner's setup — **never run it**.
3. **Twilio real** (`links/twilio.py`, `twilio` 9.13.0 SDK): dispatcher `phone.call` with its own **INV-9 guard**: only
   `contacts` rows with `verified=1` and role ∈ {emergency_centre_sim, family}; normalise to E.164; reject if the digits are
   999/112/911 (or end in them as a short code), or ≤ 4 digits, or the contact is unknown. TwiML: `<Play>` the P1 clip URL
   (public path from kv `public_clip_path`, see CONTRACTS §8) **twice**, then `<Gather numDigits="1" action="/twilio/gather">`;
   pressing 1 → `phone.call_status {status:"acknowledged"}`. Routes `POST /twilio/twiml/{call_id}`, `/twilio/status`,
   `/twilio/gather`, all validated with the SDK's `RequestValidator` (URL = PUBLIC_BASE_URL + path); `GET /twilio/twiml/test`
   dry run behind the operator cookie (use the dependency in `brain/ahbo/api/auth.py`). Daily cap 5 calls, except safety calls
   ≤ 3 per incident (`ahbo.ops.caps.check_cap` if present — import lazily).
4. **Fakes** (one source of truth: the classes live in `links/`; `sim/telegram_fake.py` and `sim/twilio_fake.py` are thin CLI
   wrappers that import them): fake Telegram writes the outbox only; fake Twilio writes the outbox with the TwiML, then emits
   `phone.call_status` ringing → in-progress → completed over 6 s (× `svc.kernel.time_scale`). `press(svc, outbox_id, button)`:
   handled/resolved/confirm/call_mum → `family.button` with payload `via:"operator_fake"` (source `telegram`) so it stays
   honest (INV-8); `press1` on a call → `phone.call_status acknowledged`. `inject_voice_note` uses
   `brain/seed/voice/demo_daughter.m4a` (if missing, make a silent placeholder with ffmpeg, length from the seed transcript).
5. **Sensors** (`links/sensors.py`): router `POST /events/sensor`: `X-Beacon-Token` checked against hashed tokens
   (sha256) from `BEACON_TOKENS`, stored in `beacons` at install; body per CONTRACTS §8; idempotent on (beacon_id, seq) via
   `sensor_seen`; respond 202 fast (queue with `kernel.submit`, don't process inline); fall_suspected / sos / heartbeat →
   `sensor.fall_suspected` / `sensor.sos_pressed` / `sensor.heartbeat` (source `beacon`); update `beacons.last_seen/battery`.
   `sim/beacon.py` CLI (`python sim/beacon.py fall --url ... --token ...`) + the reference detection rule (§9.1) as a pure
   function `detect_fall(samples)` with tests.
6. **`links/wiring.py`:** `install(svc)` registers `family.send` and `phone.call` dispatchers; `router` combines the
   Telegram, Twilio and sensor routes.

## Tests (required)
- `brain/tests/links/test_inv9_dial_guard.py`: many formats — "999", "+852 999", "112", "+1 911", " 9 9 9 ", "+852-999",
  unverified contact, unknown role, unknown contact id, short codes; plus a good verified number that IS allowed.
- Allowlist: a stranger's text and voice note never produce a Kernel event; `/whoami` works for anyone.
- Webhook secret wrong/missing → 401/403; Twilio bad signature → 403; good signature (computed with RequestValidator in the
  test) → 200. Real adapters tested only with `httpx.MockTransport` / mocks — never a real network call.
- Fake call status sequence; `press` paths; INFO rate limit merge; sensor idempotency.

## Done when
- `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/links tests/invariants` passes, and the **9 invariant
  tests that are currently skipped for lack of a `phone.call` dispatcher now run and pass** (paste the before/after counts).
- `docker run --rm -v /opt/ahbo:/repo -w /repo/brain ahbo-api python -m ahbo.evals` — report the scenarios and safety_redteam
  numbers (S4 needs your outbox; rt25–rt28 need your phone dispatcher).
- Full suite: nothing new fails.
