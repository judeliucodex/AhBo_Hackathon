# QA 23: Telegram caregiver + admin bot (functional, offline)

Read `20_QA_COMMON.md` first. Report: `docs/handoff/glm/23_REPORT.md`. Evidence: `docs/qa/23/`.
**Never contact the real Telegram API and never touch the live bot.** Drive the bot through
`ahbo.links.telegram.handle_update(svc, update)` with fake Telegram update dicts on your throwaway server or in a pytest
probe (see `brain/tests/bot/` and `brain/tests/links/` for how they build fake updates; the fake channel records replies).

**Spec:** BUILD_LOG §8 (Telegram replaces the web console), §9 (digest/plan removed), CONTRACTS §14, PRD §9.2, §2 INV-7,
INV-8, INV-9. **Code:** `brain/ahbo/links/{telegram,family,outbox,wiring}.py`, `brain/ahbo/links/bot/**`,
`brain/ahbo/settings_store.py`.

## Check (as 阿珍 = the allowlisted chat, and as a stranger)
1. **Every command and menu path** from `/start` / `/menu` (今日狀況 · 藥物 · 日程 · 設定 · 聯絡人 · 示範控制 · 系統健康) and every
   typed command in `/help`: it answers, in Traditional Cantonese, with sensible buttons; `/plan` and digest settings are gone.
2. **Edits with 確認 / 取消:** medicine catalogue and dose slots (add/change/pause/delete), calendar events, quiet hours,
   check-in times, contacts. After 確認 the database changed exactly as described; after 取消 nothing changed; half-finished
   forms expire after 10 min; typing a normal message mid-form; restart mid-form (new server on the same data dir).
3. **Persistence + effect:** a new check-in time actually moves when `checkin.due` fires; a medicine change re-renders the
   reminder clip and pushes `config.update` with the new `reminder_mirror`; quiet hours reach the device config.
4. **Phone numbers (INV-9, S1 if broken):** try 999, 112, 911, +852 999, (999), ９９９, 00852 999, 12345999, 8-digit HK
   numbers, numbers ending in 999, +1 numbers, letters. Nothing an emergency number can ever be stored or called;
   `verified` only after 確認; numbers shown masked.
5. **Strangers (INV-7, S1 if broken):** another chat id gets only 「呢個係私人家庭機械人。」 for messages, commands, menu
   buttons (`m:` callback data), alert buttons and voice notes; `/whoami` returns only their own id; forged callback data
   with 阿珍's outbox id from a stranger does nothing.
6. **Alert buttons:** 我處理緊 acknowledges an escalated fall; 已解決 closes it; 錄段聲俾媽媽 explains how to send a voice
   message; stale `call_mum` buttons still behave.
7. **Demo controls:** /fall and /sos produce `source:"operator"` (face card 操作員觸發), /reset, /panic, /scenario S1–S4ok,
   /clock, /mute, /dose, /checkin, /say (labelled operator), /thinking — each recorded as an event.
8. Voice note from 阿珍 (fake file) → stored, announced to 媽媽 (A1) when idle, playable; from a stranger → dropped.
