# QA 21: Care Kernel and safety flows

Read `20_QA_COMMON.md` first. Report: `docs/handoff/glm/21_REPORT.md`. Evidence: `docs/qa/21/`.

**Spec:** PRD §2 (INV-1…10), §4.3.1–4.3.5, §6, §13.3, §24.9; BUILD_LOG §8 (owner's fall rules) and §9.
**Code to read:** `brain/ahbo/kernel/**` (reducers `fall`, `dose`, `concern`, `checkin`, `action`, `family`, `arbiter`,
`session`, `turn`, `voice`, `misc`; `matchers.py`, `scheduler.py`, `common.py`), `brain/config/{ok_phrases,redflags,
checkins,caps}.yaml`, existing tests `brain/tests/{kernel,invariants}/`, `brain/tests/test_golden_ui.py`.

## Check (use the `harness` fixture; see the common doc)
1. **Fall, owner rules (BUILD_LOG §8):** 「我冇事」 + camera fine → Telegram INFO only, no `phone.call`, no photo in the
   message; 「我冇事」 + camera shows a fall → ALERT + call 阿珍; no answer → ALERT + call 阿珍 (never `c_ecs`, never more
   than 3 calls); help word (救命, 好痛, 起唔到身…) → escalate immediately; 「冇跌親」 escalates (help wins) — confirm that
   is what happens and judge if it's acceptable; touch button from the device resolves; the same from `source:"agent"`
   must NOT resolve; vision note arriving after 「我冇事」 (pending decision path, ≤ 6 s) — both outcomes; repeat fall
   during an open incident attaches, doesn't open a second one; 60 s cooldown; restart in the middle (save state, build a
   new Kernel on the same DB, call `kernel.load()`) — what does she see and hear?
2. **Matchers:** Simplified/formal/English variants (我没事, 我沒事, 沒事, 我無事, ok, OK啦, 冇事呀), traps (有冇事, 冇食, 唔係冇事),
   help-wins (「我冇事，不過好痛」). Report every input that gives the wrong answer.
3. **Medicine (INV-4):** every dose state (scheduled, reminding, snoozed, taken, missed) × questions 「我幾時食藥呀？」「我食咗藥未呀？」
   「我食咗喇」; the double-dose guard; `confirm_taken` outside a pending window must refuse; snooze limits; MISSED at T+60
   → INFO to 阿珍; reminders at the right virtual times after `clock.set_time`.
4. **Concern:** each red flag in `redflags.yaml` (emergency/urgent/scam) raises the level, never lowers it; emergency
   enters the fall flow at AWAITING_OK.
5. **Arbiter (§6):** P0 interrupts a reply; P2 voice note waits for an idle gap; quiet hours (22:30–07:00) allow only P0 and
   P1 doses; one pending item per class.
6. **Check-ins:** morning/afternoon/evening fire at the configured times; skipped within 20 min of a session; never in quiet
   hours; caregiver overlay (`ahbo.settings_store`) changes the time.
7. **Sign-up action (§4.3.5):** propose → yes with low `stt_confidence` (<0.6) asks again; 「唔好」 is not a yes;
   DONE / NEEDS_HUMAN / FAILED each notify 阿珍.
8. Run the existing suites once and report the counts: `tests/kernel tests/invariants tests/test_golden_ui.py`.
