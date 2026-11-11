"""`ahbo wipe` (§11 PII/PDPO): export everything to JSON, then delete all personal data + media files.

Delegates to `ahbo.memory.retention` (`export_all`, `wipe`) — the memory component owns that logic. The export
is written next to the data dir (mode 600) before anything is deleted, so `--yes` is the only confirmation
needed. Requires `--yes`; without it nothing happens.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from ahbo.core.clock import get_clock
    from ahbo.core.db import open_db
    from ahbo.core.settings import get_settings
    from ahbo.memory.retention import export_all, wipe

    p = argparse.ArgumentParser(prog="ahbo wipe", description="export then delete all personal data (§11)")
    p.add_argument("--yes", action="store_true", help="required: really delete everything")
    p.add_argument("--out", help="export path (default data/export-<date>.json)")
    a = p.parse_args(argv)
    if not a.yes:
        print("refusing to wipe without --yes (this deletes every conversation, dose record, photo and media file)",
              file=sys.stderr)
        return 2

    settings = get_settings()
    db = open_db(settings.db_path)
    offset = db.kv_get("clock_offset_s")
    if offset is not None:
        get_clock().set_offset(float(offset))
    try:
        export = export_all(db)
        out = Path(a.out) if a.out else settings.data_dir / f"export-{get_clock().today().isoformat()}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(export, ensure_ascii=False, default=str), encoding="utf-8")
        os.chmod(out, 0o600)

        counts = wipe(db, settings.data_dir)       # memory.retention: every table but `meta`, + media files
        rows = sum(v for k, v in counts.items() if k != "files")
        print(f"wiped: {rows} rows across {len(counts) - 1} tables, {counts.get('files', 0)} media files")
        print(f"export written first: {out}")
        # the rendered fixed clips on disk are not personal data; the DB rows pointing at public/ are gone,
        # so the P1 clip is re-minted into a fresh random dir by the next `ahbo render-fixed` / api start
        print("fixed-clip cache files kept; run `ahbo seed` (or Reset demo) to reload the demo data")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
