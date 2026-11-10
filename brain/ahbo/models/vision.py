"""Real vision (§9.4, §4.3.1): Claude Haiku 5.5 looks at the fall photo and writes one short advisory note.
Advisory only (INV-2): the note is shown in the console/step label and can never lower or cancel an incident.
8 s budget (models.yaml `vision.timeout_s`); any failure → None (the Kernel uses its default label).
Image block shape verified against anthropic 2.7.0 (`ImageBlockParam` + `Base64ImageSourceParam`, media types
jpeg/png/gif/webp)."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from pathlib import Path
from typing import Any

from . import pricing, runtime

log = logging.getLogger("ahbo.models.vision")

PROMPT = ("呢張相係一部陪伴長者嘅機械寵物喺偵測到可能跌倒之後影嘅。用一句好短嘅廣東話（繁體，最多二十個字）描述你見到乜，"
          "例如「可能有人跌咗喺地下」或者「見唔到有人」。唔好估病情，唔好俾建議，唔好用數字或者英文。")
MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
MAX_NOTE = 30


def clean_note(text: str) -> str | None:
    t = re.sub(r"(?i)https?://\S+|[A-Za-z]+|\d+", "", text or "").strip().strip("「」\"' ")
    t = t.split("\n")[0][:MAX_NOTE]
    return t or None


class RealVision:
    def __init__(self, settings: Any, config: Any = None, *, client: Any = None):
        self.settings, self.config, self._client = settings, config, client

    def client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self.settings.anthropic_api_key, max_retries=0)
        return self._client

    async def describe(self, photo_path: Path, *, correlation_id: str | None = None) -> str | None:
        cfg = runtime.model_cfg(self.config, "vision")
        timeout = float(cfg.get("timeout_s", 8))
        p = Path(photo_path)
        media = MEDIA.get(p.suffix.lower(), "image/jpeg")
        try:
            data = base64.standard_b64encode(p.read_bytes()).decode()
        except OSError:
            log.warning("vision: photo not readable")
            return None
        t0 = time.perf_counter()
        model = cfg.get("model", "claude-haiku-5-5")
        try:
            msg = await asyncio.wait_for(self.client().messages.create(
                model=model, max_tokens=100,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                    {"type": "text", "text": PROMPT}]}]), timeout)
        except Exception as e:
            log.warning("vision failed (%s)", type(e).__name__)
            runtime.record("anthropic", model, "vision", {}, 0.0, correlation_id=correlation_id, span="vision.describe",
                           duration_ms=(time.perf_counter() - t0) * 1000, status="error")
            return None
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        u = msg.usage
        units = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0}
        runtime.record("anthropic", model, "vision", units, pricing.usd(model, **units), correlation_id=correlation_id,
                       span="vision.describe", duration_ms=(time.perf_counter() - t0) * 1000)
        return clean_note(text)
