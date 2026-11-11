"""Real fixed-clip TTS: ElevenLabs `POST /v1/text-to-speech/{voice_id}/with-timestamps` (§10.3, BUILD_LOG §8).

Response shape VERIFIED with a real call (11 Nov 2026, eleven_v4 and eleven_v3, CY voice):
`{audio_base64, alignment: {characters, character_start_times_seconds, character_end_times_seconds},
normalized_alignment: {…same keys…}, quality_check}`; `characters` are single characters of the input line
(punctuation included), times in seconds. `output_format=pcm_24000` returns raw s16le mono 24 kHz, which ffmpeg
encodes to the manifest's AAC `.m4a` (no second lossy step). Character timings become caption segments
`[firstChar, endChar, startSec, endSec]` in UTF-16 units through the same `boundaries_to_segments` the Azure path
uses (punctuation revealed with the unit before it, opening quotes with the unit after it).

Never called in fake mode or in tests (httpx MockTransport there); it spends ElevenLabs credits.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from .audio import SAMPLE_RATE, pcm_to_m4a
from .config import API_BASE
from .segments import OPENERS, TICKS_PER_SEC, Boundary, boundaries_to_segments, is_punct

log = logging.getLogger("ahbo.speech.elevenlabs")

OUTPUT_FORMAT = f"pcm_{SAMPLE_RATE}"


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def alignment_to_boundaries(alignment: dict[str, Any]) -> list[Boundary]:
    """Character alignment → word/punctuation boundaries (Han characters one each, ASCII runs merged, opening
    quotes and spaces dropped so the next unit reveals them)."""
    chars = list(alignment.get("characters") or [])
    starts = list(alignment.get("character_start_times_seconds") or [])
    ends = list(alignment.get("character_end_times_seconds") or [])
    n = min(len(chars), len(starts), len(ends))
    out: list[Boundary] = []
    i = 0
    while i < n:
        ch = str(chars[i])
        if not ch or ch.isspace() or ch in OPENERS:
            i += 1
            continue
        j = i + 1
        if _is_word_char(ch):
            while j < n and _is_word_char(str(chars[j])):
                j += 1
        word = "".join(str(c) for c in chars[i:j])
        t0, t1 = float(starts[i]), float(ends[j - 1])
        out.append(Boundary(audio_offset=int(round(t0 * TICKS_PER_SEC)),
                            duration=int(round(max(t1 - t0, 0.0) * TICKS_PER_SEC)), text_offset=-1,
                            word_length=len(word), text=word,
                            kind="punctuation" if all(is_punct(c) for c in word) else "word"))
        i = j
    return out


def alignment_to_segments(alignment: dict[str, Any], text: str, duration: float) -> list[list[float]]:
    return boundaries_to_segments(alignment_to_boundaries(alignment), text, duration)


class ElevenLabsTTS:
    """Implements `ahbo.core.adapters.FixedTTS`: `render(text, path) -> (duration, segments)`."""
    mode = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str, model: str, language: str | None = "yue", *,
                 client: httpx.Client | None = None, timeout: float = 60.0):
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is not set")
        if not voice_id:
            raise ValueError("ELEVENLABS_VOICE_ID is not set")
        if not model:
            raise ValueError("speech.tts.fixed_model is not set")
        self.api_key, self.voice_id, self.model, self.language = api_key, voice_id, model, language
        self._client = client
        self.timeout = timeout

    @property
    def voice(self) -> str:
        """Part of the manifest version: a new voice or model re-renders every clip."""
        return f"{self.voice_id}:{self.model}:{self.language or ''}"

    def synthesize(self, text: str) -> tuple[bytes, dict[str, Any]]:
        """→ (pcm s16le 24 kHz mono, alignment)."""
        body: dict[str, Any] = {"text": text, "model_id": self.model}
        if self.language:
            body["language_code"] = self.language
        url = f"{API_BASE}/text-to-speech/{self.voice_id}/with-timestamps"
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            r = client.post(url, params={"output_format": OUTPUT_FORMAT}, json=body,
                            headers={"xi-api-key": self.api_key})
        finally:
            if self._client is None:
                client.close()
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs TTS failed: HTTP {r.status_code} {r.text[:200]}")
        j = r.json()
        pcm = base64.b64decode(j.get("audio_base64") or "")
        if not pcm:
            raise RuntimeError("ElevenLabs TTS returned no audio")
        return pcm, (j.get("alignment") or j.get("normalized_alignment") or {})

    def render(self, text: str, out_path: Path) -> tuple[float, list[list[float]]]:
        pcm, alignment = self.synthesize(text)
        duration = len(pcm) / (2 * SAMPLE_RATE)
        pcm_to_m4a(pcm, Path(out_path), SAMPLE_RATE)
        segments = alignment_to_segments(alignment, text, duration)
        log.info("elevenlabs tts rendered %s (%.2fs, %d segments)", Path(out_path).name, duration, len(segments))
        return round(duration, 3), segments
