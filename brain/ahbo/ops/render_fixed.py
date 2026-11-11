"""Fixed-clip cache (§10.3): render `brain/config/fixed_lines.yaml` into silent/real `.m4a` clips + `manifest.json`.

- Fixed ids F1 F2 F3 F4 F4p F5 A1 G1 G2, one `R_<slot_id>` per active `med_schedule` row (R_TEMPLATE, med names
  joined exactly like the agent says them), and the P1 phone clip (P1_TEMPLATE with the resident's address).
- P1 is rendered twice: into the bundle dir (`media/fixed/P1.m4a`, so `links.twilio._rendered_p1` can copy it and
  `/media/fixed/P1.m4a` serves it) and into `data/media/public/<random>/P1.m4a` for Twilio. The manifest keeps a
  `clips["P1"]` entry plus a `"public"` section with the random dir. The kv keys follow the format
  `ahbo.links.twilio.public_clip_url` reads: `public_clip_rand` = the random dir name,
  `public_clip_path` = `/media/public/<rand>/P1.m4a` (CONTRACTS §8).
- The countdown ticks are NOT rendered: the face beeps itself (§4.3.1; the kit plays its own tick sound).
- Fake mode (`settings.is_fake("speech")`) renders silent clips of 0.19 s per character with estimated caption
  segments; real mode uses the provider in `models.yaml → speech.tts` (`ahbo.speech.fixed_tts`): ElevenLabs
  `fixed_model` with character timestamps (`ahbo.speech.elevenlabs_tts`, default since 11 Nov 2026) or Azure TTS
  with word-boundary events (`ahbo.speech.azure_tts`).
- `ensure_manifest(svc)` runs at every api start-up (called by `ahbo.api.services`) and re-renders only when the
  manifest is missing, marked `"fallback": true`, or its version (sha1 of texts+voice+mode) differs — so a text-only
  fallback manifest from the platform is replaced on the next start.
- Also writes a copy of the bundle into `ios/AhBo/Resources/fixed/` (the offline fallback the app ships) and, if
  missing, the demo seed assets `brain/seed/voice/demo_daughter.m4a` and `brain/seed/fall_placeholder.jpg`.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from ahbo.core.ids import ulid
from ahbo.core.jsonutil import dumps, loads
from ahbo.core.settings import REPO_ROOT, SEED_DIR, Settings, get_settings

log = logging.getLogger("ahbo.render_fixed")

FIXED_IDS = ("F1", "F2", "F3", "F4", "F4p", "F5", "A1", "G1", "G2")
PUBLIC_CLIP_ID = "P1"
PUBLIC_CLIP_FILE = "P1.m4a"
IOS_RESOURCE_DIR = REPO_ROOT / "ios" / "AhBo" / "Resources" / "fixed"
SEED_VOICE_FILE = SEED_DIR / "voice" / "demo_daughter.m4a"
SEED_FALLBACK_PHOTO = SEED_DIR / "fall_placeholder.jpg"


# ---------------------------------------------------------------- paths
def _settings_of(target: Any) -> Settings:
    if target is None:
        return get_settings()
    s = getattr(target, "settings", None)
    return s if isinstance(s, Settings) else target


def fixed_dir(target: Any = None) -> Path:
    """`data/media/fixed` of the settings (or services) object passed in — the platform calls this at start-up."""
    return _settings_of(target).media_dir / "fixed"


# ---------------------------------------------------------------- clip texts (cheap, no TTS)
def plan_texts(config: Any, db: Any) -> dict[str, str]:
    """id → spoken text for every clip of the current config + DB (schedule, resident)."""
    from ahbo.models.fakes import join_names

    texts: dict[str, str] = {}
    for cid in FIXED_IDS:
        if cid in config.fixed_lines:
            texts[cid] = config.fixed_lines[cid]
    catalogue = {r["id"]: r["yue"] for r in db.query("SELECT id, yue FROM med_catalogue")}
    for row in db.query("SELECT slot_id, med_names FROM med_schedule WHERE active=1 ORDER BY time"):
        ids = loads(row["med_names"], [])
        names = [catalogue[i] for i in ids if i in catalogue]
        if names:
            texts[f"R_{row['slot_id']}"] = config.fixed_lines["R_TEMPLATE"].format(med_names=join_names(names))
    r = db.one("SELECT address FROM resident WHERE id='resident'")
    if r and r.get("address"):
        texts[PUBLIC_CLIP_ID] = config.fixed_lines["P1_TEMPLATE"].format(address=r["address"])
    return texts


def manifest_version(texts: dict[str, str], voice: str, mode: str) -> str:
    """`f-<sha1 of texts+voice+mode>` (CONTRACTS §11): changes whenever a line, the voice or the adapter changes."""
    h = hashlib.sha1(dumps({"texts": texts, "voice": voice, "mode": mode}).encode("utf-8")).hexdigest()[:12]
    return f"f-{h}"


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        m = loads(path.read_text(encoding="utf-8"), None)
        return m if isinstance(m, dict) and m.get("clips") else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- rendering
def _tts_for(svc: Any):
    tts = svc.extra.get("fixed_tts") if hasattr(svc, "extra") else None
    if tts is not None:
        return tts
    from ahbo.speech.fixed_tts import get_fixed_tts

    return get_fixed_tts(svc.settings, getattr(svc, "config", None))


def render_all(svc: Any, *, force: bool = False) -> dict[str, Any]:
    """Render every clip and write manifest.json (+ the iOS bundle copy and the seed assets)."""
    settings, db, config = svc.settings, svc.db, svc.config
    texts = plan_texts(config, db)
    tts = _tts_for(svc)
    version = manifest_version(texts, tts.voice, tts.mode)
    out_dir = fixed_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = _read_manifest(out_dir / "manifest.json")
    files_ok = manifest is not None and all(
        (out_dir / c.get("file", "")).is_file() for c in manifest["clips"].values())
    if not force and manifest is not None and not manifest.get("fallback") \
            and manifest.get("version") == version and files_ok:
        ensure_seed_assets(settings)
        return manifest

    clips: dict[str, Any] = {}
    for cid, text in texts.items():
        if cid == PUBLIC_CLIP_ID:
            continue
        path = out_dir / f"{cid}.m4a"
        duration, segments = tts.render(text, path)
        clips[cid] = {"file": path.name, "text": text, "segments": segments, "duration": duration}

    public: dict[str, Any] | None = None
    if PUBLIC_CLIP_ID in texts:
        rand = db.kv_get("public_clip_rand")
        if not isinstance(rand, str) or not rand or not (settings.media_dir / "public" / rand).is_dir():
            rand = ulid().lower()                       # unguessable, matches the route's RANDOM_DIR check
        pub_dir = settings.media_dir / "public" / rand
        pub_dir.mkdir(parents=True, exist_ok=True)
        duration, segments = tts.render(texts[PUBLIC_CLIP_ID], out_dir / PUBLIC_CLIP_FILE)
        shutil.copyfile(out_dir / PUBLIC_CLIP_FILE, pub_dir / PUBLIC_CLIP_FILE)   # same bytes, one render
        clips[PUBLIC_CLIP_ID] = {"file": PUBLIC_CLIP_FILE, "text": texts[PUBLIC_CLIP_ID], "segments": segments,
                                 "duration": duration}                    # so links.twilio._rendered_p1 finds it
        db.kv_set("public_clip_rand", rand)
        db.kv_set("public_clip_path", f"/media/public/{rand}/{PUBLIC_CLIP_FILE}")  # links.twilio format
        public = {"id": PUBLIC_CLIP_ID, "file": PUBLIC_CLIP_FILE, "dir": rand, "text": texts[PUBLIC_CLIP_ID],
                  "segments": segments, "duration": duration}

    manifest = {"version": version, "clips": clips}
    if public is not None:
        manifest["public"] = public
    (out_dir / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
    log.info("fixed clips rendered: version=%s, %d clips, tts=%s", version, len(clips), tts.mode)
    _copy_ios_bundle(manifest, out_dir)
    ensure_seed_assets(settings)
    return manifest


def _copy_ios_bundle(manifest: dict[str, Any], out_dir: Path) -> None:
    """The offline fallback bundled in the app (§10.3). Best effort: never breaks rendering."""
    try:
        IOS_RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_dir / "manifest.json", IOS_RESOURCE_DIR / "manifest.json")
        for c in manifest["clips"].values():
            src = out_dir / c["file"]
            if src.is_file():
                shutil.copy2(src, IOS_RESOURCE_DIR / c["file"])
    except OSError:
        log.exception("could not update the iOS fixed bundle at %s", IOS_RESOURCE_DIR)


# ---------------------------------------------------------------- seed assets (created once, owner-replaceable)
def ensure_seed_assets(settings: Settings) -> None:
    """`seed/voice/demo_daughter.m4a` (silent, length from the transcript) and `seed/fall_placeholder.jpg`."""
    if not SEED_VOICE_FILE.is_file():
        try:
            from ahbo.core.config import load_seed
            from ahbo.speech.audio import silent_m4a
            from ahbo.speech.segments import SEC_PER_CHAR

            vm = (load_seed("demo.yaml", settings.public_base_url).get("voice_messages") or [{}])[0]
            transcript = str(vm.get("transcript") or "媽，記得食飯呀！星期日我帶阿明嚟探你。")
            silent_m4a(SEED_VOICE_FILE, SEC_PER_CHAR * len(transcript))
            log.info("seed voice placeholder written: %s", SEED_VOICE_FILE)
        except Exception:
            log.exception("could not write %s", SEED_VOICE_FILE)
    if not SEED_FALLBACK_PHOTO.is_file():
        try:
            from PIL import Image

            Image.new("RGB", (640, 480), (24, 24, 28)).save(SEED_FALLBACK_PHOTO, "JPEG", quality=85)
            log.info("seed fall photo placeholder written: %s", SEED_FALLBACK_PHOTO)
        except Exception:
            log.exception("could not write %s", SEED_FALLBACK_PHOTO)


# ---------------------------------------------------------------- platform entry points
def ensure_manifest(svc: Any) -> dict[str, Any]:
    """Start-up hook (`ahbo.api.services`): render only when the manifest is missing/fallback/stale."""
    return render_all(svc, force=False)


def public_clip_url(settings: Settings, db: Any) -> str | None:
    """The Twilio-fetchable URL of the P1 clip (CONTRACTS §8), None before first render.

    Reads the same kv keys `ahbo.links.twilio.public_clip_url` writes: `public_clip_path` (a URL path) with
    `public_clip_rand` as the fallback."""
    path = db.kv_get("public_clip_path")
    if isinstance(path, str) and path.startswith("/media/public/"):
        return settings.public_base_url + path
    rand = db.kv_get("public_clip_rand")
    if isinstance(rand, str) and rand:
        return f"{settings.public_base_url}/media/public/{rand}/{PUBLIC_CLIP_FILE}"
    return None


# ---------------------------------------------------------------- CLI (`ahbo render-fixed`)
def main(argv: list[str] | None = None) -> int:
    from types import SimpleNamespace

    from ahbo.core.clock import get_clock
    from ahbo.core.config import load_config
    from ahbo.core.db import open_db

    p = argparse.ArgumentParser(prog="ahbo render-fixed", description="render the fixed-clip cache (§10.3)")
    p.add_argument("--force", action="store_true", help="re-render even if the manifest version matches")
    a = p.parse_args(argv)

    settings = get_settings()
    db = open_db(settings.db_path)
    offset = db.kv_get("clock_offset_s")
    if offset is not None:
        get_clock().set_offset(float(offset))
    svc = SimpleNamespace(settings=settings, db=db, clock=get_clock(), config=load_config(fresh=True), extra={})
    try:
        m = render_all(svc, force=a.force)
    finally:
        db.close()
    print(f"manifest: {fixed_dir(settings) / 'manifest.json'}")
    print(f"version: {m.get('version')}  clips: {', '.join(sorted(m.get('clips') or {}))}"
          + (f"  public: {m['public']['dir']}/{PUBLIC_CLIP_FILE}" if m.get("public") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
