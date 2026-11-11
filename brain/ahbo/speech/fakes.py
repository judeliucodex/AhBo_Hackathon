"""Fake fixed-clip TTS (§26.3): silent AAC clips of the estimated duration, with estimated caption segments.
Implements `ahbo.core.adapters.FixedTTS`."""
from __future__ import annotations

from pathlib import Path

from .audio import silent_m4a
from .segments import SEC_PER_CHAR, estimate_segments


class FakeTTS:
    mode = "fake"

    def __init__(self, voice: str = "fake", sec_per_char: float = SEC_PER_CHAR):
        self.voice = voice
        self.sec_per_char = sec_per_char

    def estimate(self, text: str) -> tuple[float, list[list[float]]]:
        return estimate_segments(text, self.sec_per_char)

    def render(self, text: str, out_path: Path) -> tuple[float, list[list[float]]]:
        duration, segments = self.estimate(text)
        silent_m4a(Path(out_path), duration)
        return duration, segments
