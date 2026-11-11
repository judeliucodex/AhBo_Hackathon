"""Her transcripts → Traditional Chinese (INV-10). ElevenLabs Scribe often writes Cantonese in Simplified characters
(「阿宝，我几时食药啊？」); every live transcript (partial and final) and every voice-note transcript passes here
before the agent, the Kernel or the face sees it. Reuses the spoken filter's OpenCC step and HK shield list
(`agent.filters.spoken.to_traditional`, variant `s2hk`); also drops the U+FFFD that realtime partials sometimes end
with (a cut multi-byte character, seen on the real stream 11 Nov 2026)."""
from __future__ import annotations

from ahbo.agent.filters.spoken import to_traditional

VARIANT = "s2hk"


def to_hk(text: str | None) -> str:
    if not text:
        return text or ""
    return to_traditional(text.replace("�", ""), VARIANT)
