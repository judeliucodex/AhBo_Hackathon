# Hand-off to GLM 5.3 Flash (Zcode)

Three self-contained component tasks are delegated to GLM. Claude Code runs the safety-critical ones
(Kernel, Agent + models, Links, Browser iPad) at the same time, in other directories.

How to start each one in Zcode (same SSH host, folder `/opt/ahbo`), **one task per Zcode session**:

> Read `/opt/ahbo/docs/handoff/glm/00_COMMON.md`, then `/opt/ahbo/docs/handoff/glm/<TASK FILE>`, and do the task.

| Task file | Component | Report it writes |
|---|---|---|
| `01_ops_speech_trace.md` | fixed clips, doctor, traces, costs, caps, wipe, backup | `docs/handoff/glm/01_REPORT.md` |
| `02_actions_mock_centre.md` | Playwright sign-up runner + mock registration site | `docs/handoff/glm/02_REPORT.md` |
| `03_operator_console.md` | operator console UI | `docs/handoff/glm/03_REPORT.md` |

They can run in parallel (disjoint files) or one after another. When a task's report exists, tell Claude; Claude
reviews it, runs the tests, fixes what is wrong, and commits. GLM never commits.
