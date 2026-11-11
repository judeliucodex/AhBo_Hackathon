"""Integration fixes on the platform side: life-story photos for the iPad (device token), the nightly backup using
`ahbo.ops.backup`, the console's fake controls going through the links helpers, the `ahbo` CLI handing delegated
commands their argv, and `EvalSystem.outbox()` decoding buttons."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from platform_app import login, svc_of

from ahbo.api import auth
from ahbo.api import backup as api_backup
from ahbo.ops import backup as ops_backup
from ahbo.ops import cli

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _photo(svc, photo_id: str, sub: str) -> None:
    d = svc.settings.media_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{photo_id}.png"
    p.write_bytes(PNG)
    svc.db.execute("INSERT INTO media(id, kind, path, created_at) VALUES (?,?,?,?)",
                   (photo_id, "photo", str(p), svc.clock.iso()))


# ------------------------------------------------------------------ /media/photo/{id}
def test_media_photo_device_token_only_for_life_story_photos(client):
    svc = svc_of(client)
    _photo(svc, "ph_story1", "stories")
    _photo(svc, "ph_fall1", "incidents")
    svc.db.execute("INSERT INTO life_stories(id,title,text,photo_id) VALUES (?,?,?,?)",
                   ("ls_t1", "結婚相", "一九六八年喺大會堂結婚。", "ph_story1"))
    dev = {"Authorization": f"Bearer {auth.make_device_token(svc.settings, 'ipad-1')}"}
    r = client.get("/media/photo/ph_story1", headers=dev)
    assert r.status_code == 200 and r.content == PNG
    assert client.get("/media/photo/ph_fall1", headers=dev).status_code == 403      # incident photos: console only
    assert client.get("/media/photo/ph_nope", headers=dev).status_code == 403
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/media/photo/ph_story1", headers=bad).status_code == 401
    assert client.get("/media/photo/ph_story1").status_code == 401
    login(client)
    assert client.get("/media/photo/ph_fall1").status_code == 200
    assert client.get("/media/photo/ph_story1").status_code == 200
    assert client.get("/media/photo/ph_nope").status_code == 404


# ------------------------------------------------------------------ backup: one source of truth
def test_backup_now_is_the_ops_backup(tmp_path, monkeypatch):
    db = tmp_path / "x.sqlite"
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()
    seen = []
    real = ops_backup.backup
    monkeypatch.setattr(api_backup, "backup", lambda *a, **kw: seen.append((a, kw)) or real(*a, **kw))
    out = api_backup.backup_now(db, tmp_path / "b", stamp="20261120-0330")
    assert seen and out.name == "ahbo-20261120-0330.sqlite" and out.is_file()
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert api_backup._seconds_until_next(datetime(2026, 11, 20, 3, 0, tzinfo=api_backup.HKT)) == 1800.0


async def test_nightly_loop_calls_ops_backup(tmp_path, monkeypatch):
    calls: list[tuple] = []
    done = asyncio.Event()

    def fake_backup(db_path, out_dir, *a, **kw):
        calls.append((db_path, out_dir))
        done.set()
        return Path(out_dir) / "ahbo-x.sqlite"

    monkeypatch.setattr(api_backup, "backup", fake_backup)
    monkeypatch.setattr(api_backup, "_seconds_until_next", lambda now: 0.0)
    task = asyncio.create_task(api_backup.nightly_loop(tmp_path / "db.sqlite", tmp_path / "backups"))
    try:
        await asyncio.wait_for(done.wait(), 5)
    finally:
        task.cancel()
    assert calls[0] == (tmp_path / "db.sqlite", tmp_path / "backups")


# ------------------------------------------------------------------ console fake controls → links helpers
def _wait(pred, timeout=5.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_console_fake_controls_use_links_helpers(client):
    svc = svc_of(client)
    login(client)
    r = client.post("/operator/api/trigger", json={"kind": "voice_note", "voice_id": "vm_demo"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body.get("via") == "operator_fake" and body["voice_id"].startswith("vm_")
    row = svc.db.one("SELECT contact_id, direction FROM voice_messages WHERE id=?", (body["voice_id"],))
    assert row == {"contact_id": "c_chun", "direction": "in"}
    r = client.post("/operator/api/trigger", json={"kind": "family_text", "text": "媽，記得食飯呀"})
    assert r.json()["ok"] is True and r.json()["via"] == "operator_fake"
    from ahbo.core.jsonutil import loads
    assert _wait(lambda: any(loads(e["payload"], {}).get("via") == "operator_fake" for e in
                             svc.db.query("SELECT payload FROM events WHERE type='family.text'")))
    assert client.post("/operator/api/trigger", json={"kind": "family_text", "text": " "}).json()["ok"] is False
    # outbox panel + press go through ahbo.links (outbox_list(db, limit=…), press(svc, outbox_id, button))
    assert isinstance(client.get("/operator/api/outbox").json()["items"], list)
    r = client.post("/operator/api/outbox/ob_nope/press", json={"button": "handled"})
    assert r.json().get("reason") == "not_found"


def test_links_helper_signatures_match_the_console():
    import inspect

    from ahbo.links import outbox, wiring
    assert list(inspect.signature(wiring.press).parameters) == ["svc", "outbox_id", "button"]
    assert list(inspect.signature(wiring.inject_voice_note).parameters) == ["svc", "file"]
    assert list(inspect.signature(wiring.inject_text).parameters) == ["svc", "text"]
    assert list(inspect.signature(outbox.outbox_list).parameters)[:2] == ["db", "limit"]


# ------------------------------------------------------------------ CLI: delegated commands get their argv
@pytest.mark.parametrize("argv,mod,rest", [
    (["doctor", "--latency"], "ahbo.ops.doctor", ["--latency"]),
    (["wipe", "--yes"], "ahbo.ops.wipe", ["--yes"]),
    (["render-fixed", "--force"], "ahbo.ops.render_fixed", ["--force"]),
    (["eval", "--suite", "scenarios", "--case", "S1"], "ahbo.evals.runner", ["--suite", "scenarios", "--case", "S1"]),
    (["backup", "--keep", "3"], "ahbo.ops.backup", ["--keep", "3"]),
    (["doctor", "--", "--latency"], "ahbo.ops.doctor", ["--", "--latency"]),
])
def test_cli_delegates_pass_flags_through(monkeypatch, argv, mod, rest):
    seen: list[tuple[str, list[str]]] = []

    def fake_import(name):
        return SimpleNamespace(main=lambda a, name=name: seen.append((name, a)) or 0)

    monkeypatch.setattr(cli, "_import", fake_import)
    assert cli.main(argv) == 0
    assert seen == [(mod, rest)]


def test_cli_eval_delegates_to_the_eval_runner():
    assert cli.DELEGATES["eval"] == ("ahbo.evals.runner",)
    from ahbo.evals import runner
    assert callable(runner.main)


def test_cli_non_delegated_commands_still_parse():
    with pytest.raises(SystemExit):
        cli.main(["no-such-command"])
    a = cli.build_parser().parse_args(["clock", "--set", "10:00"])
    assert a.cmd == "clock" and a.set == "10:00"


# ------------------------------------------------------------------ EvalSystem.outbox() decodes buttons
async def test_eval_outbox_decodes_buttons():
    from ahbo.core.events import P0, cmd
    from ahbo.evals.harness import EvalSystem
    s = await EvalSystem.create()
    try:
        await s.kernel.emit(cmd("family.send", P0, "inc_test", kind="ALERT", contact_ids=["c_chun"], text="測試"))
        assert await s.wait_for(lambda: bool(s.outbox()), 5)
        row = s.outbox()[0]
        assert isinstance(row["payload"], dict) and isinstance(row["buttons"], list)
        assert {b["id"] for b in row["buttons"]} >= {"handled", "resolved"}
    finally:
        await s.close()
