"""ffmpeg helpers: every clip is AAC in an `.m4a`, 24 kHz mono (§10.3)."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("ahbo.speech")

SAMPLE_RATE = 24_000
AAC_ARGS = ["-c:a", "aac", "-b:a", "48k", "-ar", str(SAMPLE_RATE), "-ac", "1", "-movflags", "+faststart"]


class AudioToolMissing(RuntimeError):
    pass


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioToolMissing("ffmpeg is not installed (it is in the ahbo-api image)")
    return exe


def _run(args: list[str], stdin: bytes | None = None) -> None:
    r = subprocess.run(args, input=stdin, capture_output=True, timeout=60, check=False)
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"ffmpeg failed ({r.returncode}): {tail}")


def _tmp_for(out_path: Path) -> Path:
    # keep the .m4a extension so ffmpeg picks the mp4 muxer
    return out_path.with_name(f".{out_path.stem}.tmp{out_path.suffix}")


def silent_m4a(out_path: Path, seconds: float) -> None:
    """Write a silent AAC .m4a of `seconds` (24 kHz mono)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(out_path)
    _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
          "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", f"{max(seconds, 0.05):.3f}", *AAC_ARGS, str(tmp)])
    tmp.replace(out_path)


def wav_to_m4a(wav: bytes, out_path: Path) -> None:
    """Encode WAV/RIFF PCM bytes (Azure Riff24Khz16BitMonoPcm) to AAC .m4a."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(out_path)
    _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0", *AAC_ARGS, str(tmp)],
         stdin=wav)
    tmp.replace(out_path)


def probe(path: Path) -> dict:
    """{duration, sample_rate, channels, codec} of an audio file (ffprobe)."""
    exe = shutil.which("ffprobe")
    if not exe:
        raise AudioToolMissing("ffprobe is not installed")
    r = subprocess.run([exe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)],
                       capture_output=True, timeout=30, check=True)
    j = json.loads(r.stdout)
    st = (j.get("streams") or [{}])[0]
    return {"duration": float(j.get("format", {}).get("duration") or 0), "codec": st.get("codec_name"),
            "sample_rate": int(st.get("sample_rate") or 0), "channels": int(st.get("channels") or 0)}
