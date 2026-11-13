# Ah Bo runbook

Demo day, rehearsal and recovery. Commands run on the VPS from `/opt/ahbo`. The host has no `make`, so every
`make <target>` in the PRD is `deploy/make.sh <target>` here (BUILD_LOG D-11). The console is
`https://srv2135273.hstgr.cloud/operator/` and the browser iPad is `/sim/` (both need the operator login).

## 1. Demo day (PRD §27.5 and §13.2)

| # | Step | How |
|---|---|---|
| 1 | Put the iPad on **your own phone hotspot** (never the venue Wi-Fi). | iPad Settings → Wi-Fi |
| 2 | Stack up, including the `browser` service (S3's sign-up needs it). | `deploy/make.sh up`, then `deploy/make.sh ps`: `api`, `browser`, `caddy` are `Up` (`api` healthy) |
| 3 | Health table: all green, or `fake` only for providers you chose to fake. | `deploy/make.sh doctor` (same table: console → **Health** → refresh). Red rows say what to fix and point at `SETUP.md`. |
| 4 | Reset the demo (seed reloaded, incidents/sessions/cards/queues cleared, face reset; ≤ 5 s). | Console → Triggers → **Reset demo** (or `deploy/make.sh demo-fake`, which also prints the URLs) |
| 5 | Precompute the day plan. | Console → Triggers → **run 07:00 plan now** |
| 6 | Warm the connections and caches with one silent session. | Console → Triggers → **Warm-up** |
| 7 | Set the clock for the scenario you're about to run. | Console → Scenarios → **Prepare** S1/S2/S3/S4 (clock anchor only). The **S1–S4 / S4ok** buttons reset, anchor and drive the scenario through the real paths. |
| 8 | Trace recording. | Always on: every event, command and span is written to SQLite (`events`, `commands`, `spans`). Nothing to start. |
| 9 | Backup video ready. | On the presenter's laptop |
| 10 | **Panic** one key away. | Console: **Esc** (or the red **PANIC** button) |

Live vs backup for each scenario (§13.1): S1 a real Telegram voice note from 阿珍's phone, or Triggers →
**inject voice note**; S2 her question live, or Overrides → force tool result; S3 live, or force the deep / action
result; S4 the beacon, or Triggers → **模擬跌倒 (fall)** (same event, `source: operator`, card says 操作員觸發).

**Never** place a real call or send a real Telegram message as a test except by pressing the console button
yourself. Nothing in the system dials 999/112/911 or any number that is not a verified contact (INV-9).

## 2. Recovery

| Symptom | Do this |
|---|---|
| Something on stage goes wrong mid-scene | **Esc** (Panic): stops all audio, closes the session, face back to idle. Then **Reset demo** and re-run the scenario button. |
| Face shows stale cards/steps | **Reset demo**. On the console mirror, **resync** rebuilds it from the snapshot. |
| An incident is stuck | Overrides → **mark OK** / **escalate** / **resolve** (each is a logged `operator.*` event). |
| Ah Bo talks when it shouldn't | Overrides → **mute** (agent paused); **unmute** afterwards. |
| Console or site not responding | `deploy/make.sh restart` (or `deploy/make.sh up` to rebuild). The face replays its state when the device reconnects. Check `deploy/make.sh logs`. |
| Login fails / forgot the password | `deploy/make.sh set-password` (types a new one, restarts `api`). The 5/min/IP login limit resets after a minute. |
| iPad shows offline / device row red | The Kernel keeps going without it: falls still escalate to Telegram + phone (A4). Check the hotspot; the app reconnects by itself and the face replays. Reminders still sound from the iPad's local mirror while offline. Meanwhile drive the demo from `/sim/` on a laptop. |
| Models / Azure / LiveKit down | The fall still works: F1–F5 are pre-rendered clips started by the Kernel (INV-3), the 「我冇事」 button and countdown are local, alerts and calls go out. For conversation: Overrides → **switch to fallback** (stt / llm / tts), or put the provider back into `AHBO_FAKE` in `.env` and `deploy/make.sh up`. |
| Telegram down | Alerts stay in the console **Outbox** with retry (backoff up to 10 min). Phone calls still go out. Press the buttons in the Outbox to continue the scene. |
| Twilio down | The Telegram alert still goes out; the call row shows `failed` in the Outbox. Use the Outbox **press 1 (答咗)** to continue the scene in fake mode. |
| Fixed cache red in doctor | `deploy/make.sh render-fixed`, or restart `api` (it renders at start-up). |
| Clock wrong | Console → Virtual clock → **clock back to now**, or `deploy/make.sh doctor` shows the offset. A jump of more than 120 s never fires stale reminders (CONTRACTS §14.2); use dose-now / check-in / plan-now. |
| Disk or database red | Free space under `data/backups/` and `data/media/actions/`; restore the newest `data/backups/` copy if `integrity_check` fails. |

## 3. Logs and data

| What | Where |
|---|---|
| Service logs (api, browser, caddy, agent) | `deploy/make.sh logs` (Docker json-file, 5 × 10 MB per service) |
| Events, commands, spans, costs, outbox | SQLite `data/ahbo.sqlite` (WAL), tables `events`, `commands`, `spans`, `costs`, `outbox`, `quality` |
| Fixed clips | `data/media/fixed/` (`manifest.json`); the public phone clip under `data/media/public/<random>/` |
| Action screenshots | `data/media/actions/<action_id>/{pre_submit,post_submit}.png` |
| Backups | `data/backups/` (nightly, 7 kept); one-off: `deploy/make.sh backup` |
| Eval report | `evals/report.md` (`deploy/make.sh eval`) |

Logs never contain keys (at most their last 4 characters).

## 4. Reading a trace

Every input is an **event** (`evt_…`: `type`, `source`, `payload`) and every output is a **command** (`cmd_…`),
and each command names the event that caused it (`event_id`). Events and commands of one story share a
**correlation id**: the incident (`inc_…`) for a fall/SOS/concern, the session (`sess_…`) for a conversation, the
action (`act_…`) for a sign-up.

1. Console → **Trace** → paste the id (or click an id in **Live state**). Same data:
   `GET /operator/api/trace?correlation_id=inc_…`.
2. Read top to bottom by time. For a fall: `sensor.fall_suspected` / `operator.fall` → `device.play_fixed F1` →
   `capture_photo` → `device.photo_uploaded` → `vision.result` (advisory) → F2/F3 + `session.open kernel_listen` →
   `ok_button` + `countdown fall_cd` → either `device.touch_ok` / her OK (RESOLVED_OK) or `timer.fired` →
   ESCALATING → `family.send ALERT` + `phone.call` → `phone.call_status` / `family.button` → ACKNOWLEDGED → closed.
3. `source` tells who did it: `beacon`, `device`, `agent`, `operator` (a console press — always visible as such),
   `telegram`, `twilio`, `scheduler`, `system`, `action`.
4. Informational events (`arbiter.decision`, `checkin.skipped`, `phone.call_refused`, `system.cap_exceeded`) explain
   why something did **not** happen.
5. `spans` give per-turn latency; `python -m ahbo.ops.doctor --latency` inside the api container prints p50/p95 of
   the last 50 turns.
