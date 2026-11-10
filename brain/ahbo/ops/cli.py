"""`ahbo` command line (§17). Run inside the api container: `docker compose run --rm api ahbo <cmd>`
(or `python -m ahbo.ops.cli <cmd>`). Never prints a secret except `show-app-secret`, which exists to print one.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import inspect
import json
import os
import subprocess
import sys
from typing import Any

from ahbo.core.settings import REPO_ROOT


def _import(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name and (name == e.name or name.startswith(e.name + ".")):
            return None
        raise


def _delegate(candidates: tuple[str, ...], argv: list[str]) -> int:
    """Run `main(argv)` of the first module that exists (other components own these commands)."""
    for modname in candidates:
        mod = _import(modname)
        if mod is not None and hasattr(mod, "main"):
            r = mod.main(argv)
            if inspect.isawaitable(r):
                r = asyncio.run(r)
            return int(r or 0)
    print(f"not available yet: {' / '.join(candidates)}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------- secrets
def cmd_init_secrets(a: argparse.Namespace) -> int:
    from . import secrets as s
    r = s.init_secrets()
    print("set in .env:", ", ".join(r["set"]) or "(nothing; already initialised)")
    if r["initial_password_file"]:
        print("initial operator password written to", r["initial_password_file"][0], "(mode 600)")
        print("read it with `make show-initial-password`, then replace it with `make set-password`.")
    return 0


def cmd_set_password(a: argparse.Namespace) -> int:
    from . import secrets as s
    pw = os.environ.get("AHBO_NEW_PASSWORD") if a.from_env else None
    if pw is None:
        pw = getpass.getpass("New operator password: ")
        if pw != getpass.getpass("Repeat: "):
            print("passwords differ", file=sys.stderr)
            return 1
    try:
        s.set_password(pw)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("operator password updated; restart the api (`make restart`) to apply it. Initial-password file removed.")
    return 0


def cmd_show_app_secret(a: argparse.Namespace) -> int:
    from . import secrets as s
    v = s.read_env().get("APP_SECRET")
    if not v:
        print("APP_SECRET is not set; run `make init-secrets`", file=sys.stderr)
        return 1
    print(v)
    return 0


def cmd_show_initial_password(a: argparse.Namespace) -> int:
    from . import secrets as s
    if not s.INITIAL_PW_PATH.exists():
        print("no initial password file (already replaced with `make set-password`)", file=sys.stderr)
        return 1
    print(s.INITIAL_PW_PATH.read_text().strip())
    return 0


# ---------------------------------------------------------------- data
def cmd_seed(a: argparse.Namespace) -> int:
    from ahbo.core.clock import get_clock
    from ahbo.core.db import open_db
    from ahbo.core.settings import get_settings
    from ahbo.ops.seed import load_demo
    st = get_settings()
    db = open_db(st.db_path)
    off = db.kv_get("clock_offset_s")
    if off is not None:
        get_clock().set_offset(float(off))
    load_demo(db, get_clock(), st)
    db.close()
    print("seed loaded into", st.db_path.name, "(restart the api or use Reset demo to clear in-memory state)")
    return 0


def cmd_clock(a: argparse.Namespace) -> int:
    body: dict[str, Any] = {}
    if a.set:
        body["set"] = a.set
    elif a.offset is not None:
        body["offset_s"] = a.offset
    elif a.anchor:
        body["anchor"] = a.anchor
    from ahbo.core.settings import get_settings
    st = get_settings()
    if not body:
        from ahbo.core.clock import Clock
        from ahbo.core.db import open_db
        db = open_db(st.db_path)
        off = float(db.kv_get("clock_offset_s", 0) or 0)
        print(json.dumps({"now": Clock(off).iso(), "offset_s": off}, ensure_ascii=False))
        return 0
    import httpx
    url = os.environ.get("AHBO_CLOCK_URL", "http://127.0.0.1:8000") + "/internal/clock"
    try:
        r = httpx.post(url, json=body, headers={"Authorization": f"Bearer {st.internal_token}"}, timeout=5)
        print(r.text)
        return 0 if r.status_code == 200 else 1
    except httpx.HTTPError:
        # api not running: persist in kv; it is loaded at the next start
        from ahbo.core.clock import Clock
        from ahbo.core.db import open_db
        c = Clock()
        if "set" in body:
            c.set_time(body["set"])
        elif "offset_s" in body:
            c.set_offset(float(body["offset_s"]))
        else:
            from ahbo.core.config import load_seed
            anchors = load_seed("demo.yaml").get("scenario_anchors") or {}
            c.set_time(anchors["S4" if body["anchor"] == "S4ok" else body["anchor"]])
        db = open_db(st.db_path)
        db.kv_set("clock_offset_s", c.offset_s)
        print(json.dumps({"ok": True, "now": c.iso(), "offset_s": c.offset_s, "note": "api not running; saved"}))
        return 0


# ---------------------------------------------------------------- chat --text
class PrintDevice:
    """A fake iPad for the REPL: prints face commands and answers like the device would."""

    def __init__(self, svc: Any, quiet: bool):
        self.svc, self.quiet = svc, quiet
        svc.kernel.on("device", self.on)

    def on(self, c: Any) -> None:
        from ahbo.core.events import Event
        p = c.payload
        if not self.quiet:
            if c.type == "device.ui":
                print("   [face]", json.dumps(p["ui"], ensure_ascii=False), flush=True)
            else:
                print("   [device]", c.type, json.dumps(p, ensure_ascii=False), flush=True)
        loop = asyncio.get_running_loop()
        k = self.svc.kernel
        if c.type == "device.play_fixed":
            dur = max(1.0, len(self.svc.config.fixed_lines.get(p["clip_id"], "")) * 0.19)
            loop.call_later(dur * k.time_scale, k.submit_nowait, Event(
                type="device.playback_done", source="device", payload={"clip_id": p["clip_id"], "say_id": p["say_id"]}))
        elif c.type == "device.play_voice_message":
            loop.call_later(float(p.get("duration_s") or 2) * k.time_scale, k.submit_nowait, Event(
                type="device.playback_done", source="device",
                payload={"message_id": p["message_id"], "say_id": p["say_id"]}))
        elif c.type == "device.capture_photo":
            loop.call_later(0.3, k.submit_nowait, Event(type="device.photo_uploaded", source="device",
                            payload={"photo_id": "ph_cli", "incident_id": p["incident_id"]}))


async def _chat(a: argparse.Namespace) -> int:
    import logging

    from ahbo.api.services import build_services
    from ahbo.core.clock import Clock
    from ahbo.core.settings import get_settings
    logging.basicConfig(level=logging.WARNING)
    st = get_settings()
    clock = Clock()
    db_path = a.db or ":memory:"
    svc = build_services(st, db_path=db_path, clock=clock, time_scale=a.time_scale, with_hub=False,
                         seed_if_empty=True)
    if a.at:
        clock.set_time(a.at)
    elif db_path != ":memory:":
        clock.set_offset(float(svc.db.kv_get("clock_offset_s", 0) or 0))
    PrintDevice(svc, quiet=not a.verbose)

    def on_event(kind: str, obj: dict[str, Any]) -> None:
        if kind == "kernel_event" and obj.get("type") == "agent.reply_start":
            print("阿寶:", (obj.get("payload") or {}).get("text"), flush=True)

    svc.kernel.subscribe(on_event)
    await svc.start()
    try:
        print(f"(virtual time {clock.now():%Y-%m-%d %H:%M}; text session; Ctrl-D to quit)", flush=True)
        lines = list(a.say or [])
        interactive = not lines and sys.stdin.isatty()
        while True:
            if lines:
                text = lines.pop(0)
                print("王婆婆:", text, flush=True)
            elif a.say:
                break
            else:
                try:
                    text = await asyncio.to_thread(input, "王婆婆: ") if interactive else \
                        await asyncio.to_thread(sys.stdin.readline)
                except EOFError:
                    break
                if not interactive:
                    if text == "":
                        break
                    text = text.strip()
                    print("王婆婆:", text, flush=True)
            if not text:
                continue
            await svc.text_loop.utterance(text)
            if svc.text_loop.task:
                try:
                    await asyncio.wait_for(asyncio.shield(svc.text_loop.task), 120)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            await asyncio.sleep(0.2)
    finally:
        await svc.stop()
    return 0


def cmd_chat(a: argparse.Namespace) -> int:
    return asyncio.run(_chat(a))


# ---------------------------------------------------------------- delegations
def cmd_sim(a: argparse.Namespace) -> int:
    script = REPO_ROOT / "sim" / f"{a.what}.py"
    if not script.exists():
        alt = REPO_ROOT / "sim" / f"{a.what}_fake.py"
        script = alt if alt.exists() else script
    if not script.exists():
        print(f"not available yet: sim/{a.what}.py", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(script), *a.rest])


DELEGATES = {
    "doctor": ("ahbo.ops.doctor",),
    "wipe": ("ahbo.ops.wipe",),
    "render-fixed": ("ahbo.ops.render_fixed",),
    "gen-wake-table": ("ahbo.ops.gen_wake_table", "ahbo.wake.table", "ahbo.wake.gen_table"),
    "eval": ("ahbo.ops.eval", "evals.runner"),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ahbo", description="Ah Bo (阿寶) harness CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-secrets", help="create/complete .env (mode 600)").set_defaults(fn=cmd_init_secrets)
    sp = sub.add_parser("set-password", help="set the operator password (getpass)")
    sp.add_argument("--from-env", action="store_true", help="read AHBO_NEW_PASSWORD (automation)")
    sp.set_defaults(fn=cmd_set_password)
    sub.add_parser("show-app-secret", help="print APP_SECRET for the iPad").set_defaults(fn=cmd_show_app_secret)
    sub.add_parser("show-initial-password").set_defaults(fn=cmd_show_initial_password)
    sub.add_parser("seed", help="load seed/demo.yaml into the DB").set_defaults(fn=cmd_seed)
    c = sub.add_parser("chat", help="text REPL on the real Kernel")
    c.add_argument("--text", action="store_true", help="text session (the only kind on the CLI)")
    c.add_argument("--say", action="append", help="say this line (repeatable), then exit")
    c.add_argument("--at", help="virtual time HH:MM (default: now)")
    c.add_argument("--db", help="database path (default: a fresh in-memory demo DB)")
    c.add_argument("--time-scale", type=float, default=0.2, help="timer scale (1.0 = real time)")
    c.add_argument("-v", "--verbose", action="store_true", help="print every face command")
    c.set_defaults(fn=cmd_chat)
    ck = sub.add_parser("clock", help="show or set the virtual clock")
    g = ck.add_mutually_exclusive_group()
    g.add_argument("--set", help="HH:MM")
    g.add_argument("--offset", type=float, help="seconds")
    g.add_argument("--anchor", choices=["S1", "S2", "S3", "S4", "S4ok"])
    ck.set_defaults(fn=cmd_clock)
    sm = sub.add_parser("sim", help="sim/beacon.py or sim/telegram_fake.py")
    sm.add_argument("what", choices=["beacon", "telegram", "twilio"])
    sm.add_argument("rest", nargs=argparse.REMAINDER)
    sm.set_defaults(fn=cmd_sim)
    for name, mods in DELEGATES.items():
        d = sub.add_parser(name, help=f"delegates to {mods[0]}")
        d.add_argument("rest", nargs=argparse.REMAINDER)
        d.set_defaults(fn=lambda a, mods=mods: _delegate(mods, a.rest))
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return int(a.fn(a) or 0)


if __name__ == "__main__":
    sys.exit(main())
