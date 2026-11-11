# GLM task 10: Gate G3 — the fake demo, visually, against the kit

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/10_REPORT.md` and `docs/screens/g3/REPORT.md`. Read
`docs/FULL_GUIDE.md` §2 (the G3 row) and `evals/visual/kit_capture.py`.

A Claude agent started this and was stopped. **On disk, uncommitted:** `evals/visual/screens.py` (new), screenshots in
`docs/screens/sim/` (S1–S3 keyframes, `S1.trace.json`, `S2.trace.json`, `idle.png`; S4/S4ok missing; three older
GLM-task-05 images were deleted), `docs/screens/operator/S1.png`, `S2.png`, and a `docs/screens/g3/` folder. Its leftover
containers were already removed. Finish it.

**You own:** `evals/visual/screens.py`, `evals/visual/README.md`, `docs/screens/{sim,operator,g3}/`.

1. `evals/visual/screens.py` must be rerunnable with one documented command (`evals/visual/README.md`;
   `deploy/make.sh screens` will call it). It creates a private Docker network, runs `ahbo-api` (real app, `AHBO_FAKE=all`,
   `AHBO_DATA_DIR=/tmp/<x>`, throwaway secrets + a bcrypt hash for a test password — never the real `.env`) and
   `ahbo-browser`, logs in, opens `/sim/` (iPad frame 1180×820) and `/operator/`, and for each of **S1, S2, S3, S4, S4ok**
   POSTs `/operator/api/scenario/<name>` and captures keyframes of the sim's iPad frame with **exactly the kit's keyframe
   rule** (`kit_capture.py`: after each `say_end` +450 ms, `countdown` +1500 ms, `ok_button` show, moods
   alarm/warm/curious/thinking, step done/failed), plus one console screenshot per scenario. **Name keyframes like the kit**
   (`S2_03_say_end_<n>.png`, not with turn ids) so pairs line up by scenario and order. It always removes its containers and
   network, even on failure.
2. Compare each sim keyframe with the kit keyframe of the same scenario and order (`docs/screens/kit/`) by opening both
   images. The G3 rule: same eyes, captions, chips, steps and moods; any visual difference is a defect. Acceptable: caption
   *text* where the PRD wins (F5 is 「好啦，冇事就好。阿寶喺度陪住你。」, not the kit's line), capture-moment timing, `when`
   reveal timing. Defects: missing/extra cards, wrong labels/smalls/icons/pill colours, wrong step labels/states, wrong mood
   or eye shape, wrong layout.
3. `docs/screens/g3/REPORT.md`: one table per scenario (keyframe, kit file, sim file, verdict: match / acceptable
   difference / DEFECT + description), and at the top: G3 PASS or FAIL. For each defect, say which component likely causes
   it (Kernel `brain/ahbo/kernel`, tools `brain/ahbo/agent/tools`, sim `sim/`).
