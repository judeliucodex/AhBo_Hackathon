# GLM task 06: Finish the integration fixes

Read `00_COMMON.md` first (the "known failures" list there is out of date: today the only failing test is
`tests/actions/test_runner_browser_unavailable.py::test_domain_checks_still_run_before_the_browser`). Report goes to
`docs/handoff/glm/06_REPORT.md`. Also read `docs/FULL_GUIDE.md` §5.

A Claude agent started these fixes and was stopped mid-way. **Its edits are on disk, uncommitted** — read them first
(`git diff -- <file>` is read-only and allowed; no other git commands):
modified `brain/ahbo/actions/runner.py`, `brain/ahbo/api/{backup,demo}.py`, `brain/ahbo/api/routes/media.py`,
`brain/ahbo/evals/harness.py`, `brain/ahbo/kernel/reducers/{__init__,action}.py`, `brain/ahbo/ops/cli.py`, `Makefile`,
`deploy/make.sh`; new `brain/ahbo/kernel/reducers/family.py`, `brain/tests/kernel/test_family_message.py`,
`brain/tests/api/test_api_fixes.py`, `brain/tests/actions/test_runner_browser_unavailable.py`.

**You own exactly those files** (plus new tests next to them). Docs files and `evals/visual/` belong to other tasks.

Finish and verify each item (each needs a passing test; check whether the stopped agent already did it):
1. Kernel handles `family.message_request` (payload: `brain/ahbo/agent/tools/family.py`): only an allowlisted, verified
   family contact; emits `family.send {kind:"INFO", contact_ids, text}` from a fixed template + her words in quotes (never
   free model text); replies `{ok:true, contact}` via `ctx.reply`, else `{ok:false, reason}`. End to end in fake mode the
   `send_message_to_family` tool now produces an outbox row.
2. Kernel handles `action.message_send` (from `brain/ahbo/actions/telegram_msg.py`): re-check the recipient against the
   `contacts` allowlist → `family.send {kind:"NOTIFY", contact_ids:[id], text, action_id}`. `_finish`'s done text for
   `kind=="message"` actions: 「已幫王婆婆傳咗訊息俾{title}。」.
3. `ops/cli.py`: delegated commands (`doctor`, `wipe`, `render-fixed`, `gen-wake-table`, `eval`) get their remaining
   argv unparsed, so `ahbo doctor --latency` and `ahbo wipe --yes` work; `eval` → `ahbo.evals.runner.main`.
   `Makefile` / `deploy/make.sh`: `eval` target and `screens` target (`python evals/visual/screens.py` per
   `evals/visual/README.md` if it exists; else a clear placeholder that says task 10 builds it).
4. `api/backup.py`: the nightly loop calls `ahbo.ops.backup.backup` (one source of truth).
5. `api/demo.py`: the console's fake controls call `ahbo.links.wiring.press / inject_voice_note / inject_text` and
   `ahbo.links.outbox.outbox_list` with their exact signatures.
6. `api/routes/media.py`: `GET /media/photo/{id}` also accepts the device token, **only** for photos referenced by
   `life_stories`; incident photos stay console-only (test both).
7. `evals/harness.py`: `EvalSystem.outbox()` also decodes the `buttons` JSON.
8. `actions/runner.py`: browser unreachable → the action ends `needs_human` with reason `browser_unavailable` (阿珍 still
   notified through the normal path), so eval case S3-turn3-signup passes without the browser container. **Domain and
   forbidden-field checks must still run first** — that is what the failing test checks; fix the code, not the test,
   unless the test is wrong (explain).

Done when: full suite has 0 failures (`docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q`), the five
golden traces pass, and `docker run --rm -v /opt/ahbo:/repo -w /repo/brain ahbo-api python -m ahbo.evals` shows
scenarios 9/9 (paste the summary table).
