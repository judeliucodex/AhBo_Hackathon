> **UPDATE 12 Nov (Claude): the root cause is FIXED** — the harness ran timers 20× faster, so the 30 s session-idle
> timer closed the session (cancelling the turn) before the real AI answered; real models now run at real speed
> (`evals/harness.py time_scale()`). Claude is running the full real-model evals → `docs/qa/real_model_evals.md`.
> If that report exists, **skip steps 1–2**: only do step 3's analysis (list failing cases + what the AI said) and step 4.

# GLM task 27: Why do the evals get NO reply from the real AI? (fix allowed in `brain/ahbo/evals/` only)

Read `20_QA_COMMON.md` (rules) but note: this task MAY edit `brain/ahbo/evals/**` and `brain/tests/evals/**`, and may make
real model calls through Opper (owner approved, keep total spend < $1). Report: `docs/handoff/glm/27_REPORT.md`.

## The bug
With real models (`AHBO_FAKE=speech,livekit,telegram,twilio,vision`, Opper key from `.env`), every eval case gets
**0 spoken lines and 0 tool calls** (`docs/qa/real_model_eval_broken_run.md`: cantonese 0/40 "expected ≥1 spoken lines,
got 0"). But real Flash works directly:
`get_flash().chat([...])` → `RealFlash` answers 「早晨呀！…」 in ~1 s, and live voice through LiveKit works.
So the break is between the eval harness (`brain/ahbo/evals/harness.py` EvalSystem, `runner.py`) and the
TurnAgent/TextLoop with RealFlash — e.g. a timeout (the harness scales time ×0.05: does it cut real model calls short?),
`wait_idle` timing, the turn task being cancelled by the next step, exceptions swallowed in `agent/runner.py`, or
`models.runtime` not being set up in the harness.

## Do
1. Reproduce with one case: `docker run --rm -v /opt/ahbo:/repo --env-file <(grep -E '^(OPPER_API_KEY|PUBLIC_BASE_URL|TZ)=' /opt/ahbo/.env) -e AHBO_FAKE=speech,livekit,telegram,twilio,vision -w /repo/brain ahbo-api python -m ahbo.evals --help` (find the single-case option) and log what the TurnAgent does.
2. Fix it inside `brain/ahbo/evals/` (if the real cause is in `agent/`, describe the exact fix in the report instead).
3. Run all suites on the real models once, write the report to `docs/qa/real_model_evals.md` (do NOT overwrite
   `evals/report.md`, which holds the fake-mode results), and list every failing case with what the AI actually said.
4. Fake mode must still pass: `docker run --rm -v /opt/ahbo:/repo -e AHBO_FAKE=all -w /repo/brain ahbo-api python -m ahbo.evals`.
