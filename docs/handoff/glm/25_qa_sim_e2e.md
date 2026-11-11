# QA 25: Browser iPad end-to-end + sign-up on the mock centre (Playwright)

Read `20_QA_COMMON.md` first. Report: `docs/handoff/glm/25_REPORT.md`. Evidence (screenshots, traces): `docs/qa/25/`.
Start your throwaway server **with** the `ahbo-browser` container (see the common doc) and run Playwright from a
container on the same network: `docker run --rm --network ahbo-qa25 -v /tmp/ahbo-qa-25:/scratch ahbo-browser python /scratch/e2e.py`.

**Spec:** PRD §1.3 (S1–S4), §4.3.5, §8.3, §23 (face), §26.3 (browser iPad); BUILD_LOG D-5…D-10, §8 (fall rules, F4 text,
「致電阿珍」); the kit storyboards in `face/dev.html` and their screenshots in `docs/screens/kit/`.
**Code:** `sim/*.js|html`, `face/` (read-only), `brain/ahbo/api/demo.py`, `brain/ahbo/actions/**`, `mock-centre/`.

## Check
1. Log in, open `/sim/` (1600×1000), wait for the face. For each of **S1, S2, S3, S4, S4ok**: `POST /operator/api/scenario/<n>`
   with the same cookie, screenshot at each key moment (after each caption ends, at the countdown, at the end), and record
   the frames the page receives (hook `WebSocket` in the page). Compare with `docs/screens/kit/<S>_*.png`: eyes, mood,
   captions, cards (labels, small text, pill colours), check-mark steps, the 我冇事 button, the countdown. Owner changes
   (S4: 「致電阿珍」 instead of 緊急中心, new F4 line; S4ok: photo step 「相片已送出」) are expected — anything else is a defect.
2. **Her side in the page:** type 「阿寶，我幾時食藥呀？」 in the text box → S2 answer; tap the face while idle → wake; during a
   fall press the on-screen 「我冇事」 → resolves; type 「救命」 during the countdown → escalates.
3. **S3 turn 3 sign-up:** after S3, type 「好呀，幫我報名啦」 → the action runs Playwright against `/mock-centre/` (your
   server's own) → DONE with a booking reference, both screenshots stored, 阿珍 notified (outbox), 「已報名」 card. Then try
   `?variant=hkid` and `?variant=captcha` (NEEDS_HUMAN). Report what happens if the browser container is stopped.
4. **Voice note:** inject 阿珍's voice note (operator trigger `voice_note`) → A1 announcement, waveform card playing, caption
   「阿珍：…」.
5. **Robustness:** reload the page mid-scenario (face replays state); kill the network for 35 s (offline dot appears, then
   clears); two `/sim/` tabs at once.
