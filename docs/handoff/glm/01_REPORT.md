# GLM task 01 (ops, speech, trace) report

Date: 10 Nov 2026 · Branch: main (no git commands run, per rules)

## Post-handoff re-check (in-depth, same day)

After tasks 02–04 landed, the whole suite was re-run and **three integration mismatches with the links
component (task 04) were found and fixed in my files** — all covered by new tests:

1. **kv `public_clip_path` format clash** — links' `twilio.public_clip_url` reads it as a URL path
   (`/media/public/<rand>/P1.m4a`) plus a `public_clip_rand` dir-name key, and overwrites foreign values.
   My renderer originally stored the bare dir name there, so the two components would fight over the key at
   every restart. Fixed: `render_fixed` now writes **both** keys in links' format and reuses `public_clip_rand`
   when its dir still exists. Verified: with my render first, `links_public_clip_url(svc)` reuses my dir,
   mints nothing, and `links._rendered_p1` copies my exact rendered clip (test `test_kv_format_matches_links_twilio`).
2. **P1 was invisible to links' copy path** — `_rendered_p1` looks for `clips["P1"]["file"]`, which I had
   deliberately left out of `clips`. Fixed: P1 is now also rendered into the bundle dir (`media/fixed/P1.m4a`,
   one ffmpeg render, copied to the public dir) and listed in `clips` — so `/media/fixed/P1.m4a` serves the
   real clip and links copies it instead of generating a stand-in. Additive manifest key; nothing plays it as
   a fixed clip. The `"public"` manifest section stays for the console.
3. **Doctor's TwiML dry-run looked at the wrong module** (`ahbo.links.phone`, which never landed; the real
   module is `ahbo.links.twilio` with the pure builder `call_twiml(clip_url, gather_url)`). Fixed: `_twiml_row`
   discovers `call_twiml` in `ahbo.links.twilio` (keeping the `phone` fallback) and builds the exact XML the
   live route returns, with no network and no call placed (test `test_twiml_dry_run_row`).

Re-verification after the fixes:
- `python -m pytest -q tests/speech tests/trace tests/ops` → **60 passed**.
- Full suite **including** `tests/links` (which had been un-collectable mid-flight at handoff): **586 passed, 37 skipped**.
- Container gates re-run in a throwaway container: 13 clips, every ffprobe duration matches the manifest (±0.1 s);
  fallback manifest replaced; `links.twilio.call_twiml` builds TwiML containing my rendered clip's public URL;
  `/media/fixed/P1.m4a` present; seed assets present; doctor table on a rendered dir: `Fixed cache ok f-b2464e4d08b4 · 13 clips`,
  every faked provider `fake`.


## Files created/changed

**Created:**
- `brain/ahbo/ops/render_fixed.py` — fixed-clip cache (§10.3): `fixed_dir(settings_or_svc)`, `plan_texts`, `manifest_version`, `render_all`, `ensure_manifest(svc)`, `public_clip_url(settings, db)`, `main(argv)`; renders F1–F5, F4p, A1, G1, G2, `R_<slot_id>` per active `med_schedule` row, and the P1 phone clip into `data/media/public/<random>/P1.m4a` (random dir persisted in kv `public_clip_path`). Writes the iOS bundle copy to `ios/AhBo/Resources/fixed/` and, if missing, `brain/seed/voice/demo_daughter.m4a` + `brain/seed/fall_placeholder.jpg`.
- `brain/ahbo/ops/doctor.py` — `rows(svc)` (async, never raises) + `main(argv)`; 37 checks in fake mode. Every red row says what to do with a SETUP.md pointer. `--latency` prints p50/p95 of the last 50 turns.
- `brain/ahbo/ops/wipe.py` — `main(argv)` requiring `--yes`; export → `memory.retention.wipe`.
- `brain/ahbo/ops/backup.py` — `backup(db_path, dest_dir, keep=7)` (sqlite3 online-backup API, chmod 600, prune), `next_nightly_seconds`, `main(argv)`.
- `brain/ahbo/speech/wiring.py` — optional `install(svc)`: builds the fixed-clip TTS adapter onto `svc.extra["fixed_tts"]`.
- `brain/ahbo/trace/wiring.py` — optional `install(svc)`: `trace.spans.set_default_db(svc.db)` + the caps notifier (submits an informational `system.cap_exceeded` Kernel event).
- Tests: `brain/tests/speech/{test_speech_segments,test_speech_azure}.py`, `brain/tests/trace/{test_trace_spans,test_trace_costs,test_trace_wiring}.py`, `brain/tests/ops/{test_render_fixed,test_caps,test_doctor,test_wipe_backup}.py` (the latter two gained links-integration tests post-handoff: `test_kv_format_matches_links_twilio`, `test_twiml_dry_run_row`).

**Changed (pre-existing files from the stopped agent):**
- `brain/ahbo/speech/audio.py` — `subprocess.run(..., check=False)` (explicit; behaviour unchanged).
- `brain/ahbo/ops/caps.py` — merged a nested `if` (SIM102); behaviour identical.
- `brain/ahbo/trace/costs.py` — removed a stale `# noqa` comment. Nothing else.

**Reviewed and kept unchanged** (they were correct against the installed SDK): `speech/{__init__,segments,fakes,azure_tts}.py`, `trace/spans.py`.

**Generated artifacts now on disk** (written by the code, not by hand): `brain/seed/voice/demo_daughter.m4a` (silent AAC, ≈3.6 s), `brain/seed/fall_placeholder.jpg` (640×480 dark JPEG, 5.4 KB), `ios/AhBo/Resources/fixed/{manifest.json, 12 × .m4a}` (regenerated on every render). The orchestrator decides what to commit; `data/` is untouched (my tests and verifies used throwaway data dirs).

## Tests (exact command + the real last line of pytest output)

```
docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/speech tests/trace tests/ops
→ 60 passed in 87.90s        (post-handoff; 58 before the links-integration fixes)
```

## Full-suite run (last line)

Post-handoff, whole repo including `tests/links`, `tests/actions`, `tests/operator_ui`, `tests/sim`:

```
docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q
→ 586 passed, 37 skipped, 1 warning in 488.29s (0:08:08)
```

(At handoff time, before the links agent finished, the same run with `--ignore=tests/links` was
`510 passed, 33 skipped`. Nothing of mine ever failed.) The three "known failures" named in `00_COMMON.md`
(`test_fall_storyboards[S4|S4ok]`, `test_inv_06_traced`, `test_inv_08_operator_visible`) **pass**.

## What is real vs faked

- **Fake mode (everything that runs today, `AHBO_FAKE=all`):** all deterministic local work — FakeTTS renders real silent AAC files via ffmpeg (`anullsrc`, 24 kHz mono); segments estimated at 0.19 s/char in UTF-16 offsets; manifest versioning, kv writes, iOS copy, seed assets, spans/costs/caps tables, doctor's structural rows (DB integrity, disk, clock, devices, beacons, fixed cache) — all real code against real SQLite/files.
- **Real adapters written but never executed** (no keys exist; the rules forbid it): `AzureTTS` (real Speech SDK render + word boundaries), doctor's tiny model calls (`_tiny_openai`, `_tiny_anthropic`), Azure STT check, Telegram `getWebhookInfo`, Twilio account/verified-caller-ids checks, Playwright `connect(settings.browser_ws)`. In fake mode these rows are `fake (needs owner)` and the doctor provably makes no network call (test monkeypatches every real path with an alarm).
- The LiveKit check is a **local token mint** (HMAC JWT, no network) — it would genuinely work the moment keys are added.

## Deviations from PRD/CONTRACTS (and why)

1. **`manifest.json` has an extra top-level `"public"` key and a `clips["P1"]` entry** (CONTRACTS §11 shows only `{version, clips}`). P1 IS now in `clips` (post-handoff fix): the file is rendered into the bundle dir, so `/media/fixed/P1.m4a` serves it, the iOS bundle ships it, and `links.twilio._rendered_p1` finds it. The extra `"public"` section carries the random dir/duration/segments for the console. Both are additive JSON; no Kernel code sends `play_fixed {clip_id: "P1"}`.
2. **kv `public_clip_path` holds the URL path `/media/public/<rand>/P1.m4a` and `public_clip_rand` holds the dir name** — the format `ahbo.links.twilio.public_clip_url` reads (the task said "the random dir name"; that format lost to the one the money path actually consumes — see Post-handoff re-check). `render_fixed.public_clip_url(settings, db)` builds the full URL and tolerates both keys.
3. **R-clip med names joined with `join_names` from `models/fakes.py`** → 「王婆婆，夠鐘食藥喇：血壓藥、糖尿藥、膽固醇藥，同埋薄血丸。」 — the task's example had no comma before 同埋; I chose the shared helper so the reminder clip says the names exactly like the fake agent says them (one source of truth).
4. **`system.cap_exceeded` event type** is not in the CONTRACTS §4.1 catalogue (which is "minimum"); it is recorded via `kernel.note()` (informational, INV-6/INV-8) so cap warnings are visible in the console trace.
5. **Doctor exits 1 when any row is red** (fakes don't fail it) — not specified anywhere; chosen so `make doctor`/CI can gate on it.
6. **Countdown ticks are NOT rendered** — the face beeps itself (§4.3.1 / kit behaviour), as the task asked me to state here.
7. F4 and F4p are both always rendered (the Kernel plays whichever the profile wants); rendering both keeps the manifest stable across profile switches.
8. `seed/voice/demo_daughter.m4a` is created only **if missing**, so the owner can drop in a real recording (§27.2 #6) without it being overwritten.

## VERIFY results (library + version + exact names checked in the image)

- **azure-cognitiveservices-speech 1.53.0** (read in site-packages `speech.py` + live `dir()`):
  - `SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm` ✓ — and **no AAC output format exists** (24 kHz members are Raw/Riff PCM, MP3, Opus, Silk only), so the WAV→ffmpeg AAC route in `azure_tts.py` is the only correct one.
  - `PropertyId.SpeechServiceResponse_RequestWordBoundary` ✓, `SpeechServiceResponse_RequestPunctuationBoundary` ✓ (`SpeechServiceResponse_SentenceBoundaryEnabled` does NOT exist).
  - `SpeechSynthesisWordBoundaryEventArgs`: `audio_offset` → `int` (100-ns ticks), `duration` → `timedelta` (speech.py:2188), `text_offset`/`word_length` → `int` characters (`text_offset` = −1 when the raw value ≥ 2³¹), `text` → `str`, `boundary_type` → `SpeechSynthesisBoundaryType` with members `Word | Punctuation | Sentence`. `boundary_from_event` handles all of these (timedelta→ticks, `.name` lowercased).
  - `SpeechSynthesizer(speech_config, audio_config=None)` ✓ (default `audio_config` is a real object; passing None keeps audio in `result.audio_data`), `speak_ssml_async(ssml)` ✓, `synthesis_word_boundary` signal ✓, `ResultReason.SynthesizingAudioCompleted` ✓, `result.cancellation_details` (`.reason/.error_details/.code`) ✓, `result.audio_duration` → timedelta ✓.
  - STT side: `SpeechRecognizer(speech_config, audio_config=PushAudioInputStream(stream_format=AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)))` ✓ — note there is **no** `AudioStreamFormat.create_wave_format` factory in the Python SDK (it takes the params in `__init__`).
- **livekit-api 1.2.2**: `AccessToken(api_key, api_secret).with_identity(...).with_name(...).with_grants(VideoGrants(room_join=True, room=...)).to_jwt()` ✓ (`VideoGrants` is a dataclass with `room_join` and `room`).
- **playwright 1.64.1** (async API): `async_playwright()` context → `pw.chromium.connect(endpoint, *, timeout=...)` ✓ — the async API has no `connect_over_ws` (only `connect` and `connect_over_cdp`).
- **Pillow 13.0.0**: `Image.new("RGB", (640, 480), (24, 24, 28)).save(path, "JPEG", quality=85)` ✓ (verified by file magic + size in the image).
- **ffmpeg/ffprobe** (in image): `lavfi anullsrc=r=24000:cl=mono` + `-movflags +faststart` AAC 48k mono ✓; `ffprobe -show_entries stream=codec_name,sample_rate,channels:format=duration` ✓.
- **ahbo.links.twilio** (task 04's module, read from source post-handoff): `public_clip_url(svc)` reads kv `public_clip_rand` (dir name) + `public_clip_path` (URL path `/media/public/<rand>/P1.m4a`), mints them only when both are missing; `call_twiml(clip_url, gather_url) -> str` is the pure TwiML builder the live `POST /twilio/twiml/{call_id}` route and `GET /twilio/twiml/test` both use; `_rendered_p1(svc)` copies `clips["P1"]["file"]` out of my manifest into the public dir. All three verified against my renderer in a throwaway container (reuse, no kv thrash, TwiML carries my URL).

## Changes needed in files I don't own

1. **`ops/cli.py` (platform): delegated commands still cannot take flags** (re-checked after handoff — the REMAINDER subparsers are unchanged at line 293). `ahbo doctor --latency` and `ahbo wipe --yes` fail with `argparse: unrecognized arguments`; so does `ahbo doctor -- --latency`. Suggested minimal fix in `cli.main()`:
   ```python
   def main(argv=None):
       rest = list(sys.argv[1:] if argv is None else argv)
       if rest and rest[0] in DELEGATES:
           return _delegate(DELEGATES[rest[0]], rest[1:])
       a = build_parser().parse_args(rest)
       return int(a.fn(a) or 0)
   ```
   Until then `python -m ahbo.ops.doctor --latency` / `python -m ahbo.ops.wipe --yes` work (both modules have `__main__` blocks; verified rc=0/1 correctly).
2. **`api/backup.py` (platform): nightly loop should call `ahbo.ops.backup.backup`** instead of keeping its own copy of the sqlite3-backup logic (`backup_now` is byte-for-byte the same semantics minus my `next_nightly_seconds` helper). One source of truth; `backup_now` can stay as a thin alias. Still duplicated as of the post-handoff re-check. I did not edit it (report only, per task).
3. ~~Links agent (task 04): kv format~~ — **resolved post-handoff**: I adopted links' format (see Post-handoff re-check); nothing needed from them.
4. **SETUP.md still doesn't exist** (A6, another deliverable): doctor's red rows point at `SETUP.md §27.1 #n` — whoever generates it should follow PRD §27.1's row numbering (1=DeepSeek … 10=beacon), which is what the doctor references.
5. Cosmetic for whoever owns ruff gates: the remaining findings on my files are `BLE001` in `doctor.py` (deliberate: every check converts an exception into a red row; matches the spine's `except Exception: log.exception` style) and pre-existing `PYI034` in `trace/spans.py`.

## Known gaps / things I did not verify

- **No real provider check has ever run.** The real-mode code paths (tiny model calls, Azure STT/TTS, Telegram webhook info, Twilio HTTP checks, Playwright connect, LiveKit keys) are verified only by tests with mocks and by reading the installed SDKs / links source. First `make doctor` after keys are added may still surface surprises (e.g. exact Twilio JSON shapes, Telegram `getWebhookInfo` field names).
- `_tiny_anthropic(..., with_image=True)` (vision row) sends a 1×1 PNG with `max_tokens=1` — reasonable but untested against a live Anthropic API.
- Twilio verified-numbers matching compares exact E.164 strings (`+852…`) against `OutgoingCallerIds.phone_numbers`; if the owner's `.env` numbers are formatted differently, that row will need normalisation.
- `doctor.rows()` runs on every console (operator stream refresh ~30 s); in real mode that repeats the tiny billable model calls. Cheap, but the owner should know. `_twiml_row`'s kv lookup opens a second short-lived sqlite connection (WAL readers are safe, but noted).
- The stopped agent's `trace/spans.py`/`costs.py` SQL was tested behaviourally (insert/aggregate/percentiles) but I did not audit every query path other components may use.
- Segment estimates (0.19 s/char) are the PRD's fake-mode rule, not real speech timing; real Azure word boundaries will differ per clip (by design, `ensure_manifest` re-renders when `mode` changes so switching to real Azure regenerates the bundle).
- I never opened or read any file named `Root_password.md`; no secrets printed (keys masked to last 4 via `settings.mask`); no real API calls, no dials, no host changes, no git state changes (read-only `git status`/`log` only).
