"""LiveKit Agents worker (image `ahbo-agent`, livekit-agents 1.8.11; §24, §5). Run: `python -m ahbo.agent.worker start`.

agent_name `ahbo-agent` (explicit dispatch: the platform's room config dispatches it, `api/routes/device.py`).
Per job: claim the session (`POST /internal/session/claim`), load the context (`GET /internal/context/{id}`), then
run one AgentSession:
  STT  `models.yaml → speech.stt` (owner decision 11 Nov 2026): ElevenLabs Scribe live (`scribe_v3_live`, `yue`,
       keyterms) through livekit-plugins-elevenlabs (`ahbo.speech.elevenlabs_stt.live_stt`; manual commits, so
       Silero VAD + the patient endpointing below decide the end of her turn). Provider `azure` keeps the old
       Azure zh-HK STT + phrase list. Every transcript (partial and final) is converted Simplified → Traditional
       (`ahbo.speech.transcript.to_hk`, OpenCC s2hk + the spoken filter's shield list) in `stt_node`, so captions,
       the Kernel, Stage 3 and the LLM never see Simplified text (INV-10).
  LLM  Flash through the OpenAI-compatible plugin (Opper route), thinking OFF (`extra_body.thinking.disabled`)
  TTS  `speech.tts`: ElevenLabs streaming, the CY voice (`ELEVENLABS_VOICE_ID`), `live_model`, `language_code=yue`,
       with the Chinese-punctuation sentence tokenizer (auto_mode: one sentence per flush, §24.5). Azure TTS
       (HiuMaan, rate 0.9, StreamAdapter) only as the FallbackAdapter's second voice when AZURE_SPEECH_KEY is set;
       no Azure key is required any more.
  VAD  Silero; patient endpointing (0.8–3.0 s); interruption on/off from the console knob
  Transcription synced to audio (`TextOutputOptions(sync_transcription=True)`).
Tools are proxied to `POST /internal/tool/{name}` (they execute in `api`, the single writer); her final transcripts
go to `POST /internal/turn` first, and in `kernel_listen`/`paused` the worker never replies (StopResponse).
Kernel `agent.*` commands arrive by long-poll (`GET /internal/pull/{id}`): inject → generate_reply(instructions),
mode → tool list + interrupt, close → shutdown. Every reply passes the spoken filter (StreamFilter) in `llm_node`;
the length guard cuts the stream after two sentences. Stage 3 wake check (§20.4): when opened by the wake word,
`ahbo.wake.matcher.stage3_check` runs on `s2_text` (her first utterance; the first final transcript if the iPad sent
none, within `stage3_timeout_s`); a reject closes the session silently.

Not yet run as a whole session: the pure helpers are unit-tested in the api image, the module imports and builds its
components in `ahbo-agent`, and the ElevenLabs STT/TTS objects were exercised against the real API on 11 Nov 2026.
"""
from __future__ import annotations

import asyncio
import time
import logging
import re
from typing import Any

log = logging.getLogger("ahbo.agent.worker")

AGENT_NAME = "ahbo-agent"
SILENT_MODES = ("kernel_listen", "paused")
REASSURE_TOOLS = ("get_incident_status", "get_time")
LK_STATES = {"initializing": "idle", "idle": "idle", "listening": "listening", "thinking": "thinking",
             "speaking": "speaking"}
SPLIT_ZH = re.compile(r"(?<=[。！？；，!?;])")
MIN_CHUNK = 2


# ====================================================================== pure helpers (unit-tested in the api image)
def split_zh(text: str) -> list[str]:
    """TTS chunks on 。！？；， (§24.5). Very short pieces are merged into the next one."""
    out: list[str] = []
    carry = ""
    for p in SPLIT_ZH.split(text or ""):
        if not p:
            continue
        carry += p
        if len(carry.strip()) >= MIN_CHUNK:
            out.append(carry)
            carry = ""
    if carry.strip():
        if out:
            out[-1] += carry
        else:
            out.append(carry)
    return out


def split_zh_spans(text: str) -> list[tuple[str, int, int]]:
    """`TokenizeCallable` for BufferedSentenceStream: (token, start, end)."""
    spans, pos = [], 0
    for tok in split_zh(text):
        spans.append((tok, pos, pos + len(tok)))
        pos += len(tok)
    return spans


def tools_for_mode(schemas: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """CONTRACTS §6 mode filter, applied to the LLM's tool list (the api enforces it again per call)."""
    if mode in SILENT_MODES:
        return []
    if mode == "reassure":
        return [s for s in schemas if s["function"]["name"] in REASSURE_TOOLS]
    return schemas


def stage3_decision(reason: str | None, wake: dict[str, Any] | None, first_text: str | None
                    ) -> tuple[str, str | None]:
    """→ ("skip" | "accept" | "reject", remainder). Only sessions opened by the wake word are checked."""
    if reason not in ("wakeword", "wake") and not wake:
        return "skip", None
    text = (wake or {}).get("s2_text") or first_text
    if text is None:
        return "reject", None
    from ahbo.wake.matcher import stage3_check
    ok, rem = stage3_check(text)
    return ("accept", rem) if ok else ("reject", None)


def turn_handling(interruption_enabled: bool = True) -> dict[str, Any]:
    """§24.6: patient endpointing for elderly speech; barge-in on with a toggle; false-interrupt resume."""
    return {"endpointing": {"min_delay": 0.8, "max_delay": 3.0},
            "interruption": {"enabled": bool(interruption_enabled), "min_duration": 0.6,
                             "resume_false_interruption": True}}


def load_phrases() -> list[str]:
    try:
        from ahbo.core.config import load_config
        return [str(p) for p in load_config().phrases or []]
    except Exception:
        log.warning("phrases.yaml unreadable; STT runs without a phrase list")
        return []


class ReplyFilter:
    """Wraps the spoken StreamFilter for one reply: push text, get speakable sentences; `stopped` after 2."""

    def __init__(self) -> None:
        from .filters.spoken import StreamFilter
        self.f = StreamFilter()
        self.parts: list[str] = []

    def push(self, text: str) -> list[str]:
        out = self.f.push(text or "")
        self.parts += out
        return out

    def flush(self) -> str:
        s = self.f.flush()
        if s:
            self.parts.append(s)
        return s

    @property
    def stopped(self) -> bool:
        return self.f.stopped

    @property
    def text(self) -> str:
        return "".join(self.parts)

    @property
    def quality(self) -> list[str]:
        return self.f.quality


class InternalClient:
    """`/internal/*` over the Docker network (Bearer INTERNAL_TOKEN). Never logs the token."""

    def __init__(self, base_url: str, token: str, *, client: Any = None):
        import httpx
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0,
                                                headers={"Authorization": f"Bearer {token}"})

    async def post(self, path: str, body: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        r = await self.http.post(path, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    async def get(self, path: str, timeout: float = 10.0) -> dict[str, Any]:
        r = await self.http.get(path, timeout=timeout)
        r.raise_for_status()
        return r.json()

    async def claim(self, room: str) -> dict[str, Any]:
        return await self.post("/internal/session/claim", {"room": room})

    async def context(self, sid: str) -> dict[str, Any]:
        return await self.get(f"/internal/context/{sid}")

    async def turn(self, **body: Any) -> dict[str, Any]:
        return await self.post("/internal/turn", body)

    async def tool(self, name: str, sid: str, turn_id: str | None, call_id: str | None, args: dict[str, Any]
                   ) -> dict[str, Any]:
        return await self.post(f"/internal/tool/{name}", {"session_id": sid, "turn_id": turn_id, "call_id": call_id,
                                                         "args": args}, timeout=20.0)

    async def event(self, type_: str, payload: dict[str, Any]) -> None:
        try:
            await self.post("/internal/event", {"type": type_, "payload": payload})
        except Exception as e:
            log.warning("internal event %s failed (%s)", type_, type(e).__name__)

    async def ui_state(self, sid: str, state: str, user_speaking: bool = False) -> None:
        try:
            await self.post("/internal/ui", {"op": "agent_state", "session_id": sid, "state": state,
                                             "user_speaking": user_speaking})
        except Exception as e:
            log.warning("agent_state failed (%s)", type(e).__name__)

    async def pull(self, sid: str) -> list[dict[str, Any]]:
        return (await self.get(f"/internal/pull/{sid}", timeout=35.0)).get("items") or []


# ====================================================================== LiveKit parts (ahbo-agent image only)
def hk_event(ev: Any) -> Any:
    """Convert every alternative's text of an STT SpeechEvent to Traditional (s2hk) in place; strings too."""
    from ahbo.speech.transcript import to_hk

    if isinstance(ev, str):
        return to_hk(ev)
    for alt in getattr(ev, "alternatives", None) or []:
        if getattr(alt, "text", None):
            alt.text = to_hk(alt.text)
    return ev


def zh_sentence_tokenizer() -> Any:
    from livekit.agents import tokenize

    class ZhSentenceTokenizer(tokenize.SentenceTokenizer):
        def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
            return split_zh(text)

        def stream(self, *, language: str | None = None) -> Any:
            return tokenize.BufferedSentenceStream(tokenizer=split_zh_spans, min_token_len=MIN_CHUNK, min_ctx_len=4)

    return ZhSentenceTokenizer()


def build_stt(settings: Any, cfg: Any) -> Any:
    """`speech.stt` → the LiveKit STT. ElevenLabs needs ELEVENLABS_API_KEY only."""
    if cfg.provider == "azure":
        from livekit.plugins import azure
        return azure.STT(speech_key=settings.azure_speech_key, speech_region=settings.azure_speech_region,
                         language="zh-HK", phrase_list=load_phrases() or None)
    from ahbo.speech.elevenlabs_stt import live_stt
    return live_stt(cfg, settings.elevenlabs_api_key)


def _azure_tts(settings: Any) -> Any:
    from livekit.agents import tts
    from livekit.plugins import azure
    from livekit.plugins.azure.tts import ProsodyConfig

    a = azure.TTS(voice=settings.azure_tts_voice, language="zh-HK", prosody=ProsodyConfig(rate=0.9),
                  speech_key=settings.azure_speech_key, speech_region=settings.azure_speech_region)
    return tts.StreamAdapter(tts=a, sentence_tokenizer=zh_sentence_tokenizer())


def build_tts(settings: Any, cfg: Any) -> Any:
    """`speech.tts` → the LiveKit TTS: ElevenLabs streaming (CY voice, live model, yue), Azure only as fallback."""
    if cfg.provider == "azure":
        return _azure_tts(settings)
    from livekit.agents import tts
    from livekit.plugins import elevenlabs

    voice_id = cfg.voice_id(settings)
    if not settings.elevenlabs_api_key or not voice_id:
        raise RuntimeError("speech.tts.provider=elevenlabs needs ELEVENLABS_API_KEY and the voice id")
    kw: dict[str, Any] = {"voice_id": voice_id, "model": cfg.live_model, "api_key": settings.elevenlabs_api_key,
                          "word_tokenizer": zh_sentence_tokenizer(), "auto_mode": True,
                          "preferred_alignment": "original"}      # CJK: align captions on the original text
    if cfg.language:
        kw["language"] = cfg.language
    voice: Any = elevenlabs.TTS(**kw)
    if cfg.azure_fallback and settings.azure_speech_key:
        voice = tts.FallbackAdapter([voice, _azure_tts(settings)])
    return voice


def build_components(settings: Any, vad: Any) -> dict[str, Any]:
    from livekit.plugins import openai

    from ahbo.models.flash import thinking_fields
    from ahbo.models.runtime import model_cfg
    from ahbo.speech.config import speech_cfg

    sp = speech_cfg()
    cfg = model_cfg(None, "flash", "voice_turn")
    opper = cfg.get("provider") == "opper"
    from ahbo.models.opper import OPPER_URL
    import httpx
    from livekit.agents import llm
    # 12 Nov live test: the plugin's default read timeout (5 s) cut off Opper mid-turn → silence. Give each attempt
    # room, and let FallbackAdapter apply the PRD's rule instead: first token > 5 s → retry on Haiku (§20.1, §24.4).
    timeout = httpx.Timeout(connect=10.0, read=float(cfg.get("read_timeout_s", 20)), write=10.0, pool=10.0)
    flash = openai.LLM(model=cfg.get("model", "deepseek-flash"),
                       api_key=settings.opper_api_key if opper else settings.deepseek_api_key,
                       base_url=OPPER_URL if opper else settings.deepseek_base_url, timeout=timeout,
                       **({"extra_body": thinking_fields("off")["extra_body"]} if cfg.get("send_thinking", True) else {}))
    llms = [flash]
    fb = cfg.get("fallback") or {}
    if fb.get("provider") == "opper" and settings.opper_api_key:
        llms.append(openai.LLM(model=fb.get("model", "claude-haiku-5-5"), api_key=settings.opper_api_key,
                               base_url=OPPER_URL, timeout=timeout))
    brain: Any = flash if len(llms) == 1 else llm.FallbackAdapter(
        llms, attempt_timeout=float(cfg.get("first_token_timeout_s", 5)), max_retry_per_llm=1, retry_interval=0.2)
    return {"stt": build_stt(settings, sp.stt), "llm": brain, "tts": build_tts(settings, sp.tts), "vad": vad}


def make_agent_class():
    from livekit.agents import Agent, llm
    from livekit.agents.llm import StopResponse

    class AhBoAgent(Agent):
        def __init__(self, run: SessionRun, instructions: str, tools: list[Any]):
            super().__init__(instructions=instructions, tools=tools)
            self.run = run

        async def stt_node(self, audio: Any, model_settings: Any):
            """Her words in Traditional before anything sees them (captions, Kernel, Stage 3, LLM; INV-10)."""
            async for ev in Agent.default.stt_node(self, audio, model_settings):
                yield hk_event(ev)

        async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
            from ahbo.speech.transcript import to_hk

            raw = (new_message.text_content or "").strip()
            text = to_hk(raw)                             # idempotent; stt_node already converted it
            self.run.start_timing()
            r = self.run
            if not text:
                raise StopResponse()
            if text != raw:
                new_message.content = [text]
            if r.stage3_pending:
                verdict, rem = stage3_decision(r.reason, None, text)
                r.stage3_pending = False
                if verdict == "reject":
                    await r.reject_wake()
                    raise StopResponse()
                text = rem or text
                new_message.content = [text]
            r.turn_id = f"turn_{r.next_turn()}"
            res = await r.api.turn(op="user", session_id=r.sid, turn_id=r.turn_id, text=text,
                                   stt_confidence=stt_confidence_of(new_message), final=True)
            r.mode = res.get("mode", r.mode)
            if res.get("consumed") or r.mode in SILENT_MODES:
                raise StopResponse()                      # kernel_listen: transcripts go to the Kernel only
            await r.refresh_context(self)

        async def llm_node(self, chat_ctx: Any, tools: list[Any], model_settings: Any):
            r = self.run
            filt = ReplyFilter()
            started = False
            try:
                async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                    text = chunk if isinstance(chunk, str) else (getattr(getattr(chunk, "delta", None), "content", None)
                                                                or "")
                    if not isinstance(chunk, str) and getattr(getattr(chunk, "delta", None), "tool_calls", None):
                        yield llm.ChatChunk(id=chunk.id, delta=llm.ChoiceDelta(role="assistant",
                                                                               tool_calls=chunk.delta.tool_calls))
                    if not text:
                        continue
                    r.mark("llm_ttft")
                    for s in filt.push(text):
                        if not started:
                            started = True
                            await r.reply_start(s)
                        yield s
                    if filt.stopped:
                        break                              # §5.6 step 4: cut the generation after two sentences
            except Exception:                              # every model attempt failed (§10.4): never stay silent
                log.exception("LLM failed after fallbacks; speaking the generic fallback line G1")
                await r.api.event("quality.llm_failed", {"session_id": r.sid, "turn_id": r.turn_id})
                if not started:
                    started = True
                    g1 = g1_text()
                    await r.reply_start(g1)
                    yield g1
            rest = filt.flush()
            if rest:
                if not started:
                    started = True
                    await r.reply_start(rest)
                yield rest
            for q in filt.quality:
                await r.api.event(f"quality.{q}", {"session_id": r.sid, "turn_id": r.turn_id})
            if started:
                await r.api.turn(op="reply_end", session_id=r.sid, turn_id=r.turn_id)

    return AhBoAgent


def g1_text() -> str:
    """The generic fallback line (fixed_lines.yaml G1): 「阿寶而家諗唔到嘢，等陣再試吓好嗎？」"""
    try:
        from ahbo.core.config import load_config
        return load_config().fixed_lines["G1"]
    except Exception:
        return "阿寶而家諗唔到嘢，等陣再試吓好嗎？"


def stt_confidence_of(message: Any) -> float | None:
    """ElevenLabs Scribe gives no confidence; LiveKit then reports 0.0. Send None ("unknown") rather than a fake 0.0,
    so the sign-up check (action reducer, < 0.6 → ask again) only fires on a real low score (live test 12 Nov)."""
    c = getattr(message, "transcript_confidence", None)
    return float(c) if isinstance(c, (int, float)) and c > 0 else None


class SessionRun:
    """State of one LiveKit job (one Ah Bo session)."""

    def __init__(self, api: InternalClient, claim: dict[str, Any]):
        self.api = api
        self.sid: str = claim["session_id"]
        self.reason: str = claim.get("reason", "touch")
        self.mode: str = claim.get("mode", "companion")
        self.wake = claim.get("wake")
        self.stage3_pending = False
        self.turn_id: str | None = None
        self._n = 0
        self.session: Any = None
        self.agent: Any = None
        self.instructions = ""
        self.all_tools: list[Any] = []
        self.closed = asyncio.Event()
        self.timer: Any = None                           # per-turn latency (§15): eos → stt_final → llm_ttft → tts
        self.last_eou_delay = 0.0

    def start_timing(self) -> None:
        from ahbo.trace.spans import TurnTimer
        now = time.monotonic()
        self.timer = TurnTimer(self.turn_id or "turn", self.sid)
        self.timer.mark("eos", now - max(0.0, float(self.last_eou_delay or 0.0)))
        self.timer.mark("stt_final", now)

    def mark(self, stage: str) -> None:
        if self.timer is not None:
            self.timer.mark(stage)

    async def finish_timing(self) -> None:
        """First audio of the reply: send the turn's latency to the api (recorded as a `turn.latency` span)."""
        t, self.timer = self.timer, None
        if t is None or "llm_ttft" not in t.marks:
            return
        t.mark("tts_first_audio")
        await self.api.event("quality.turn_latency", {"session_id": self.sid, "turn_id": self.turn_id,
                                                      **t.latency()})

    def next_turn(self) -> int:
        self._n += 1
        return self._n

    async def reply_start(self, text: str) -> None:
        try:
            await self.api.turn(op="reply_start", session_id=self.sid, turn_id=self.turn_id or f"turn_{self._n}",
                                text=text)
        except Exception as e:
            log.warning("reply_start failed (%s)", type(e).__name__)

    async def refresh_context(self, agent: Any) -> None:
        """§5.2: rebuild the Kernel blocks each turn; update the instructions only when they changed."""
        try:
            ctx = await self.api.context(self.sid)
        except Exception:
            return
        instr = ctx.get("instructions") or ""
        if instr and instr != self.instructions:
            self.instructions = instr
            await agent.update_instructions(instr)

    async def apply_mode(self, mode: str) -> None:
        self.mode = mode
        if self.agent is not None:
            names = {s["function"]["name"] for s in tools_for_mode(_schemas(), mode)}
            await self.agent.update_tools([t for t in self.all_tools if _tool_name(t) in names])
        if mode in SILENT_MODES and self.session is not None:
            self.session.interrupt(force=True)

    async def reject_wake(self) -> None:
        log.info("wake.rejected_s3 session=%s", self.sid)       # the transcript is never logged (privacy)
        await self.api.event("session.closed", {"session_id": self.sid, "why": "wake_rejected"})
        self.closed.set()

    async def handle(self, item: dict[str, Any]) -> None:
        kind = item.get("kind")
        if kind == "inject" and self.session is not None and self.mode not in SILENT_MODES:
            self.turn_id = f"turn_{self.next_turn()}"
            self.session.generate_reply(instructions=item.get("instructions", ""))
        elif kind == "mode":
            await self.apply_mode(item.get("mode") or "companion")
        elif kind == "interrupt" and self.session is not None:
            self.session.interrupt(force=True)
        elif kind == "close":
            self.closed.set()

    async def pull_loop(self) -> None:
        while not self.closed.is_set():
            try:
                items = await self.api.pull(self.sid)
            except Exception as e:
                log.warning("pull failed (%s); retrying", type(e).__name__)
                await asyncio.sleep(1.0)
                continue
            for it in items:
                await self.handle(it)


def _schemas() -> list[dict[str, Any]]:
    from .tools import schemas
    return schemas("companion") + [s for s in schemas("reassure") if s["function"]["name"] == "get_incident_status"]


def _tool_name(t: Any) -> str:
    info = getattr(t, "info", None)
    return getattr(info, "name", "") or ""


def proxied_tools(run: SessionRun) -> list[Any]:
    """Every §6 tool as a LiveKit raw function tool that calls `POST /internal/tool/{name}`."""
    import json

    from livekit.agents import llm

    out = []
    for s in _schemas():
        fn = s["function"]

        def make(name: str):
            async def handler(raw_arguments: dict[str, object]) -> str:
                res = await run.api.tool(name, run.sid, run.turn_id, None, dict(raw_arguments or {}))
                return json.dumps(res, ensure_ascii=False)
            return handler

        out.append(llm.function_tool(make(fn["name"]), raw_schema={"name": fn["name"], "description": fn["description"],
                                                                   "parameters": fn["parameters"]}))
    return out


async def entrypoint(ctx: Any) -> None:
    from livekit.agents import AgentSession, room_io

    from ahbo.core.settings import get_settings
    settings = get_settings()
    await ctx.connect()
    api = InternalClient(settings.api_internal_url, settings.internal_token)
    claim = await api.claim(ctx.room.name)
    if not claim.get("ok"):
        log.warning("claim refused (%s); leaving", claim.get("reason"))
        ctx.shutdown(reason="claim_refused")
        return
    run = SessionRun(api, claim)
    ctx_body = await api.context(run.sid)
    run.instructions = ctx_body.get("instructions") or ""
    run.all_tools = proxied_tools(run)
    comps = build_components(settings, ctx.proc.userdata.get("vad"))
    session = AgentSession(stt=comps["stt"], llm=comps["llm"], tts=comps["tts"], vad=comps["vad"],
                           turn_handling=turn_handling(claim.get("interruption_enabled", True)))
    run.session = session
    names = {s["function"]["name"] for s in tools_for_mode(_schemas(), run.mode)}
    run.agent = make_agent_class()(run, run.instructions, [t for t in run.all_tools if _tool_name(t) in names])

    @session.on("agent_state_changed")
    def _on_agent_state(ev: Any) -> None:
        asyncio.ensure_future(api.ui_state(run.sid, LK_STATES.get(str(ev.new_state), "idle")))
        if str(ev.new_state) == "speaking":
            asyncio.ensure_future(run.finish_timing())

    @session.on("metrics_collected")
    def _on_metrics(ev: Any) -> None:
        m = getattr(ev, "metrics", None)
        if getattr(m, "type", "") == "eou_metrics":       # verified: EOUMetrics.end_of_utterance_delay (s), 1.8.11
            run.last_eou_delay = float(getattr(m, "end_of_utterance_delay", 0.0) or 0.0)

    @session.on("user_state_changed")
    def _on_user_state(ev: Any) -> None:
        if str(ev.new_state) == "speaking":
            asyncio.ensure_future(api.ui_state(run.sid, "listening", user_speaking=True))

    @session.on("close")
    def _on_close(_ev: Any) -> None:
        asyncio.ensure_future(api.event("session.closed", {"session_id": run.sid, "why": "agent"}))
        run.closed.set()

    await session.start(agent=run.agent, room=ctx.room, room_options=room_io.RoomOptions(
        text_output=room_io.TextOutputOptions(sync_transcription=True)))

    verdict, rem = ("skip", None)
    if run.wake or run.reason in ("wakeword", "wake"):
        if run.wake and run.wake.get("s2_text") is not None:
            verdict, rem = stage3_decision(run.reason, run.wake, None)
            if verdict == "reject":
                await run.reject_wake()
        else:
            run.stage3_pending = True
            timeout = float(_wake_cfg().get("stage3_timeout_s", 1.5))

            async def stage3_timer() -> None:
                await asyncio.sleep(timeout + 3.0)        # + endpointing: the first final transcript must arrive
                if run.stage3_pending:
                    run.stage3_pending = False
                    await run.reject_wake()
            asyncio.ensure_future(stage3_timer())
    if verdict == "accept" and rem:                       # s2_text after 「喂阿寶」 is her first utterance
        run.turn_id = f"turn_{run.next_turn()}"
        res = await api.turn(op="user", session_id=run.sid, turn_id=run.turn_id, text=rem, final=True)
        if not res.get("consumed"):
            session.generate_reply(user_input=rem)
    puller = asyncio.ensure_future(run.pull_loop())
    await run.closed.wait()
    puller.cancel()
    await session.aclose()
    ctx.shutdown(reason="session_closed")


def _wake_cfg() -> dict[str, Any]:
    try:
        from ahbo.core.config import load_config
        return dict(load_config().wake_match or {})
    except Exception:
        return {}


def prewarm(proc: Any) -> None:
    from livekit.plugins import silero
    proc.userdata["vad"] = silero.VAD.load()


def main() -> None:
    from livekit.agents import WorkerOptions, cli

    from ahbo.core.settings import get_settings
    s = get_settings()
    logging.basicConfig(level=logging.INFO)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm, agent_name=AGENT_NAME,
                              ws_url=s.livekit_url or None, api_key=s.livekit_api_key or None,
                              api_secret=s.livekit_api_secret or None))


if __name__ == "__main__":
    main()
