"""G3 (fake demo, visually): drive /operator/ + /sim/ through S1, S2, S3, S4, S4ok and screenshot keyframes.

Two modes, one file:

* **Isolated run (default, on the host; stdlib only, nothing installed):**
      python3 evals/visual/screens.py
  Creates the private Docker network `ahbo-t-g3`, starts an `ahbo-api` container (repo mounted read-only,
  `AHBO_FAKE=all`, data in the container's /tmp, throwaway secrets + a bcrypt hash of a throwaway password made
  inside the container) and an `ahbo-browser` container that re-runs this file in capture mode, then removes
  both containers and the network. Never touches the live stack, its ports, its data or `.env`.

* **Capture mode (inside the `ahbo-browser` image):** chosen by `--capture` or when `AHBO_BASE_URL` is set
  (that is what `deploy/make.sh screens` does against the live compose stack).
      python evals/visual/screens.py --capture --base http://api:8000
  The operator password comes from `AHBO_SCREENS_PASSWORD`, else `data/secrets/operator_password.initial`.

Capture: logs in, opens `/sim/` (browser iPad, 1180×820 frame at scale 1) and `/operator/` as two pages, hooks
the sim's face iframe `message` events exactly like `kit_capture.py`, and for each scenario clicks the console's
scenario button (POST /operator/api/scenario/<S>). Keyframes use kit_capture's rule (say_end +450 ms,
countdown +1500 ms, ok_button show, moods alarm/warm/curious/thinking, step done/failed) and are saved as
`docs/screens/sim/<S>_<nn>_<keyframe>.png` (+ `<S>_99_end.png`, the face command trace `<S>.trace.json`);
one console screenshot per scenario goes to `docs/screens/operator/<S>.png`. Finally `docs/screens/g3/pairs.json`
pairs every kit keyframe with the sim keyframe of the same name (order-aligned) for the visual review.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STORIES = ["S1", "S2", "S3", "S4", "S4ok"]
NET, API_NAME, BROWSER_NAME, ALIAS = "ahbo-t-g3", "ahbo-t-g3-api", "ahbo-t-g3-browser", "g3api"

QUIET_S = 5.0        # a scenario is over after this long with no face command, no caption and no OK button up
MAX_S = 150.0        # hard cap per scenario
VIEWPORT = {"width": 1600, "height": 1000}   # sim: 1240 px stage → bezel (1180×820 + 14 px border) at scale 1

# Same recorder as kit_capture.py: every {ahbo: cmd} the face iframe receives, in order.
RECORDER = """
(() => {
  if (window.__rec) return;
  window.__rec = [];
  addEventListener("message", ev => {
    if (ev.data && ev.data.ahbo) {
      const c = ev.data.ahbo;
      window.__rec.push({t: performance.now(), cmd: c});
      if (window.__keyframe) window.__keyframe(c);
    }
  });
})();
"""


def keyframe_name(cmd: dict) -> str | None:
    """Identical to kit_capture.keyframe_name."""
    t = cmd.get("type")
    if t == "say_end":
        return f"say_end_{cmd.get('id')}"
    if t == "countdown":
        return "countdown"
    if t == "ok_button" and cmd.get("show"):
        return "ok_button"
    if t == "mood" and cmd.get("mood") in ("alarm", "warm", "curious", "thinking"):
        return f"mood_{cmd['mood']}"
    if t == "step" and cmd.get("state") in ("done", "failed"):
        return f"step_{cmd.get('group')}_{cmd.get('id')}_{cmd['state']}"
    return None


# ====================================================================== isolated run (host)
def _docker(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, text=True,
                          stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)


def _cleanup() -> None:
    for name in (BROWSER_NAME, API_NAME):
        _docker("rm", "-f", name, check=False, capture=True)
    _docker("network", "rm", NET, check=False, capture=True)


def isolated_run(extra: list[str]) -> int:
    _cleanup()
    password = secrets.token_urlsafe(18)
    with tempfile.TemporaryDirectory(prefix="ahbo-g3-") as tmp:
        pw_env = Path(tmp) / "pw.env"
        pw_env.write_text(f"PW={password}\n")
        os.chmod(pw_env, 0o600)
        hashed = _docker("run", "--rm", "--env-file", str(pw_env), "ahbo-api:latest", "python", "-c",
                         "import bcrypt,os;print(bcrypt.hashpw(os.environ['PW'].encode(),bcrypt.gensalt(12)).decode())",
                         capture=True).stdout.strip()
        api_env = Path(tmp) / "api.env"
        api_env.write_text("\n".join([
            "AHBO_FAKE=all", "AHBO_REPO=/repo", "AHBO_DATA_DIR=/tmp/g3data",
            f"PUBLIC_BASE_URL=http://{ALIAS}:8000", f"AHBO_API_INTERNAL_URL=http://{ALIAS}:8000",
            f"APP_SECRET={secrets.token_urlsafe(32)}", f"SESSION_SECRET={secrets.token_urlsafe(32)}",
            f"INTERNAL_TOKEN={secrets.token_urlsafe(32)}", f"OPERATOR_PASSWORD_HASH={hashed}", ""]))
        os.chmod(api_env, 0o600)
        br_env = Path(tmp) / "browser.env"
        br_env.write_text(f"AHBO_SCREENS_PASSWORD={password}\n")
        os.chmod(br_env, 0o600)
        try:
            _docker("network", "create", NET, capture=True)
            _docker("run", "-d", "--name", API_NAME, "--network", NET, "--network-alias", ALIAS,
                    "-v", f"{REPO}:/repo:ro", "--env-file", str(api_env), "ahbo-api:latest", capture=True)
            probe = f"import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
            for _ in range(60):
                if _docker("exec", API_NAME, "python", "-c", probe, check=False, capture=True).returncode == 0:
                    break
                time.sleep(1)
            else:
                print(_docker("logs", "--tail", "80", API_NAME, check=False, capture=True).stderr, file=sys.stderr)
                print("api did not become healthy", file=sys.stderr)
                return 2
            print("api healthy on", NET)
            r = _docker("run", "--rm", "--name", BROWSER_NAME, "--network", NET, "--init", "--shm-size", "1g",
                        "-v", f"{REPO}:/repo", "-w", "/repo", "--env-file", str(br_env),
                        "ahbo-browser:latest", "python", "evals/visual/screens.py", "--capture",
                        "--base", f"http://{ALIAS}:8000", *extra, check=False)
            if r.returncode != 0:
                logs = _docker("logs", "--tail", "60", API_NAME, check=False, capture=True)
                print(logs.stdout[-6000:], logs.stderr[-6000:], file=sys.stderr)
            return r.returncode
        finally:
            _cleanup()


# ====================================================================== capture (browser image)
def _password() -> str:
    pw = os.environ.get("AHBO_SCREENS_PASSWORD", "")
    if pw:
        return pw
    f = REPO / "data" / "secrets" / "operator_password.initial"
    if f.is_file():
        return f.read_text().strip()
    raise SystemExit("no operator password: set AHBO_SCREENS_PASSWORD (or keep data/secrets/operator_password.initial)")


async def _login(context, base: str, password: str) -> None:
    """POST /login, then put the session cookie into the browser context without the Secure flag, so this also
    works over plain http against a stack whose PUBLIC_BASE_URL is https (make screens on the compose network)."""
    r = await context.request.post(f"{base}/login", form={"password": password, "next": "/operator/"},
                                   max_redirects=0)
    cookie = None
    for h in r.headers_array:
        if h["name"].lower() == "set-cookie":
            m = re.match(r"\s*ahbo_session=([^;]+)", h["value"])
            if m:
                cookie = m.group(1)
    if r.status not in (302, 303) or not cookie:
        raise SystemExit(f"login failed: HTTP {r.status}")
    await context.add_cookies([{"name": "ahbo_session", "value": cookie, "url": base, "httpOnly": True,
                                "sameSite": "Lax"}])


def _kind(name: str) -> str:
    """Keyframe kind for pairing: the Kernel's say/turn ids differ from the kit's (s1a, msg…), so
    say_end_<id> → say_end and step_<group>_<id>_<state> → step_<state>; moods/countdown/ok_button as is."""
    if name.startswith("say_end_"):
        return "say_end"
    if name.startswith("step_"):
        return "step_" + name.rsplit("_", 1)[1]
    return name


def _pairs(kit_dir: Path, sim_dir: Path) -> dict:
    """Pair kit and sim keyframes per scenario by keyframe kind, aligned in order (difflib)."""
    out: dict = {}
    pat = re.compile(r"^(S4ok|S\d)_(\d\d)_(.+)\.png$")

    def frames(d: Path, s: str) -> list[tuple[str, str]]:
        rows = []
        for p in sorted(d.glob(f"{s}_*.png")):
            m = pat.match(p.name)
            if m and m.group(1) == s and m.group(2) != "99":
                rows.append((m.group(3), p.name))
        return rows

    for s in STORIES:
        kit, sim = frames(kit_dir, s), frames(sim_dir, s)
        sm = difflib.SequenceMatcher(a=[_kind(k) for k, _ in kit], b=[_kind(k) for k, _ in sim], autojunk=False)
        rows = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                rows += [{"keyframe": _kind(kit[i][0]), "kit": kit[i][1], "sim": sim[j][1]}
                         for i, j in zip(range(i1, i2), range(j1, j2))]
            else:
                rows += [{"keyframe": kit[i][0], "kit": kit[i][1], "sim": None} for i in range(i1, i2)]
                rows += [{"keyframe": sim[j][0], "kit": None, "sim": sim[j][1]} for j in range(j1, j2)]
        rows.append({"keyframe": "end", "kit": f"{s}_99_end.png", "sim": f"{s}_99_end.png"})
        out[s] = rows
    return out


async def capture(base: str, out_sim: Path, out_op: Path, out_g3: Path, kit_dir: Path, only: list[str]) -> None:
    from playwright.async_api import async_playwright

    password = _password()
    for d in (out_sim, out_op, out_g3):
        d.mkdir(parents=True, exist_ok=True)
    for p in [*out_sim.glob("*.png"), *out_sim.glob("*.trace.json"), *out_op.glob("*.png")]:
        if p.name.split("_")[0].split(".")[0] in only or p.name == "idle.png":
            p.unlink()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport=VIEWPORT, locale="zh-HK", timezone_id="Asia/Hong_Kong")
        await _login(context, base, password)

        op = await context.new_page()
        await op.goto(f"{base}/operator/")
        await op.locator("[data-scenario=S1]").wait_for(timeout=20000)

        sim = await context.new_page()
        pending: list[asyncio.Task] = []
        state = {"story": "", "n": 0, "last": time.monotonic(), "says": set(), "ok": False}

        async def shoot(name: str, delay_ms: int) -> None:
            await asyncio.sleep(delay_ms / 1000)
            state["n"] += 1
            path = out_sim / f"{state['story']}_{state['n']:02d}_{name}.png"
            await sim.locator("#bezel").screenshot(path=str(path))

        async def on_keyframe(source, cmd):  # noqa: ARG001
            state["last"] = time.monotonic()
            t = cmd.get("type")
            if t == "say_start":
                state["says"].add(cmd.get("id"))
            elif t == "say_end":
                state["says"].discard(cmd.get("id"))
            elif t == "ok_button":
                state["ok"] = bool(cmd.get("show"))
            elif t == "reset":
                state["says"].clear()
                state["ok"] = False
            name = keyframe_name(cmd)
            if name and state["story"]:
                pending.append(asyncio.create_task(shoot(name, 1500 if name == "countdown" else 450)))

        await sim.expose_binding("__keyframe", on_keyframe)
        await sim.goto(f"{base}/sim/")
        await sim.locator("#conn[data-state=online]").wait_for(timeout=30000)
        face = await (await sim.locator("#face").element_handle()).content_frame()
        await face.locator("#eyes").wait_for()
        await face.evaluate(RECORDER)
        await asyncio.sleep(1.5)
        await sim.locator("#bezel").screenshot(path=str(out_sim / "idle.png"))

        for s in only:
            face = await (await sim.locator("#face").element_handle()).content_frame()
            await face.evaluate(RECORDER)                       # idempotent; re-hooks if the face reloaded
            await face.evaluate("window.__rec.length = 0")
            state.update(story=s, n=0, last=time.monotonic(), says=set(), ok=False)
            t0 = time.monotonic()
            async with op.expect_response(lambda r: r.url.endswith(f"/operator/api/scenario/{s}")) as resp:
                await op.locator(f"[data-scenario={s}]").click()
            body = await (await resp.value).json()
            if not body.get("ok"):
                raise SystemExit(f"{s}: scenario refused: {body}")
            while True:
                await asyncio.sleep(0.25)
                now = time.monotonic()
                busy = state["says"] or state["ok"]
                if now - t0 > MAX_S or (now - t0 > 3 and not busy and now - state["last"] > QUIET_S):
                    break
            if pending:
                await asyncio.gather(*pending)
                pending.clear()
            rec = await face.evaluate("window.__rec")
            r0 = rec[0]["t"] if rec else 0
            trace = [{"t_ms": round(r["t"] - r0), "cmd": r["cmd"]} for r in rec]
            (out_sim / f"{s}.trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1))
            await sim.locator("#bezel").screenshot(path=str(out_sim / f"{s}_99_end.png"))
            await op.screenshot(path=str(out_op / f"{s}.png"), full_page=True)
            print(s, len(trace), "face commands,", state["n"], "keyframes,", round(time.monotonic() - t0, 1), "s")
            state["story"] = ""
            await asyncio.sleep(2.5)
        await browser.close()

    pairs = _pairs(kit_dir, out_sim)
    (out_g3 / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    for s, rows in pairs.items():
        miss = [r["keyframe"] for r in rows if not r["kit"] or not r["sim"]]
        print(f"{s}: {len(rows)} pairs" + (f", unpaired: {miss}" if miss else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", action="store_true", help="capture mode (inside the ahbo-browser image)")
    ap.add_argument("--base", default=os.environ.get("AHBO_BASE_URL", ""))
    ap.add_argument("--only", default=",".join(STORIES), help="comma list of scenarios (default: all five)")
    ap.add_argument("--out", default=str(REPO / "docs" / "screens"))
    args = ap.parse_args()
    only = [s for s in args.only.split(",") if s]
    if bad := [s for s in only if s not in STORIES]:
        ap.error(f"unknown scenarios: {bad}")
    if not (args.capture or os.environ.get("AHBO_BASE_URL")):
        return isolated_run(["--only", ",".join(only)])
    if not args.base:
        ap.error("--base (or AHBO_BASE_URL) is required in capture mode")
    out = Path(args.out)
    asyncio.run(capture(args.base.rstrip("/"), out / "sim", out / "operator", out / "g3", out / "kit", only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
