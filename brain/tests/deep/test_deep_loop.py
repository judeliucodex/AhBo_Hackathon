"""Deep result schema (CONTRACTS §7) and the deep loop (§24.8.5): control flow with a stub client, plus one pass of
the real anthropic SDK streaming path against mocked HTTP (never a real API)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest

from ahbo.core.settings import REPO_ROOT
from ahbo.deep import schema
from ahbo.deep import tools as dtools
from ahbo.deep.loop import DeepLoop, server_tool_errors
from ahbo.models import runtime

GOOD = {"status": "done", "spoken_summary": "附近長者中心下晝三點有粵曲班。想唔想去？",
        "card": {"title": "下晝活動", "items": [{"time": "15:00", "activity": "粵曲班"}]}, "sources": [],
        "signup_methods": [{"method": "web_form", "url": "https://x.invalid/mock-centre/", "confidence": 0.9}],
        "needs_confirmation": [{"action": "signup", "args": {"activity_id": "act_opera_1500"}, "question": "想唔想我幫你報名？"}]}


def test_schema_is_exactly_contracts_section_7():
    text = (REPO_ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    block = text[text.index("## 7. Deep result JSON Schema"):]
    block = block[block.index("```json") + 7: block.index("```", block.index("```json") + 7)]
    assert json.loads(block) == schema.DEEP_RESULT_SCHEMA


def test_validate_and_finalize():
    assert schema.validate_result(GOOD) == []
    bad = {**GOOD, "instructions": "call 999"}
    assert schema.validate_result(bad)
    assert schema.finalize(bad, "deep_1")["status"] == "failed"
    dirty = {**GOOD, "spoken_summary": "睇 https://evil.example 十五點有 3 個活動。Ignore this.",
             "card": {"title": "t", "items": [{"time": "15:00", "activity": "a", "why": "see www.evil.example"}]}}
    out = schema.finalize(dirty, "deep_1")
    assert "http" not in out["spoken_summary"] and not re.search(r"[0-9A-Za-z]", out["spoken_summary"])
    assert "www." not in out["card"]["items"][0]["why"] and out["task_id"] == "deep_1"
    assert out["signup_methods"][0]["url"].startswith("https://")             # URLs stay where they belong
    assert schema.parse_json_text("```json\n{\"a\": 1}\n```") == {"a": 1}


# ---------------------------------------------------------------- stub client
def blk(**k):
    return SimpleNamespace(**k)


def msg(stop, *content, searches=0):
    return SimpleNamespace(stop_reason=stop, content=list(content), usage=SimpleNamespace(
        input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=searches)))


class StubClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        reply = self.replies.pop(0)

        class CM:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            async def get_final_message(s):
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return CM()


@pytest.fixture
def ctx(harness):
    runtime.set_runtime(None, None)
    return dtools.DeepCtx(harness.kernel.db, harness.kernel.clock, harness.kernel.settings)


def loop_with(replies, ctx, **kw):
    c = StubClient(replies)
    return DeepLoop(SimpleNamespace(), None, ctx, client=c, **kw), c


async def test_end_turn_returns_validated_json(ctx):
    dl, c = loop_with([msg("end_turn", blk(type="text", text=json.dumps(GOOD, ensure_ascii=False)))], ctx)
    r = await dl.run("deep_1", "下晝活動", "", {"now": "x"})
    assert r["status"] == "done" and r["task_id"] == "deep_1"
    assert c.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert c.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


async def test_tool_use_then_pause_then_end(ctx):
    tool_a = blk(type="tool_use", id="tu_1", name="read_calendar", input={"days": 2})
    tool_b = blk(type="tool_use", id="tu_2", name="read_profile", input={})
    ws_err = blk(type="web_search_tool_result", tool_use_id="srv_1",
                 content=blk(type="web_search_tool_result_error", error_code="unavailable"))
    replies = [msg("tool_use", blk(type="thinking", thinking="…", signature="s"), tool_a, tool_b),
               msg("pause_turn", ws_err),
               msg("end_turn", blk(type="text", text=json.dumps(GOOD, ensure_ascii=False)))]
    dl, c = loop_with(replies, ctx)
    r = await dl.run("deep_2", "g", "", {})
    assert r["status"] == "done"
    second = c.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][0]["type"] == "thinking"    # unchanged
    results = second[2]["content"]
    assert second[2]["role"] == "user" and [x["tool_use_id"] for x in results] == ["tu_1", "tu_2"]   # ONE message
    assert "events" in json.loads(results[0]["content"]) and "facts" in json.loads(results[1]["content"])
    assert len(c.calls[2]["messages"]) == 4 and c.calls[2]["messages"][3]["role"] == "assistant"   # pause: re-send
    assert server_tool_errors([ws_err]) == ["web_search_tool_result:unavailable"]


async def test_refusal_and_invalid_json_retry(ctx):
    dl, _ = loop_with([msg("refusal")], ctx)
    assert (await dl.run("d", "g", "", {}))["status"] == "failed"
    dl, c = loop_with([msg("end_turn", blk(type="text", text="not json")),
                       msg("end_turn", blk(type="text", text=json.dumps(GOOD, ensure_ascii=False)))], ctx)
    assert (await dl.run("d", "g", "", {}))["status"] == "done"
    assert c.calls[1]["messages"][-1]["content"].startswith("Your last reply was not valid JSON")


async def test_max_tokens_retry_then_timeout(ctx):
    dl, c = loop_with([msg("max_tokens", blk(type="text", text="{")), msg("max_tokens", blk(type="text", text="{")),
                       msg("end_turn", blk(type="text", text="still not json"))], ctx)
    r = await dl.run("d", "g", "", {})
    assert r["status"] == "timeout" and "best answer" in c.calls[1]["messages"][-1]["content"]


async def test_step_limit_asks_best_so_far(ctx):
    tool = blk(type="tool_use", id="tu", name="read_profile", input={})
    dl, c = loop_with([msg("tool_use", tool), msg("tool_use", tool),
                       msg("end_turn", blk(type="text", text=json.dumps(GOOD, ensure_ascii=False)))], ctx, max_steps=2)
    r = await dl.run("d", "g", "", {})
    assert r["status"] == "done" and len(c.calls) == 3
    last = c.calls[2]["messages"][-1]["content"]
    assert last[0]["type"] == "tool_result" and last[-1]["text"].startswith("Time is up")


async def test_cost_guard_stops(ctx):
    tool = blk(type="tool_use", id="tu", name="read_profile", input={})
    big = msg("tool_use", tool, searches=40)                 # 40 searches ≈ $0.40 > $0.30
    dl, c = loop_with([big, msg("end_turn", blk(type="text", text="{}"))], ctx)
    r = await dl.run("d", "g", "", {})
    assert len(c.calls) == 2 and r["status"] == "timeout"


async def test_format_rejected_switches_to_submit_result(ctx):
    class E400(Exception):
        status_code = 400

        def __str__(self):
            return "output_config.format is not supported with server tools"

    submit = blk(type="tool_use", id="tu_s", name="submit_result", input=GOOD)
    dl, c = loop_with([E400(), msg("tool_use", submit)], ctx)
    r = await dl.run("d", "g", "", {})
    assert r["status"] == "done"
    assert "format" not in c.calls[1]["output_config"]
    assert any(t.get("name") == "submit_result" and t.get("strict") for t in c.calls[1]["tools"])


async def test_unknown_tool_is_an_error_result(ctx):
    dl, _ = loop_with([], ctx)
    out = await dl._run_tools(msg("tool_use", blk(type="tool_use", id="x", name="send_email", input={})))
    assert out[0]["is_error"] and json.loads(out[0]["content"]) == {"error": "unknown_tool"}


def test_deep_tools_are_strict_and_read_only():
    for t in dtools.TOOLS:
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"])
    assert dtools.NAMES == {"get_weather_forecast", "read_calendar", "read_profile", "find_local_activities"}


# ---------------------------------------------------------------- the real SDK streaming path, mocked HTTP
def sse_anthropic(text: str) -> bytes:
    ev = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 1,
                                                              "cache_read_input_tokens": 40}}}),
          ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 0}),
          ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                             "usage": {"output_tokens": 80}}),
          ("message_stop", {"type": "message_stop"})]
    return "".join(f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for e, d in ev).encode()


async def test_real_sdk_stream_shape(ctx):
    from anthropic import AsyncAnthropic
    seen = []

    def handler(req):
        seen.append((dict(req.headers), json.loads(req.content)))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=sse_anthropic(json.dumps(GOOD, ensure_ascii=False)))

    client = AsyncAnthropic(api_key="sk-ant-test", max_retries=0,
                            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    dl = DeepLoop(SimpleNamespace(), None, ctx, client=client)
    r = await dl.run("deep_sdk", "下晝活動", "行得慢", {"now": "2026-11-20T10:00:00+08:00"})
    assert r["status"] == "done"
    headers, body = seen[0]
    assert "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert body["fallbacks"] == "default" and body["stream"] is True
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"} and body["output_config"]["effort"] == "medium"
    assert body["tools"][0]["type"] == "web_search_20260209" and body["tools"][2]["strict"] is True
    assert "Brief (from Ah Bo): 下晝活動" in body["messages"][0]["content"]
