"""ElevenLabs speech (BUILD_LOG §8): config, fixed-clip renderer, batch STT, live-STT kwargs, transcript → Traditional.

Nothing real is called: every HTTP request goes to an `httpx.MockTransport`. The response shapes mirror what the
real API returned on 11 Nov 2026 (with-timestamps alignment; speech-to-text JSON).
"""
from __future__ import annotations

import base64
import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from ahbo.core.config import load_config
from ahbo.core.settings import load_settings
from ahbo.kernel.matchers import match_ok
from ahbo.speech.audio import probe
from ahbo.speech.config import SttCfg, speech_cfg
from ahbo.speech.elevenlabs_stt import (PLUGIN_REALTIME_ID, SttError, batch_form, live_stt_kwargs,
                                        transcribe_file)
from ahbo.speech.elevenlabs_tts import ElevenLabsTTS, alignment_to_segments
from ahbo.speech.fakes import FakeTTS
from ahbo.speech.fixed_tts import get_fixed_tts
from ahbo.speech.segments import u16len
from ahbo.speech.transcript import to_hk

CFG = load_config(fresh=True)
F3 = "王婆婆，如果你冇跌親，請講「我冇事」。"
# the real eleven_v4 alignment for F3 (11 Nov 2026)
F3_STARTS = [0.0, 0.24, 0.52, 0.8, 0.88, 1.013, 1.147, 1.28, 1.36, 1.6, 2.08, 2.2, 2.32, 2.8, 2.88, 2.96, 3.28, 3.44,
             3.52]
F3_ENDS = F3_STARTS[1:] + [3.84]


def _alignment(text: str, starts: list[float], ends: list[float]) -> dict:
    return {"characters": list(text), "character_start_times_seconds": starts, "character_end_times_seconds": ends}


# ---------------------------------------------------------------- config
def test_speech_config_is_elevenlabs_with_model_ids_from_yaml() -> None:
    sp = speech_cfg(CFG)
    assert (sp.stt.provider, sp.stt.live_model, sp.stt.file_model, sp.stt.language) == \
        ("elevenlabs", "scribe_v3_live", "scribe_v2", "yue")
    assert sp.tts.provider == "elevenlabs" and sp.tts.voice_id_env == "ELEVENLABS_VOICE_ID"
    assert sp.tts.live_model and sp.tts.fixed_model and sp.tts.live_model != sp.tts.fixed_model


def test_keyterms_have_no_ok_phrase_and_no_food_trap() -> None:
    """A keyterm once turned 我冇事 into 我冇食: no OK phrase and no 食-word may bias the recogniser."""
    terms = speech_cfg(CFG).stt.active_keyterms(live=True)
    assert terms and len(terms) <= 50
    for t in terms:
        assert "食" not in t, t
        assert match_ok(t, CFG) != "ok", t
        assert len(t) <= 50 and t.count(" ") <= 4


def test_keyterms_switch_off() -> None:
    cfg = dataclasses.replace(speech_cfg(CFG).stt, use_keyterms=False)
    assert cfg.active_keyterms() == [] and "keyterms" not in batch_form(cfg)
    assert "keyterms" not in live_stt_kwargs(cfg, "k")


def test_missing_section_falls_back_to_defaults() -> None:
    sp = speech_cfg({"flash": {}})
    assert sp.stt.live_model == "scribe_v3_live" and sp.tts.provider == "elevenlabs"


# ---------------------------------------------------------------- caption segments from character alignment
def test_alignment_to_segments_utf16_punctuation_and_quotes() -> None:
    segs = alignment_to_segments(_alignment(F3, F3_STARTS, F3_ENDS), F3, 3.84)
    shown = [F3[a:b] for a, b, _, _ in segs]
    assert shown[2] == "婆，"                       # punctuation revealed with the unit before it
    assert "「我" in shown and shown[-1] == "事」。"   # opening quote with the unit after it, closers with the last
    assert segs[0][0] == 0 and segs[-1][1] == u16len(F3)
    for (a, b, s, e), nxt in zip(segs, segs[1:] + [None]):
        assert a < b and s <= e <= 3.84
        if nxt:
            assert b == nxt[0] and e <= nxt[2] + 1e-9       # contiguous, never overlapping


def test_alignment_counts_utf16_for_non_bmp_and_merges_ascii() -> None:
    text = "𠝹OK好"
    starts, ends = [0.0, 0.3, 0.4, 0.5], [0.3, 0.4, 0.5, 0.8]
    segs = alignment_to_segments(_alignment(text, starts, ends), text, 0.8)
    assert [s[:2] for s in segs] == [[0, 2], [2, 4], [4, 5]]          # 𠝹 = 2 units; "OK" one segment
    assert segs[1][2:] == [0.3, 0.5]


# ---------------------------------------------------------------- fixed-clip renderer (MockTransport)
def _tts_transport(seen: list[httpx.Request], seconds: float = 0.5, status: int = 200):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if status != 200:
            return httpx.Response(status, json={"detail": {"status": "quota_exceeded"}})
        body = json.loads(req.content)
        text = body["text"]
        n = len(text)
        starts = [i * seconds / n for i in range(n)]
        ends = [(i + 1) * seconds / n for i in range(n)]
        pcm = b"\0\0" * int(24000 * seconds)
        return httpx.Response(200, json={"audio_base64": base64.b64encode(pcm).decode(),
                                         "alignment": _alignment(text, starts, ends),
                                         "normalized_alignment": _alignment(text, starts, ends),
                                         "quality_check": None})
    return httpx.MockTransport(handler)


def test_elevenlabs_render_writes_m4a_24k_mono_and_segments(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    tts = ElevenLabsTTS("sk_test_key_1234", "VOICE123", "eleven_v4", "yue",
                        client=httpx.Client(transport=_tts_transport(seen)))
    duration, segs = tts.render(F3, tmp_path / "F3.m4a")
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/VOICE123/with-timestamps"
    assert req.url.params["output_format"] == "pcm_24000"
    assert req.headers["xi-api-key"] == "sk_test_key_1234"
    assert json.loads(req.content) == {"text": F3, "model_id": "eleven_v4", "language_code": "yue"}
    assert duration == 0.5 and segs and segs[-1][1] == u16len(F3)
    p = probe(tmp_path / "F3.m4a")
    assert p["codec"] == "aac" and p["sample_rate"] == 24000 and p["channels"] == 1
    assert tts.voice == "VOICE123:eleven_v4:yue" and tts.mode == "elevenlabs"


def test_elevenlabs_render_error_never_leaks_the_key(tmp_path: Path) -> None:
    tts = ElevenLabsTTS("sk_secret_ABCDEFGH", "V", "eleven_v4", client=httpx.Client(transport=_tts_transport([],
                                                                                                         status=401)))
    with pytest.raises(RuntimeError) as e:
        tts.render("你好", tmp_path / "x.m4a")
    assert "401" in str(e.value) and "sk_secret" not in str(e.value)
    assert not (tmp_path / "x.m4a").exists()


def test_get_fixed_tts_dispatch() -> None:
    base = load_settings()
    fake = dataclasses.replace(base, fake=frozenset({"speech"}), elevenlabs_api_key="k", elevenlabs_voice_id="v")
    assert isinstance(get_fixed_tts(fake, CFG), FakeTTS)                      # fake mode unchanged
    real = dataclasses.replace(fake, fake=frozenset())
    t = get_fixed_tts(real, CFG)
    assert isinstance(t, ElevenLabsTTS) and t.model == speech_cfg(CFG).tts.fixed_model and t.voice_id == "v"
    nokey = dataclasses.replace(real, elevenlabs_api_key="")
    assert isinstance(get_fixed_tts(nokey, CFG), FakeTTS)                     # no key: silent clips, no crash
    azure_cfg = {"speech": {"tts": {"provider": "azure"}}}
    from ahbo.speech.azure_tts import AzureTTS
    az = dataclasses.replace(real, azure_speech_key="az")
    assert isinstance(get_fixed_tts(az, azure_cfg), AzureTTS)                 # Azure stays an alternative


def test_render_all_uses_elevenlabs_when_speech_is_real(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from ahbo.core.clock import FakeClock, get_clock, set_clock
    from ahbo.core.db import open_db
    from ahbo.ops import render_fixed
    from ahbo.ops.seed import load_demo

    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))
    settings = dataclasses.replace(load_settings(), data_dir=tmp_path, fake=frozenset(),
                                   elevenlabs_api_key="k", elevenlabs_voice_id="v")
    db = open_db(":memory:")
    load_demo(db, get_clock(), settings)
    svc = SimpleNamespace(settings=settings, db=db, config=CFG, extra={})
    assert isinstance(render_fixed._tts_for(svc), ElevenLabsTTS)


# ---------------------------------------------------------------- batch STT (MockTransport)
def _stt_transport(seen: list[httpx.Request], text: str = "阿宝，我几时食药啊？", status: int = 200):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if status != 200:
            return httpx.Response(status, text="forbidden")
        return httpx.Response(200, json={"language_code": "yue", "language_probability": 1.0, "text": text,
                                         "words": []})
    return httpx.MockTransport(handler)


async def test_transcribe_file_sends_form_with_repeated_keyterms() -> None:
    seen: list[httpx.Request] = []
    cfg = speech_cfg(CFG).stt
    async with httpx.AsyncClient(transport=_stt_transport(seen)) as c:
        j = await transcribe_file(b"OggS-audio", "n.ogg", api_key="sk_k", cfg=cfg, client=c)
    assert j["text"] == "阿宝，我几时食药啊？" and to_hk(j["text"]) == "阿寶，我幾時食藥啊？"
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/speech-to-text" and req.headers["xi-api-key"] == "sk_k"
    body = req.content.decode("utf-8", "replace")
    for field, value in (("model_id", "scribe_v2"), ("language_code", "yue"), ("tag_audio_events", "false")):
        assert f'name="{field}"\r\n\r\n{value}\r\n' in body
    assert body.count('name="keyterms"') == len(cfg.active_keyterms())
    assert 'name="file"; filename="n.ogg"' in body and "OggS-audio" in body


async def test_transcribe_file_errors_are_stt_errors() -> None:
    async with httpx.AsyncClient(transport=_stt_transport([], status=403)) as c:
        with pytest.raises(SttError, match="403"):
            await transcribe_file(b"x", "n.ogg", api_key="k", cfg=SttCfg(), client=c)
    with pytest.raises(SttError):
        await transcribe_file(b"x", "n.ogg", api_key="", cfg=SttCfg())


def test_live_stt_kwargs_use_the_plugin_realtime_id_and_yue() -> None:
    cfg = speech_cfg(CFG).stt
    kw = live_stt_kwargs(cfg, "k")
    assert kw["model"] == PLUGIN_REALTIME_ID == "scribe_v2_realtime"     # swapped to cfg.live_model after build
    assert kw["language_code"] == "yue" and kw["sample_rate"] == 16000
    assert kw["keyterms"] == cfg.active_keyterms(live=True)


# ---------------------------------------------------------------- transcripts → Traditional (INV-10)
@pytest.mark.parametrize("raw,want", [
    ("阿宝，我几时食药啊？", "阿寶，我幾時食藥啊？"),        # the owner's real-voice test
    ("我没事", "我沒事"),
    ("佢搵唔到晒啲嘢", "佢搵唔到晒啲嘢"),                    # HK colloquial characters are shielded
    ("到时我会提你�", "到時我會提你"),                  # a cut multi-byte char in a realtime partial
    ("我冇事", "我冇事"),
    ("", ""),
])
def test_to_hk(raw: str, want: str) -> None:
    assert to_hk(raw) == want
