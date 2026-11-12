# QA 23 evidence (Telegram caregiver + admin bot, offline / fake mode)

- `t1_commands.log` … `t6_demo_voice.log` — final output of each probe run (all passing).
- `probes/` — the probe files, exactly as run. They import only from the read-only repo
  (`/repo/brain/tests/bot/bot_helpers.py`); they never touch the network and never edit the repo.

## How to re-run (from the host; repo mounted read-only, scratch in /tmp)

```bash
mkdir -p /tmp/ahbo-qa-23
cp docs/qa/23/probes/*.py /tmp/ahbo-qa-23/
docker run --rm -v /opt/ahbo:/repo:ro -v /tmp/ahbo-qa-23:/scratch ahbo-api \
  python -m pytest -q -p no:cacheprovider --rootdir=/repo/brain -c /repo/brain/pyproject.toml \
  /scratch/t1_commands.py -s          # and likewise t2_edits t3_effects t4_phones t5_strangers_alerts t6_demo_voice
```

Everything runs in-process against the real services built in fake mode (`AHBO_FAKE=all`, the bot conftest's
`build_services` recipe): replies land in `svc.extra["telegram_channel"].replies` (FakeTelegram); no container,
port, network or external service is used. The fixed-clip renderer's `_copy_ios_bundle` is stubbed so the repo is
never written. One defect reproduced by `t1_commands.py::test_help_text_must_not_advertise_removed_digest`.
