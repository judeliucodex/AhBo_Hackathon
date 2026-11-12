"""QA 26 D1 (public clip route serves only the current dir) and QA 25 D10 (sim placeholder photo)."""
from __future__ import annotations

from platform_app import login, svc_of

from ahbo.memory.retention import wipe

CUR, OLD = "01JCURRENTRANDOMDIR0000000", "01JOLDRANDOMDIR00000000000"


def _clip(svc, rand: str) -> None:
    d = svc.settings.media_dir / "public" / rand
    d.mkdir(parents=True, exist_ok=True)
    (d / "P1.m4a").write_bytes(b"\x00" * 64)


def test_public_clip_only_for_the_current_rand(client):
    svc = svc_of(client)
    _clip(svc, CUR)
    _clip(svc, OLD)
    svc.db.kv_set("public_clip_rand", CUR)
    assert client.get(f"/media/public/{CUR}/P1.m4a").status_code == 200       # Twilio fetches it without auth
    assert client.get(f"/media/public/{OLD}/P1.m4a").status_code == 404       # an old URL is dead
    svc.db.execute("DELETE FROM kv WHERE key='public_clip_rand'")
    assert client.get(f"/media/public/{CUR}/P1.m4a").status_code == 404       # no current clip → nothing served


def test_public_clip_gone_after_wipe(client):
    svc = svc_of(client)
    _clip(svc, CUR)
    svc.db.kv_set("public_clip_rand", CUR)
    assert client.get(f"/media/public/{CUR}/P1.m4a").status_code == 200
    wipe(svc.db, svc.settings.data_dir)
    assert client.get(f"/media/public/{CUR}/P1.m4a").status_code == 404
    assert not (svc.settings.media_dir / "public" / CUR / "P1.m4a").exists()


def test_sim_placeholder_photo_is_served(client):
    login(client)
    r = client.get("/sim/fall_placeholder.jpg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg")
    assert r.content[:2] == b"\xff\xd8"
