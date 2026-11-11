"""Opper route (owner decision 11 Nov): DeepSeek Flash, Haiku fallback, vision and the Sonnet deep loop all go
through the OpenAI-compatible endpoint. Stub clients only; no network."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from ahbo.core.config import load_config
from ahbo.core.settings import load_settings
from ahbo.deep import tools as dtools
from ahbo.deep.compat_loop import CompatDeepLoop
from ahbo.models.flash import RealFlash
from ahbo.models.vision import RealVision


def _resp(content=None, calls=None, finish="stop"):
    tcs = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a, ensure_ascii=False))) for i, (n, a) in
           enumerate(calls or [])]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tcs or None), finish_reason=finish if not tcs
                          else "tool_calls")], usage=NS(prompt_tokens=10, completion_tokens=5))


class Stub:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


GOOD = {"status": "done", "spoken_summary": "附近長者中心下晝三點有粵曲班。", "card": {"title": "下晝活動", "items": [
    {"time": "15:00", "activity": "粵曲班", "place": "長者中心"}]}, "sources": [],
    "signup_methods": [{"method": "web_form", "url": "https://x.invalid/mock-centre/", "confidence": 0.9}],
    "needs_confirmation": []}


def test_config_routes_everything_through_opper():
    m = load_config(fresh=True).models
    assert m["flash"]["provider"] == "opper" and m["flash"]["model"] == "deepseek-v4.1-flash"
    assert m["sonnet"]["provider"] == "opper" and m["sonnet"]["model"] == "claude-sonnet-5-5"
    assert m["vision"]["provider"] == "opper" and m["flash"]["fallback"]["provider"] == "opper"


async def test_compat_deep_loop_runs_tools_then_submits(harness):
    ctx = dtools.DeepCtx(db=harness.kernel.db, clock=harness.kernel.clock, settings=harness.kernel.settings)
    stub = Stub([_resp(calls=[("find_local_activities", {"interest": "粵曲"})]),
                 _resp(calls=[("submit_result", GOOD)])])
    out = await CompatDeepLoop(load_settings(), ctx=ctx, client=stub).run("deep_t1", "下晝去邊", "", {})
    assert out["status"] == "done" and out["task_id"] == "deep_t1"
    sent = stub.calls[0]
    assert sent["model"] == "claude-sonnet-5-5" and "thinking" not in sent and "output_config" not in sent
    assert {t["function"]["name"] for t in sent["tools"]} >= {"find_local_activities", "submit_result"}
    tool_msg = next(m for m in stub.calls[1]["messages"] if m["role"] == "tool")
    assert "粵曲" in tool_msg["content"]


async def test_compat_deep_loop_repairs_once_then_fails():
    stub = Stub([_resp(content="唔係JSON"), _resp(content="都唔係")])
    out = await CompatDeepLoop(load_settings(), client=stub).run("deep_t2", "x", "", {})
    assert out["status"] == "failed"


async def test_compat_deep_loop_error_is_failed():
    stub = Stub([RuntimeError("500")])
    out = await CompatDeepLoop(load_settings(), client=stub).run("deep_t3", "x", "", {})
    assert out["status"] == "failed"


async def test_flash_falls_back_to_haiku_over_opper():
    class Boom:
        chat = NS(completions=NS(create=None))

        async def _c(self, **kw):
            raise TimeoutError

    boom = Boom()
    boom.chat = NS(completions=NS(create=boom._c))
    fb = Stub([_resp(content="王婆婆你好呀！")])
    f = RealFlash(load_settings(), client=boom, fallback_client=fb)
    r = await f.chat([{"role": "user", "content": "你好"}], None)
    assert r.text == "王婆婆你好呀！" and r.model == "claude-haiku-5-5"
    assert "extra_body" not in fb.calls[0] and fb.calls[0]["model"] == "claude-haiku-5-5"


async def test_vision_over_opper_sends_data_url(tmp_path):
    p = tmp_path / "f.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    stub = Stub([_resp(content="可能有人跌咗喺地下")])
    note = await RealVision(load_settings(), client=stub).describe(p)
    assert note == "可能有人跌咗喺地下"
    part = stub.calls[0]["messages"][0]["content"][0]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,")
