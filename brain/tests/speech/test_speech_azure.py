"""speech.azure_tts: SSML building and the boundary→segments conversion, with synthetic events only.

The real Azure service is NEVER called here (no key, no spend): `synthesize_wav` is monkeypatched, and the
SDK enum/attribute names this relies on are verified against the installed 1.53.0 package in the report.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ahbo.speech.audio import probe
from ahbo.speech.azure_tts import DEFAULT_RATE, AzureTTS, boundary_from_event, build_ssml, get_fixed_tts
from ahbo.speech.segments import Boundary, boundaries_to_segments, u16len

VOICE = "zh-HK-HiuMaanNeural"
TEXT = "王婆婆，夠鐘食藥喇：血壓藥，同埋薄血丸。"


class FakeSdkEvent:
    """Shaped like SpeechSynthesisWordBoundaryEventArgs (VERIFY results in the report)."""

    def __init__(self, audio_offset_s: float, word: str, char_offset: int, length: int,
                 kind: str = "word", duration_s: float = 0.15):
        self.audio_offset = int(audio_offset_s * 10_000_000)      # 100-ns ticks
        self.duration = timedelta(seconds=duration_s)             # 1.53 returns a timedelta
        self.text_offset = char_offset
        self.word_length = length
        self.text = word
        self.boundary_type = kind


def _events_for(text: str) -> list[FakeSdkEvent]:
    """Synthetic word boundaries: one per Han unit, punctuation boundaries between, 0.19 s per character."""
    evs: list[FakeSdkEvent] = []
    t = 0.0
    i = 0
    chars = list(text)
    while i < len(chars):
        ch = chars[i]
        if ch in "，。！？「」":
            evs.append(FakeSdkEvent(t, ch, i, 1, kind="punctuation", duration_s=0.0))
            t += 0.19
            i += 1
            continue
        evs.append(FakeSdkEvent(t, ch, i, 1, duration_s=0.19))
        t += 0.19
        i += 1
    return evs


# ---------------------------------------------------------------- SSML
def test_build_ssml_wraps_prosody_and_reports_offset() -> None:
    ssml, start = build_ssml(TEXT, VOICE)
    assert f'<voice name="{VOICE}">' in ssml
    assert f'<prosody rate="{DEFAULT_RATE}">' in ssml and DEFAULT_RATE == "-10%"
    assert ssml.startswith('<speak version="1.0"')
    assert 'xml:lang="zh-HK"' in ssml
    assert ssml[start:start + 3] == TEXT[:3]          # the hint maps SSML offsets back to the line
    assert build_ssml(TEXT, VOICE)[1] > 0


def test_build_ssml_rejects_non_hk_voice() -> None:
    with pytest.raises(ValueError, match="zh-HK"):
        build_ssml("你好", "zh-CN-XiaoxiaoNeural")     # INV-10/D16: no Mandarin voices anywhere


# ---------------------------------------------------------------- boundary conversion
def test_boundary_from_event_normalises_units() -> None:
    b = boundary_from_event(FakeSdkEvent(0.4, "王婆婆", 0, 3))
    assert isinstance(b, Boundary)
    assert b.audio_offset == 4_000_000
    assert b.duration == 1_500_000                    # timedelta → ticks
    assert (b.text_offset, b.word_length, b.text, b.kind) == (0, 3, "王婆婆", "word")
    bp = boundary_from_event(FakeSdkEvent(0.6, "，", 3, 1, kind="punctuation", duration_s=0.0))
    assert bp.kind == "punctuation"


def test_boundaries_to_segments_are_monotonic_and_cover_text() -> None:
    evs = _events_for(TEXT)
    boundaries = [boundary_from_event(e) for e in evs]
    total = round(0.19 * len(TEXT), 3)
    out = boundaries_to_segments(boundaries, TEXT, total)
    assert out, "no segments produced"
    prev_end_t = -1.0
    for a, bch, s, e in out:
        assert 0 <= a < bch <= u16len(TEXT)
        assert s >= prev_end_t - 1e-9, "time overlap"
        assert e <= total + 0.05, "segment runs past the audio"
        prev_end_t = e
    assert out[-1][1] == u16len(TEXT)                 # trailing punctuation rides with the last word


def test_synthesize_render_writes_aac_and_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The full render path with `synthesize_wav` faked: real ffmpeg encode, synthetic boundaries."""
    tts = AzureTTS(key="key-not-a-secret", region="eastasia", voice=VOICE)
    wav_silence = _silent_wav_bytes(0.19 * len(TEXT))
    monkeypatch.setattr(tts, "synthesize_wav", lambda text: (wav_silence, 0.19 * len(TEXT),
                                                             [boundary_from_event(e) for e in _events_for(TEXT)],
                                                             0))
    out = tmp_path / "R_s_1200.m4a"
    duration, segs = tts.render(TEXT, out)
    assert out.is_file() and out.stat().st_size > 0
    info = probe(out)
    assert info["codec"] == "aac" and info["sample_rate"] == 24_000 and info["channels"] == 1
    assert abs(info["duration"] - duration) < 0.1
    assert segs and segs[-1][1] == u16len(TEXT)


def _silent_wav_bytes(seconds: float) -> bytes:
    """A real 24 kHz mono PCM WAV built by ffmpeg (no Azure involved)."""
    import subprocess

    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.3f}",
                        "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"], capture_output=True, check=True)
    return r.stdout


# ---------------------------------------------------------------- adapter choice
def test_get_fixed_tts_falls_back_to_fake_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {"speech"}

    class S:
        azure_tts_voice = VOICE
        azure_speech_key = ""
        azure_speech_region = "eastasia"

        def is_fake(self, item: str) -> bool:
            return item in fake

    from ahbo.speech.fakes import FakeTTS

    assert isinstance(get_fixed_tts(S()), FakeTTS)            # fake mode
    fake.clear()                                              # real mode but no key configured → still fake
    assert isinstance(get_fixed_tts(S()), FakeTTS)
    S.azure_speech_key = "k"                                  # now real
    assert isinstance(get_fixed_tts(S()), AzureTTS)


def test_get_fixed_tts_builds_azure_when_real() -> None:
    class S:
        azure_tts_voice = VOICE
        azure_speech_key = "k"
        azure_speech_region = "eastasia"

        def is_fake(self, item: str) -> bool:
            return False

    tts = get_fixed_tts(S())
    assert isinstance(tts, AzureTTS)
    assert tts.voice == VOICE and tts.region == "eastasia"
