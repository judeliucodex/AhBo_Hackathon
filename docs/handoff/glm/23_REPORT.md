# QA 23 report — Telegram caregiver + admin bot (functional, offline)

## Summary

- **51 probe tests passed, 0 failed** across 6 probe files (`docs/qa/23/probes/`, evidence logs in `docs/qa/23/`).
- **1 defect found: S3** (stale `/help` text advertising the removed digest setting). No S1/S2 defects.
- **Overall verdict: usable.** All 8 checks from `23_qa_telegram_bot.md` were exercised end-to-end through
  `ahbo.links.telegram.handle_update(svc, update)` with fake Telegram updates, against the real services
  (Kernel, links wiring, Device Hub, settings store) built in fake mode. INV-7 and INV-9 hold under everything
  I threw at them.

## Defects

### D1 [S3] `/help` still advertises the removed daily digest (摘要)
- Where: `brain/ahbo/links/bot/ui.py:65` (`HELP`: `/settings 設定（靜音時段、問候時間、摘要）`)
- Spec: BUILD_LOG §9 (owner decision 12 Nov 2026): the 20:00 digest is removed; Telegram loses `/plan`, the
  「即刻做 07:00 計劃」 button **and the digest time setting**. The settings view itself is correct (no digest row),
  but `/help` still tells 阿珍 the settings include 摘要.
- Repro: send `/help` to the bot (or `t1_commands.py::test_help_text_must_not_advertise_removed_digest`).
- Actual: the reply contains `/settings 設定（靜音時段、問候時間、摘要）`; there is no digest setting anywhere in
  `/settings`, and nothing happens if she looks for one. Cosmetic, but it invites 阿珍 to hunt for a control that
  no longer exists. Evidence: `docs/qa/23/t1_commands.log` (HELP TEXT block).

## Passed checks

**1. Commands and menus** (`t1_commands.log`)
- `/start` and `/menu` both show the main menu with all 7 items (今日狀況 · 藥物 · 日程 · 設定 · 聯絡人 · 示範控制 · 系統健康).
- All 7 menu views and **all 39 second-level buttons** answer; every view offers a ⬅️ 主選單 way back; no dead ends.
- All 25 commands listed in `/help` answer (each either a view, a form, a 確認 prompt, or a usage message).
- Every reply is Traditional Cantonese — checked every reply with OpenCC s2t (no Simplified-only characters).
- `/plan` answers 「唔知呢個指令」; the demo menu has no plan button; the settings view has no digest row (D1 above
  is only the help text).

**2. Edits with 確認／取消** (`t2_edits.log`, 16 tests)
- Medicine catalogue: add (summary 「加藥物：胃藥（Antacid）500 mg，藥丸：白／粉紅」 → DB row matches field by
  field, incl. pill tokens), edit (「之前：/之後：」 summary; `settings.changed` carries correct before/after),
  delete (unused only — a medicine still in a slot is refused).
- Dose slots: add (clash on an existing time refused; empty slot refused), pause → `active=0`, resume → 1, delete.
- Calendar: add with date button + typed time/title, skipped optional place/route → row `source=telegram`,
  `status=confirmed`; 取消約會 → `cancelled`; declining the 確認 leaves the row byte-identical.
- Quiet hours (typed `9點半` parses to 09:30; overlay kv + resident mirror) and check-in times; identical
  quiet start/end refused.
- Contacts: E.164 normalisation, `verified=1` only in fake mode after 確認, deleting yourself refused (lockout guard).
- After 取消 — by button or typed 取消 — nothing is written and no `settings.changed` event is recorded.
- Half-finished form expires after 10 min (real-time TTL): later input is no longer consumed, is relayed to 婆婆 as
  `family.text`, and a form button answers 「表格已經過期（十分鐘冇郁）」.
- A normal message typed mid-form is consumed by the form and never relayed; a non-answer while a 確認 is pending
  prompts 「請撳「確認」或者「取消」」 and changes nothing; typed 確認/取消 both work.
- **Restart mid-form**: quiet-hours form half-done → process stopped → new `build_services` on the same data dir →
  the form continues and 確認 applies; a pending 確認 button also survives the restart.

**3. Persistence + effect** (`t3_effects.log`, 4 tests)
- Control run: seeded morning check-in fires `checkin.due` at 08:30 through the real `Scheduler.tick` loop.
  After 阿珍 moves morning to 09:10 (before it fires), nothing fires at 08:30 and `checkin.due {kind: morning}`
  fires at exactly 09:10. The overlay survives a restart and fires at the moved time after it.
- Medicine change: adding 胃藥 + a 16:30 slot re-renders the fixed clips for real (`R_s_1630` appears in
  `media/fixed/manifest.json`) and pushes `device.config_update` whose `reminder_mirror` contains the new slot
  with 「胃藥」 plus `fixed_cache_version`. Read-only views push no config.
- Quiet hours: `/quiet 21:00 08:00` → `device.config_update {quiet_hours}` + `resident.quiet_hours` mirror +
  `kernel.common.quiet_hours()` true at 07:30 / false at 15:00 + a check-in during quiet hours records
  `checkin.skipped {reason: quiet_hours}`.

**4. Phone numbers (INV-9)** (`t4_phones.log`, 4 tests)
- End-to-end through the contact form: `999`, `112`, `911`, `+852 999`, `(999)`, `９９９` (full-width), `00852 999`,
  `12345999`, `9999 9999`→stored (see note), `9123456` (7 digits), `abc`, `打俾警察啦` — every emergency/bad form
  is refused with a Cantonese reason and nothing is stored; genuine numbers store as E.164
  (`9123 4567`→`+85291234567`, `91235999`→`+85291235999`, `+1 416 555 0123`→`+14165550123`).
- Second line of defence: hand-seeded `contacts` rows holding `999`, `+852 999`, `911`, `112`, full-width
  `８５２９９９` and short-code `24999` (all `verified=1`) — a real `phone.call` command through the Kernel
  dispatcher refuses each one (`phone.call_refused`), **writes no outbox row**, and none of them is
  `callable_contact` or in `call_order` (the fall flow can never pick them). 阿珍's own verified contact remains
  callable (control).
- `verified` only after 確認: no row exists at all before 確認; after it, `verified=1` (fake Twilio).
- Numbers shown masked everywhere: form summary, `/contacts`, contact detail — only `••••4567` forms appear;
  `settings.changed` payloads carry the masked form, never the number.

**5. Strangers (INV-7)** (`t5_strangers_alerts.log`, 5 tests)
- 33 texts (every command incl. `/fall`, `/reset`, `/scenario S4`, 確認, 取消, Cantonese, English, 「幫我打999」)
  and 19 buttons (`m:menu`, `m:v:*`, `m:f:*`, `m:a:*`, `m:ok:*`, forged `b:handled/resolved/voice_mum/confirm`)
  from another chat id → exactly one fixed reply 「呢個係私人家庭機械人。」 per message (buttons: callback answer
  only, no message); `/whoami` returns only the stranger's own id.
- Zero trace: no events recorded, no `voice_messages` row, no `bot_convo` state, 阿珍's contact untouched.
- Forged presses on 阿珍's **real** ALERT outbox id (`b:handled:<real oid>` etc.) and her **real** pending-確認
  nonce → no `family.button` event, the incident is never acknowledged/closed by the stranger, 阿珍's pending
  change does not fire. (The incident may advance ESCALATING→REASSURING on its own — that is the autonomous flow.)

**6. Alert buttons** (`t5_strangers_alerts.log`)
- A confirmed `/fall` escalates; 阿珍's ALERT carries exactly 我處理緊 · 錄段聲俾媽媽 · 已解決.
- 我處理緊 → incident `ACKNOWLEDGED`; 已解決 → `CLOSED`; each press submits `family.button {via: telegram}` with
  the right `outbox_id`.
- 錄段聲俾媽媽 (and a stale `call_mum` from an old message) → the 「而家可以直接錄一段語音訊息俾媽媽…」 explanation,
  mapped to `button=voice_mum`, and adds **no** Twilio call.

**7. Demo controls** (`t6_demo_voice.log`, 8 tests)
- `/fall` and `/sos`: nothing happens before 確認; after it, `sensor.fall_suspected` / `sensor.sos_pressed` with
  `source:"operator"`, incident `source=operator`, and the face card says 「操作員觸發」; every control also records
  the `operator.telegram {op, args, by: c_chun, via: telegram}` note (CONTRACTS §14).
- `/reset`, `/panic`, `/mute`, `/unmute`, `/dose 12:00`, `/checkin morning`, `/clock 07:58|+30`, `/thinking flash
  high` — each asks 確認 first, then records the note + the matching `operator.*` event; knobs and the virtual
  clock really change.
- `/say 王婆婆，飲啖水先`: proposes with 「（會標明操作員）」, confirms with 「阿寶講咗（標明操作員）」, records
  `operator.say`, and the line reaches the device.
- `/scenario S1|S2|S3|S4|S4ok`: each starts from Telegram with an `operator.telegram op=scen` note and a running
  scenario task; S1 runs to completion end-to-end.
- Note (by design, not a defect): while an incident is open, a second trigger (`/sos` during a fall) attaches to
  the open incident instead of opening a parallel one (`fall.start` docstring: "Open an incident (or attach to
  the open one)").

**8. Voice notes** (`t6_demo_voice.log`)
- From 阿珍 through the webhook handler: stored as a real AAC `.m4a` (genuine OGG/Opus input converted by ffmpeg,
  1.5 s duration), `voice_messages` row with `direction=in`, `contact_id=c_chun`, `to_name=王婆婆`; file on disk
  (playable); `family.voice_note` reaches the Kernel; when idle the arbiter announces with **clip A1**
  (「王婆婆，阿珍留咗段錄音俾你。」, `device.play_fixed {clip_id: A1}`). Transcript is `None` — correct in fake
  mode (speech faked; a wrong transcript would be worse, per the code's own rule).
- From a stranger: one fixed reply, nothing stored, no Kernel event, and `channel.download` is never even called.

## Observations (behaviour verified as designed; for the record, not defects)
1. **`9999 9999` is accepted and stored as `+852 9999 9999`.** That is a genuine 8-digit HK local number, not a
   formatting of the 999 emergency line: the short-code rule (999 as a tail with ≤ 5 extra digits) is judged
   *before* +852 is prepended (documented in FULL_GUIDE §5a and `bot/phones.py`). Every bare 999/112/911
   formatting is still refused, and the dial-guard layer refuses them independently. No action needed; noting it
   because the task asked to try it.
2. **A check-in moved to a later time after it already fired today does not fire again today** (the scheduler's
   once-per-day `fired` set). Moving it *before* its time works as spec'd (verified). Reasonable, but if 阿珍
   moves a missed check-in to "right now +5 min" she may expect it to fire — worth a line in the bot's reply some
   day (S3 idea, not filed as a defect).
3. **Leftover digest plumbing with no user-facing surface**: `settings_store.KEYS` still accepts a `digest`
   override and `views.CHECKIN_NAMES` still maps `digest`→每日摘要, but no bot path can set it and the scheduler
   no longer emits `digest.due` (BUILD_LOG §9). Harmless; will confuse the next reader.

## Not tested / blocked
- **Real Telegram delivery** (webhook HTTP round-trip, `sendMessage`/`answerCallbackQuery` to the real API): hard
  rule — never contact Telegram. The webhook route's secret handling is covered by the repo's own
  `tests/links/test_telegram.py`; everything above drives `handle_update` directly, as the task allowed.
- **Real Twilio caller-ID verification** (`phones.twilio_verified`): only the fake path (confirm ⇒ verified) was
  exercised; the `httpx.MockTransport` cases are covered by repo tests `tests/bot/test_bot_phones.py`.
- **Voice-note transcription**: `speech` is faked, so ElevenLabs STT never ran (transcript stays `None` by design).
- **`/story` with a photo** (`_save_story_photo` needs a real `getFile` download): only the no-photo path was
  exercised; the download path depends on the real channel.
- Live performance (Bot API rate limits, webhook latency) — out of scope offline.
