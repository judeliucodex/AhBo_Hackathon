"""ops.render_fixed (§10.3): clip planning, manifest versioning, ensure_manifest staleness rules."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ahbo.core.clock import FakeClock, get_clock, set_clock
from ahbo.core.config import load_config
from ahbo.core.db import open_db
from ahbo.core.settings import REPO_ROOT, SEED_DIR, load_settings
from ahbo.ops.render_fixed import (
    PUBLIC_CLIP_ID,
    ensure_manifest,
    fixed_dir,
    manifest_version,
    plan_texts,
    public_clip_url,
    render_all,
)
from ahbo.ops.seed import load_demo


@dataclass
class Svc:
    """Minimal services stand-in: settings/db/clock/config only (what render_all touches)."""
    settings: Any
    db: Any
    config: Any
    extra: dict = field(default_factory=dict)

    @property
    def clock(self):
        return get_clock()


def make_svc(tmp_path: Path) -> Svc:
    settings = replace(load_settings(), data_dir=tmp_path)
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))     # the demo loader anchors to the virtual clock
    db = open_db(":memory:")
    load_demo(db, get_clock(), settings)
    return Svc(settings=settings, db=db, config=load_config(fresh=True))


def test_fixed_dir_uses_settings(tmp_path: Path) -> None:
    settings = replace(load_settings(), data_dir=tmp_path)
    assert fixed_dir(settings) == tmp_path / "media" / "fixed"       # Settings
    svc = Svc(settings=settings, db=None, config=None)
    assert fixed_dir(svc) == tmp_path / "media" / "fixed"            # Services
    assert fixed_dir().name == "fixed"


def test_plan_texts_renders_slots_and_phone_clip(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    texts = plan_texts(svc.config, svc.db)
    for cid in ("F1", "F2", "F3", "F4", "F4p", "F5", "A1", "G1", "G2"):
        assert cid in texts, cid
    # one R clip per active schedule slot, med names joined like the agent says them
    assert {t for t in texts if t.startswith("R_")} == {"R_s_0800", "R_s_1200", "R_s_2000"}
    assert texts["R_s_1200"] == "王婆婆，夠鐘食藥喇：血壓藥、糖尿藥、膽固醇藥，同埋薄血丸。"
    assert texts["R_s_0800"] == "王婆婆，夠鐘食藥喇：糖尿藥。"
    # P1 carries the resident address from the resident table
    assert "九龍觀塘秀茂坪示範邨 12 樓 1203 室（示範地址）" in texts[PUBLIC_CLIP_ID]
    assert texts[PUBLIC_CLIP_ID].startswith("你好，呢度係阿寶。")


def test_version_changes_with_texts_voice_mode() -> None:
    v = manifest_version({"F1": "哎"}, "zh-HK-HiuMaanNeural", "fake")
    assert v.startswith("f-") and len(v) == 14
    assert v != manifest_version({"F1": "呀"}, "zh-HK-HiuMaanNeural", "fake")
    assert v != manifest_version({"F1": "哎"}, "zh-HK-HiuGaaiNeural", "fake")
    assert v != manifest_version({"F1": "哎"}, "zh-HK-HiuMaanNeural", "azure")


def test_render_all_writes_clips_manifest_public_and_ios_copy(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    m = render_all(svc)
    assert m["version"].startswith("f-")
    clips = m["clips"]
    assert set(clips) == {"F1", "F2", "F3", "F4", "F4p", "F5", "A1", "G1", "G2",
                          "R_s_0800", "R_s_1200", "R_s_2000", "P1"}
    for cid, c in clips.items():
        assert (fixed_dir(svc.settings) / c["file"]).is_file(), cid
        assert c["duration"] > 0 and c["segments"] and c["text"]
    # P1 lives in a random public dir; the kv keys follow the links.twilio format
    rand = svc.db.kv_get("public_clip_rand")
    assert isinstance(rand, str) and len(rand) >= 16
    assert svc.db.kv_get("public_clip_path") == f"/media/public/{rand}/P1.m4a"
    assert (svc.settings.media_dir / "public" / rand / "P1.m4a").is_file()
    assert m["public"]["dir"] == rand
    url = public_clip_url(svc.settings, svc.db)
    assert url == f"{svc.settings.public_base_url}/media/public/{rand}/P1.m4a"
    # iOS bundle copy
    ios = REPO_ROOT / "ios" / "AhBo" / "Resources" / "fixed"
    assert (ios / "manifest.json").is_file()
    assert (ios / "F1.m4a").is_file()
    # seed assets
    assert (SEED_DIR / "voice" / "demo_daughter.m4a").is_file()
    assert (SEED_DIR / "fall_placeholder.jpg").is_file()
    photo_bytes = (SEED_DIR / "fall_placeholder.jpg").read_bytes()
    assert photo_bytes[:3] == b"\xff\xd8\xff" and len(photo_bytes) > 500   # a real small JPEG


def test_ensure_manifest_is_idempotent_and_replaces_fallback(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    m1 = ensure_manifest(svc)
    ver = m1["version"]
    path = fixed_dir(svc.settings) / "manifest.json"
    first_mtime = path.stat().st_mtime_ns

    m2 = ensure_manifest(svc)                       # same version → untouched
    assert m2["version"] == ver
    assert path.stat().st_mtime_ns == first_mtime

    m3 = json.loads(path.read_text())
    m3["fallback"] = True                           # platform's text-only fallback marker
    m3["version"] = "f-deadbeefdead"
    path.write_text(json.dumps(m3, ensure_ascii=False))
    m4 = ensure_manifest(svc)                       # → replaced
    assert m4["version"] == ver and not m4.get("fallback")
    assert (fixed_dir(svc.settings) / "F1.m4a").is_file()

    path.unlink()                                   # missing → rebuilt
    m5 = ensure_manifest(svc)
    assert m5["version"] == ver and (fixed_dir(svc.settings) / "F1.m4a").is_file()


def test_ensure_manifest_rerenders_when_a_line_changes(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    ver = ensure_manifest(svc)["version"]
    cfg = replace(svc.config, fixed_lines={**svc.config.fixed_lines, "F1": "哎呀！唔好驚！"})
    svc.config = cfg
    ver2 = ensure_manifest(svc)["version"]
    assert ver2 != ver
    assert json.loads((fixed_dir(svc.settings) / "manifest.json").read_text())["version"] == ver2


def test_ensure_manifest_reuses_persisted_public_dir(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    ensure_manifest(svc)
    rand = svc.db.kv_get("public_clip_rand")
    path = svc.db.kv_get("public_clip_path")
    render_all(svc, force=True)
    assert svc.db.kv_get("public_clip_rand") == rand           # the unguessable dir is kept across renders
    assert svc.db.kv_get("public_clip_path") == path
    svc.db.kv_set("public_clip_rand", "gone-dir")              # stale dir name → a fresh random dir is minted
    render_all(svc, force=True)
    assert svc.db.kv_get("public_clip_rand") != "gone-dir"
    assert svc.db.kv_get("public_clip_path").startswith("/media/public/")


def test_kv_format_matches_links_twilio(tmp_path: Path) -> None:
    """`ahbo.links.twilio.public_clip_url` must find ready keys and reuse the rendered dir (no thrash)."""
    from ahbo.links.twilio import public_clip_url as links_public_clip_url

    svc = make_svc(tmp_path)
    render_all(svc)
    rand = svc.db.kv_get("public_clip_rand")
    url1 = links_public_clip_url(svc)
    assert url1 == f"{svc.settings.public_base_url}/media/public/{rand}/P1.m4a"
    assert svc.db.kv_get("public_clip_rand") == rand           # links did not mint its own dir
    assert (svc.settings.media_dir / "public" / rand / "P1.m4a").is_file()
    # an old-format bare dirname in public_clip_path is healed on the next forced render
    svc.db.kv_set("public_clip_path", rand)
    render_all(svc, force=True)
    assert svc.db.kv_get("public_clip_path") == f"/media/public/{rand}/P1.m4a"
