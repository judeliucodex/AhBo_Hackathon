# GLM task 08: Adversarial safety review of Links (READ-ONLY on the repo)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/08_REPORT.md`. **Do not edit any repo file.** You may write
scratch test scripts under `/tmp` inside throwaway containers and run them
(`docker run --rm -v /opt/ahbo:/repo -v /tmp/ahbo-review:/scratch ahbo-api python -m pytest -q /scratch/...`).

Scope: `brain/ahbo/links/{family,outbox,telegram,twilio,sensors,wiring,__init__}.py`, `sim/{beacon,telegram_fake,twilio_fake}.py`,
and the Kernel's guard in `brain/ahbo/kernel/common.py`. Context: PRD §2 (INV-2, INV-7, INV-8, INV-9), §4.3.1, §9.1–9.3,
§10.4, §11; CONTRACTS §4, §8. A previous reviewer was stopped before reporting; start fresh.

Your job is to **prove it unsafe or wrong**, with concrete inputs you actually run:
1. **INV-9 dial guard** (Links and Kernel): try to reach a call to 999/112/911 in every format (spaces, dashes,
   `+852`/`+1`/`00` prefixes, full-width ９９９, other Unicode digits, `tel:` URIs, extensions, leading zeros), an unverified
   contact, a contact with another role, an unknown contact id, a raw number not from `contacts`. Try to exceed 3 safety
   calls per incident or 5 calls/day.
2. **Telegram allowlist (INV-7)**: can a chat not on the allowlist produce ANY Kernel event or agent input — text, voice,
   photo, `/fact`, `/story`, `/status`, callback buttons, edited messages, channel posts, group messages, forwarded
   messages? Is `/whoami` the only exception and does it leak anything else? Webhook secret: constant-time compare;
   missing header rejected; what if `TELEGRAM_WEBHOOK_SECRET` is empty?
3. **Twilio webhooks**: signature checked on every route with the right URL; behaviour with an empty auth token; can a
   forged `/twilio/gather` with Digits=1 acknowledge an incident?
4. **Buttons (INV-2, INV-8)**: can a stranger or a forged callback acknowledge/resolve an incident? Are console fake
   presses honestly labelled?
5. **Sensors**: token hashing and compare; does empty `BEACON_TOKENS` accept anything; idempotency on (beacon_id, seq);
   queued, not processed inline.
6. **Secrets**: any token/key in logs, outbox payloads, stored URLs (e.g. the bot token inside Telegram file URLs saved
   in the DB), or error messages.
7. **Fake vs real parity**: does the fake path skip a guard the real path has, or the reverse?

Report: findings, each with severity (critical/high/medium/low), file:line, one-line defect, the exact input, and
CONFIRMED (paste the output) or PLAUSIBLE; then what you checked and found correct. Findings only — no fixes.
