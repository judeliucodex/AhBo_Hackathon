"""Real fixed-clip TTS: Azure Speech SDK (azure-cognitiveservices-speech 1.53.0), §10.3 + §24.5.

Voice `AZURE_TTS_VOICE` (zh-HK only), SSML `<prosody rate="-10%">`. The SDK has no AAC output format (the
24 kHz formats are PCM, MP3, Opus and SILK), so it renders `Riff24Khz16BitMonoPcm` (WAV) and ffmpeg encodes
24 kHz mono AAC `.m4a`. Word-boundary events become caption segments (`speech.segments`).

Never called in fake mode; it needs `AZURE_SPEECH_KEY` and spends Azure quota.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .audio import wav_to_m4a
from .segments import Boundary, boundaries_to_segments

log = logging.getLogger("ahbo.speech.azure")

DEFAULT_RATE = "-10%"
ALLOWED_LOCALE = "zh-HK"     # INV-10 / D16: no Mandarin voices anywhere


def build_ssml(text: str, voice: str, rate: str = DEFAULT_RATE) -> tuple[str, int]:
    """Return (ssml, offset of the line inside the SSML string) so boundary offsets can be mapped back."""
    if not voice.startswith(ALLOWED_LOCALE + "-"):
        raise ValueError(f"voice {voice!r} is not a {ALLOWED_LOCALE} voice")
    head = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{ALLOWED_LOCALE}">'
            f'<voice name="{escape(voice)}"><prosody rate="{escape(rate)}">')
    body = escape(text)
    return head + body + "</prosody></voice></speak>", len(head)


def boundary_from_event(evt: Any) -> Boundary:
    """SpeechSynthesisWordBoundaryEventArgs → Boundary. Verified in 1.53.0: `audio_offset` (int, 100-ns ticks),
    `duration` (datetime.timedelta), `text_offset`/`word_length` (int, characters; -1 offset when unknown),
    `text` (str), `boundary_type` (SpeechSynthesisBoundaryType.Word|Punctuation|Sentence)."""
    dur = getattr(evt, "duration", 0)
    ticks = int(dur.total_seconds() * 10_000_000) if hasattr(dur, "total_seconds") else int(dur or 0)
    bt = getattr(evt, "boundary_type", None)
    kind = str(getattr(bt, "name", bt or "Word")).lower()
    return Boundary(audio_offset=int(evt.audio_offset), duration=ticks, text_offset=int(evt.text_offset),
                    word_length=int(evt.word_length), text=str(evt.text or ""), kind=kind)


class AzureTTS:
    mode = "azure"

    def __init__(self, key: str, region: str, voice: str, rate: str = DEFAULT_RATE):
        if not key:
            raise ValueError("AZURE_SPEECH_KEY is not set")
        self.key, self.region, self.voice, self.rate = key, region, voice, rate

    def _synthesizer(self):
        import azure.cognitiveservices.speech as speechsdk

        cfg = speechsdk.SpeechConfig(subscription=self.key, region=self.region)
        cfg.speech_synthesis_voice_name = self.voice
        cfg.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm)
        cfg.set_property(speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary, "true")
        cfg.set_property(speechsdk.PropertyId.SpeechServiceResponse_RequestPunctuationBoundary, "true")
        # audio_config=None keeps the audio in result.audio_data (no speaker on the VPS)
        return speechsdk, speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)

    def synthesize_wav(self, text: str) -> tuple[bytes, float, list[Boundary], int]:
        """→ (wav bytes, duration s, boundaries, ssml text start)."""
        speechsdk, synth = self._synthesizer()
        ssml, start = build_ssml(text, self.voice, self.rate)
        events: list[Boundary] = []
        lock = threading.Lock()

        def on_boundary(evt: Any) -> None:
            with lock:
                events.append(boundary_from_event(evt))

        synth.synthesis_word_boundary.connect(on_boundary)
        result = synth.speak_ssml_async(ssml).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            det = result.cancellation_details
            reason = getattr(det, "reason", result.reason)
            detail = (getattr(det, "error_details", "") or "")[:200]
            raise RuntimeError(f"Azure TTS failed: {reason} {detail}")
        duration = result.audio_duration.total_seconds()
        return bytes(result.audio_data), duration, events, start

    def render(self, text: str, out_path: Path) -> tuple[float, list[list[float]]]:
        wav, duration, events, start = self.synthesize_wav(text)
        wav_to_m4a(wav, Path(out_path))
        segments = boundaries_to_segments(events, text, duration, ssml_text_start=start)
        log.info("azure tts rendered %s (%.2fs, %d boundaries)", Path(out_path).name, duration, len(events))
        return round(duration, 3), segments


def get_fixed_tts(settings: Any, voice: str | None = None):
    """FakeTTS when `speech` is faked (or no key), else AzureTTS with the configured (or given) voice."""
    from .fakes import FakeTTS

    v = voice or settings.azure_tts_voice
    if settings.is_fake("speech") or not settings.azure_speech_key:
        return FakeTTS(voice=v)
    return AzureTTS(settings.azure_speech_key, settings.azure_speech_region, v)
