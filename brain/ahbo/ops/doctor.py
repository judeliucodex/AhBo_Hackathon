"""`ahbo doctor` (§13.2, §16.3 A5, §27): one green/red/fake row per thing the rehearsal checklist needs.

Faked items (AHBO_FAKE) show as `fake` with detail "fake (needs owner)". Real items get exactly one tiny live
check — a 1-token model call, a tiny TTS render, a Telegram/Twilio status read, a local LiveKit token mint —
and only when their provider is NOT faked (nothing is called from fake mode: no spend, INV-rule 7). Every red
row says what to do and points at the SETUP.md section (the generated SETUP.md follows PRD §27's numbering).
`--latency` prints p50/p95 of the last 50 turns from the `spans` table (§15).

`rows(svc)` is async and never raises: a failing check is a red row. `main(argv)` is the CLI
(`ahbo doctor`, delegated from ahbo.ops.cli). Exit code 1 when any row is red.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import shutil
import sys
import time
from datetime import datetime
from typing import Any

from ahbo.core.settings import Settings, mask

log = logging.getLogger("ahbo.doctor")

HINT = "SETUP.md §27.1 #"
GREEN, RED, FAKE = "green", "red", "fake"

# (settings attr, env name, required at all, SETUP.md pointer)
KEYS: tuple[tuple[str, str, bool, str], ...] = (
    ("app_secret", "APP_SECRET", True, "SETUP.md §27.1 #9"),
    ("internal_token", "INTERNAL_TOKEN", True, "SETUP.md §26.2"),
    ("session_secret", "SESSION_SECRET", True, "SETUP.md §26.2"),
    ("operator_password_hash", "OPERATOR_PASSWORD_HASH", True, "SETUP.md §27.1 #8"),
    ("azure_tts_voice", "AZURE_TTS_VOICE", True, "SETUP.md §27.2 #5"),
    ("deepseek_api_key", "DEEPSEEK_API_KEY", False, HINT + "1"),
    ("anthropic_api_key", "ANTHROPIC_API_KEY", False, HINT + "2"),
    ("opper_api_key", "OPPER_API_KEY", False, "SETUP.md §24.16 (optional route)"),
    ("elevenlabs_api_key", "ELEVENLABS_API_KEY", False, HINT + "5 (optional TTS fallback)"),
    ("livekit_url", "LIVEKIT_URL", False, HINT + "4"),
    ("livekit_api_key", "LIVEKIT_API_KEY", False, HINT + "4"),
    ("livekit_api_secret", "LIVEKIT_API_SECRET", False, HINT + "4"),
    ("telegram_bot_token", "TELEGRAM_BOT_TOKEN", False, HINT + "6"),
    ("telegram_webhook_secret", "TELEGRAM_WEBHOOK_SECRET", False, HINT + "6"),
    ("family_telegram_chat_ids", "FAMILY_TELEGRAM_CHAT_IDS", False, HINT + "6"),
    ("twilio_account_sid", "TWILIO_ACCOUNT_SID", False, HINT + "7"),
    ("twilio_auth_token", "TWILIO_AUTH_TOKEN", False, HINT + "7"),
    ("twilio_from", "TWILIO_FROM", False, HINT + "7"),
    ("emergency_sim_phone", "EMERGENCY_SIM_PHONE", False, HINT + "7"),
    ("family_phone", "FAMILY_PHONE", False, HINT + "7"),
    ("beacon_tokens", "BEACON_TOKENS", False, HINT + "10"),
)

MODEL_PROVIDER_KEYS = {"deepseek": ("deepseek_api_key", HINT + "1"), "anthropic": ("anthropic_api_key", HINT + "2"),
                       "opper": ("opper_api_key", "SETUP.md §24.16"), "openai": ("openai_api_key", "SETUP.md §20.3")}


def _row(item: str, status: str, detail: str) -> dict[str, str]:
    return {"item": item, "status": status, "detail": detail}


# ---------------------------------------------------------------- keys (masked, never printed)
def key_rows(settings: Settings) -> list[dict[str, str]]:
    out = []
    for attr, env, required, ref in KEYS:
        v = getattr(settings, attr, "")
        if isinstance(v, dict):
            detail = f"{len(v)} pair(s): {', '.join(sorted(k + '=…' for k in v))}" if v else "unset"
        elif v:
            detail = f"set ({mask(v) if env != 'AZURE_TTS_VOICE' else v})"      # a voice name is not a secret
        else:
            detail = "unset"
        if v:
            status = GREEN
        elif required:
            status = RED
            detail += f" → set it in .env ({ref})"
        else:
            status = GREEN
            detail += " (optional)"
        out.append(_row(env, status, detail))
    return out


# ---------------------------------------------------------------- model routes (§20.3, §27.1 #1/#2)
def _model_routes(config: Any) -> list[tuple[str, str, str]]:
    m = config.models or {}
    out: list[tuple[str, str, str]] = []
    flash, sonnet, vision = m.get("flash") or {}, m.get("sonnet") or {}, m.get("vision") or {}
    out.append(("model flash", str(flash.get("provider", "deepseek")), str(flash.get("model", "deepseek-flash"))))
    fb = flash.get("fallback") or {}
    if fb:
        out.append(("model flash fallback", str(fb.get("provider", "anthropic")), str(fb.get("model", ""))))
    out.append(("model sonnet (deep)", str(sonnet.get("provider", "anthropic")), str(sonnet.get("model", ""))))
    out.append(("model vision", str(vision.get("provider", "anthropic")), str(vision.get("model", ""))))
    return out


# tiny live calls — ONLY called when the provider is not faked and its key exists. Sync + blocking.
def _tiny_openai(key: str, base_url: str, model: str, thinking: bool) -> dict[str, Any]:
    from openai import OpenAI

    from ahbo.models.flash import OPPER_URL, thinking_fields
    client = OpenAI(api_key=key, base_url=base_url or OPPER_URL, max_retries=0, timeout=20.0)
    kwargs: dict[str, Any] = {"model": model, "max_tokens": 1,
                              "messages": [{"role": "user", "content": "你好"}]}
    if thinking:
        kwargs.update(thinking_fields("off"))          # §20.3: doctor reports the thinking field accepted
    t0 = time.perf_counter()
    r = client.chat.completions.create(**kwargs)
    return {"model_id": getattr(r, "model", model), "ms": round((time.perf_counter() - t0) * 1000),
            "thinking_ok": thinking}


def _tiny_anthropic(key: str, model: str, *, with_image: bool = False) -> dict[str, Any]:
    import base64

    from anthropic import Anthropic
    client = Anthropic(api_key=key, max_retries=0, timeout=20.0)
    content: Any = "你好"
    if with_image:                                     # §27.1 #2: Haiku image input must work
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
                               "AAAABJRU5ErkJggg==")
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}},
                   {"type": "text", "text": "你好"}]
    t0 = time.perf_counter()
    r = client.messages.create(model=model, max_tokens=1, messages=[{"role": "user", "content": content}])
    return {"model_id": getattr(r, "model", model), "ms": round((time.perf_counter() - t0) * 1000)}


async def model_route_rows(settings: Settings, config: Any) -> list[dict[str, str]]:
    faked = settings.is_fake("models")
    out = []
    for label, provider, model in _model_routes(config):
        if faked:
            out.append(_row(f"{label} ({provider}/{model})", FAKE, "fake (needs owner)"))
            continue
        key_attr, ref = MODEL_PROVIDER_KEYS.get(provider, ("", "SETUP.md §20.3"))
        key = getattr(settings, key_attr, "") if key_attr else ""
        if not key:
            env = key_attr.upper() or f"{provider.upper()}_API_KEY"
            out.append(_row(f"{label} ({provider}/{model})", RED,
                            f"{env} unset → set it in .env, remove 'models' from "
                            f"AHBO_FAKE, make up + make doctor ({ref})"))
            continue
        try:
            if provider in ("deepseek", "opper"):
                base = settings.deepseek_base_url if provider == "deepseek" else ""
                r = await asyncio.to_thread(_tiny_openai, key, base, model, provider == "deepseek")
            elif provider == "anthropic":
                with_image = label.startswith("model vision")
                r = await asyncio.to_thread(_tiny_anthropic, key, model, with_image=with_image)
            else:
                out.append(_row(f"{label} ({provider}/{model})", RED,
                                f"no doctor check for provider {provider!r} (SETUP.md §20.3)"))
                continue
            det = f"{r['model_id']} · {r['ms']} ms" + (" · thinking field accepted" if r.get("thinking_ok") else "")
            out.append(_row(f"{label} ({provider}/{model})", GREEN, det))
        except Exception as e:
            out.append(_row(f"{label} ({provider}/{model})", RED,
                            f"{repr(e)[:140]} → check the key/model id in .env ({ref})"))
    return out


# ---------------------------------------------------------------- Azure STT + TTS (§27.1 #3)
def _azure_tts_tiny(key: str, region: str, voice: str) -> dict[str, Any]:
    from ahbo.speech.azure_tts import AzureTTS

    wav, duration, _, _ = AzureTTS(key, region, voice).synthesize_wav("你好")
    return {"wav_bytes": len(wav), "duration_s": round(duration, 2)}


def _azure_stt_tiny(key: str, region: str) -> dict[str, Any]:
    """Transcribe 0.4 s of silence (16 kHz PCM WAV built locally): endpoint + auth work, nothing recognisable."""
    import subprocess

    import azure.cognitiveservices.speech as speechsdk

    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                          "-i", "anullsrc=r=16000:cl=mono", "-t", "0.4", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
                         capture_output=True, check=True).stdout
    fmt = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
    push = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
    cfg = speechsdk.SpeechConfig(subscription=key, region=region)
    cfg.speech_recognition_language = "zh-HK"          # INV-10/D16: Cantonese only, never a Mandarin locale
    rec = speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=push)
    push.write(raw)
    push.close()
    r = rec.recognize_once_async().get()
    return {"reason": str(r.reason), "detail": getattr(getattr(r, "cancellation_details", None), "error_details", "")
            or getattr(r, "text", "") or ""}


async def azure_rows(settings: Settings) -> list[dict[str, str]]:
    if settings.is_fake("speech"):
        return [_row("Azure TTS", FAKE, "fake (needs owner)"), _row("Azure STT", FAKE, "fake (needs owner)")]
    key, region, voice = settings.azure_speech_key, settings.azure_speech_region, settings.azure_tts_voice
    if not key:
        hint = f"AZURE_SPEECH_KEY unset → .env, remove 'speech' from AHBO_FAKE ({HINT}3)"
        return [_row("Azure TTS", RED, hint), _row("Azure STT", RED, hint)]
    out = []
    try:
        r = await asyncio.to_thread(_azure_tts_tiny, key, region, voice)
        out.append(_row("Azure TTS", GREEN, f"rendered 你好 ({r['duration_s']} s, {r['wav_bytes']} B) as {voice}"))
    except Exception as e:
        out.append(_row("Azure TTS", RED, f"{repr(e)[:140]} → check key/region/voice in .env ({HINT}3)"))
    try:
        r = await asyncio.to_thread(_azure_stt_tiny, key, region)
        canceled = "Canceled" in r["reason"]
        out.append(_row("Azure STT", RED if canceled else GREEN,
                        f"transcribed a silent wav: {r['reason']}" + (f" {r['detail'][:80]}" if canceled else "")))
    except Exception as e:
        out.append(_row("Azure STT", RED, f"{repr(e)[:140]} → check key/region in .env ({HINT}3)"))
    return out


# ---------------------------------------------------------------- LiveKit (§27.1 #4) — mint is local, no API call
async def livekit_row(settings: Settings) -> dict[str, str]:
    if settings.is_fake("livekit"):
        return _row("LiveKit", FAKE, "fake (needs owner)")     # text sessions while faked (§26.3)
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return _row("LiveKit", RED, f"LIVEKIT_URL / API_KEY / API_SECRET unset → .env, remove 'livekit' from "
                                    f"AHBO_FAKE ({HINT}4)")
    try:
        from livekit import api as lk

        token = (lk.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
                 .with_identity("doctor-check").with_name("Ah Bo doctor")
                 .with_grants(lk.VideoGrants(room_join=True, room="ahbo-doctor-check")).to_jwt())
        n = len(token.split(".")) if isinstance(token, str) else 0
        return _row("LiveKit", GREEN if n == 3 else RED,
                    f"token minted for room ahbo-doctor-check ({'jwt' if n == 3 else 'bad token'})")
    except Exception as e:
        return _row("LiveKit", RED, f"{repr(e)[:140]} → check the LiveKit keys in .env ({HINT}4)")


# ---------------------------------------------------------------- Telegram webhook (§27.1 #6)
async def telegram_row(settings: Settings) -> dict[str, str]:
    if settings.is_fake("telegram"):
        return _row("Telegram webhook", FAKE, "fake (needs owner)")     # messages go to the console outbox
    if not settings.telegram_bot_token:
        return _row("Telegram webhook", RED, f"TELEGRAM_BOT_TOKEN unset → .env, remove 'telegram' from AHBO_FAKE "
                                             f"({HINT}6)")
    import httpx

    want = f"{settings.public_base_url}/telegram/webhook"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getWebhookInfo")
        j = r.json()
        if not j.get("ok"):
            return _row("Telegram webhook", RED, f"getWebhookInfo failed: {str(j)[:100]} → check the token ({HINT}6)")
        got = (j.get("result") or {}).get("url") or ""
        if got == want:
            return _row("Telegram webhook", GREEN, f"set to {want}")
        return _row("Telegram webhook", RED, f"webhook is {got or '(unset)'} → set it to {want} ({HINT}6)")
    except Exception as e:
        return _row("Telegram webhook", RED, f"{repr(e)[:140]} → network or token problem ({HINT}6)")


# ---------------------------------------------------------------- Twilio (§27.1 #7)
async def twilio_rows(settings: Settings) -> list[dict[str, str]]:
    if settings.is_fake("twilio"):
        return [_row("Twilio", FAKE, "fake (needs owner)")]     # calls go to the console outbox
    sid, tok = settings.twilio_account_sid, settings.twilio_auth_token
    if not (sid and tok):
        hint = f"TWILIO_ACCOUNT_SID / AUTH_TOKEN unset → .env, remove 'twilio' from AHBO_FAKE ({HINT}7)"
        return [_row("Twilio auth", RED, hint), _row("Twilio verified numbers", RED, hint),
                _row("Twilio TwiML dry-run", RED, hint)]
    import httpx

    base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    out: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=10, auth=(sid, tok)) as c:
        try:
            r = await c.get(f"{base}.json")
            j = r.json()
            out.append(_row("Twilio auth", GREEN if r.status_code == 200 and j.get("status") else RED,
                            f"account {j.get('friendly_name', sid)} ({j.get('status', r.status_code)})"
                            if r.status_code == 200 else f"{str(j)[:100]} → check sid/token ({HINT}7)"))
        except Exception as e:
            out.append(_row("Twilio auth", RED, f"{repr(e)[:140]} ({HINT}7)"))
        try:
            r = await c.get(f"{base}/OutgoingCallerIds.json")
            verified = {str(x.get("phone_number", "")) for x in (r.json().get("outgoing_caller_ids") or [])}
            missing = [p for p in (settings.emergency_sim_phone, settings.family_phone) if p and p not in verified]
            if missing:
                out.append(_row("Twilio verified numbers", RED,
                                f"not verified caller IDs: {', '.join(missing)} → verify them in the Twilio console "
                                f"({HINT}7; INV-9: never an emergency number)"))
            else:
                out.append(_row("Twilio verified numbers", GREEN,
                                f"{', '.join(sorted(verified)) or '(none needed yet)'}"))
        except Exception as e:
            out.append(_row("Twilio verified numbers", RED, f"{repr(e)[:140]} ({HINT}7)"))
        # dry-run TwiML: the same builder the live route uses, no network
        out.append(_twiml_row(settings))
    return out


def _twiml_row(settings: Settings) -> dict[str, str]:
    """Build the exact TwiML a call would play via the links builder (no HTTP request, no call placed)."""
    for mod_name, fn_names in (("ahbo.links.twilio", ("call_twiml", "twiml_test", "build_twiml")),
                               ("ahbo.links.phone", ("twiml_test", "build_twiml"))):
        try:
            mod = __import__(mod_name, fromlist=["x"])
        except ImportError:
            continue
        for fn_name in fn_names:
            fn = getattr(mod, fn_name, None)
            if not callable(fn):
                continue
            try:
                if fn_name == "call_twiml":
                    clip = _clip_url_for_twiml(settings)
                    xml = fn(clip, f"{settings.public_base_url}/twilio/gather?call_id=doctor-check")
                else:
                    xml = fn(settings=settings) if _wants_settings(fn) else fn()
                ok = isinstance(xml, str) and "<Response>" in xml
                return _row("Twilio TwiML dry-run", GREEN if ok else RED,
                            f"{fn_name}: {len(xml or '')} chars, Play+Gather" if ok
                            else f"bad TwiML: {str(xml)[:80]}")
            except Exception as e:
                return _row("Twilio TwiML dry-run", RED, f"{fn_name} failed: {repr(e)[:120]} ({HINT}7)")
    return _row("Twilio TwiML dry-run", RED, "no TwiML builder in ahbo.links.twilio/phone (links hand-off)")


def _clip_url_for_twiml(settings: Settings) -> str:
    """The public P1 URL for the dry run: kv first, else a placeholder (the TwiML shape is what's checked)."""
    try:
        from ahbo.core.db import open_db
        from ahbo.ops.render_fixed import public_clip_url

        db = open_db(settings.db_path)
        try:
            url = public_clip_url(settings, db)
        finally:
            db.close()
        if url:
            return url
    except Exception:
        log.debug("doctor: could not read the public clip URL from kv", exc_info=True)
    return f"{settings.public_base_url}/media/public/doctor-check/P1.m4a"


def _wants_settings(fn: Any) -> bool:
    try:
        return "settings" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- Playwright (§13.2) — the action runner
async def playwright_row(settings: Settings) -> dict[str, str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return _row("Playwright", RED, "playwright not installed in this image → SETUP.md §27.3 (Dockerfile.browser)")
    try:
        async def probe() -> int:
            async with async_playwright() as pw:
                b = await pw.chromium.connect(settings.browser_ws, timeout=3000)
                v = b.version
                await b.close()
                return v
        version = await probe()
        return _row("Playwright", GREEN, f"connected to {settings.browser_ws} (chromium {version})")
    except Exception as e:
        return _row("Playwright", RED, f"cannot reach {settings.browser_ws}: {repr(e)[:120]} → start the browser "
                                       f"container (docker compose up -d browser, SETUP.md §27.3)")


# ---------------------------------------------------------------- devices / beacons / cache / clock / disk / db
def ipad_row(svc: Any) -> dict[str, str]:
    rows = svc.db.query("SELECT id, last_seen, battery, app_version FROM devices WHERE online=1 ORDER BY id")
    if rows:
        det = "; ".join(f"{r['id']} (v{r.get('app_version') or '?'}, battery "
                        f"{round(100 * r['battery']) if r.get('battery') is not None else '?'}%)" for r in rows)
        return _row("iPad connected", GREEN, det)
    return _row("iPad connected", RED, "no device online → open /sim/ (browser iPad) or run the app (SETUP.md §27.4)")


def beacon_row(svc: Any) -> dict[str, str]:
    now = svc.clock.now()
    best: tuple[str, float] | None = None
    for r in svc.db.query("SELECT id, last_seen FROM beacons"):
        try:
            ago = (now - datetime.fromisoformat(r["last_seen"])).total_seconds() if r["last_seen"] else None
        except ValueError:
            ago = None
        if ago is not None and (best is None or ago < best[1]):
            best = (r["id"], ago)
    if best and best[1] <= 90:
        return _row("Beacon heartbeat", GREEN, f"{best[0]} last seen {best[1]:.0f} s ago")
    if best:
        return _row("Beacon heartbeat", RED, f"{best[0]} last seen {best[1] / 60:.0f} min ago → check the beacon "
                                             f"is on the body and charged ({HINT}10)")
    return _row("Beacon heartbeat", RED, f"no heartbeat yet → flash the ESP32 + set BEACON_TOKENS ({HINT}10); "
                                         f"the operator fall trigger still works (§10.4)")


def fixed_cache_row(svc: Any) -> dict[str, str]:
    from ahbo.ops.render_fixed import fixed_dir

    path = fixed_dir(svc) / "manifest.json"
    try:
        import json

        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = None
    if not isinstance(m, dict) or not m.get("version"):
        return _row("Fixed cache", RED, f"{path} missing → run `ahbo render-fixed` (SETUP.md §27.3)")
    if m.get("fallback"):
        return _row("Fixed cache", RED, f"manifest is the text-only fallback ({m.get('version')}) → run "
                                        f"`ahbo render-fixed` or restart the api (SETUP.md §27.3)")
    return _row("Fixed cache", GREEN, f"{m.get('version')} · {len(m.get('clips') or {})} clips")


def clock_row(svc: Any) -> dict[str, str]:
    off = svc.clock.offset_s
    status = RED if abs(off) > 48 * 3600 else GREEN        # an offset is a feature; a 48 h+ one is a mistake
    act = " → clear it: `ahbo clock --offset 0`" if status == RED else ""
    return _row("Clock", status, f"virtual {svc.clock.iso()} (offset {off:+.0f} s){act}")


def disk_row(svc: Any) -> dict[str, str]:
    free_gb = shutil.disk_usage(svc.settings.data_dir).free / 1e9
    if free_gb < 5:
        return _row("Disk", RED, f"{free_gb:.1f} GB free → clear data/backups and old screenshots (SETUP.md §27.3)")
    return _row("Disk", GREEN, f"{free_gb:.1f} GB free")


def db_row(svc: Any) -> dict[str, str]:
    r = svc.db.one("PRAGMA integrity_check")
    ok = str((r or {}).get("integrity_check", "")) == "ok"
    if ok:
        return _row("Database", GREEN, "integrity_check ok")
    return _row("Database", RED, f"integrity_check: {str(r)[:80]} → restore data/backups/ (SETUP.md §27.3)")


# ---------------------------------------------------------------- assembly
async def rows(svc: Any) -> list[dict[str, str]]:
    out = key_rows(svc.settings)
    out += await model_route_rows(svc.settings, svc.config)
    out += await azure_rows(svc.settings)
    out.append(await livekit_row(svc.settings))
    out.append(await telegram_row(svc.settings))
    out += await twilio_rows(svc.settings)
    out.append(await playwright_row(svc.settings))
    out.append(ipad_row(svc))
    out.append(beacon_row(svc))
    out.append(fixed_cache_row(svc))
    out.append(clock_row(svc))
    out.append(disk_row(svc))
    out.append(db_row(svc))
    return out


def _print_table(rows: list[dict[str, str]]) -> None:
    width = max(len(r["item"]) for r in rows) + 2
    for r in rows:
        mark = {"green": "ok  ", "red": "!!  ", "fake": "fake"}[r["status"]]
        print(f"{mark}  {r['item']:<{width}} {r['detail']}")


def _doctor_context() -> Any:
    """A minimal svc (settings/db/clock/config) for the CLI: no Kernel, no hub."""
    from types import SimpleNamespace

    from ahbo.core.clock import get_clock
    from ahbo.core.config import load_config
    from ahbo.core.db import open_db
    from ahbo.core.settings import get_settings

    settings = get_settings()
    db = open_db(settings.db_path)
    offset = db.kv_get("clock_offset_s")
    if offset is not None:
        get_clock().set_offset(float(offset))
    return SimpleNamespace(settings=settings, db=db, clock=get_clock(), config=load_config(fresh=True), extra={})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ahbo doctor", description="green/red/fake table (§13.2)")
    p.add_argument("--latency", action="store_true", help="p50/p95 of the last 50 turns (§15)")
    a = p.parse_args(argv)

    from ahbo.trace.spans import latency_stats

    svc = _doctor_context()
    try:
        table = asyncio.run(rows(svc))
        if a.latency:
            st = latency_stats(svc.db, n=50)
            if st["count"]:
                for key in ("total_ms", "stt_final_ms", "llm_ttft_ms", "llm_ttft_to_tts_first_audio_ms"):
                    if st.get(key, {}).get("p50") is not None:
                        print(f"latency {key}: p50 {st[key]['p50']} ms · p95 {st[key]['p95']} ms (n={st[key]['n']})")
            else:
                print("latency: no turns recorded yet")
        _print_table(table)
        reds = sum(1 for r in table if r["status"] == "red")
        fakes = sum(1 for r in table if r["status"] == "fake")
        print(f"\n{len(table)} checks: {reds} red, {fakes} fake, {len(table) - reds - fakes} green"
              + (" — red rows need the owner (see SETUP.md)" if reds else ""))
        return 1 if reds else 0
    finally:
        svc.db.close()


if __name__ == "__main__":
    sys.exit(main())
