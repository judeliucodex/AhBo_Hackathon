# QA 26: Operations — doctor, clips, caps, wipe/export, backups, traces

Read `20_QA_COMMON.md` first. Report: `docs/handoff/glm/26_REPORT.md`. Evidence: `docs/qa/26/`.

**Spec:** PRD §10.3 (fixed clips + manifest), §11 (retention, wipe, spending caps), §13.2 (doctor), §15 (observability),
§16.3 A5, §26.2; CONTRACTS §11. **Code:** `brain/ahbo/ops/{doctor,render_fixed,caps,wipe,backup,cli}.py`,
`brain/ahbo/speech/**`, `brain/ahbo/trace/**`, `brain/ahbo/memory/retention.py`, `brain/ahbo/api/backup.py`.
Everything runs on **your throwaway server or in throwaway containers with `AHBO_DATA_DIR=/tmp/…`** — never on `/opt/ahbo/data`.

## Check
1. **Doctor** (`docker exec ahbo-qa26-api python -m ahbo.ops.doctor`): every faked provider shows `fake (needs owner)`,
   nothing is red that shouldn't be, every red row says what to do, no secret printed beyond the last 4 chars.
   `--latency` works with no data.
2. **Fixed clips (fake speech):** `ahbo render-fixed` (or the module's `main`) writes a manifest per CONTRACTS §11: every
   clip in `fixed_lines.yaml` + one `R_<slot>` per dose slot + the phone clip under a random public path; durations match
   `ffprobe` (±0.1 s); segments are monotonic with UTF-16 end offsets = text length; F4/F4p text is the owner's 「我已經打電話俾阿珍…」.
   Change a dose slot and re-render: version changes, R clips update.
3. **Caps:** push each cap (llm_usd, deep_tasks, twilio_calls, playwright_actions, sessions_per_day) over its limit with
   fake records: the feature is refused with a console/Telegram-visible warning; safety calls are exempt up to 3 per incident.
4. **Wipe/export:** `ahbo wipe` refuses without `--yes`; with it, an export file appears first, then personal data and media
   are gone; the system still starts and Reset demo works afterwards.
5. **Backup/restore:** `ops.backup.backup()` makes a consistent copy (open it, `PRAGMA integrity_check`), keeps 7; restore a
   backup into a new data dir and start a server on it.
6. **Retention:** photos > 7 days, voice notes and transcripts > 30 days are deleted (fake their dates); seed files are never
   deleted.
7. **Traces:** after a fall and a conversation, every command has an `event_id` and `correlation_id` (INV-6);
   `/operator/api/trace?correlation_id=…` returns the whole story.
