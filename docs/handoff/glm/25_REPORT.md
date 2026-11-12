# QA 25 report — Browser iPad end-to-end + sign-up on the mock centre

Tested 12 Nov 2026 on a throwaway fake-mode server (`ahbo-qa25-api` + `ahbo-qa25-browser`, network `ahbo-qa25`,
`AHBO_FAKE=all`, repo mounted read-only), Playwright 1.64.1 (Chromium) at 1600×1000 from the `ahbo-browser` image.
Evidence is in `docs/qa/25/` (`check1/`, `check2/`, `check3/`, `check45/`, `evidence/actions/`, `scripts/`).

## Summary

| Severity | Count |
|---|---|
| S1 | 1 (demo seed data only — see D1) |
| S2 | 1 |
| S3 | 9 |

**Verdict: usable with issues.** All five storyboards play end to end on `/sim/` and match the kit apart from the
owner-approved changes; wake, the medicine answer, both fall branches, the sign-up (done / needs-human / failed) and
the voice note all work. The two things to fix first: the demo seed marks the 08:00 dose as taken even when the demo
is reset before 08:00 (D1), and the browser iPad never notices a silent network loss (D2).

Setup note for other testers: the recipe in `20_QA_COMMON.md` §3 does not set `AHBO_ACTION_INTERNAL_BASE`, so the
Action Runner navigates to `http://api:8000/…` (the compose name) and every sign-up fails with
`ERR_NAME_NOT_RESOLVED`. Add `-e AHBO_ACTION_INTERNAL_BASE=http://ahbo-$N-api:8000`. Also, another session ran
`docker kill ahbo-qa25-api` once during my run; I recreated it (no results were lost).

## Defects

### D1 [S1] Demo seed marks today's 08:00 dose "taken at 08:05" even when Reset demo runs before 08:00
- Where: `brain/seed/demo.yaml:23` (`seeded_today: [{slot: s_0800, state: taken_self_reported, at: "08:05"}]`), loaded by `brain/ahbo/ops/seed.py:44` with no check against the clock.
- Spec: INV-4 / PRD §4.3.2 — a dose is only "taken" if she reported it; the next-dose answer and reminders come from that state.
- Repro: `docker run --rm --network ahbo-qa25 -v /tmp/ahbo-qa-25:/scratch ahbo-api python /scratch/med_probe.py 07:00` (sets the clock to 07:00, Reset demo, wakes, says 「阿寶，我幾時食藥呀？」), then `med_probe2.py` (clock 07:59, watch across 08:00; control at 11:59→12:00). Scripts: `docs/qa/25/scripts/`.
- Actual: at 07:00 `dose_events` has `s_0800 … taken_self_reported, resolved_at 08:05` (65 min in the future) and Ah Bo says 「五個鐘之後，十二點正。有血壓藥…」, skipping the 08:00 糖尿藥. At 08:00 `dose.due s_0800` fires but no reminder clip, card or session follows; the 12:00 control plays the full reminder. I first saw it through the page at 02:27 (「大約九個半鐘之後，十二點正」). Evidence: `check2/med_probe_output.txt` — the 07:00 reply and the 08:00/12:00 trace rows in that file are pasted from my first run's console output (the saved re-run lost them because the trace API sorts by virtual time); re-run the two scripts on a fresh server to regenerate.
- Scope: demo seed data only (no real resident data is affected), and only for a reset without a scenario anchor between 00:00 and 08:00. Rated S1 because the rubric lists "medicine fact wrong"; at the S2 anchor (10:00) the answer is correct.

### D2 [S2] Browser iPad does not notice a silent network loss: no offline dot, status stays "online"
- Where: `sim/channel.js:86-94,107-114` — "disconnected" is only detected by the socket's `onclose`; there is no heartbeat/pong watchdog.
- Spec: PRD §23.9 (tiny amber dot when offline), `channel.js` header ("after 30 s disconnected → onOffline(true)"); task check 5.
- Repro: start `check5b.py` in a named container, and from the host `docker network disconnect ahbo-qa25 ahbo-qa25-runner; sleep 35; docker network connect ahbo-qa25 ahbo-qa25-runner` (exact commands in the header of `scripts/check5b.py`; `check5b75.py` = 75 s cut).
- Actual: for both a 35 s and a 75 s cut the pill stayed `online` and the dot never showed; the socket only closed (1006) about 3 s **after** the network came back, then reconnected. The server did log `device.offline` during the cut. Output: no `offline` face command at all. Evidence: `check45/5b_real_t*.png`, `5b_real75_t*.png`, `5b_real_offline.ws.json`.
- Note: Chromium's network emulation (`Network.emulateNetworkConditions offline`) does not cut an open WebSocket, so that method shows nothing either way. Not tested: whether a 「我冇事」 tap during such a cut is lost (by reading `channel.send`, it would be written to the dead socket and reported as sent).
- Works: when the socket closes visibly (I stopped my api container for 38 s) the dot appears 30 s after the close and clears after reconnect (`check45/5b_serverdown2_t*.png`). See D11 for the slow clear.

### D3 [S3] Her words are not shown on the face during the fall check (text sessions)
- Where: `brain/ahbo/kernel/reducers/turn.py:81-85` returns before `heard` when the session is `kernel_listen`; `reducers/fall.py:600-606` sends none either.
- Spec: PRD §23.5 ("During `kernel_listen` her words are still shown, so the room sees 「我冇事」 land"); kit S4ok sends `heard {text:"我冇事", final:true}` (`docs/screens/kit/S4ok_08_*.png` shows it).
- Repro: `check1.py S4ok`; or `check2_rest.py B` (types 「救命」 during the countdown).
- Actual: no `heard` command in `check1/S4ok.trace.json`; heard line empty after 「今日天氣點呀？」, 「救命」 and the scripted 「我冇事」 (`check2/B2_help_escalated.png`, `check1/S4ok_08_*.png`). The match itself works.

### D4 [S3] F4 starts ~0.6 s late and the face turns warm ~0.3 s before F4 finishes
- Where: device hub's 600 ms minimum-active hold on `step tell/call done` queues the P0 `play_fixed F4` behind it; the Kernel's clip timer (`kernel/common.py:35-37`, 0.19 s/char + 0.3 s) starts at emit time, so it fires before the iPad's `playback_done` and `_reassure` (`reducers/fall.py:422`) sends `mood warm`.
- Spec: PRD §23.6 rule 4 ("the hub delays the `done` command; it never delays speech"); kit S4 stays `alarm` until F4 ends.
- Repro: `check1.py S4`.
- Actual (`check1/S4.ws.json`, ms from scenario start): `step call active` 13925 → `step tell done` 14521 → `play_fixed F4` 14528 → `mood warm` 19546 → `playback_done F4` 19881. Same pattern in every escalation I ran. Screenshot: `check1/S4_11_mood_warm.png` (warm eyes while the F4 caption is still revealing).

### D5 [S3] The sign-up step 「幫你報緊名…」 never gets its ✓ / ✕ label
- Where: `config/ui_steps.yaml` `confirm_action.done {done: 報咗名喇, needs_human: 叫咗阿珍幫手, failed: 報唔到名}`; no `step … done/failed` is sent when `action.status` arrives.
- Spec: PRD §23.6 rule 3 (after the tool returns: `done` with the filled label, or `failed`).
- Repro: `check3.py done2` (also `nobrowser`, and the first `done` run which failed).
- Actual: in all three outcomes the step stays `active` and is removed by `steps_clear` ~4 s after the first reply (`check3/done2.ws.json`: `steps` at +36 ms, card at +5346, `steps_clear` at +7846, no `step` frame). The card and spoken result are correct.

### D6 [S3] Caregiver messages contain raw program output
- Where: `brain/ahbo/kernel/reducers/action.py:170` (`…但係做唔到（{str(detail)[:80]}）`) and `:161` (`已準備資料：{dumps(prefill or {})}`).
- Spec: PRD §4.3.5 / §9.2 — INFO in Traditional Chinese; HANDOFF = link + prefilled details + screenshot.
- Repro: any failed sign-up (I got one with the default recipe, see setup note) and any needs-human one (`variant_probe.py --pin hkid`).
- Actual: INFO 「王婆婆想報名粵曲班，但係做唔到（{'reason': 'browser_error', 'error': 'Error(\'Page.goto: net::ERR_NAME_NOT_RESOL）。請你幫手睇下。」 (`check3/done.outbox.json`); HANDOFF ends 「已準備資料：{}」 with no prefilled details (`check3/variants_output.txt`, `nobrowser.outbox.json`).

### D7 [S3] Operator-triggered voice note plays with no 「阿珍：…」 caption
- Where: `brain/ahbo/links/wiring.py:142-146` inserts the fake note with `transcript` None, so `play_voice_message` has no `text`.
- Spec: task check 4 (caption 「阿珍：…」); PRD §23.5 shows the transcript when available — the seeded demo note's words are known (S1 shows them).
- Repro: `check45.py 4` (reset, `POST /operator/api/trigger {"kind":"voice_note"}`, then type 「好呀」).
- Actual: A1 「王婆婆，阿珍留咗段錄音俾你。」 with warm eyes ✓, then 「想唔想而家聽阿珍嘅錄音呀？」; after 「好呀」 the card 「阿珍嘅錄音 · 今朝 10:01」 shows and its waveform plays ✓, but the caption is empty (`check45/4_voice_note_yes_1.png`). Note the note does not play until she says yes (PRD §1.3 "offers to play it").

### D8 [S3] Countdown chip keeps ticking after an early escalation
- Where: `reducers/fall.py:352` `escalate()` cancels the Kernel timer but sends no `card_clear` (the resolve path does).
- Spec: kit — the countdown is gone when the flow moves on.
- Repro: `check2_rest.py B`.
- Actual: after 「救命」 the face shows the escalation cards next to 「⏳2」 until the face's own countdown ends ~3 s later (`check2/B2_help_escalated.png`; `countdown_done` at +3172 ms).

### D9 [S3] After a reload the photo step reads 「相片已送出」 instead of 「可能有人跌咗喺地下」
- Where: `brain/ahbo/kernel/ui_state.py:89-99` replays `{label: 可能有人跌咗喺地下, state: done, doneLabel: 相片已送出}` and the face shows `doneLabel` for a done step.
- Spec: CONTRACTS §2.4 (replay each step's current state).
- Repro: `check45.py reload` (S4, reload during the countdown).
- Actual: before reload 「可能有人跌咗喺地下」, after reload 「相片已送出」 (`check45/5a_before_reload.png`, `5a_after_reload.png`). Everything else replays (alarm, incident card, steps, 我冇事 button) and the fall goes on to escalate. The countdown chip and the in-flight caption are not replayed (not in the contract).

### D10 [S3] `/sim/fall_placeholder.jpg` is a 404; the page uploads a self-drawn photo with English text
- Where: `sim/photo.js:39`; nothing serves `brain/seed/fall_placeholder.jpg` under `/sim/`.
- Spec: PRD §26.3 ("otherwise it uploads `seed/fall_placeholder.jpg`").
- Repro: any fall on `/sim/`; `check1/placeholder_404.txt`.
- Actual: console 404 on every fall; log line `photo ph_… (placeholder_drawn)`; that drawn image (caption "browser iPad (sim) — placeholder photo …") is what 阿珍's ALERT carries.

### D11 [S3] Offline dot clears ~30 s after the server is back
- Where: `sim/channel.js:73-95` — a connect attempt that hangs has no timeout, and the next retry is only scheduled on close.
- Repro: `scripts/check5bs.py` while stopping my api container for 38 s.
- Actual: dot on at t+33 s; server back at ≈t+42 s; reconnect at t+69 s; dot off at t+72 s.

## Passed checks
- S1, S2, S3 on `/sim/`: mood, step labels and ✓, cards (labels, small text, icons, pill colours), captions and happy squint match the kit frame by frame (`check1/cmp/S1–S3_cmp_*.png`, traces `check1/S*.trace.json`).
- S4: alarm eyes, incident card 「偵測到跌倒 · 感應器：急速下跌」, F1–F3, flash, fall steps, 我冇事 button (238×97 pt, spec ≥ 220×88), countdown 5→, ✕ 冇回應, cards, F4 — match the kit with only the owner changes (「致電阿珍 / 已致電阿珍」, card 「致電阿珍 · 已致電」, new F4 text). Extra: D4.
- S4ok: photo step 「相片已送出」 (owner change), ✓ 婆婆話冇事, ✕ 唔使致電, warm, F5, idle, steps cleared. F5 text is the PRD/`fixed_lines.yaml` one (「好啦，冇事就好。阿寶喺度陪住你。」), not the kit storyboard's older wording — I did not count that as a defect.
- Tap the face while idle: `expr wake` at once, `wake {trigger:"touch"}`, mood listening, text session opens, text box enables (it is disabled before).
- 「阿寶，我幾時食藥呀？」 in the text box at 10:00: step ✓ 「下一次：十二點正，四隻藥」, ⏰ card + 4 pill chips, the PRD's exact reply.
- On-screen 「我冇事」 with the camera showing she is fine (S4ok, pressed before the scripted line): RESOLVED_OK (touch) → F5 → CLOSED; one INFO to 阿珍 (「撳咗「我冇事」掣…相片睇落冇問題」, button 錄段聲俾媽媽, no photo), no call.
- On-screen 「我冇事」 with the default fake camera (sees a fall): escalates with ✕ 「相片見到可能跌親」, ALERT + call to 阿珍. This is the owner rule in BUILD_LOG §8, so the task's "press → resolves" only holds when the camera shows no problem.
- 「救命」 typed during the countdown: escalates within 15 ms (✕ 「婆婆話要幫手」), F4, ALERT to 阿珍 with photo, vision note, time, address and buttons 我處理緊 / 錄段聲俾媽媽 / 已解決, plus one call row to 阿珍. No 999/112/911 anywhere in the outbox.
- An unrelated line during the countdown (「今日天氣點呀？」): no reply from Ah Bo, state stays AWAITING_OK.
- Sign-up after S3 (「好呀，幫我報名啦」): 「好呀，我而家幫你報名…」, then card 「已報名 · 編號 DEMO-0423」 and 「報咗名喇，下晝三點嘅粵曲班…」 in 5.3 s; form filled only with name, English name, age group, 阿珍's phone placeholder, activity, session; both screenshots stored and sent in the NOTIFY; calendar event 15:00 粵曲班 `booked`; mock site labelled 示範用 Demo (`evidence/actions/*.png`).
- `?variant=hkid` → `needs_human` (`forbidden_field: 身份證號碼*`), `?variant=captcha` → `needs_human` (`captcha`); each sends a HANDOFF with a screenshot and registers nothing. A non-allowlisted URL → `needs_human domain_not_allowed`, never opened. The variant URL had to be pinned in the action row of my own DB (the technique in `brain/tests/actions/test_playwright_integration.py`), because the Kernel ignores a URL passed to `request_action` and always uses the seed one — a good property.
- Browser container stopped: `needs_human` in ~5 s, HANDOFF to 阿珍, card 「已通知阿珍 · 幫手報名」, 「我已經叫阿珍幫手完成報名。」.
- Voice note trigger: A1 announcement, offer, and on yes the waveform card playing (caption missing: D7).
- Full page reload mid-fall and the "Reload face" button mid-reply: state replayed, flow continues (label change: D9).
- Two `/sim/` tabs: both share one device id, both receive every command and show the same captions; S2 and S4ok complete once (one INFO in the outbox).

## Not tested / blocked
- Real audio by ear, the camera (`getUserMedia`) path, "Talk (browser mic)", portrait rotation: headless, fake mode.
- `?variant=login` and `?variant=payment`; the 120 s action timeout.
- Whether touches made during a silent network cut are lost (D2).
- Repeat sign-ups: four calls for the same class created DEMO-0423…0426 with no duplicate check; three of them were direct tool calls, so I did not verify whether she can trigger this by voice.
- Real-mode clip lengths: D4's timer uses 0.19 s/char; with real ElevenLabs clips the gap could be larger or absent. Fake mode only.
- iPad app (out of scope); the live stack was not touched.
- Housekeeping: containers `ahbo-qa25-api`, `ahbo-qa25-browser`, network `ahbo-qa25` and my `/tmp` scratch are removed. I ran one read-only `git status` at the end to confirm I had changed nothing but this report and `docs/qa/25/` (the common rules say not to run git).
