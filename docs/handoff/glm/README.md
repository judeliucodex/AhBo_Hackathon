# Hand-off to GLM 5.3 Flash (Zcode)

Five component tasks are delegated to GLM. Claude Code built the Kernel and Agent + models (committed) and reviews
every GLM result (Links' safety code line by line) before committing it.

How to start each one in Zcode (same SSH host, folder `/opt/ahbo`), **one task per Zcode session**:

> Read `/opt/ahbo/docs/handoff/glm/00_COMMON.md`, then `/opt/ahbo/docs/handoff/glm/<TASK FILE>`, and do the task.

| Task file | Component | Report it writes |
|---|---|---|
| `01_ops_speech_trace.md` | fixed clips, doctor, traces, costs, caps, wipe, backup | `docs/handoff/glm/01_REPORT.md` |
| `02_actions_mock_centre.md` | Playwright sign-up runner + mock registration site | `docs/handoff/glm/02_REPORT.md` |
| `03_operator_console.md` | operator console UI | `docs/handoff/glm/03_REPORT.md` |
| `04_links.md` | Telegram, Twilio (INV-9 guard), beacon, fakes, outbox | `docs/handoff/glm/04_REPORT.md` |
| `05_browser_ipad.md` | browser iPad simulator `sim/` | `docs/handoff/glm/05_REPORT.md` |
| `06_finish_integration.md` | finish the integration fixes (1 failing test) | `06_REPORT.md` |
| `07_docs_setup.md` | CONTRACTS/BUILD_LOG updates, README, RUNBOOK, SETUP.md generator | `07_REPORT.md` |
| `08_review_links_safety.md` | read-only adversarial safety review of Links | `08_REPORT.md` |
| `09_review_components.md` | read-only adversarial review of ops/actions/console/sim | `09_REPORT.md` |
| `10_g3_visual.md` | gate G3: sim screenshots vs kit | `10_REPORT.md` + `docs/screens/g3/REPORT.md` |
| `11_mcp_setup.md` | Zcode MCP connectors: Playwright, Context7, XcodeBuildMCP bridge to the Mac | `11_REPORT.md` |

Second wave (06–10): run **at most 3 at a time** on this 2-CPU server. Suggested order: first 06 + 07 + 08 together
(code fixes, docs, read-only review: no file overlap); then 09 + 10 once 06 is done (they test the finished code).

They can run in parallel (disjoint files) or one after another. When a task's report exists, tell Claude; Claude
reviews it, runs the tests, fixes what is wrong, and commits. GLM never commits.

## QA round (tasks 21–26): testers, read-only on the repo — iPad excluded

Each tester runs its own throwaway fake-mode server (repo mounted read-only), tries to break one area, and writes
`docs/handoff/glm/2N_REPORT.md` with S1/S2/S3 defects and reproductions. Nobody edits code; Claude fixes what they find.
Run at most **3 at a time** (the server has 2 CPUs). Suggested order: 21 + 23 + 24 first (safety), then 22 + 25 + 26.

| Task | Area | Report |
|---|---|---|
| `21_qa_kernel_safety.md` | fall flow (owner rules), medicine truth, concern, arbiter, check-ins, sign-up machine, INV tests | `21_REPORT.md` |
| `22_qa_conversation.md` | conversation, Cantonese output rules, memory, deep task, modes, evals | `22_REPORT.md` |
| `23_qa_telegram_bot.md` | Telegram caregiver/admin bot, offline (every menu, edits, INV-9 numbers, strangers) | `23_REPORT.md` |
| `24_qa_platform_security.md` | auth matrix, device channel protocol, restart/replay, secrets, webhooks, live exposure (read-only) | `24_REPORT.md` |
| `25_qa_sim_e2e.md` | browser iPad S1–S4ok end to end with Playwright, sign-up on the mock centre, voice note | `25_REPORT.md` |
| `26_qa_ops.md` | doctor, fixed clips, caps, wipe/export, backup/restore, retention, traces | `26_REPORT.md` |

Paste prompt for each Zcode session (replace NN with 21…26 and the file name):

> Read `/opt/ahbo/docs/handoff/glm/20_QA_COMMON.md` fully, then `/opt/ahbo/docs/handoff/glm/<NN_file>.md`, and do that QA
> task. You are a tester: do not edit any repo file except your report `docs/handoff/glm/NN_REPORT.md` and evidence under
> `docs/qa/NN/`. Never touch the live stack, never call real external services, never read `.env`. Clean up your containers.

## Task 27 (fix): real-model evals get no replies — `27_fix_real_model_evals.md` (may edit `brain/ahbo/evals/` only).
