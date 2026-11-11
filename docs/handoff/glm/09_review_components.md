# GLM task 09: Adversarial review of ops, actions, operator console, browser iPad (READ-ONLY on the repo)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/09_REPORT.md`. **Do not edit any repo file.** Scratch scripts
go under `/tmp` and run in throwaway containers; browser checks use `ahbo-browser` on a private Docker network you create
and remove (never the live stack, never ports 80/443/8000). For a real app, run `ahbo-api` with `AHBO_DATA_DIR=/tmp/<x>`
and throwaway generated secrets + a bcrypt hash for a test password (never read the real `.env`).

Scope and specs (read the code; don't trust `docs/handoff/glm/0{1,2,3,5}_REPORT.md`, verify them):
- Ops/speech/trace: `brain/ahbo/{speech,trace}/**`, `brain/ahbo/ops/{doctor,render_fixed,wipe,backup,caps}.py` — PRD §10.3,
  §11, §13.2, §15, §26.3, §27.
- Actions + mock centre: `brain/ahbo/actions/**`, `mock-centre/**` — PRD §4.3.5, §8.3.
- Operator console: `operator-ui/**` — PRD §12, CONTRACTS §9, backend `brain/ahbo/api/routes/operator.py`, `api/demo.py`.
- Browser iPad: `sim/*.js|html|css` — CONTRACTS §2 and §3 exactly.

Try to **prove each wrong**. Priorities: (1) safety/privacy — the runner typing anything outside the allowed profile
fields, submitting to a non-allowlisted domain, following a cross-domain redirect, entering HKID-like data; caps really
enforced; doctor/console never showing more than the last 4 characters of a secret; (2) the fixed-clip manifest —
UTF-16 segment offsets, durations vs `ffprobe`, version changes, the P1 public path unguessable and the only public file;
(3) sim protocol fidelity — field names (`message_id`, not `id`), an `ack` for every command within 2 s, de-dup by id,
never hanging on missing audio; (4) every console button posts the body the backend accepts; Esc = panic; operator
speech labelled operator (INV-8); nothing misleadingly labelled; (5) anything that would break the S1–S4 demo.

Report: findings with severity, file:line, one-line defect, failing scenario, CONFIRMED (with output) or PLAUSIBLE; then
what you checked and found correct. Findings only — no fixes.
