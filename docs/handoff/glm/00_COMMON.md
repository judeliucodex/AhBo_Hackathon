# Common rules for every GLM task (read fully first)

You are building one component of **Ah Bo (阿寶)**, a Cantonese robot-pet companion for an elderly woman
(王婆婆). The repo is `/opt/ahbo` on the production VPS. Other agents (Claude) are editing other directories
at the same time. Correctness beats speed; tests beat claims.

## 1. Read before writing code
1. `/opt/ahbo/docs/AGENT_BRIEF.md` — the shared brief. **All of its rules apply to you.** (Ignore its "Final report"
   section: write your report to the file named in your task instead.)
2. `/opt/ahbo/docs/CONTRACTS.md` — frozen interfaces. Do not change them.
3. `/opt/ahbo/docs/BUILD_LOG.md` — decisions D-1…D-10.
4. The PRD sections named in your task, in `/opt/ahbo/PRD_ahbo.md` (the source of truth for behaviour).
5. The existing code your task names. **Some of your files were already started by an earlier agent that was
   stopped mid-way.** Read them first; keep what is correct, finish or fix the rest. Don't start over blindly.

## 2. Hard rules (breaking one fails the task)
- Never open, read, print or use any file named `Root_password.md`.
- Never print secrets from `.env` (show at most the last 4 characters). Never put keys in code, UI, logs or git.
- **Spend no money**: never call a real external API (Azure, Anthropic, DeepSeek, Telegram, Twilio, LiveKit). There
  are no keys; the system runs in fake mode (`AHBO_FAKE=all`). Real adapters are written and unit-tested with mocks only.
- Never dial or allow 999 / 112 / 911.
- Don't touch sshd, ufw, fail2ban, users, or the running stack (`docker compose`, ports 80/443/8000). Don't stop or
  restart containers you didn't start. Don't bind host ports.
- Don't install anything on the host. Run Python inside the image:
  `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/<dir>` (working dir is `/repo/brain`).
  Browser tests use the `ahbo-browser` image (Playwright 1.64.1 + Chromium).
- Never edit `face/` or `ui-reference/` (the face is fixed; copy nothing from it).
- **Don't run git** (no add/commit/checkout/stash/reset). Claude commits after review.
- Only create or edit the files your task says you own. If you need a change elsewhere, write it in your report.
- Everything shown to 王婆婆 is colloquial Hong Kong Cantonese in Traditional characters. No Mandarin, no Simplified.

## 3. How to work
- Small steps: write a module, write its tests, run them, fix, then move on.
- Never claim a test passed unless you ran it and saw it pass. Paste the real final line of pytest output in your report.
- If you are unsure what an installed library's function is called, check it inside the image
  (`docker run --rm ahbo-api python -c "import x, inspect; print(inspect.signature(x.f))"`). Never guess.
- Before finishing, run the **whole** suite once to be sure you broke nothing:
  `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q`
  (Known failures that are not yours: `test_golden_ui.py::test_fall_storyboards[S4|S4ok]`,
  `test_inv_06_traced.py::test_repeated_questions_are_fully_traced`,
  `test_inv_08_operator_visible.py::test_console_overrides_go_through_operator_events`. Don't fix those.)

## 4. Your report (required; write it to the path your task names)
```
# <task> report
## Files created/changed
## Tests (exact command + the real last line of pytest output)
## Full-suite run (last line)
## What is real vs faked
## Deviations from PRD/CONTRACTS (and why)
## VERIFY results (library + version + exact function names you checked)
## Changes needed in files I don't own
## Known gaps / things I did not verify
```
Be honest. "Not done" is fine; a false "done" is not.
