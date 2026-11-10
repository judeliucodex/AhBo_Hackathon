"""Real model adapters against mocked HTTP only (httpx2.MockTransport; never a real API)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx2
import pytest

from ahbo.models import fakes, flash, runtime, sonnet, vision

SETTINGS = SimpleNamespace(deepseek_api_key="sk-test", deepseek_base_url="https://api.deepseek.test",
                           anthropic_api_key="sk-ant-test", opper_api_key="", is_fake=lambda _x: False)


def sse_openai(chunks: list[dict]) -> bytes:
    out = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return out.encode()


def chunk(delta: dict, finish=None, usage=None) -> dict:
    c = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "deepseek-flash",
         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        c["usage"] = usage
    return c


def anthropic_message(content, stop="end_turn", model="claude-haiku-5-5"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}}


def openai_client(handler):
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key="sk-test", base_url="https://api.deepseek.test",
                       http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), max_retries=0)


def anthropic_client(handler):
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key="sk-ant-test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
                          max_retries=0)


@pytest.fixture(autouse=True)
def no_runtime():
    runtime.set_runtime(None, None)
    yield
    runtime.set_runtime(None, None)


# ---------------------------------------------------------------- Flash (DeepSeek)
@pytest.mark.parametrize("level,body", [
    ("off", {"extra_body": {"thinking": {"type": "disabled"}}}),
    ("low", {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "low"}),
    ("high", {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high"}),
    ("max", {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "max"}),
    ("medium", {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high"}),   # bucketed
])
def test_thinking_table(level, body):
    assert flash.thinking_fields(level) == body


async def test_flash_streams_text_and_tools_and_always_sends_thinking():
    seen = []

    def handler(req: httpx2.Request):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse_openai([
            chunk({"role": "assistant", "reasoning_content": "諗"}),
            chunk({"content": "好呀，"}),
            chunk({"content": "等我睇睇。"}),
            chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                   "function": {"name": "get_med_status", "arguments": "{\"sco"}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": "pe\": \"next\"}"}}]}, finish="tool_calls",
                  usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})]))

    f = flash.RealFlash(SETTINGS, client=openai_client(handler))
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "我幾時食藥？"},
            {"role": "assistant", "content": None, "reasoning_content": "前一輪", "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c0", "content": "{}"}]
    tools = [{"type": "function", "function": {"name": "get_med_status", "description": "d",
                                               "parameters": {"type": "object", "properties": {}}}}]
    r = await f.chat(msgs, tools, purpose="voice_turn", thinking="high")
    assert r.text == "好呀，等我睇睇。" and r.reasoning_content == "諗"
    assert [(c.id, c.name, c.args) for c in r.tool_calls] == [("call_1", "get_med_status", {"scope": "next"})]
    assert r.usage["input_tokens"] == 100
    body = seen[0]
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high" and body["stream"] is True
    assert body["messages"][2]["reasoning_content"] == "前一輪"          # round-trip (§20.1 trap)
    assert body["tool_choice"] == "auto" and body["model"] == "deepseek-flash"


async def test_flash_default_is_thinking_off_and_knob_applies(monkeypatch):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=sse_openai([chunk({"content": "好"})]))

    f = flash.RealFlash(SETTINGS, client=openai_client(handler))
    await f.chat([{"role": "user", "content": "x"}])
    assert seen[-1]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in seen[-1]
    monkeypatch.setattr(runtime, "knobs", lambda: {"flash_thinking": "low"})
    await f.chat([{"role": "user", "content": "x"}], purpose="voice_turn")
    assert seen[-1]["thinking"] == {"type": "enabled"} and seen[-1]["reasoning_effort"] == "low"
    await f.chat([{"role": "user", "content": "x"}], purpose="extractor")      # knob is for voice turns only
    assert seen[-1]["thinking"] == {"type": "disabled"}


async def test_flash_first_token_timeout_falls_back_to_haiku(monkeypatch):
    async def slow(req):
        await asyncio.sleep(1.0)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=sse_openai([chunk({"content": "遲"})]))

    sent = []

    def ant(req):
        sent.append(json.loads(req.content))
        return httpx2.Response(200, json=anthropic_message([
            {"type": "text", "text": "好呀。"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_time", "input": {}}], stop="tool_use"))

    monkeypatch.setattr(runtime, "model_cfg", lambda c, s, p=None: {
        "provider": "deepseek", "model": "deepseek-flash", "thinking": "off", "max_tokens": 400,
        "first_token_timeout_s": 0.1, "fallback": {"provider": "anthropic", "model": "claude-haiku-5-5", "effort": "low"}})
    f = flash.RealFlash(SETTINGS, client=openai_client(slow), fallback_client=anthropic_client(ant))
    msgs = [{"role": "system", "content": "persona"}, {"role": "user", "content": "幾點"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c0", "content": "{\"ok\": true}"}]
    tools = [{"type": "function", "function": {"name": "get_time", "description": "t",
                                               "parameters": {"type": "object", "properties": {}}}}]
    r = await f.chat(msgs, tools)
    assert r.model == "claude-haiku-5-5" and r.text == "好呀。" and r.tool_calls[0].name == "get_time"
    b = sent[0]
    assert b["system"] == "persona" and b["model"] == "claude-haiku-5-5" and b["output_config"] == {"effort": "low"}
    assert b["messages"][1]["content"][0]["type"] == "tool_use"
    assert b["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "c0", "content": "{\"ok\": true}"}
    assert b["tools"][0]["input_schema"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------- Sonnet call shape (§20.2)
@pytest.mark.parametrize("level,thinking,effort", [
    ("off", {"type": "between_tools"}, "high"),
    ("low", {"type": "adaptive", "display": "omitted"}, "low"),
    ("medium", {"type": "adaptive", "display": "omitted"}, "medium"),
    ("xhigh", {"type": "adaptive", "display": "omitted"}, "xhigh"),
    ("max", {"type": "adaptive", "display": "omitted"}, "max"),
])
def test_sonnet_thinking_table(level, thinking, effort):
    assert sonnet.thinking_param(level, "omitted") == thinking and sonnet.effort_param(level) == effort
    assert sonnet.thinking_param("high", "summarized") == {"type": "adaptive", "display": "summarized"}


def test_sonnet_request_kwargs_never_send_forbidden_fields():
    cfg = sonnet.sonnet_cfg(None, "deep_task", "off")
    kw = sonnet.request_kwargs(cfg, system="S", tools=sonnet.server_tools(cfg), messages=[], schema={"type": "object"})
    assert kw["thinking"] == {"type": "between_tools"} and kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["tool_choice"] == {"type": "auto"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    for bad in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert bad not in json.dumps(kw)
    assert [t["type"] for t in kw["tools"]] == ["web_search_20260209", "web_fetch_20260209"]
    assert sonnet.sonnet_cfg(None, "morning_plan")["thinking"] == "high"


# ---------------------------------------------------------------- vision
async def test_vision_mocked(tmp_path):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    sent = []

    def ant(req):
        sent.append(json.loads(req.content))
        return httpx2.Response(200, json=anthropic_message([{"type": "text", "text": "「可能有人跌咗喺地下 3 person」"}]))

    v = vision.RealVision(SETTINGS, client=anthropic_client(ant))
    assert await v.describe(photo) == "可能有人跌咗喺地下"
    img = sent[0]["messages"][0]["content"][0]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/jpeg"
    assert await v.describe(tmp_path / "missing.jpg") is None


async def test_fake_vision_and_deep():
    assert await fakes.FakeVision(delay_s=0).describe(None) == "可能有人跌咗喺地下"
    d = fakes.FakeDeep(delay_s=0)
    r = await d.run("deep_1", "下晝有咩活動", "", {})
    assert r["task_id"] == "deep_1" and r["needs_confirmation"][0]["action"] == "signup"
    g = await d.run("deep_2", "比較兩間茶樓", "", {})
    assert g["status"] == "done" and g["card"]["items"]
