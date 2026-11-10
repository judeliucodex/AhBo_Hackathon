# GLM task 01: Ops, speech, trace (fixed clips, doctor, costs, caps, wipe, backup)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/01_REPORT.md`.

**PRD sections:** §10.3 (fixed-clip cache), §11 (wipe, retention, spending caps), §13.2 (doctor), §15 (observability),
§16.3 A5, §20.3, §24.5, §26.2, §26.3 (fake speech), §27 (what doctor checks per row).

**You own:** `brain/ahbo/speech/**`, `brain/ahbo/trace/**`, `brain/ahbo/ops/{doctor,render_fixed,wipe,backup,caps}.py`,
`brain/tests/{speech,trace,ops}/**`. NOT `ops/cli.py`, `ops/secrets.py`, `ops/seed.py`, `api/**` (read them).

**Already on disk from a stopped agent (review, keep what is right, finish):** `speech/{__init__,audio,segments,fakes,azure_tts}.py`,
`trace/{__init__,spans,costs}.py`, `ops/caps.py`. There are **no tests yet** for any of them.

**How the platform calls you (already written, read `brain/ahbo/api/services.py` lines ~200–260, `api/routes/operator.py`,
`api/routes/media.py`, `ops/cli.py`):**
- `ahbo.ops.render_fixed.ensure_manifest(svc)` at startup and `fixed_dir(settings_or_svc) -> Path` (default
  `data/media/fixed`). The platform currently writes a text-only fallback manifest marked `"fallback": true`;
  `ensure_manifest` must replace it.
- `ahbo.ops.doctor.rows(svc)` (sync or async) for the console Health panel → `[{item, status: "green"|"red"|"fake", detail}]`.
- `main(argv)` in `ahbo.ops.doctor`, `ahbo.ops.wipe`, `ahbo.ops.render_fixed` (the CLI delegates `ahbo doctor`, `ahbo wipe`,
  `ahbo render-fixed` to them).
- Optional `ahbo.speech.wiring.install(svc)` / `ahbo.trace.wiring.install(svc)`.

## Build (each with tests)
1. `speech/fakes.py` **FakeTTS**: a **silent** AAC `.m4a` (24 kHz mono, ffmpeg `anullsrc`; ffmpeg is in the image) of
   duration 0.19 s × characters, plus estimated caption segments `[firstChar, endChar, startSec, endSec]` with offsets in
   **UTF-16 code units**; punctuation gets a short pause. Test: durations, segment monotonicity, last endChar == UTF-16 length.
2. `speech/azure_tts.py` real adapter (Azure Speech SDK `azure.cognitiveservices.speech` 1.53, installed): voice from
   `AZURE_TTS_VOICE`, SSML `<prosody rate="-10%">`, 24 kHz mono, word-boundary events → segments. **Never run it** (no key).
   VERIFY in the image the exact enum/attribute names (`SpeechSynthesisOutputFormat` members, the word-boundary event's
   `text_offset`, `word_length`, `audio_offset` (100-ns ticks), `duration`). Unit-test only the boundary→segments conversion
   with synthetic event objects.
3. `ops/render_fixed.py`: renders from `brain/config/fixed_lines.yaml`: F1, F2, F3, F4, F4p, F5, A1, G1, G2; one `R_<slot_id>`
   per row of `med_schedule` (R_TEMPLATE, med names joined like 「血壓藥、糖尿藥、膽固醇藥同埋薄血丸」, names from
   `med_catalogue.yue`); and the P1 phone clip (P1_TEMPLATE with the resident address from the `resident` table) saved under
   `data/media/public/<random>/P1.m4a`, the random dir name persisted in the DB kv key `public_clip_path`.
   Writes `manifest.json` = `{"version": "f-<sha1 of texts+voice+mode>", "clips": {id: {file, text, segments, duration}}}`
   (CONTRACTS §11). Uses FakeTTS when `settings.is_fake("speech")`, else Azure. `ensure_manifest(svc)` re-renders only if the
   manifest is missing, marked fallback, or its version differs (fast in fake mode). Also writes a copy of the bundle into
   `ios/AhBo/Resources/fixed/` (a generated path). Also creates, if missing: `brain/seed/voice/demo_daughter.m4a` (silent,
   duration from the transcript length in `seed/demo.yaml`) and `brain/seed/fall_placeholder.jpg` (a plain dark 640×480 JPEG via Pillow).
   Countdown ticks are NOT rendered (the face beeps itself) — say so in the report.
4. `trace/spans.py`: a context manager / decorator recording spans into the `spans` table (id, correlation_id, name,
   start/end, duration_ms, status, attrs); a helper for the turn latency log (eos→stt_final→llm_ttft→tts_first_audio).
   `trace/costs.py`: `record_cost(db, provider, model, units, usd, correlation_id, purpose)` and `spend_today(db, clock)`.
5. `ops/caps.py`: `check_cap(db, clock, config, name) -> (ok, used, cap)` for llm_usd, deep_tasks, twilio_calls (safety
   calls exempt up to 3 per incident), playwright_actions, sessions_per_day, session_minutes (values in `config/caps.yaml`).
6. `ops/doctor.py`: `rows(svc)` + `main(argv)` printing a green/red/fake table. Rows: each key present (masked to last 4),
   each model route (faked → status `fake`, detail "fake (needs owner)"; real → the code for one tiny call, but ONLY run when
   that item is not in AHBO_FAKE — never run it yourself), Azure STT/TTS, LiveKit token mint, Telegram webhook, Twilio auth
   + verified numbers + dry-run TwiML, Playwright (connect to `settings.browser_ws`), iPad connected (`devices` table),
   beacon heartbeat (`beacons`), fixed-cache version, clock offset, disk free, DB `PRAGMA integrity_check`. Every red row says
   what to do (point at the SETUP.md section). `--latency` prints p50/p95 of the last 50 turns from `spans`.
7. `ops/wipe.py`: `main(argv)` requiring `--yes`; delegate to `ahbo.memory.retention.export_all(db)` then
   `ahbo.memory.retention.wipe(db, settings.data_dir)` (already written by the memory component — read it).
   `ops/backup.py`: `backup(db_path, dest_dir, keep=7)` using the sqlite3 backup API (note: `api/backup.py` already runs a
   nightly loop — check whether it should call yours; report, don't edit it).

## Done when
- `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/speech tests/trace tests/ops` all pass.
- In a throwaway container with a temp data dir, `ensure_manifest` produces real silent `.m4a` files whose `ffprobe`
  durations match the manifest (±0.1 s), and a manifest with `"fallback": true` is replaced.
- `docker run --rm -v /opt/ahbo:/repo -e AHBO_DATA_DIR=/tmp/d ahbo-api python -m ahbo.ops.doctor` prints the table with
  every faked provider as `fake`.
- Full suite: nothing new fails.
