# QA 24 report — Platform, device channel, security

Tester: GLM (read-only). Spec: PRD §10/§11/§26.4/§24.13–24.14, CONTRACTS §2/§5/§8/§9/§14, GOD_PROMPT hard
rules 6+8. Evidence: `docs/qa/24/` (logs, JSON, full probe scripts in `docs/qa/24/scripts/`). All tests ran
against a throwaway `AHBO_FAKE=all` server (recipe in 20_QA_COMMON §3) driven over real HTTP/WebSocket from
separate containers on a private Docker network; live host touched only with the read-only GETs named in the
task. Containers and network removed at the end (`qa24-api`, `ahbo-qa24-browser`, `ahbo-qa24` — gone).

## Summary

| Severity | Count |
|---|---|
| S1 (safety / secret leak / stranger access) | **0** |
| S2 (a feature doesn't work) | **0** |
| S3 (minor / cosmetic) | **3** |

**Verdict: usable with issues.** The auth matrix is exactly per contract on all ~127 route×credential
combinations, the device channel implements every §2 behaviour I could script (welcome/replay, ack, P0
resend ≤3×, dedup, two devices, offline, utterance turns, 600 ms step hold), restart recovery works, no
secret material leaks anywhere I scanned, webhook signatures genuinely validate, and input abuse never
produced a 5xx, a traversal read, or a crash.

Environment note: mid-session an external `docker compose` operation (project `ahbo`) SIGKILLed and removed
my first throwaway api container twice (it matched compose's `ahbo/api` name pattern; I created nothing on
the live stack). I recreated it as `qa24-api` with a network alias and re-verified; no check was weakened,
and the earlier evidence (checks 1–5) was captured against the identical image/recipe.

## Defects

### D1 [S3] Resolved incident's unacked P0 frames are still delivered after resolve — phantom OK button + countdown on the face
- Where: `brain/ahbo/links/device_hub.py:243-272` (`_tick` resends `self.pending` entries); only
  `reset_queues()` (`device_hub.py:183`, reset-demo/panic) clears pending. The fall `resolve()`
  (`brain/ahbo/kernel/reducers/fall.py:296`) clears the Kernel timers but nothing tells the hub to drop the
  incident's still-unacked P0 commands.
- Spec: CONTRACTS §2.4 — a P0 drops stale queued ui ("the fall flow starts with `reset`"); the face must show
  the *current* state. After resolve the face was already cleaned (`ok_button show:false`, `card_clear`,
  `steps_clear`, `mood idle`, `wake.enable on`), but the next scheduled P0 resend of the pre-resolve
  `ok_button show:true` + `countdown fall_cd n:5` + `wake.enable off` batch is still delivered — even to a
  device that connects *after* the resolve (its replay shows the clean state, then the stale P0 frames land).
  The iPad ends showing 「我冇事」 + a countdown for an incident that no longer exists, with the wake-word
  detector switched off until the next state change.
- Repro (my server): `python /scratch/probe2_device.py` (trigger `operator/api/trigger {"kind":"fall"}`,
  never ack, `POST /operator/api/override {"kind":"incident_resolve"}`, connect a second device immediately
  after). Evidence: `docs/qa/24/device_run.log` — resolve-cleanup frames `01M75W69AK3A` (ok_button False …)
  are followed by the stale `01M75W65EJ0V` batch (ok_button True / countdown / wake.enable off) on the old
  device and again on the freshly connected `iPad2-sim`.
- Actual: bounded blast radius — at most the ≤3 scheduled resends (≤ ~6 s after resolve) land late; the next
  Kernel state change repairs the face. Cosmetic-confusing, not a safety issue (escalation had already
  happened and is unaffected).

### D2 [S3] Replay omits the `mood` command when the current mood is idle
- Where: `brain/ahbo/kernel/ui_state.py:92` — `if self.mood != "idle": out.append(mood)`.
- Spec: CONTRACTS §2.4 — replay is "`ui reset`, **then mood**, every persistent card, …". With mood idle the
  replayed stream is just `[reset]` (observed in `docs/qa/24/device_run.log`, phase A), so a reloaded face
  falls back to whatever the kit's initial mood is instead of being explicitly set to idle.
- Repro: connect with a valid credential, send `hello`, read the frames after `welcome`
  (`docs/qa/24/scripts/probe2c_replay_ack.py` prints them). Behaviourally near-noop if the kit defaults to
  idle; it is a letter-of-contract deviation only.

### D3 [S3] `/ws/device` completes the HTTP upgrade (101) before rejecting an unauthenticated client
- Where: `brain/ahbo/links/device_hub.py:284-291` — accept-then-close per BUILD_LOG D-12.
- Spec: task check says "/ws/device without auth (must not upgrade)"; CONTRACTS §2.1 says wrong auth → close
  4401. D-12 (BUILD_LOG §3, an owner decision that wins per 20_QA_COMMON §1) deliberately accepts the socket
  first so the client sees 4401 instead of an HTTP 403.
- Actual, live host (read-only GET, `docs/qa/24/live_host.log`): `curl --http1.1 -H "Upgrade: websocket" …
  https://srv2135273.hstgr.cloud/ws/device` → `HTTP/1.1 101 Switching Protocols`. On my throwaway server the
  accepted socket closes with **4401** immediately for every wrong-auth combination (no hello, no commands,
  no replay — verified in `docs/qa/24/ws_login_public.log`). So no access is gained; the only exposure is
  that unauthenticated clients can complete handshakes (resource noise). Listed as a defect only so the
  orchestrator consciously re-confirms D-12 against the task's "must not upgrade" wording.

## Passed checks

**Check 1 — auth matrix** (`docs/qa/24/auth_matrix.log|json`, 127 rows; `ws_login_public.log`; `ratelimit.log`)
- Public exactly as contracted: `/healthz`, `/login`, `/face/*`, `/mock-centre/*` (+`/mock-centre/api/*`),
  provider webhooks. Everything else refuses: pages (`/`, `/sim/`, `/operator/*`) 303→login; `/operator/api/*`
  and `/operator/stream` 401; media device-token routes 401.
- Operator API (`state`, `health`, `outbox`, `outbox press`, `trace`, `fixed`, `trigger`, `clock`): 401
  without/with-forged cookie and with a device token; 200 with the operator cookie only.
- Device-token routes: `/session`, `/media/photo` POST, `/media/fixed/*`, `/media/voice/{id}` accept device
  token **or** cookie; `Bearer APP_SECRET` is correctly *not* a device token (401); forged `dt.…` → 401.
  Incident (fall) photo via device token → 403 `console_only`; via cookie → 200.
- `/internal/*` (context/turn/event/clock): 401 without/wrong token; works with `INTERNAL_TOKEN`.
- `WS /ws/device`: no-auth / wrong-bearer / device-token-as-bearer / cookie-without-`sim=1` / cookie with
  `sim=0` / forged cookie+`sim=1` → all close **4401**; `Bearer APP_SECRET` and operator-cookie+`?sim=1` open.
- Login rate limit: exactly `[401×5, 429, 429]` per IP; a second IP unaffected; successful logins also count.
- Cookie flags on login: `HttpOnly`, `SameSite=strict`, `Path=/`, `Max-Age=43200` (12 h); `Secure` is
  conditional on an https `PUBLIC_BASE_URL` (`settings.secure_cookies`), so it is absent on my http test
  server and present in production config (not verifiable without a live POST — see Not tested).
- `/media/public/{rand}/{file}` serves without auth by design (unguessable path); wrong file → 404; a
  `../`-laden rand/name → 404.
- `POST /logout` public, clears the cookie.

**Check 2 — device channel** (`device_run.log`, `p0_timeline.log|json`, `replay_ack.log|json`, `stephold2.log`)
- `hello` → `welcome` with all contract fields: `device_token` (`dt.…`, HMAC), `server_time` (+08:00),
  `fixed_cache_version`, `text_sessions:true` (livekit faked), `config.quiet_hours/interruption_enabled/wake`
  (6 matcher keys) and `reminder_mirror` with the 3 seed slots and their med cards.
- Replay on `face_event ready`/hello, mid-incident: `reset → mood alarm → card incident → steps fall →
  ok_button true` — a reloaded face is never blank during an incident.
- `utterance {final:true}` starts a real text turn over the device channel: `heard → mood thinking → steps →
  step done → mood idle → 5 cards → say_start → say_end`, reply 「大約十個鐘之後，十二點正。…」.
- Step hold (PRD §23.6 rule 4): `steps{active}` → `step{done}` wire gap **600.2 / 601.2 ms** ≥ 600 ms with a
  prompt ack client (an earlier 513–580 ms reading was my own reader's ack-sleep artifact — both runs saved:
  `stephold.log` = artifact, `stephold2.log` = clean). `get_time` correctly produces no step.
- P0 resend: with zero acks, every P0 frame delivered exactly **initial + 3 resends at 2.09 s intervals,
  same frame id**, then stops (29 ids / 107 frames in `p0_timeline.json`); `unacked_p0` reached 9 on the
  console and one late ack decrements it.
- Ack handling: commands acked ≤ 2 s count as acked; latency recorded per device; first ack wins.
- Two devices (native + sim): both receive the same command ids (9 shared frames); `playback_done` sent from
  both with the same `say_id` → exactly **1** `device.playback_done` Kernel event.
- Disconnect: after 30 s the hub emits `device.offline` and the console shows `online:false`.

**Check 3 — restart recovery** (`restart.log`, `restart_pre.json`, `restart_results.json`)
- Fall started → AWAITING_OK (ok_button + countdown) → `docker restart` → healthz back in ~4–5 s →
  reconnect: replay restores `mood alarm`, `incident` card, `esc_call`/`esc_tell` cards and the `fall` step
  group with per-step states (`photo done, ask failed, call done, tell done`).
- The `_loaded` hook re-armed the flow: fresh ok_prompt (dropped `no_device` — no iPad connected yet), fresh
  5 s countdown, then **escalation completed after the restart**: outbox shows `twilio CALL in-progress` +
  `telegram ALERT sent` (both faked). Sending `touch_ok` afterwards → `REASSURING`. Sensible continuation.
- Reset demo: `POST /operator/api/reset` → `{ok:true}` in **14 ms** (≤ 5 s required).

**Check 4 — secrets** (`secrets_webhooks.json`, `tg_health_full.txt`, `sse_dump.txt`, container logs)
- Scanned every secret's full value minus the last 4 chars (APP_SECRET, INTERNAL_TOKEN, SESSION_SECRET,
  TELEGRAM_WEBHOOK_SECRET, beacon token, TWILIO_AUTH_TOKEN/SID) against: `/operator/api/state`,
  `/operator/api/health`, 37 KB of `/operator/stream` (state/turn/ui/health events), the whole api container
  log, and the **full 1905-char Telegram `/health` reply** (driven through the real webhook path and read
  from the fake channel's record): **zero hits**. The health table shows only `set (…6dff)`-style masks.

**Check 5 — webhooks** (`secrets_webhooks.json`)
- `/telegram/webhook`: no secret → 401 `missing_secret`; wrong → 403 `bad_secret`; correct → 200.
- `/twilio/twiml/{id}`, `/twilio/gather`, `/twilio/status`: no signature / garbage signature → 403
  `bad_signature`; a signature computed with Twilio's real HMAC-SHA1 algorithm over the exact URL+params →
  200 with correct TwiML (so the validator validates, it doesn't just reject); press-1 (`Digits=1`) works.
  `GET /twilio/twiml/test`: 401 without cookie, 200 with.
- `/events/sensor`: no/wrong `X-Beacon-Token` → 401; token valid for a *different* beacon id → 401 (no
  beacon-id enumeration); valid → `202 {"ok":true,"queued":true}`; duplicate `(beacon_id, seq)` →
  `202 {"ok":true,"duplicate":true}`; a real `fall_suspected` through the beacon path opened an ALARM incident.

**Check 6 — input abuse** (`abuse.log`, `abuse_results.json`)
- 25 MB JSON bodies → 400 in 0.3 s (no 500, no hang); malformed JSON / JSON array / wrong types → 400/401;
  `trigger kind=123|nonsense` → 400; `clock` bad args → 400; unknown scenario → 400; `trace limit=99999999`
  and `limit=-5` clamped; SQL-ish `correlation_id` → empty result (parameterised); 20 k-char CJK + control
  characters through `/internal/turn` → 200, no corruption; 5000-char tool name → `unknown_tool`.
- Path traversal on `/media/fixed|voice|screens|photo|public` with raw `../`, `%2e%2e%2f`, double-encoded,
  backslashes, `....//`, and `%00` targets → **404 everywhere** (targets chosen as `pyproject.toml`/`passwd`;
  `.env` was never fetched).
- Photo upload: 9 MB → 413; GIF-magic bytes → 415; `incident_id="../../evil"` and 200-char ids → 422;
  300-char multipart filename accepted harmlessly (the stored name is a server-generated `ph_…` id).
- Login `next=//evil.com` sanitised → 303 to `/operator/` (open-redirect blocked).
- WebSocket garbage (non-JSON, arrays, numbers, unknown types, ack-without-ref, face_event with a string /
  a traversal event type, 100 k-char utterance, 10 k-char error detail) — all ignored; the same connection
  then completed a normal text turn; server healthz stayed 200. A binary frame cleanly closes that one
  connection (code 1000), others unaffected.

**Read-only live host** (`live_host.log`)
- `/healthz` 200; `/internal/turn`, `/INTERNAL/turn`, `/%69nternal/turn` all **404** (Caddy blocks the path in
  every casing/encoding tried); `/operator/api/state` 401; `/sim/` 303 without a cookie; plain GET `/ws/device`
  → 404, and the handshake attempt shows the 101-then-4401 pattern of D3/D-12 above.
- `ss -ltnp`: only Caddy (docker-proxy) on `0.0.0.0:80/443`; api bound to `127.0.0.1:8000`; no other ahbo
  container port published (browser/agent have none). Port 22 (sshd) and two loopback-only host agents
  (`monarx-agent`, `node` on 127.0.0.1) pre-date the project.

## Not tested / blocked
- **Live login POST** (real rate limit and the live `Secure` cookie flag): forbidden — no POST to the live
  host. Verified on the throwaway server instead; the code makes `Secure` follow the https public URL.
- **Life-story photo via device token** (`/media/photo/{id}` → 200 with `dt.` token): the demo seed has no
  `life_stories` row with a `photo_id`, so only the fall-photo 403 `console_only` path was reachable.
- Real Telegram / Twilio / LiveKit / ElevenLabs / Opper calls — forbidden; everything ran with `AHBO_FAKE=all`.
- Caddy's `X-Forwarded-For` overwrite on live (the rate limiter trusts the last XFF entry on a bare
  connection — on my network a rotating XFF bypassed it, as the code's own comment predicts; behind Caddy
  the header is rewritten). Not directly verifiable without POSTing to the live host.
- `/media/public/…` on live (the random directory name is not readable without the live DB; the mechanism was
  verified on the throwaway server).
- Long-run SSE behaviour (>32 s captured), WS frame-rate limits (none in the contract), and the iOS app
  (out of scope per 20_QA_COMMON).
