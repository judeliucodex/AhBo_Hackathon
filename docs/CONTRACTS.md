# Ah Bo contracts (FROZEN)

This file is frozen before any component is built. Every component implements against it. A change needs
an entry in `docs/BUILD_LOG.md` and an update here in the same commit. The Python source of truth for the
shared types is `brain/ahbo/core/` (events, db schema, config, clock, number words, adapters); where this
file and that code disagree, fix both.

PRD references: §10 (device), §23.4 + `face/README.md` (face), §26.4 (endpoints), §5.5 + §26.6 (tools),
§24.8.3 (deep result), §4.1–4.2 (events), §14 (schema), §26.5–26.6 (config, seed).

Contents: 1 Conventions · 2 Device channel · 3 Face commands · 4 Events and commands (Kernel) · 5 Internal
agent→api API · 6 Tools · 7 Deep result schema · 8 HTTP endpoints · 9 Operator API · 10 SQLite schema ·
11 Config and seed formats · 12 Adapter interfaces · 13 Golden UI trace comparison · 14 Additions after the freeze

---

## 1. Conventions

- **Ids**: `<prefix>_<ULID>` (`evt_`, `cmd_`, `inc_`, `dose_`, `sess_`, `turn_`, `act_`, `deep_`, `vm_`, `ph_`,
  `call_`, `ob_`). Device frames use a bare ULID string for `id`.
- **Time**: ISO-8601 with offset, always HKT (`+08:00`), from the **virtual clock** (`ahbo.core.clock`).
  Never `datetime.now()` in Kernel or tool code.
- **JSON**: UTF-8, `ensure_ascii=False`. Text shown to Po-po is Traditional Chinese (HK Cantonese).
- **Tool results** always contain `ok: bool`; failures carry `reason: str` (short, snake_case).
- **Priorities**: `0` P0 safety, `1` P1 care, `2` P2 social, `3` P3 ambient (§6). Commands carry a priority.

## 2. Device channel (`wss://<host>/ws/device`), §10.1

### 2.1 Handshake
1. Connect with `Authorization: Bearer <APP_SECRET>` (iPad), or with the operator cookie and `?sim=1`
   (browser iPad / headless tests). Anything else: close code **4401**.
2. First iPad frame:
   ```json
   {"id":"01J…","type":"hello","device_id":"ipad-air5-1","app_version":"0.1.0",
    "fixed_cache_version":"f-3c9a…","wake_engine":"speech_transcriber"}
   ```
   `wake_engine` ∈ `speech_transcriber | dictation | none`. The sim sends `"none"` and `device_id:"sim-…"`.
3. Server reply:
   ```json
   {"id":"01J…","type":"welcome","device_token":"dt.…","server_time":"2026-11-20T10:00:00.000+08:00",
    "fixed_cache_version":"f-3c9a…","text_sessions":true,
    "config":{"quiet_hours":{"start":"22:30","end":"07:00"},"interruption_enabled":true,
              "wake":{"require_wai":true,"require_two_updates":true,"utterance_start_silence_ms":300,
                      "cooldown_after_reject_s":5,"allow_missing_aa":true,"plain_abou_wakes":false},
              "reminder_mirror":{"date":"2026-11-20","slots":[{"slot_id":"s_1200","time":"12:00",
                      "clip_id":"R_s_1200","cards":[{"id":"med_bp","kind":"med","label":"血壓藥","small":"Amlodipine 5 mg","pill":["--pill-b","--pill-a"]}]}]}}}
   ```
   `text_sessions: true` when `livekit` is faked (§26.3): sessions are text sessions. `device_token` is an
   HMAC-signed token (`dt.<device_id>.<expiry>.<sig>`, 24 h) used for `POST /session`, `/media/*`.
4. Immediately after `welcome` the server **replays the current face state** as `ui` commands (§2.4).

### 2.2 Server → iPad commands
Every command frame: `{"id": "<ulid>", "type": "<type>", "priority": 0..3, ...fields}`. The iPad answers
every command with `ack` within 2 s and de-duplicates by `id`.

Liveness (added 12 Nov 2026, QA 25 D2): the server answers every `heartbeat` with `{"type":"pong","ref":"<heartbeat
id>"}`. It has no `id`, is not a command and is never acked (clients ignore frames without an `id`). A client that
has seen a pong treats ~25 s without any frame as a dead connection (the browser iPad's `sim/channel.js` does).

| type | fields | example |
|---|---|---|
| `ui` | `ui`: one face command (§3) | `{"id":"01J…","type":"ui","priority":0,"ui":{"type":"mood","mood":"alarm"}}` |
| `play_fixed` | `clip_id`, `say_id`, `mood?` | `{"id":"…","type":"play_fixed","priority":0,"clip_id":"F1","say_id":"say_inc_01J_F1"}` |
| `play_voice_message` | `id`, `url`, `say_id`, `from_name`, `text?`, `duration_s?` | `{"id":"…","type":"play_voice_message","priority":2,"id_":"…"}` — see note |
| `capture_photo` | `incident_id` | `{"id":"…","type":"capture_photo","priority":0,"incident_id":"inc_01J…"}` |
| `session.open` | `reason`, `mode`, `session_id`, `text_only` | `{"id":"…","type":"session.open","priority":1,"reason":"fall_check","mode":"kernel_listen","session_id":"sess_01J…","text_only":true}` |
| `session.close` | `session_id` | |
| `wake.enable` | `on` | `{"id":"…","type":"wake.enable","priority":0,"on":false}` |
| `config.update` | any of `quiet_hours`, `interruption_enabled`, `wake`, `fixed_cache_version`, `reminder_mirror` | |
| `stop_all` | | panic switch: stop audio, leave room, face `reset` |

**Note on `play_voice_message`:** the frame's own `id` is the command id, so the voice-message id travels as
**`message_id`** (the PRD's `id` field is renamed to avoid the clash):
```json
{"id":"01JCMD…","type":"play_voice_message","priority":2,"message_id":"vm_demo",
 "url":"/media/voice/vm_demo","say_id":"say_vm_demo_1","from_name":"阿珍",
 "text":"媽，記得食飯呀！星期日我帶阿明嚟探你。","duration_s":4.1}
```

**Fixed clips (`play_fixed`)**: the iPad looks up `clip_id` in `manifest.json`, starts the audio and in the same
tick sends its own face `say_start {id: say_id, text, segments}`; at the end `say_end {id: say_id}`; then replies
`playback_done {clip_id, say_id}`. **Voice notes**: face `say_start {id: say_id, from:"family", fromName, text?,
durationSec}`; then `playback_done {message_id, say_id}`.

### 2.3 iPad → server events
Frame: `{"id":"<ulid>","type":"<type>", ...fields}`. (Events need no ack.)

| type | fields | when |
|---|---|---|
| `hello` | §2.1 | first frame |
| `heartbeat` | `battery` (0–1), `thermal` (`nominal\|fair\|serious\|critical`), `in_session` (bool) | every 10 s |
| `ack` | `ref`, `ok`, `error?` | every command, ≤ 2 s |
| `wake` | `trigger` (`wakeword\|touch`), `s1_score?`, `s2_text?` | confirmed wake word, or face tapped while idle |
| `touch_ok` | | face reported `ok_tapped` |
| `playback_done` | `clip_id?`, `message_id?`, `say_id` | clip or voice note finished (also when it failed to play: add `error`) |
| `photo_uploaded` | `photo_id`, `incident_id` | after `POST /media/photo` succeeded |
| `face_event` | `event`: the face event object (§3.2) | every other face event (`say_done`, `countdown_done`, `tap` on a card, `error`, `ready`) |
| `utterance` | `text`, `final` (bool), `session_id?` | **text sessions only** (fake LiveKit, §26.3): what she "said" in the browser iPad's text box |
| `error` | `code`, `detail` | native failure (`mic_denied`, `livekit_join_failed`, `camera_failed`, …) |
| `settings` | `interruption_enabled` (bool) | the owner flipped the interruption toggle in the iPad's hidden settings (added 11 Nov 2026, see BUILD_LOG D-29) |

`face_event` with a host-made event (added 11 Nov 2026, see BUILD_LOG D-30): `{"type":"face_event","event":
{"type":"reminder_confirmed","slot_id":"s_1200","at":"<ISO time she tapped>","source":"touch_offline"}}` — a
reminder she confirmed by touch while the iPad was offline (§10.4 local mirror), sent after reconnecting. The dose
reducer marks the slot `taken_self_reported` if `at` lies within the confirm window of the slot's due time.

### 2.4 Delivery and replay
- Every command needs an `ack` within 2 s. **P0** commands without an ack are resent (same `id`) up to 3 times
  at 2 s intervals; then the console shows the device red and the Kernel continues (escalation never depends on
  the iPad).
- **Ordering:** commands are sent in Kernel order. The UI emitter may hold a step `done` until the step has been
  `active` ≥ 600 ms (§23.6); everything queued behind it waits too (order is never broken). A **P0** command
  drops all queued non-P0 `ui` commands (they are stale; the fall flow starts with `reset`).
- **Replay** (on `welcome` and on `face_event {event:{type:"ready"}}` after a reload): `ui reset`, then `mood`,
  every persistent card, every open step group (with each step's current state), `ok_button` if shown,
  `offline` if set. Built by `kernel.ui_state.replay_commands()`.
- Several devices may be connected (iPad + browser iPad). Commands go to **all**; the first `ack` counts;
  `playback_done` is de-duplicated by `say_id`.

## 3. Face command contract (§23.4; `face/README.md` is authoritative)

### 3.1 Commands (host → face)
`mood {mood}` · `expr {expr}` · `heard {text, final?}` · `say_start {id, text?, from?, fromName?, segments?,
durationSec?, paged?}` · `say_update {id, text?, revealed?}` · `say_end {id}` · `level {v}` ·
`card {card:{id, kind, label, small?, icon?, when?, persistent?, pill?, wave?, photo?}}` ·
`card_update {id, patch:{label?, small?, icon?, playing?}}` · `card_clear {id?}` ·
`steps {group:{id, title?, steps:[{id, label, doneLabel?, state?}]}}` · `step {group, id, state, label?}` ·
`steps_clear {group?}` · `ok_button {show, label?}` · `countdown {id, n}` · `flash {}` · `birth {}` ·
`offline {on}` · `reset {}`.

Moods: `idle listening thinking curious warm alarm sleepy`. Card kinds: `med event plan voice_note incident action
message story today photo activity info`. Step states: `pending active done failed`.

### 3.2 Events (face → host)
`ready {version}` · `tap {target:"face"|"card", id?}` · `ok_tapped {}` · `countdown_done {id}` · `say_done {id}` ·
`birth_done {}` · `error {message}`.

### 3.3 Transport
- iPad app: `webView.evaluateJavaScript("window.AhBo.apply(<json>)")`; events via `webkit.messageHandlers.ahbo`.
- Browser iPad / operator mirror: `iframe.contentWindow.postMessage({ahbo: cmd}, "*")`; events come back as
  `{ahboEvent: evt}`.
- The iPad (and the sim) maps face events: `ok_tapped` → device event `touch_ok`; `tap {target:"face"}` while
  idle → face `expr wake` locally, then device event `wake {trigger:"touch"}`; everything else → `face_event`.

### 3.4 Card and step conventions used by the Kernel
- Card ids: `next_dose`, `med_<catalogue id>`, `ev_<calendar id>`, `vn_<voice id>`, `act_<activity id>`,
  `incident` (persistent), `esc_call`, `esc_tell`, `today` (persistent), `rep_<intent>` (persistent),
  `action_<action id>`.
- Card time labels are 12-hour `h:mm` (`12:00`, `9:30`, `3:00`); `small` relative/place words in Cantonese.
- Step groups: one per user turn, `group.id = turn_id`, ≤ 4 steps, no title. The fall group: `id:"fall"`,
  `title:"跌倒處理"`, steps `photo, ask, call, tell`.
- Countdown id: `fall_cd`.

## 4. Kernel events and commands (§4.1, §4.2)

### 4.1 Event
```json
{"id":"evt_01J…","ts":"2026-11-20T15:02:11.204+08:00","type":"sensor.fall_suspected",
 "source":"beacon","correlation_id":"inc_01J…","payload":{"peak_g":3.1,"freefall_ms":180,"stillness_s":2.4,
 "beacon_id":"beacon1","battery":0.81}}
```
`source` ∈ `beacon device telegram twilio agent deep operator scheduler system action`.

Event types (payload highlights). Bold = must exist for M1.

| type | source | payload |
|---|---|---|
| **`sensor.fall_suspected`** | beacon/operator | `peak_g?`, `freefall_ms?`, `stillness_s?`, `beacon_id?`, `battery?` |
| **`sensor.sos_pressed`** | beacon/operator | `beacon_id?` |
| `sensor.heartbeat` / `sensor.lost` | beacon/system | `beacon_id`, `rssi?`, `battery?` |
| **`device.wake`** | device | `trigger`, `s2_text?`, `device_id` |
| **`device.touch_ok`** | device | `device_id` |
| `device.heartbeat` / `device.online` / `device.offline` | device/system | `device_id`, `app_version?`, `fixed_cache_version?`, `battery?` |
| **`device.photo_uploaded`** | device | `photo_id`, `incident_id` |
| **`device.playback_done`** | device | `clip_id?`, `message_id?`, `say_id` |
| `device.ack` | device | `ref`, `ok`, `error?`, `latency_ms` |
| `device.face_event` | device | `event` |
| **`timer.fired`** | system | `key`, `data` (whatever the reducer set) |
| **`schedule.tick`** | scheduler | `now` (virtual) — every 15 s |
| **`dose.due`** / `checkin.due` / `event.leave_soon` / `digest.due` / `plan.due` | scheduler | `slot_id`/`kind`/`event_id`, `date` |
| **`session.opened`** / **`session.closed`** | system/agent | `session_id`, `reason`, `mode`, `text_only` |
| **`user.utterance`** | agent | `text`, `session_id`, `turn_id`, `stt_confidence`, `final` |
| `agent.state` | agent | `session_id`, `state` (`listening thinking speaking idle`), `user_speaking?` |
| **`agent.tool_call`** | agent | `name`, `args`, `session_id`, `turn_id`, `call_id` |
| `agent.tool_result` | agent | `name`, `call_id`, `ok`, `result` |
| **`agent.concern`** | agent | `level` (`info urgent emergency`), `category`, `reason`, `session_id` |
| `agent.reply` | agent | `turn_id`, `text` (after the spoken filter), `session_id` |
| `family.voice_note` / `family.text` / `family.button` | telegram/operator | `contact_id`, `voice_id?`, `text?`, `callback_data?` |
| `phone.call_status` | twilio | `call_id`, `status` (`queued ringing in-progress completed no-answer busy failed acknowledged`), `incident_id?` |
| `vision.result` | system | `incident_id`, `note` (advisory only) |
| `deep.result` | deep | `task_id`, `result` (validated §7) |
| `action.status` | action | `action_id`, `status` (`planned filling submitted failed needs_human done`), `screenshots`, `detail` |
| `operator.*` | operator | `operator.fall`, `operator.ok`, `operator.escalate`, `operator.resolve`, `operator.mute`, `operator.unmute`, `operator.say`, `operator.play_fixed`, `operator.force_tool_result`, `operator.reset`, `operator.clock`, `operator.model_config`, `operator.provider_switch`, `operator.panic`, `operator.dose_now`, `operator.checkin_now`, `operator.plan_now` |
| `quality.*` | agent/system | `quality.mandarin_drift`, `quality.too_long`, `quality.digits_spoken`, `quality.tool_error`, `quality.stt_low_confidence` |
| `arbiter.decision` | system | `request`, `priority`, `outcome` (`now queued dropped`), `reason` |

Additions (added 11 Nov 2026, see BUILD_LOG). "Note" = recorded with `Kernel.note(ev)`: written to the trace and the
console stream (INV-6/INV-8) without running reducers.

| type | source | payload | how |
|---|---|---|---|
| `arbiter.decision` | system | as above | note (never submitted; reducers don't see it) |
| `checkin.skipped` | system | `kind` (`morning afternoon evening`), `reason` (`quiet_hours incident session_open_30min session_recent`) | note |
| `session.closed` (field) | system/agent/operator | adds `why`: `agent` (default), `wake_rejected` (LiveKit worker's Stage 3 rejected the wake), `panic` (operator Esc) | submitted |
| `memory.extracted` | system | `session_id`, `ok`, `reason?`, `facts_active`, `facts_pending` | submitted after `memory.session_closed` |
| `memory.write_result` | system | `op`, `command_id`, `ok`, `id?`, `reason?` (`bad_op bad_args not_found` or a `MemoryRejected` reason) | submitted, correlated with the `memory.write` command |
| `memory.repetition` | system | `intent`, `count` | note; the persistent `rep_<intent>` card (P2, from the 3rd same question today) links to it (INV-6) |
| `memory.repetition_refresh` | system | `cards` (count) | note; refreshed/cleared `rep_*` cards on `schedule.tick` link to it |
| `family.message_request` | agent | `contact_id`, `contact_name`, `kind` (`text\|voice_reply`), `text` (templated 「媽媽（王婆婆）託阿寶話你知：「…」」), `her_words`, `session_id`, `turn_id` | Kernel request from `send_message_to_family`; reply `{ok:true, contact}` / `{ok:false, reason}`. The Kernel decides and emits `family.send` (INFO) |
| `action.message_send` | action | `action_id`, `contact_id`, `contact_name`, `text`, `template: true` | submitted by the Action Runner's `telegram_message` method; the Kernel re-checks the allowlist and emits `family.send` (a component never sends to a third party itself, INV-1) |
| `family.send_result` | telegram | `command_id`, `ok`, `kind`, `status` (`sent failed`), `outbox_id?`, `contact_id?`, `attempts?`, `provider_id?`, `reason?`, `fake` | submitted by the Family Link, correlated with the `family.send` command |
| `phone.call_refused` | twilio | `reason` (INV-9 guard reason), `command_id`, `incident_id?` | note; a refused call writes **no** outbox row |
| `system.cap_exceeded` | system | `name` (cap name), `used`, `cap`, `date` | note (caps notifier, `trace/wiring.py`) |
| `device.error` | device | `device_id`, `code`, `detail` (≤ 500 chars) | submitted by the Device Hub for the iPad `error` frame |

### 4.2 Command
```json
{"id":"cmd_01J…","event_id":"evt_01J…","type":"device.play_fixed","target":"device","priority":0,
 "correlation_id":"inc_01J…","payload":{"clip_id":"F1","say_id":"say_inc_01J_F1"}}
```

| type | target | payload | dispatched to |
|---|---|---|---|
| `device.ui` | device | `ui` (face command) | Device Hub (+ operator mirror) |
| `device.play_fixed` | device | `clip_id`, `say_id` | Device Hub |
| `device.play_voice_message` | device | `message_id`, `url`, `say_id`, `from_name`, `text?`, `duration_s?` | Device Hub |
| `device.capture_photo` | device | `incident_id` | Device Hub |
| `device.session_open` / `device.session_close` | device | `session_id`, `reason`, `mode`, `text_only` | Device Hub |
| `device.wake_enable` | device | `on` | Device Hub |
| `device.config_update` / `device.stop_all` | device | §2.2 | Device Hub |
| `session.open` / `session.close` | agent | `session_id`, `reason`, `mode` | Session manager (text loop or LiveKit dispatch) |
| `agent.inject` | agent | `session_id?`, `kind` (`提醒 問候 背景結果 錄音 行動結果 家人`), `content`, `instruction`, `priority` | Arbiter → agent |
| `agent.mode` | agent | `mode` (`companion reminder kernel_listen reassure paused`), `session_id?` | agent |
| `agent.pause` / `agent.resume` | agent | `hard?`, `mode?` | agent |
| `family.send` | family | `kind` (`ALERT NOTIFY INFO DIGEST HANDOFF`), `contact_ids`, `text`, `photo_id?`, `buttons?`, `screenshots?` | Family Link (Telegram) |
| `phone.call` | phone | `contact_id`, `clip`, `incident_id` | Phone Link (Twilio) — **INV-9 guard** |
| `vision.analyze` | vision | `incident_id`, `photo_id` | Vision adapter (async, 8 s) |
| `action.run` | action | `action_id` | Action Runner |
| `deep.start` | deep | `task_id`, `goal`, `constraints`, `session_id?` | Deep Worker |
| `memory.write` | memory | `op`, … | Memory service |
| `memory.write` ops (added 11 Nov 2026, see BUILD_LOG) | memory | `op` ∈ `add_fact`/`remember` {`fact`, `category?`, `source?`} · `supersede_fact` {`old_id`, `fact`, `category?`, `source?`} · `confirm_fact` {`fact_id`, `accept?`} · `add_story` {`text`, `title?`, `photo_id?`, `era?`, `topic?`, `ok_to_reminisce?`} · `mark_story_used` {`story_id`} | Memory service → `memory.write_result` |
| `timer.set` / `timer.cancel` | kernel | `key`, `delay_s`, `data` | Kernel timers (fire `timer.fired`) |

## 5. Internal agent → api API (§26.4), `Authorization: Bearer <INTERNAL_TOKEN>`, Docker network only

Caddy never proxies `/internal/*`. In fake mode (text sessions) the same functions are called in-process by
`ahbo.agent.text_loop`; the HTTP routes are thin wrappers around `ahbo.agent.bridge.AgentBridge`.

| Method, path | Body | Result |
|---|---|---|
| `POST /internal/turn` | `{"op":"user","session_id","turn_id","text","stt_confidence":0.92,"final":true}` | `{"ok":true,"consumed":false,"mode":"companion","text":"<text with wake syllables stripped>"}` — `consumed:true` in `kernel_listen` (Flash must not reply) |
| | `{"op":"reply_start","session_id","turn_id","text","tone?":"family\|propose\|calm"}` | `{"ok":true}` — Kernel flushes mood + turn cards; in text sessions it also sends `say_start`/`say_end` |
| | `{"op":"reply_end","session_id","turn_id"}` | `{"ok":true}` |
| `POST /internal/ui` | `{"op":"steps_plan","session_id","turn_id","tools":["list_events","get_route"]}` | `{"ok":true}` — opens the turn's step group with the first planned step active, rest pending |
| | `{"op":"agent_state","session_id","state":"thinking\|speaking\|listening\|idle","user_speaking":false}` | `{"ok":true}` |
| `POST /internal/tool/{name}` | `{"session_id","turn_id","call_id","args":{…}}` | the tool's result (§6). Steps (`ui_steps.yaml`) and cards are emitted by the Kernel around the call. |
| `GET /internal/context/{session_id}` | | `{"ok":true,"mode":"companion","instructions":"<persona + blocks, ≤2.8k tokens>","blocks":{…},"pending_injections":[…]}` |
| `POST /internal/event` | `{"type":"session.closed\|agent.state\|quality.*","payload":{…}}` | `{"ok":true}` |
| `POST /internal/session/claim` | `{"room":"ahbo-sess_…"}` | `{"ok":true,"session_id","reason","mode"}` (LiveKit worker on dispatch) |
| `GET /internal/pull/{session_id}` | | long-poll (≤ 25 s): `{"items":[{"kind":"inject","instructions":"…"},{"kind":"mode","mode":"paused"},{"kind":"interrupt"}]}` — how Kernel `agent.*` commands reach the LiveKit worker |

## 6. Tools (§5.5 + §26.6). Every result has `ok`. Args validated with Pydantic; invalid → `{"ok":false,"reason":"bad_args"}`.

| Tool | Args | Result (on success) | Steps/cards |
|---|---|---|---|
| `get_time` | — | `{ok, date, weekday_words, time, time_words, period}` | none |
| `get_weather` | — | `{ok, now_words, temp_c, forecast_words, weather_short, source:"hko\|fake"}` | step |
| `get_med_status` | `scope: "next"\|"today"` (default next) | `{ok, next:{slot_id, time, time_words, rel_words, meds:[{id,yue,en,dose,pill}], count}\|null, today:[{slot_id,time,time_words,state,meds:[yue…]}], taken_today:[slot_id…]}` | step + `next_dose` + `med_*` cards |
| `confirm_taken` | `slot_id?`, `utterance` | `{ok:true, slot_id, status:"taken_self_reported"}` / `{ok:false, reason:"no_dose_pending"}` | none |
| `snooze_dose` | `minutes ≤ 15` | `{ok, slot_id, until}` / `{ok:false, reason:"no_dose_pending"\|"max_snoozes"}` | none |
| `list_events` | `date?` (`today` default, ISO date) | `{ok, events:[{id,time,time_words,title,place,kind,status}], count, count_words}` | step + `ev_*` cards |
| `add_event` | `datetime`, `title`, `place?`, `status: tentative\|confirmed` | `{ok, id}` | step + card |
| `get_day_plan` | — | `{ok, date, summary, plan:{…}\|null}` | step (+ `today` card) |
| `get_route` | `destination` | `{ok, destination, place_short, route_text, minutes?}` | step |
| `find_local_activities` | `interest?`, `when?` (`morning\|afternoon\|evening\|today`) | `{ok, activities:[{id,title,venue,venue_short,time,time_words,walk_minutes,signup:{method,url?}}], top_title}` | step + `act_*` card |
| `check_activity_logistics` | `activity_id` | `{ok, activity_id, free, conflicts:[…], walk_minutes, free_words, travel_words}` | step |
| `recall` | `query` | `{ok, facts:[{id,fact,category}], episodes:[{session_id,date,summary}]}` (top 5) | none |
| `remember` | `fact`, `category` | `{ok, id}` | none |
| `recall_life_story` | `topic?` | `{ok, story:{id,title,text,era,photo_url?}\|null}` | `story` card |
| `list_voice_messages` | — | `{ok, messages:[{id,from_name,received_words,duration_s,played}], has_new, from_name}` | step + `vn_*` card |
| `play_voice_message` | `id` | `{ok, id, started:true}` (playback is a device command; reply waits for `playback_done`) | card_update playing |
| `send_message_to_family` | `contact` (`阿珍`), `kind: text\|voice_reply`, `text?` | `{ok, contact}` — asks the Kernel via `family.message_request` (added 11 Nov 2026, see BUILD_LOG) | step |
| `deep_task` | `goal`, `constraints?` | `{ok:true, status:"started", task_id}` / `{ok:false, status:"busy"}` / `{ok:false, status:"busy", reason:"daily_cap"}` (added 11 Nov 2026, see BUILD_LOG): `caps.yaml deep_tasks` reached today | step (active until result) |
| `request_action` | `kind: signup\|message`, `target`, `details?` | `{ok, action_id, question}` | none |
| `confirm_action` | `action_id`, `utterance`, `stt_confidence?` | `{ok, status:"running"}` / `{ok:false, reason:"low_confidence"\|"not_proposed"\|"not_affirmative"}` — `not_affirmative` (added 11 Nov 2026, see BUILD_LOG): empty utterance or a negative word in it | step |
| `raise_concern` | `level`, `category`, `reason` | `{ok, level}` (level may be raised by the red-flag matcher, never lowered) | none |
| `get_incident_status` | — | `{ok, state, words}` (reassure mode only; otherwise `{ok:false, reason:"mode"}`) | none |
| `show_card` | `card` (kit card object) | `{ok}` | card |

Mode filter: in `kernel_listen` and `paused` every tool returns `{ok:false, reason:"mode"}`; in `reassure` only
`get_incident_status` and `get_time` are allowed.

## 7. Deep result JSON Schema (§24.8.3) — `brain/ahbo/deep/schema.py: DEEP_RESULT_SCHEMA`

```json
{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","additionalProperties":false,
 "required":["status","spoken_summary","card","sources","signup_methods","needs_confirmation"],
 "properties":{
  "task_id":{"type":"string"},
  "status":{"enum":["done","failed","timeout"]},
  "spoken_summary":{"type":"string","maxLength":120},
  "card":{"type":"object","additionalProperties":false,"required":["title","items"],
    "properties":{"title":{"type":"string"},"items":{"type":"array","maxItems":6,"items":{"type":"object",
      "additionalProperties":false,"required":["time","activity"],
      "properties":{"time":{"type":"string"},"activity":{"type":"string"},"place":{"type":"string"},"why":{"type":"string"}}}}}},
  "sources":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["title","url"],
    "properties":{"title":{"type":"string"},"url":{"type":"string"}}}},
  "signup_methods":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["method"],
    "properties":{"method":{"enum":["web_form","phone","in_person","telegram_contact"]},"url":{"type":"string"},
      "phone":{"type":"string"},"notes":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1},
      "activity_id":{"type":"string"}}}},
  "needs_confirmation":{"type":"array","items":{"type":"object","additionalProperties":false,
    "required":["action","args","question"],
    "properties":{"action":{"enum":["add_event","signup"]},"args":{"type":"object"},"question":{"type":"string"}}}}}}
```
Post-validation (code, not schema): `spoken_summary` passes the spoken filter (no digits/URLs/markdown, ≤ 2
sentences); URLs only in `sources` / `signup_methods`.

## 8. HTTP endpoints (§26.4)

| Method, path | Auth | Notes |
|---|---|---|
| `GET /healthz` | none | `{"ok":true,"version":"0.1.0","fake":["models",…],"profile":"demo"}` |
| `GET /login`, `POST /login` (form `password`), `POST /logout` | none → cookie | 5 attempts/min/IP; cookie `ahbo_session`, `HttpOnly; Secure; SameSite=Strict`, 12 h, signed with `SESSION_SECRET` |
| `GET /` | cookie | 302 → `/operator/` |
| `GET /operator/` (+ static), `GET /operator/stream`, `/operator/api/*` | cookie | §9 |
| `GET /sim/` (+ static) | cookie | browser iPad |
| `GET /face/*` | none | static kit (`dev.html` included; no data in it) |
| `WS /ws/device` | Bearer `APP_SECRET`, or cookie + `?sim=1` | §2 |
| `POST /session` | device token (`Authorization: Bearer dt.…`) or cookie | `{reason, mode}` → `{livekit_url, token, room, expires_in, session_id}`; fake LiveKit → `{text_only:true, session_id}` |
| `POST /media/photo` | device token or cookie | multipart `file`, `incident_id` → `{ok, photo_id}` |
| `GET /media/fixed/manifest.json`, `GET /media/fixed/{file}` | device token or cookie | |
| `GET /media/voice/{id}` | device token or cookie | AAC (`.m4a`) |
| `GET /media/photo/{id}`, `GET /media/screens/{file}` | cookie | console only |
| `GET /media/public/{random}/{file}` | unguessable | Twilio phone clip only |
| `POST /events/sensor` | `X-Beacon-Token` | `{beacon_id, type: fall_suspected\|sos\|heartbeat, ts, peak_g?, freefall_ms?, stillness_s?, battery?, rssi?, seq}` → `202 {"ok":true,"queued":true}`; idempotent on `(beacon_id, seq)` (`{"ok":true,"duplicate":true}`) |
| `POST /telegram/webhook` | `X-Telegram-Bot-Api-Secret-Token` | |
| `POST /twilio/twiml/{call_id}`, `POST /twilio/status`, `POST /twilio/gather` | Twilio signature (`X-Twilio-Signature`) | `GET /twilio/twiml/test` is a dry run (cookie) |
| `GET /mock-centre/`, `POST /mock-centre/api/register` | none | demo registration site, labelled 示範用 Demo |
| `/internal/*` | `INTERNAL_TOKEN` | §5; never proxied by Caddy |
| `POST /internal/reset` (added 11 Nov 2026, see BUILD_LOG) | `INTERNAL_TOKEN` | Reset demo (same as the console button); used by `make demo-fake` → `{ok, now, ms}` |
| `POST /internal/clock` (added 11 Nov 2026, see BUILD_LOG) | `INTERNAL_TOKEN` | body as `POST /operator/api/clock`; used by `ahbo clock` inside the api container → `{ok, now}` / 400 `bad_args` |

## 9. Operator API (cookie auth; JSON) — consumed by `operator-ui/`

| Method, path | Body → result |
|---|---|
| `GET /operator/api/state` | → snapshot: `{now, clock_offset_s, profile, fake:[…], incident:{id,kind,state,source,opened_at,timeline,vision_note}\|null, doses:[{slot_id,time,state,meds}], actions:[…], deep_tasks:[…], session:{id,mode,reason,text_only,agent_state}\|null, arbiter:{queue:[…]}, devices:[{device_id,online,last_seen,battery,wake_engine,ack_latency_ms,unacked_p0}], beacons:[…], spend:{llm_usd,deep_tasks,twilio_calls,playwright_actions,sessions}, caps:{…}, knobs:{flash_thinking,sonnet_thinking,interruption_enabled,muted}, ui:{mood,cards:[…],steps:[…],ok_button}}` |
| `GET /operator/stream` | SSE. Event names: `kernel_event` (Event), `command` (Command), `ui` (face command, for the live mirror), `turn` (`{role, text, session_id, turn_id, stt_confidence?, latency?}`), `state` (snapshot, on change, ≤ 2/s), `outbox` (outbox row), `health` |
| `POST /operator/api/trigger` | `{"kind":"fall"\|"sos"\|"dose_now"\|"checkin"\|"voice_note"\|"family_text"\|"plan_now"\|"reset"\|"wake"\|"utterance"\|"birth"\|"warmup", "slot_id?", "checkin?":"morning\|afternoon\|evening", "text?"}` → `{ok}` |
| `POST /operator/api/override` | `{"kind":"incident_ok"\|"incident_escalate"\|"incident_resolve"\|"force_tool_result"\|"play_fixed"\|"mute"\|"unmute"\|"say"\|"provider"\|"panic"\|"knobs"\|"interruption", …}` → `{ok}` (each becomes an `operator.*` event, INV-8) |
| `POST /operator/api/clock` | `{"set":"07:58"}` \| `{"offset_s":0}` \| `{"anchor":"S2"}` → `{ok, now}` |
| `POST /operator/api/scenario/{S1\|S2\|S3\|S4\|S4ok}` | → `{ok}`; resets, sets the anchor, then drives the scenario's inputs through the real paths |
| `GET /operator/api/outbox` | → `{items:[outbox rows]}` |
| `POST /operator/api/outbox/{id}/press` | `{"button":"handled"\|"call_mum"\|"resolved"\|"confirm"\|"press1"}` → `{ok}` |
| `GET /operator/api/trace?correlation_id=…&limit=…` | → `{events:[…], commands:[…], spans:[…]}` |
| `GET /operator/api/health` | → doctor table `{rows:[{item, status:"green"\|"red"\|"fake", detail}]}` |
| `GET /operator/api/fixed` | → `{clips:{id:{text,duration}}}` |
| `POST /operator/api/reset` (added 11 Nov 2026, see BUILD_LOG) | → `{ok, now, ms}`: Reset demo — reload the seed, re-anchor relative times, clear incidents/sessions/cards/queues/timers, face `reset` + mood `idle` + `wake.enable on` (≤ 5 s) |
| `POST /operator/api/panic` (added 11 Nov 2026, see BUILD_LOG) | → `{ok}`: Esc — `operator.panic` event, `device.stop_all` (P0), session closed with `why:"panic"`, face `reset` + mood `idle` |

## 10. SQLite schema (§14) — source: `brain/ahbo/core/db.py` (`SCHEMA_SQL`)

See the DDL in `brain/ahbo/core/db.py`; reproduced verbatim by `make contracts-ddl` into this section's
appendix (`docs/schema.sql`). WAL mode, `foreign_keys=ON`, single writer (the `api` process). Every Chinese
text column that is searched has an FTS5 table with `tokenize='trigram'` (`facts_fts`, `sessions_fts`,
`life_stories_fts`, `turns_fts`), kept in sync by triggers.

## 11. Config and seed formats

- `brain/config/*.yaml` hold exactly the §26.5 contents (+ `models.yaml` §20, `ui_steps.yaml` §23.6,
  `checkins.yaml`). Loaded by `ahbo.core.config.load_config()`; `{PUBLIC_BASE_URL}` is substituted at load.
- `brain/seed/demo.yaml` and `brain/seed/activities.yaml`: §26.6 (+ `venue_short` per activity, BUILD_LOG D-9).
  Loaded by `ahbo seed` / Reset demo. Relative times (`"09:30"`, `days_ago`) are anchored to the virtual clock's
  date at reset.
- `config/wake_cases.yaml`: `{accept:[…], reject:[…]}` fixture for both matchers (Python + Swift).
- Fixed clips manifest `media/fixed/manifest.json`:
  `{"version":"f-<sha1 of texts+voice>","clips":{"F1":{"file":"F1.m4a","text":"…","segments":[[0,2,0.0,0.4],…],"duration":2.6}}}`
  (segments: `[firstChar, endChar, startSec, endSec]`, UTF-16 offsets).

## 12. Adapter interfaces (`brain/ahbo/core/adapters.py`)

Chosen at start-up from `AHBO_FAKE` (`models, speech, livekit, telegram, twilio, vision`; `all`; `none`).
Real and fake implement the same Protocol; the Kernel cannot tell them apart.

`FlashLLM.chat(messages, tools, purpose, thinking?) → FlashResponse{text, tool_calls, usage}` ·
`DeepRunner.run(task_id, goal, constraints, context) → dict (§7)` · `Vision.describe(photo_path) → str|None` ·
`FixedTTS.render(text, out_path) → (duration, segments)` · `FamilyChannel.send(kind, chat_ids, text, photo?,
buttons?) → result` · `PhoneChannel.call(to_e164, clip_url, call_id) → result` · `SessionLauncher.open(session_id,
reason, mode) → {livekit_url, token, room}|{text_only:true}` · `BrowserRunner.run(action) → result`.

## 13. Golden UI trace comparison (§23.8) — `brain/tests/test_golden_ui.py`

Inputs: the kit traces `brain/tests/golden/kit/*.json` (captured from `face/dev.html` by
`evals/visual/kit_capture.py`) and the commands the Device Hub sends during the same scenario in fake mode.

Normalisation (both sides), in order:
1. Server side only: `play_fixed {clip}` → `say_start {from:"ahbo"}` + `say_end`; `play_voice_message` →
   `say_start {from:"family", fromName}` + `say_end`.
2. Drop `heard`, `level`, `expr`, `reset`, `say_update`, `birth`, `offline`.
3. Ignore all ids, timing fields (`durationSec`, `segments`, `t_ms`) and card `when` (it only times the reveal).
4. Steps: a `steps` group becomes its title + `[(label, state)]`; a `step` becomes `(group index, step index,
   state, effective label)` where the effective label is the command's `label`, else the group's `doneLabel`
   when `done`, else the step's label. Groups/steps are indexed by first appearance.
5. Cards: `(kind, label, small, icon or the kit's default icon for the kind, persistent, pill)`.
6. `say_start` → `("say", from)`; text is not compared (the PRD's F5 wording wins over the kit's).
Pass: the Kernel sequence, truncated to the kit sequence's length, equals the kit sequence. Extra commands
after the kit's last command are allowed (e.g. REASSURING's `mood warm` after F4) and listed in the report.

## 14. Additions after the freeze (added 11 Nov 2026, see BUILD_LOG)

Everything here was added after components were built; each item is also marked in the section it extends.
Source code named in brackets is authoritative.

### 14.1 Incident cards and step groups (`kernel/reducers/fall.py`)
- The persistent `incident` card's `label` / `small` per incident kind:
  `fall` 「偵測到跌倒」/「感應器：急速下跌」 · `sos` **「求救掣」/「婆婆撳咗求救掣」** · `concern` (emergency-level
  `agent.concern`) **「緊急關注」/「婆婆話身體唔舒服」**. Incidents triggered from the console (`source: operator`)
  use `small` 「操作員觸發」.
- The incident step group keeps `id:"fall"` and steps `photo, ask, call, tell` for every kind (`photo` is dropped
  when the incident starts directly at AWAITING_OK); its `title` is
  「跌倒處理」 for `fall` and `sos`, and **「緊急處理」** for `concern`.

### 14.2 Scheduler jump rule (`kernel/scheduler.py`)
A scheduled item (dose, check-in, leave-soon, digest, 07:00 plan) fires only when a `schedule.tick` crosses its
due time naturally: previous tick < due ≤ now **and** the virtual clock moved ≤ 120 s since the previous tick.
Items inside a larger jump (the operator setting the clock) are skipped, never fired in a burst; nothing fires on
the first tick after start. Use the console's `dose_now` / `checkin_now` / `plan_now` instead.

### 14.3 kv keys for the public phone clip (`ops/render_fixed.py`, `links/twilio.py`)
- `public_clip_rand`: the random directory name under `data/media/public/` (a lowercase ULID).
- `public_clip_path`: the URL path `/media/public/<rand>/P1.m4a` that TwiML plays (prefixed with `PUBLIC_BASE_URL`).
Both components read and write the same format; the renderer reuses `public_clip_rand` while its directory exists.

### 14.4 Fixed-clip manifest extras (`ops/render_fixed.py`)
The manifest (§11) also lists **`clips.P1`** (the phone clip; rendered into `media/fixed/P1.m4a`, served at
`/media/fixed/P1.m4a` and copied to the public dir) and a top-level **`"public"`** object (random dir, duration,
segments) for the console. No Kernel code sends `play_fixed {clip_id:"P1"}`. While nothing is rendered yet the
platform writes a text-only manifest with `"fallback": true` (doctor shows it red).

### 14.5 `mock_registrations` table (`actions/mock_centre.py`)
```sql
CREATE TABLE IF NOT EXISTS mock_registrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT UNIQUE, ts TEXT NOT NULL,
  name_zh TEXT NOT NULL, name_en TEXT, age_group TEXT NOT NULL, phone TEXT NOT NULL,
  activity_id TEXT NOT NULL, session TEXT NOT NULL);
```
Created at install time by `actions/wiring.py`, **not** in `core/db.py` and **not** in the Reset-demo tables (demo
site data, not care data). References are `DEMO-%04d`. `GET /mock-centre/api/activities` (no auth) serves the
centre's activities from `seed/activities.yaml`; `POST /mock-centre/api/register` refuses the `hkid`, `captcha`,
`login` and `payment` variants with 400.

### 14.6 Action status screenshots
`action.status.screenshots` carries absolute paths `data/media/actions/<action_id>/{pre_submit,post_submit}.png`;
the Family Link accepts them as they are.

## 14. Additions after the freeze (owner decisions 11 Nov; BUILD_LOG §7–8)

- **Telegram is the caregiver + admin interface**; the web operator console (`operator-ui/`) is removed. `GET /`
  redirects to `/sim/`. The operator HTTP API (§9) remains for the scenario runner, `/sim/` and tests.
- Events: `settings.changed {what, before, after, by}` (source `telegram`; phone numbers masked) and the note-only
  `operator.telegram {op, args, by}` for every Telegram demo control. Telegram fall/SOS keep `source:"operator"`.
- Alert buttons: `handled` · `voice_mum` (「錄段聲俾媽媽」, replaces `call_mum`; old `call_mum` data maps to it) ·
  `resolved`. `press` (§9) accepts `voice_mum`.
- kv keys: `settings_overlay` (quiet_start, quiet_end, morning, afternoon, evening, digest; defaults from
  `checkins.yaml`, read by `kernel/common.quiet_hours` and `kernel/scheduler.daily_items`), `bot_convo:<chat_id>`
  (multi-step edit state, 10 min), `fake_vision_note` (fake-mode camera result per scenario).
- Fall escalation calls the caregiver (role `family`) only; see BUILD_LOG §8 for the camera rule.
