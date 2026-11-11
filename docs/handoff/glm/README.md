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

Second wave (06–10): run **at most 3 at a time** on this 2-CPU server. Suggested order: first 06 + 07 + 08 together
(code fixes, docs, read-only review: no file overlap); then 09 + 10 once 06 is done (they test the finished code).

They can run in parallel (disjoint files) or one after another. When a task's report exists, tell Claude; Claude
reviews it, runs the tests, fixes what is wrong, and commits. GLM never commits.
