# QA tasks 21–26: common rules (read fully first)

You are a **tester** for Ah Bo (阿寶), a Cantonese robot-pet companion for 王婆婆 (78, lives alone, Kowloon). The repo is
`/opt/ahbo` on the production VPS. Your job is to **find what is broken or unsafe**, prove it with a reproduction, and
report it. You do **not** fix code: other agents are editing the repo right now, and the orchestrator (Claude) fixes
what you find. **The iPad app (`ios/`) is out of scope.**

## 1. Read first
1. `docs/FULL_GUIDE.md` (plan, gates, current state; §5a–5d are the latest decisions and open items).
2. `docs/BUILD_LOG.md` §3 and §7–9 (decisions: Opper models, owner's fall rules, Telegram replaces the web console,
   ElevenLabs speech, digest/plan removed).
3. `docs/CONTRACTS.md` (interfaces; §14 = additions after the freeze).
4. The PRD sections your task names (`PRD_ahbo.md`). The PRD is the source of truth for behaviour, **except where
   BUILD_LOG §7–9 records an owner decision** — those win.

## 2. Hard rules (breaking one fails the task)
- **Read-only on the repo.** Never edit, create or delete files under `/opt/ahbo`, except your report
  (`docs/handoff/glm/2N_REPORT.md`) and evidence files under `docs/qa/2N/` (screenshots, logs). Don't run git.
- Scratch code goes in `/tmp/ahbo-qa-2N/` on the host (mounted into containers as `/scratch`).
- **Never touch the live stack**: no `docker compose`, no restarting/stopping `ahbo-*` containers you didn't start, no
  calls to ports 80/443/8000 on this host, no requests to `https://srv2135273.hstgr.cloud` (only task 24 may make the
  read-only exposure checks listed there). Never send a Telegram message, never call Telegram/Twilio/ElevenLabs/Opper/
  LiveKit for real. Your throwaway server runs with `AHBO_FAKE=all`.
- Never read or print `/opt/ahbo/.env`, `data/secrets/*` or any file named `Root_password.md`.
- Don't install anything on the host. Use the images: `ahbo-api` (Python, tests), `ahbo-browser` (Playwright 1.64.1).
- Clean up every container and network you create (`docker rm -f`, `docker network rm`).
- The server has 2 CPUs: don't run more than one full `pytest` at a time per task.

## 3. Your own throwaway server (fake mode, repo mounted read-only)
Replace `qa21` with your task number. Copy-paste:

```bash
N=qa21; mkdir -p /tmp/ahbo-$N
docker network create ahbo-$N
HASH=$(docker run --rm ahbo-api python -c "import bcrypt;print(bcrypt.hashpw(b'qa-test-password',bcrypt.gensalt()).decode())")
docker run -d --name ahbo-$N-browser --network ahbo-$N ahbo-browser        # only if your task uses a browser
docker run -d --name ahbo-$N-api --network ahbo-$N -v /opt/ahbo:/repo:ro -v /tmp/ahbo-$N:/scratch \
  -e AHBO_FAKE=all -e AHBO_PROFILE=demo -e AHBO_DATA_DIR=/tmp/qa -e TZ=Asia/Hong_Kong \
  -e PUBLIC_BASE_URL=http://ahbo-$N-api:8000 -e AHBO_BROWSER_WS=ws://ahbo-$N-browser:3000/ \
  -e APP_SECRET=$(openssl rand -hex 24) -e INTERNAL_TOKEN=$(openssl rand -hex 24) \
  -e SESSION_SECRET=$(openssl rand -hex 24) -e "OPERATOR_PASSWORD_HASH=$HASH" ahbo-api
sleep 8
```
- Talk to it **from another container on the same network** (no host ports):
  `docker run --rm --network ahbo-$N -v /tmp/ahbo-$N:/scratch ahbo-api python /scratch/probe.py`
  with `base_url="http://ahbo-qa21-api:8000"`; log in with `POST /login` form `password=qa-test-password`.
- To read its database: `docker exec ahbo-$N-api python -c "from ahbo.core.db import open_db; db=open_db('/tmp/qa/ahbo.sqlite'); print(db.query('SELECT …'))"`.
- Expected noise: one log line `could not update the iOS fixed bundle … Read-only file system` (the repo is read-only).
- Tear down: `docker rm -f ahbo-$N-api ahbo-$N-browser; docker network rm ahbo-$N`.

The existing automated tests run with: `docker run --rm -v /opt/ahbo:/repo:ro ahbo-api python -m pytest -q -p no:cacheprovider tests/<dir>`.
Write **new** probe tests in `/tmp/ahbo-qa-2N/` and run them against the read-only repo (verified recipe):
```python
# /tmp/ahbo-qa-2N/test_probe.py
import sys
sys.path.insert(0, "/repo/brain/tests")
from conftest import harness  # noqa: F401  real Kernel + in-memory DB + FakeClock + fake device recorder

async def test_s2_answer(harness):
    harness.kernel.clock.set_time("10:00")
    await harness.say("阿寶，我幾時食藥呀？")
    says = [x["ui"] for x in harness.device.sent if x["type"] == "device.ui" and x["ui"].get("type") == "say_start"]
    assert says and "十二點正" in says[-1]["text"]
```
```bash
docker run --rm -v /opt/ahbo:/repo:ro -v /tmp/ahbo-qa-2N:/scratch ahbo-api python -m pytest -q -p no:cacheprovider \
  --rootdir=/repo/brain -c /repo/brain/pyproject.toml /scratch/test_probe.py
```
`harness.device.sent` lists every command the server would send to the iPad; `harness.kernel.db` is the database;
`harness.event(type, source, **payload)` injects an event; timers run 20× faster (`AHBO_TEST_TIME_SCALE=0.05`).

## 4. How to test
- Test **behaviour against the spec**, not the code against itself. For each check: what the PRD/decision says should
  happen, what you did, what happened.
- Try to break it: wrong order, repeats, timeouts, missing data, odd Cantonese/Simplified/English input, restarts.
- Prefer real paths (HTTP, WebSocket, scenario API, Telegram `handle_update`) over calling internals.
- Every defect needs a **reproduction** someone else can run.

## 5. Report (write `docs/handoff/glm/2N_REPORT.md`)
```
# QA 2N report — <area>
## Summary            (counts by severity; overall verdict: usable / usable with issues / broken)
## Defects            (one block each, most severe first)
### D1 [S1|S2|S3] <one-line title>
- Where: path:line (if known)
- Spec: what should happen (PRD § / BUILD_LOG §)
- Repro: exact steps/commands
- Actual: what happened (paste output; evidence file in docs/qa/2N/)
## Passed checks      (one line each — what you verified works)
## Not tested / blocked (and why)
```
Severity: **S1** safety or data loss (missed fall alert, wrong call target, medicine fact wrong, secret leaked, stranger
gets access) · **S2** a feature doesn't work · **S3** cosmetic / minor. Be honest: "not tested" is fine, a false
"passed" is not.
