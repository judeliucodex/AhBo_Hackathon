# Ah Bo eval report

Run: 2026-11-12 01:52:29 · fake: `livekit,models,speech,telegram,twilio,vision` · judge: pending keys · rubrics v1.0 · 6.9 s

Status per case: **pass**; **fail** (a defect); **blocked** (a component the case needs is not built yet, named in the row); **error** (the case crashed). Blocked and error cases count as not passed.

Components present: reducers `fall, concern, dose, checkin, action, family, session, turn, voice, arbiter, misc` · wiring `actions, agent, kernel, links, memory, speech` · 23 tools

## Summary

| Suite | Cases | Pass | Fail | Blocked | Error | Criteria | Verdict |
|---|---|---|---|---|---|---|---|
| scenarios | 2 | 1 | 1 | 0 | 0 | pass_rate 50% (bar 100%, n=2) | FAIL |

## Pending


## scenarios: not passed (1)

- **S3-turn3-signup** [fail] — owner: actions (runner) + links (NOTIFY) — She says yes → confirm_action → runner submits the mock form → Ah Chun notified, event booked
  - no outbox row {'channel': 'telegram', 'to_contact': 'c_chun', 'kind': '~NOTIFY\|HANDOFF'} (outbox: [])
  - db SELECT state FROM actions → [['running']] has no row matching ['~(?i)done\|needs_human']
  - spoken: 十點半你要去聯合醫院覆診，我已經查好路線，九點半喺樓下搭巴士就得。十二點半同阿珍飲茶。 / 附近長者中心下晝三點有粵曲班，可以識多啲朋友。想唔想去？我幫你報名吖。 / 好呀，我而家幫你報名，報好咗再話你知。
