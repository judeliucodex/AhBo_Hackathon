"""Pure parts of the LiveKit worker (the worker itself needs the ahbo-agent image and keys; unrun)."""
from __future__ import annotations

import pytest

from ahbo.agent import worker
from ahbo.agent.tools import schemas


def test_split_zh_on_chinese_punctuation():
    assert worker.split_zh("王婆婆，你好呀。我係阿寶！你今日點呀？好") == ["王婆婆，", "你好呀。", "我係阿寶！", "你今日點呀？好"]
    assert worker.split_zh_spans("好呀。食咗未？") == [("好呀。", 0, 3), ("食咗未？", 3, 7)]


def test_tools_for_mode():
    s = schemas("companion")
    assert worker.tools_for_mode(s, "kernel_listen") == [] and worker.tools_for_mode(s, "paused") == []
    assert {x["function"]["name"] for x in worker.tools_for_mode(s + schemas("reassure"), "reassure")} == \
        {"get_time", "get_incident_status"}
    assert worker.tools_for_mode(s, "companion") == s


@pytest.mark.parametrize("reason,wake,first,verdict,rem", [
    ("touch", None, "今日幾號", "skip", None),
    ("wakeword", {"s2_text": "喂阿寶，今日幾號呀"}, None, "accept", "今日幾號呀"),
    ("wakeword", {"s2_text": "今日天氣好好"}, None, "reject", None),
    ("wakeword", None, "喂阿寶", "accept", ""),
    ("wakeword", None, None, "reject", None),
])
def test_stage3_decision(reason, wake, first, verdict, rem):
    v, r = worker.stage3_decision(reason, wake, first)
    assert v == verdict
    if verdict == "accept":
        assert (r or "").lstrip("，") == rem


def test_turn_handling_is_patient_with_toggle():
    th = worker.turn_handling(False)
    assert th["endpointing"] == {"min_delay": 0.8, "max_delay": 3.0}
    assert th["interruption"]["enabled"] is False and th["interruption"]["min_duration"] == 0.6


def test_reply_filter_cuts_and_cleans():
    f = worker.ReplyFilter()
    out = f.push("王婆婆，而家係15:00。") + f.push("今日天氣好好，可以落街行吓。") + f.push("仲有好多嘢想同你講，下次再講啦。")
    assert out == ["王婆婆，而家係下晝三點。", "今日天氣好好，可以落街行吓。"] and f.stopped and "too_long" in f.quality


def test_phrases_loaded():
    assert "阿寶" in worker.load_phrases() and "薄血丸" in worker.load_phrases()


async def test_session_run_handles_pull_items():
    class Sess:
        def __init__(self):
            self.calls = []

        def generate_reply(self, **k):
            self.calls.append(("reply", k))

        def interrupt(self, force=False):
            self.calls.append(("interrupt", force))

    run = worker.SessionRun(api=None, claim={"session_id": "sess_1", "reason": "touch", "mode": "companion"})
    run.session = Sess()
    await run.handle({"kind": "inject", "instructions": "[阿寶系統｜提醒｜唔係王婆婆講嘅] x。"})
    await run.handle({"kind": "mode", "mode": "paused"})
    await run.handle({"kind": "inject", "instructions": "y"})           # dropped: paused
    await run.handle({"kind": "close"})
    assert run.session.calls == [("reply", {"instructions": "[阿寶系統｜提醒｜唔係王婆婆講嘅] x。"}), ("interrupt", True)]
    assert run.mode == "paused" and run.closed.is_set()


# ---------------------------------------------------------------- ElevenLabs switch (BUILD_LOG §8)
class _Alt:
    def __init__(self, text: str):
        self.text = text


class _Ev:
    def __init__(self, *texts: str):
        self.alternatives = [_Alt(t) for t in texts]


def test_hk_event_converts_every_transcript_to_traditional():
    ev = worker.hk_event(_Ev("阿宝，我几时食药啊？", "我没事"))
    assert [a.text for a in ev.alternatives] == ["阿寶，我幾時食藥啊？", "我沒事"]
    assert worker.hk_event("喂阿宝") == "喂阿寶"
    empty = _Ev()
    assert worker.hk_event(empty) is empty


def test_stage3_accepts_a_converted_simplified_wake():
    v, rem = worker.stage3_decision("wakeword", None, worker.hk_event("喂阿宝，今日几号呀"))
    assert v == "accept" and rem.lstrip("，") == "今日幾號呀"


def test_components_build_in_the_agent_image(monkeypatch):
    """Runs only where livekit-agents is installed (ahbo-agent): STT/TTS construct with ElevenLabs only."""
    pytest.importorskip("livekit.plugins.elevenlabs")
    import dataclasses

    from ahbo.core.settings import load_settings
    from ahbo.speech.config import speech_cfg

    s = dataclasses.replace(load_settings(), elevenlabs_api_key="sk_test", elevenlabs_voice_id="VOICE",
                            azure_speech_key="", opper_api_key="op")
    sp = speech_cfg()
    stt = worker.build_stt(s, sp.stt)
    assert stt.capabilities.streaming and stt.model == sp.stt.live_model
    tts = worker.build_tts(s, sp.tts)
    assert type(tts).__module__.startswith("livekit.plugins.elevenlabs") and tts.model == sp.tts.live_model
    comps = worker.build_components(s, None)
    assert set(comps) == {"stt", "llm", "tts", "vad"}
    with_azure = worker.build_tts(dataclasses.replace(s, azure_speech_key="az"), sp.tts)
    assert type(with_azure).__name__ == "FallbackAdapter"


def test_g1_fallback_line_is_the_fixed_text():
    from ahbo.agent.worker import g1_text
    assert g1_text() == "阿寶而家諗唔到嘢，等陣再試吓好嗎？"
