"""`ahbo init-secrets` / `set-password` (BUILD_LOG D-4), the backup helper and `ahbo chat --text`."""
from __future__ import annotations

import sqlite3
import stat

from ahbo.api.auth import verify_password
from ahbo.api.backup import backup_now
from ahbo.ops import cli
from ahbo.ops import secrets as s


def _mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_init_secrets_creates_private_env(tmp_path):
    env, ex, pw = tmp_path / ".env", tmp_path / ".env.example", tmp_path / "secrets" / "operator_password.initial"
    ex.write_text("# comment\nAPP_SECRET=\nAHBO_FAKE=\nOPERATOR_PASSWORD_HASH=\nDEEPSEEK_API_KEY=\n")
    r = s.init_secrets(env, ex, pw)
    assert _mode(env) == 0o600 and _mode(pw) == 0o600
    vals = s.read_env(env)
    for k in ("APP_SECRET", "INTERNAL_TOKEN", "SESSION_SECRET", "TELEGRAM_WEBHOOK_SECRET"):
        assert len(vals[k]) >= 40, k
    assert vals["AHBO_FAKE"] == "all" and vals["AHBO_PROFILE"] == "demo" and vals["TZ"] == "Asia/Hong_Kong"
    assert vals["PUBLIC_BASE_URL"] == "https://srv2135273.hstgr.cloud"
    assert verify_password(pw.read_text().strip(), vals["OPERATOR_PASSWORD_HASH"])
    raw = env.read_text()
    assert "OPERATOR_PASSWORD_HASH='$2b$" in raw          # quoted: Compose must not interpolate `$`
    assert raw.startswith("# comment")
    assert pw.read_text().strip() not in raw              # plaintext never in .env
    assert "OPERATOR_PASSWORD_HASH" in r["set"] and r["initial_password_file"]
    # idempotent: nothing regenerated
    before = env.read_text()
    assert s.init_secrets(env, ex, pw)["set"] == []
    assert env.read_text() == before


def test_set_password_replaces_hash_and_deletes_initial(tmp_path):
    env, ex, pw = tmp_path / ".env", tmp_path / ".env.example", tmp_path / "pw.initial"
    ex.write_text("")
    s.init_secrets(env, ex, pw)
    s.set_password("a-much-better-password", env, pw)
    assert not pw.exists()
    assert verify_password("a-much-better-password", s.read_env(env)["OPERATOR_PASSWORD_HASH"])
    try:
        s.set_password("short", env, pw)
        raise AssertionError("short password accepted")
    except ValueError:
        pass


def test_backup_keeps_seven(tmp_path):
    db = tmp_path / "a.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()
    out = tmp_path / "backups"
    for i in range(9):
        backup_now(db, out, stamp=f"2026111{i}-0330")
    files = sorted(out.glob("ahbo-*.sqlite"))
    assert len(files) == 7 and files[0].name == "ahbo-20261112-0330.sqlite"
    assert sqlite3.connect(files[-1]).execute("SELECT x FROM t").fetchone() == (1,)


def test_chat_text_answers_med_question(capsys, tmp_path, monkeypatch):
    from ahbo.core.settings import reset_settings_cache
    monkeypatch.setenv("AHBO_DATA_DIR", str(tmp_path))      # keep the repo's data/ untouched
    reset_settings_cache()
    rc = cli.main(["chat", "--text", "--at", "10:00", "--time-scale", "0.05", "--say", "我幾時食藥呀？"])
    assert rc == 0
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if x.startswith("阿寶:"))
    assert "十二點" in line
    assert (tmp_path / "media" / "fixed" / "manifest.json").exists()
    reset_settings_cache()
