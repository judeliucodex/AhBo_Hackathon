"""ops.doctor: rows() in fake mode (no network), key masking, red-row hints, --latency, mocked real checks.

The real-mode tiny calls are exercised with monkeypatched `_tiny_*` functions only — the doctor itself must
never spend money in tests (no keys exist).
"""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from ahbo.core.clock import FakeClock, get_clock, set_clock
from ahbo.core.config import load_config
from ahbo.core.db import open_db
from ahbo.core.settings import load_settings
from ahbo.ops import doctor
from ahbo.ops.doctor import HINT, key_rows, main, model_route_rows, rows
from ahbo.ops.render_fixed import render_all
from ahbo.ops.seed import load_demo


def make_svc(tmp_path: Path, **kw) -> SimpleNamespace:
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))
    settings = dataclasses.replace(load_settings(), data_dir=tmp_path, **kw)
    db = open_db(":memory:")
    load_demo(db, get_clock(), settings)
    return SimpleNamespace(settings=settings, db=db, clock=get_clock(), config=load_config(fresh=True), extra={})


async def _rows_of(svc) -> list[dict[str, str]]:
    return await rows(SimpleNamespace(settings=svc.settings, db=svc.db, clock=svc.clock, config=svc.config,
                                      extra={}))


def test_fake_mode_rows_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake mode: every faked provider is `fake` + "fake (needs owner)"; nothing calls out (A5)."""
    def explode(*_a, **_k):                     # any real call attempt fails the test
        raise AssertionError("doctor tried a live call in fake mode")

    for name in ("_tiny_openai", "_tiny_anthropic", "_azure_tts_tiny", "_azure_stt_tiny"):
        monkeypatch.setattr(doctor, name, explode)
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", explode, raising=False)
    svc = make_svc(tmp_path)
    render_all(svc)                             # what the api start-up does before the first health check
    table = asyncio.run(_rows_of(svc))

    by_item = {r["item"]: r for r in table}

    def find(prefix: str) -> dict[str, str]:
        for k, r in by_item.items():
            if k.startswith(prefix):
                return r
        raise AssertionError(f"no row starting with {prefix!r}")

    for prefix in ("model flash (", "model flash fallback (", "model sonnet (", "model vision (",
                   "Azure TTS", "Azure STT", "LiveKit", "Telegram webhook", "Twilio"):
        r = find(prefix)
        assert r["status"] == "fake", r
        assert r["detail"] == "fake (needs owner)", r
    # structural rows
    assert by_item["Database"]["status"] == "green"
    assert by_item["Fixed cache"]["status"] == "green"
    assert by_item["Clock"]["status"] == "green"
    assert by_item["Disk"]["status"] == "green"
    assert {r["status"] for r in table} <= {"green", "red", "fake"}


def test_every_red_row_says_what_to_do(tmp_path: Path) -> None:
    table = asyncio.run(_rows_of(make_svc(tmp_path)))
    for r in table:
        if r["status"] == "red":
            assert "→" in r["detail"] or "SETUP.md" in r["detail"], r
    red_items = {r["item"] for r in table if r["status"] == "red"}
    assert "iPad connected" in red_items and "Beacon heartbeat" in red_items     # fresh demo DB: nothing connected


def test_fixed_cache_red_when_fallback(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    render_all(svc)
    import json

    from ahbo.ops.render_fixed import fixed_dir
    path = fixed_dir(svc.settings) / "manifest.json"
    m = json.loads(path.read_text())
    m["fallback"] = True
    path.write_text(json.dumps(m, ensure_ascii=False))
    row = doctor.fixed_cache_row(svc)
    assert row["status"] == "red" and "render-fixed" in row["detail"]


def test_key_rows_mask_and_hint() -> None:
    s = load_settings()
    settings = dataclasses.replace(s, app_secret="0123456789abcdef", operator_password_hash="", beacon_tokens={})
    table = {r["item"]: r for r in key_rows(settings)}
    assert table["APP_SECRET"]["status"] == "green"
    assert table["APP_SECRET"]["detail"].startswith("set (…cdef")       # last 4 only, never the secret
    assert table["OPERATOR_PASSWORD_HASH"]["status"] == "red"
    assert "SETUP.md §27.1 #8" in table["OPERATOR_PASSWORD_HASH"]["detail"]
    assert table["DEEPSEEK_API_KEY"]["status"] == "green" and "(optional)" in table["DEEPSEEK_API_KEY"]["detail"]
    assert table["BEACON_TOKENS"]["status"] == "green"                  # optional, unset


def test_real_mode_missing_key_is_red_with_hint(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    settings = dataclasses.replace(svc.settings, fake=svc.settings.fake - {"models"}, deepseek_api_key="")
    table = asyncio.run(model_route_rows(settings, svc.config))
    for r in table:
        if "(deepseek/" in r["item"]:
            assert r["status"] == "red" and "DEEPSEEK_API_KEY" in r["detail"] and HINT + "1" in r["detail"], r


def test_real_mode_tiny_call_gives_model_id_and_ttft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = make_svc(tmp_path)
    settings = dataclasses.replace(svc.settings, fake=svc.settings.fake - {"models"},
                                   deepseek_api_key="key", anthropic_api_key="key")
    monkeypatch.setattr(doctor, "_tiny_openai", lambda *a, **k: {"model_id": "deepseek-flash", "ms": 42,
                                                                 "thinking_ok": True})
    monkeypatch.setattr(doctor, "_tiny_anthropic", lambda *a, **k: {"model_id": "claude-sonnet-5-5", "ms": 30})
    table = asyncio.run(model_route_rows(settings, svc.config))
    assert {r["status"] for r in table} == {"green"}
    flash = [r for r in table if r["item"].startswith("model flash (")]
    assert flash and "deepseek-flash" in flash[0]["detail"] and "42 ms" in flash[0]["detail"]
    assert "thinking field accepted" in flash[0]["detail"]


def test_ipad_and_beacon_rows(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    assert doctor.ipad_row(svc)["status"] == "red"
    svc.db.execute("INSERT INTO devices(id, last_seen, online, app_version, battery) VALUES (?,?,?,?,?)",
                   ("ipad-air5-1", get_clock().iso(), 1, "0.1.0", 0.81))
    r = doctor.ipad_row(svc)
    assert r["status"] == "green" and "ipad-air5-1" in r["detail"] and "81%" in r["detail"]
    assert doctor.beacon_row(svc)["status"] == "red"                     # never seen
    svc.db.execute("INSERT INTO beacons(id, token_hash, last_seen) VALUES (?,?,?)",
                   ("beacon1", "x", get_clock().iso()))
    r = doctor.beacon_row(svc)
    assert r["status"] == "green" and "0 s ago" in r["detail"]
    svc.db.execute("UPDATE beacons SET last_seen=?", ("2026-11-19T09:00:00+08:00",))
    assert doctor.beacon_row(svc)["status"] == "red" and "min ago" in doctor.beacon_row(svc)["detail"]


def test_twiml_dry_run_row(tmp_path: Path) -> None:
    """Real-mode TwiML dry run uses the landed links builder — no network, no call placed."""
    svc = make_svc(tmp_path)
    render_all(svc)
    r = doctor._twiml_row(svc.settings)
    assert r["status"] == "green", r
    assert "call_twiml" in r["detail"] and "chars" in r["detail"]
    # and the XML it built is exactly what the live route returns for the same inputs
    from ahbo.links.twilio import call_twiml

    xml = call_twiml(doctor._clip_url_for_twiml(svc.settings),
                     f"{svc.settings.public_base_url}/twilio/gather?call_id=doctor-check")
    assert "<Response>" in xml and "<Play" in xml and "<Gather" in xml


def test_doctor_cli_prints_table_and_latency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("AHBO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AHBO_FAKE", "all")
    from ahbo.core import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))
    from ahbo.trace.spans import record_span

    svc = make_svc(tmp_path)
    db = svc.db
    db.close()
    db = open_db(tmp_path / "ahbo.sqlite")           # same path the CLI's get_settings().db_path resolves to
    load_demo(db, get_clock(), settings_mod.get_settings())
    render_all(SimpleNamespace(settings=settings_mod.get_settings(), db=db, clock=get_clock(), config=svc.config,
                               extra={}))
    for i in range(3):
        record_span(db, "turn.latency", correlation_id=f"s{i}", duration_ms=3000.0 + i * 100,
                    attrs={"turn_id": f"t{i}", "total_ms": 3000.0 + i * 100})
    rc = main(["--latency"])
    out = capsys.readouterr().out
    assert "checks:" in out and "fake" in out
    assert "latency total_ms: p50 3100.0 ms · p95 3190.0 ms (n=3)" in out
    assert rc == 1                        # fresh container: iPad/beacon rows are red
    settings_mod.get_settings.cache_clear()
    db.close()
