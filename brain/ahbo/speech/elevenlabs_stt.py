"""ElevenLabs Scribe speech-to-text (BUILD_LOG §8): batch (voice notes, doctor) and live (LiveKit worker).

Batch — `POST /v1/speech-to-text`, multipart `file`, `model_id` (`speech.stt.file_model`), `language_code`
(`yue`), `tag_audio_events=false`, `keyterms` as repeated form fields. Response `{language_code,
language_probability, text, …}`; the text is often Simplified, so callers convert with `speech.transcript.to_hk`.

Live — the installed `livekit-plugins-elevenlabs` 1.8.11 `STT` already speaks the realtime protocol (VERIFIED in
site-packages and against the real endpoint with `scribe_v3_live`, 11 Nov 2026):
`wss://…/v1/speech-to-text/realtime?model_id=…&audio_format=pcm_16000&commit_strategy=manual&language_code=yue
&keyterms=…`, `input_audio_chunk {audio_base_64, commit, sample_rate}` frames, and the server's
`session_started` / `partial_transcript` (+`turn_probability`) / `committed_transcript` messages. Its only gap:
it switches to streaming solely for the exact id `"scribe_v2_realtime"` (`use_realtime = model ==
"scribe_v2_realtime"`), so `live_stt()` builds it with that id and then puts the configured live model
(`scribe_v3_live`) into `_opts.model_id`, the field `_connect_ws` sends. Commits are manual: LiveKit's Silero VAD
flushes the stream at the end of her speech (`STTCapabilities.manual_flush`), which keeps the patient endpointing
of §24.6 on our side.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import API_BASE, SttCfg

log = logging.getLogger("ahbo.speech.elevenlabs")

PLUGIN_REALTIME_ID = "scribe_v2_realtime"     # the id livekit-plugins-elevenlabs 1.8.11 gates streaming on


class SttError(RuntimeError):
    pass


def batch_form(cfg: SttCfg, *, model: str | None = None, keyterms: bool = True) -> dict[str, Any]:
    """Form fields for `POST /v1/speech-to-text` (httpx repeats a list value as repeated fields)."""
    data: dict[str, Any] = {"model_id": model or cfg.file_model, "tag_audio_events": "false"}
    if cfg.language:
        data["language_code"] = cfg.language
    terms = cfg.active_keyterms() if keyterms else []
    if terms:
        data["keyterms"] = terms
    return data


async def transcribe_file(audio: bytes, filename: str, *, api_key: str, cfg: SttCfg, content_type: str = "audio/ogg",
                          client: httpx.AsyncClient | None = None, timeout: float = 60.0,
                          model: str | None = None, keyterms: bool = True) -> dict[str, Any]:
    """Batch transcription → the response JSON (`text` as returned, usually Simplified). Raises SttError."""
    if not api_key:
        raise SttError("ELEVENLABS_API_KEY is not set")
    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await c.post(f"{API_BASE}/speech-to-text", headers={"xi-api-key": api_key},
                         data=batch_form(cfg, model=model, keyterms=keyterms),
                         files={"file": (filename, audio, content_type)})
    except httpx.HTTPError as e:
        raise SttError(f"ElevenLabs STT unreachable ({type(e).__name__})") from e
    finally:
        if own:
            await c.aclose()
    if r.status_code != 200:
        raise SttError(f"ElevenLabs STT failed: HTTP {r.status_code} {r.text[:200]}")
    j = r.json()
    if not isinstance(j, dict):
        raise SttError("ElevenLabs STT returned an unexpected body")
    return j


def live_stt_kwargs(cfg: SttCfg, api_key: str) -> dict[str, Any]:
    """Constructor kwargs for `livekit.plugins.elevenlabs.STT` (pure; unit-tested in the api image)."""
    kw: dict[str, Any] = {"api_key": api_key, "model": PLUGIN_REALTIME_ID, "sample_rate": 16000}
    if cfg.language:
        kw["language_code"] = cfg.language
    terms = cfg.active_keyterms(live=True)
    if terms:
        kw["keyterms"] = terms
    return kw


def live_stt(cfg: SttCfg, api_key: str) -> Any:
    """The LiveKit streaming STT for `cfg.live_model` (ahbo-agent image only)."""
    from livekit.plugins import elevenlabs

    if not api_key:
        raise SttError("ELEVENLABS_API_KEY is not set")
    stt = elevenlabs.STT(**live_stt_kwargs(cfg, api_key))
    if not stt.capabilities.streaming:
        raise SttError("livekit-plugins-elevenlabs did not enable realtime streaming")
    stt._opts.model_id = cfg.live_model          # see module doc: the wire protocol is the same (verified)
    return stt
