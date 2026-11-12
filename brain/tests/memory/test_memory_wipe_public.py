"""QA 26 D1: `wipe` deletes `data/media/public/` (the P1 phone clip with her name and address)."""
from __future__ import annotations

from ahbo.memory.retention import wipe


def test_wipe_deletes_the_public_clip_dir(db, tmp_path):
    pub = tmp_path / "media" / "public"
    for rand in ("01JOLDRANDOMDIRNAME000000A", "01JNEWRANDOMDIRNAME000000B"):
        (pub / rand).mkdir(parents=True)
        (pub / rand / "P1.m4a").write_bytes(b"\x00" * 64)
    keep = tmp_path / "media" / "fixed" / "F1.m4a"           # the fixed-clip cache is kept by design
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"\x00")
    db.kv_set("public_clip_rand", "01JNEWRANDOMDIRNAME000000B")
    counts = wipe(db, tmp_path)
    assert counts["files"] >= 2
    assert not any(pub.rglob("*")), list(pub.rglob("*"))
    assert keep.is_file()
    assert db.kv_get("public_clip_rand") is None


def test_wipe_without_a_public_dir_is_fine(db, tmp_path):
    assert wipe(db, tmp_path)["files"] == 0
