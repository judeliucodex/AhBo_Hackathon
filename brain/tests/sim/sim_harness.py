"""Shared helpers for the sim tests (a uniquely named module, not `conftest`, because several test
directories in this repo have a conftest.py and `import conftest` would be ambiguous)."""
from __future__ import annotations

import asyncio
import math
import struct
import wave
from pathlib import Path


async def wait_online(page, timeout_s: float = 8.0) -> None:
    async def ok() -> bool:
        return await page.locator("#conn[data-state='online']").count() > 0
    await _wait(page, ok, "sim never came online", timeout_s)


async def wait_online_off(page, timeout_s: float = 8.0) -> None:
    async def ok() -> bool:
        return await page.locator("#conn[data-state='online']").count() == 0
    await _wait(page, ok, "sim still online", timeout_s)


def face_frame(page):
    for f in page.frames:
        if "/face/" in f.url:
            return f
    raise AssertionError("face iframe not loaded")


async def trace(page) -> list[dict]:
    """The sim's protocol trace: [{t, dir: in|out|face, obj}, …] oldest first."""
    return await page.evaluate("window.__simtrace")


async def wait_trace(page, js_pred: str, timeout_s: float = 6.0) -> dict:
    """First `window.__simtrace` entry matching the JS arrow `e => …`; e = {t, dir, obj}."""
    return await _wait_js(page, f"window.__simtrace.find(e => ({js_pred})) || null",
                          f"trace entry never matched: {js_pred}", timeout_s)


async def count_trace(page, js_pred: str) -> int:
    return await page.evaluate(f"window.__simtrace.filter(e => ({js_pred})).length")


async def wait_stub(stub, pred, timeout_s: float = 6.0) -> dict:
    """First recorded sim→server frame matching pred(dict) -> bool."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        for f in stub.hub.frames:
            if pred(f):
                return f
        if loop.time() > deadline:
            raise AssertionError(f"no device frame matched {pred}; "
                                 f"got {[f.get('type') for f in stub.hub.frames]}")
        await asyncio.sleep(0.05)


async def _wait(page, cond, msg: str, timeout_s: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not await cond():
        if loop.time() > deadline:
            raise AssertionError(msg)
        await page.wait_for_timeout(50)


async def _wait_js(page, js: str, msg: str, timeout_s: float):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        out = await page.evaluate(js)
        if out is not None:
            return out
        if loop.time() > deadline:
            raise AssertionError(msg)
        await page.wait_for_timeout(50)


def tone_wav(path: Path, seconds: float = 0.9, rate: int = 8000, freq: int = 440) -> None:
    """A real PCM WAV the browser can decode (16-bit mono sine)."""
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            env = min(1.0, i / (rate * 0.02), (n - i) / (rate * 0.02))
            frames += struct.pack("<h", int(12000 * env * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(frames))
