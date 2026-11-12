# QA 21 report — Care Kernel and safety flows

## Summary
132 new probe tests written in `/tmp/ahbo-qa-21/` (copies in `docs/qa/21/probes/`), all run in throwaway
`ahbo-api` containers against the read-only repo (`AHBO_FAKE=all`, harness fixture, FakeClock, no real services,
live stack untouched). Existing suites: `tests/kernel` 81 passed · `tests/invariants` 72 passed (with a
writable mount; 71+1 env-failure under the documented read-only recipe, see D4) · `tests/test_golden_ui.py` 5 passed.

**Counts: 1 × S1 · 3 × S3 · 0 × S2.**
**Overall verdict: usable with issues.** The fall/dose/concern/check-in/action state machines behave as specified
across ~130 probes, including restart recovery and the owner's 11 Nov camera rule. The one S1 is a deterministic
matcher hole that can end a fall incident on a negated OK (「唔OK」/“not ok”) — it needs the two-line
negation guard in `matchers.is_ok` extended to OK-word negations.

## Defects

### D1 [S1] A negated OK (「唔OK」, "not ok") resolves a fall incident as "she's fine"
- Where: `brain/ahbo/kernel/matchers.py:65-85` (`is_ok` strips Chinese negation contexts `NOT_OK_CONTEXTS` but has
  no guard for negations of the OK *words* themselves); phrase source `brain/config/ok_phrases.yaml:4`
  (`"OK"`, `"ok"`, `"冇問題"` matched as bare substrings).
- Spec: PRD §4.3.1 — the OK-matcher is deterministic and may resolve AWAITING_OK only when she actually says she's
  fine; a wrong OK is the dangerous direction (same reasoning as BUILD_LOG §10's keyterms rule: "anything that could
  read an OK from a non-OK is the dangerous direction"). BUILD_LOG §10 explicitly notes STT once heard 我冇事 → 我冇食,
  i.e. mixed/odd transcripts are a real case.
- Repro (runs anywhere the repo is mounted at /repo):
  ```bash
  docker run --rm -v /opt/ahbo:/repo:ro -v /tmp/ahbo-qa-21:/scratch ahbo-api python -m pytest -q -p no:cacheprovider \
    --rootdir=/repo/brain -c /repo/brain/pyproject.toml /scratch/test_qa21_matchers.py -k negated_ok_resolves -s
  ```
  (probe copy: `docs/qa/21/probes/test_qa21_matchers.py`) — AWAITING_OK, camera verdict `fine`, she says 「唔OK」.
- Actual: `match_ok("唔OK") == "ok"`; the incident goes RESOLVED_OK `ok_how: voice`, an INFO goes to 阿珍 and
  **no call is placed** — probe output in `docs/qa/21/matchers_out.txt`:
  `D1-REPRO 唔OK → RESOLVED_OK why: voice | family.send kinds: ['INFO'] | phone.call: 0`.
  Same wrong answer for: 唔ok, 我唔係OK, 唔係ok, `not ok`, `I am not ok`, 冇事個頭. 唔OK is ordinary HK Cantonese for
  "not okay", so this is a plausible utterance, not a contrived one.
- Mitigations already present (why it is not worse): a help word anywhere in the utterance still wins
  (「唔OK，我好痛」 escalates, verified); a `problem` camera verdict re-escalates after an OK (verified); the operator
  can escalate. With camera `fine`/`unclear` there is no second line of defence.
- Suggested fix direction (for the fixer, not applied): extend the negation guard — e.g. drop an OK match when the
  OK phrase is directly preceded by 唔/唔係/唔好/未 or when the utterance contains `not`/`never` right before an
  OK word (mirror of `NOT_OK_CONTEXTS`, plus keeping the help-wins rule).

### D2 [S3] One urgent utterance sends two identical INFO messages to 阿珍
- Where: `brain/ahbo/kernel/reducers/concern.py:57-61` — the 10-minute repeat guard keys on `category`; the
  deterministic matcher files its concern under `health` (`brain/ahbo/kernel/matchers.py:97`) while the agent's
  `raise_concern` call for the same utterance arrives with its own category (`symptom` in fake mode; the real Flash
  will equally choose its own string). Each path sends its own `family.send`.
- Spec: PRD §4.3.4 / concern.py docstring — "urgent → family.send INFO now … once per category per 10 min"; alert
  fatigue is exactly what this rule exists to prevent.
- Repro: `docs/qa/21/probes/test_qa21_concern.py::test_urgent_repeat_suppression` (needs `-s`);
  output in `docs/qa/21/concern_out.txt`: `D-REPEAT first utterance sends: 2`, both
  「王婆婆話身體唔舒服：「阿寶，我發燒」。請盡快打電話關心下佢。」. The guard itself works: a second urgent
  utterance within 10 min adds nothing (verified for both categories), and after 11 min it may send again.
- Fix direction: dedup on the utterance/level (or a canonical category), or suppress the matcher send when an
  agent concern for the same event already fired (or vice versa).

### D3 [S3] Quiet-hours rule is bypassed on the P1 max-wait timer path
- Where: `brain/ahbo/kernel/arbiter.py:242-248` — the `arb:p1:` timer branch delivers a queued P1 item after 30 s,
  checking only `incident_active` and `muted`; the normal paths (`_decide`, `pump`) do enforce
  `quiet_hours → P1 non-dose dropped` (`arbiter.py:73-77`, verified).
- Spec: PRD §6 — "Quiet hours: only P0 and P1 doses."
- Repro: `docs/qa/21/probes/test_qa21_arbiter.py::test_p1_maxwait_timer_vs_quiet_hours`;
  output in `docs/qa/21/arbiter_out.txt`: a P1 non-dose (家人 message) queued at 22:00 while a session is busy;
  clock → 23:30; the max-wait timer delivers it (`('家人', 'now', 'max_wait')`) and `agent.inject` fires at 23:30.
  Narrow window (needs a queued P1 non-dose plus a ≥30 s busy stretch across 22:30), hence S3.
- Fix direction: repeat the quiet-hours check (dose items exempt) in the `arb:p1:` branch, and let `pump`/tick
  re-evaluate instead of blind delivery.

### D4 [S3] INV-9 invariant test cannot pass under the documented read-only test recipe
- Where: `brain/ahbo/links/twilio.py:293-309` (`_ensure_clip_file` must write `data/media/public/<rand>/P1.m4a`
  before the outbox row is recorded) vs the recipe in `docs/handoff/glm/20_QA_COMMON.md` §3 (`-v /opt/ahbo:/repo:ro`).
- Spec/impact: `tests/invariants/test_inv_09_never_999.py::test_fall_calls_only_verified_contacts` fails with
  `OSError(30, 'Read-only file system')` under the documented recipe (`docs/qa/21/suite_invariants_ro.txt`);
  with a writable mount the suite is 72/72 (`docs/qa/21/suites_summary.txt`). The dial guard itself is fine —
  this is a test-environment conflict, but it will confuse every future QA run that follows the doc verbatim.
- Side-effect worth knowing: on writable runs each EvalSystem leaks one orphaned
  `data/media/public/<48-hex>/P1.m4a` into the live data dir (the rand lives only in the test's in-memory kv).
  The dir is created by `ahbo-api`'s own fake, in the repo's data dir; it is not git-tracked and the live stack
  ignores it. I removed the dirs my runs created; hundreds of identical dirs from earlier runs already existed.

## Passed checks
All probes below are in `docs/qa/21/probes/`, outputs in `docs/qa/21/*_out.txt`.
1. **Fall owner rules (BUILD_LOG §8 / PRD §4.3.1)** — 16 probes: 「我冇事」+camera fine → INFO only, no
   `phone.call`, no photo in the message, `buttons:["voice_mum"]` · 「我冇事」+camera problem → ALERT + call 阿珍,
   never `c_ecs` · no-answer → ALERT + call 阿珍, exactly 3 calls max across repeated T_ack expiries, never the
   centre · 救命/好痛/起唔到身/跌親 escalate immediately (`why: help`) · 「冇跌親」 escalates (help wins) ·
   `device.touch_ok` from `source:"device"` resolves, from `source:"agent"` does nothing · vision note after OK:
   problem within the ≤6 s decide window escalates (`why: camera`), fine cancels and sends INFO, never arriving
   sends INFO after the decide timer; a problem note after the window does not reopen · repeat fall during an open
   incident attaches to it · 60 s cooldown after a CLOSED incident, new incident after 61 s · escalation
   family buttons: `handled` → ACKNOWLEDGED + NOTIFY, `resolved` → CLOSED.
2. **Restart in the middle** — `test_qa21_restart.py` on a file DB, new Kernel + `kernel.load()`:
   during AWAITING_OK she sees the ok button and a fresh 5 s countdown, the `kernel_listen` session is reopened,
   no clip is replayed, and her touch still resolves (INFO after); during ESCALATING nothing new plays, the T_ack
   timer re-arms and the next call to 阿珍 is still placed, ≤3 total.
3. **Matchers** — 56 probes: all OK variants (我冇事/我没事/我沒事/沒事/我無事/ok/OK啦/冇事呀/冇問題/沒問題/我好好)
   match OK · traps give no answer (有冇事？/你有沒有事/唔係冇事/點會冇事/冇食/我未食藥) · help forms incl.
   起不來/摔倒/救護車/頭暈 and mixed 「我冇事，不過好痛」 → help · in-flow decisions agree with the matcher
   (OK resolves, help escalates, trap leaves AWAITING_OK untouched). The only wrong answers are D1's negated-OK
   forms. Judgement on 「冇跌親」→escalate: **acceptable** — it is the explicit owner decision (D-16, safety bias),
   and the camera can never remove a call, only add.
4. **Medicine (INV-4)** — 11 probes over scheduled/reminding/snoozed/taken/missed × 「我幾時食藥呀？」「我食咗藥未呀？」
   「我食咗喇」: questions answered from `get_med_status` via the Kernel's dose state · reminder delivery (cards,
   R_s_1200 clip, inject, `reminder` session) · confirm while reminding records `taken_self_reported` +
   utterance + source · second claim refused (`no_dose_pending`, double-dose guard) · confirm for scheduled /
   already-taken / not-due-yet doses refused · voice confirm for a SCHEDULED slot refused · snooze clamped to
   15 min, max 2, expiry re-prompts · re-prompts at T+15/T+30 · MISSED at T+61 → INFO to 阿珍 + queued reminder
   dropped · MISSED within 3 h still confirmable, beyond 3 h refused · dose.due fires through a natural scheduler
   tick crossing (≤120 s rule) · offline `reminder_confirmed` accepted within the window, ignored far outside.
5. **Concern** — 24 probes: every emergency phrase (6) opens the fall machine at AWAITING_OK with card 「緊急關注」,
   group 「緊急處理」, no photo step, resolvable by 「我冇事」 · every urgent phrase (7) → INFO to 阿珍 + spoken
   offer (no incident) · every scam marker (7) → urgent `scam` with the scam wording · matcher raises an
   agent-info concern with an emergency phrase to emergency · during an open incident every concern reports
   `emergency`, changes nothing and sends nothing · urgent repeat guard works per category (modulo D2).
6. **Arbiter (§6)** — 7 probes: P0 preempts an in-flight reply (hard pause, alarm mood, caption closed) · P2 voice
   note queues during a turn (`idle_gap`) and is delivered in the gap after the reply · quiet hours: P2 →
   `quiet_hours_next_session` (then speaks at the next session), second P2 replaces the first (one per class),
   P3 check-in skipped (`checkin.skipped quiet_hours`), P1 non-dose dropped, P1 dose reminder delivered · two P1
   dose reminders merge into one item (both instructions and cards) · every decision recorded as
   `arbiter.decision` · nothing non-P0 is delivered during an open incident.
7. **Check-ins** — 6 probes: morning fires at the configured time (curious mood, `checkin_morning` session,
   greeting inject) · never in quiet hours · skipped within 20 min of a session, allowed 21 min after · deferred
   while a session is open, dropped after 30 min deferred · caregiver overlay (`ahbo.settings_store.set_values`)
   moves the morning check-in to 09:15 and the scheduler fires it on the natural crossing · no reply within 20 s
   closes the session (`checkin_no_reply`).
8. **Sign-up action (§4.3.5)** — 10 probes: propose records `proposed` with a good/唔好-style question ·
   confirm with `stt_confidence 0.45` refused (`low_confidence`, still proposed — asking again is the agent's
   move; the Kernel gate works) · 「唔好啦」 and empty utterance refused (`not_affirmative`) · clear yes records
   the exact transcript + confidence + timestamp and starts the runner only then (`action.run`) · second confirm
   refused (`not_proposed`) · DONE → NOTIFY to 阿珍 (reference no.), calendar event booked with `action_id`,
   action card + P2 行動結果 inject to her · NEEDS_HUMAN → HANDOFF · FAILED → INFO · 120 s timeout → FAILED+INFO ·
   status for an unknown action ignored.

## Not tested / blocked
- Real providers (Telegram, Twilio, vision, ElevenLabs/Opper) — hard rule; everything above is fake mode.
- The production-profile parameters (`params()`: countdown 30 s, T_ack 300 s, F4p, F3 repeat at 15 s) — read and
  reasoned about, not executed; only the demo profile runs under the harness.
- The agent-side UX of the low-confidence re-ask (whether Flash actually re-asks) — the Kernel refusal is
  verified; the model's behaviour needs the real model or an agent-loop test, out of scope for kernel QA.
- Face rendering of the restart recovery (whether the iPad re-paints mood/cards from the ui_state mirror) —
  kernel/ui_state side verified only; `ios/` and `face/` are out of scope.
- Live scheduler wall-clock behaviour over a real 15 s tick (probes drive `Scheduler.tick()`/events directly).
