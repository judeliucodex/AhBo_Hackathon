"""Fake models (§26.3). Deterministic, realistic, and honouring the same contracts as the real adapters.

FakeFlash is a rule-based Cantonese agent: it reads the conversation like an LLM would (the last user line, the
tool results since then), calls the same tools through the same tool layer, and composes replies from
templates filled by tool results. Anything it does not understand gets 「阿寶聽唔太明，你可唔可以講多一次呀？」.

`LLMResponse.plan` is a fake-only hint: the tools it intends to call this turn, in order, so the face can show
the planned steps up front exactly like the kit's storyboards. Real models leave it None.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ahbo.core.adapters import LLMResponse, ToolCall
from ahbo.core.ids import new_id

DIDNT_GET = "阿寶聽唔太明，你可唔可以講多一次呀？"
SYS_RE = re.compile(r"^\[阿寶系統｜(?P<kind>[^｜]+)｜唔係王婆婆講嘅\]\s*(?P<body>.*)$", re.S)


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def join_names(names: list[str]) -> str:
    """血壓藥、糖尿藥、膽固醇藥，同埋薄血丸"""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return "、".join(names[:-1]) + "，同埋" + names[-1]


class FakeFlash:
    model = "fake-flash"

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   purpose: str = "voice_turn", thinking: str | None = None,
                   correlation_id: str | None = None) -> LLMResponse:
        if purpose != "voice_turn":
            return self._utility(messages, purpose)
        user_idx = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        if user_idx < 0:
            return LLMResponse(text=DIDNT_GET)
        text = str(messages[user_idx]["content"])
        results = self._results_since(messages, user_idx)
        history = messages[:user_idx]
        allowed = {t["function"]["name"] for t in tools or []}
        m = SYS_RE.match(text)
        if m:
            return self._system(m.group("kind"), m.group("body"), results)
        return self._turn(text, results, history, allowed)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _results_since(messages: list[dict[str, Any]], idx: int) -> dict[str, dict[str, Any]]:
        names: dict[str, str] = {}
        out: dict[str, dict[str, Any]] = {}
        for m in messages[idx + 1:]:
            if m["role"] == "assistant":
                for tc in m.get("tool_calls") or []:
                    names[tc["id"]] = tc["function"]["name"]
            elif m["role"] == "tool":
                name = names.get(m.get("tool_call_id", ""), "?")
                try:
                    out[name] = json.loads(m["content"])
                except (ValueError, TypeError):
                    out[name] = {"ok": False}
        return out

    @staticmethod
    def call(name: str, **args: Any) -> ToolCall:
        return ToolCall(id=new_id("call"), name=name, args=args)

    def _tools(self, *calls: ToolCall, plan: list[str] | None = None, text: str = "") -> LLMResponse:
        return LLMResponse(text=text, tool_calls=list(calls), model=self.model, plan=plan)

    def _say(self, text: str, *calls: ToolCall) -> LLMResponse:
        return LLMResponse(text=text, tool_calls=list(calls), model=self.model)

    @staticmethod
    def _last_action_question(history: list[dict[str, Any]]) -> str | None:
        """The action id of the most recent request_action in this session (for 「好呀」)."""
        for m in reversed(history):
            if m["role"] == "tool":
                try:
                    r = json.loads(m["content"])
                except (ValueError, TypeError):
                    continue
                if r.get("action_id") and r.get("question") is not None:
                    return r["action_id"]
        return None

    # ---------------------------------------------------------------- the turn rules
    def _turn(self, text: str, R: dict[str, dict[str, Any]], history: list[dict[str, Any]],
              allowed: set[str]) -> LLMResponse:
        t = text.replace(" ", "")

        # S1: did anyone look for me / any messages?
        if _has(t, "搵過我", "有冇人搵", "有冇訊息", "有冇錄音", "有冇留言"):
            if "list_voice_messages" not in R:
                return self._tools(self.call("list_voice_messages"), plan=["list_voice_messages"])
            r = R["list_voice_messages"]
            if "play_voice_message" in R:
                return self._say("")
            if r.get("ok") and r.get("has_new"):
                when = (r["messages"][0]["received_words"].split(" ")[0]) if r.get("messages") else "今日"
                first = next(x for x in r["messages"] if not x["played"])
                return self._say(f"有呀！{r['from_name']}{when}留咗段錄音俾你，我而家播俾你聽。",
                                 self.call("play_voice_message", id=first["id"]))
            return self._say("今日暫時冇人搵你呀，有事我即刻話你知。")

        # Dosing questions: never advise (INV-4)
        if _has(t, "食多粒", "食多一粒", "食多啲藥", "加藥", "食雙份", "減藥", "唔食得唔得", "可唔可以唔食"):
            return self._say("食幾多藥要聽醫生講先得呀，阿寶唔可以話你知。我幫你問吓阿珍好唔好？")

        # S2 and the double-dose guard: medicine facts only from get_med_status
        if _has(t, "藥"):
            asked_taken = _has(t, "食咗未", "食咗藥未", "有冇食", "食咗啦", "食咗喇", "食過")
            if "get_med_status" not in R:
                scope = "today" if asked_taken else "next"
                return self._tools(self.call("get_med_status", scope=scope), plan=["get_med_status"])
            r = R["get_med_status"]
            if not r.get("ok"):
                return self._say("阿寶而家睇唔到藥單，等陣再問我好嗎？")
            if asked_taken:
                return self._say(self._taken_answer(r))
            n = r.get("next")
            if not n:
                return self._say("今日啲藥都食晒喇，好叻呀！")
            names = join_names([x["yue"] for x in n["meds"]])
            if n.get("due_now"):
                return self._say(f"而家就係食藥時間喇，有{names}。")
            return self._say(f"{n['rel_words']}，{n['time_words']}。有{names}。到時我會提你㗎。")

        # S3 turn 1: today's schedule (+ the bus route for the appointment)
        if _has(t, "有咩安排", "今日做咩", "有咩約", "今日有咩做", "今日要做咩", "幾點出門"):
            if "list_events" not in R:
                return self._tools(self.call("list_events"), plan=["list_events", "get_route"])
            evs = R["list_events"].get("events", [])
            med = next((e for e in evs if e["kind"] == "medical"), None)
            if med and "get_route" not in R:
                return self._tools(self.call("get_route", destination=med["place"]))
            return self._say(self._schedule_line(evs, R.get("get_route")))

        # S3 turn 2: bored → a seeded local activity (quick tools, not deep_task)
        if _has(t, "好悶", "悶", "有咩好做", "有咩活動", "去邊度玩"):
            if "find_local_activities" not in R:
                when = "afternoon" if _has(t, "下晝") or True else "today"
                return self._tools(self.call("find_local_activities", when=when),
                                   plan=["find_local_activities", "check_activity_logistics"])
            f = R["find_local_activities"]
            acts = f.get("activities") or []
            if not acts:
                return self._say("附近暫時搵唔到啱你嘅活動，不如我哋傾吓偈？")
            top = acts[0]
            if "check_activity_logistics" not in R:
                return self._tools(self.call("check_activity_logistics", activity_id=top["id"]))
            if (top.get("signup") or {}).get("method") == "web_form" and "request_action" not in R \
                    and "request_action" in allowed:
                return self._tools(self.call("request_action", kind="signup", target=top["id"],
                                             details={"activity_id": top["id"], "title": top["title"]}))
            venue = top.get("venue_short") or top["venue"]
            friends = "可以識多啲朋友。" if "識朋友" in top.get("interests", []) else ""
            offer = "我幫你報名吖。" if "request_action" in R and R["request_action"].get("ok") else ""
            return self._say(f"附近{venue}{top['time_words']}有{top['title']}，{friends}想唔想去？{offer}")

        # S3 turn 3: she says yes to a proposed action
        if _has(t, "好呀", "好啊", "好吖", "想去", "幫我報", "報名", "得呀", "係呀", "好喎") and len(t) <= 12:
            action_id = self._last_action_question(history)
            if action_id and "confirm_action" in allowed:
                if "confirm_action" not in R:
                    return self._tools(self.call("confirm_action", action_id=action_id, utterance=text),
                                       plan=["confirm_action"])
                r = R["confirm_action"]
                if r.get("ok"):
                    return self._say("好呀，我而家幫你報名，報好咗再話你知。")
                return self._say("報唔到名，我叫阿珍幫你睇下。")
            return self._say("好呀！")

        if _has(t, "幾點", "咩時間", "而家幾"):
            if "get_time" not in R:
                return self._tools(self.call("get_time"))
            r = R["get_time"]
            return self._say(f"而家係{r['time_words']}，{r['weekday_words']}。")

        if _has(t, "我冇事", "冇事"):
            return self._say("你冇事就好，阿寶喺度陪住你。")

        if _has(t, "你好", "早晨", "午安", "晚安", "喂阿寶", "阿寶") and len(t) <= 6:
            return self._say("王婆婆你好呀！有咩可以幫你？")

        return self._say(DIDNT_GET)

    @staticmethod
    def _taken_answer(r: dict[str, Any]) -> str:
        from ahbo.core import numwords as nw
        now_slots = r.get("today") or []
        due = [s for s in now_slots if s["state"] != "scheduled"]
        if not due:
            n = r.get("next")
            return f"今日仲未到食藥時間，下一次係{n['time_words']}。" if n else "今日仲未有藥要食。"
        last = due[-1]
        h, m = (int(x) for x in last["time"].split(":"))
        tw = nw.time_words(h, m, period=True)
        if last["state"] == "taken_self_reported":
            return f"你{tw}嗰次已經食咗喇。"
        return f"{tw}嗰次你仲未話我知食咗喎。"

    @staticmethod
    def _schedule_line(evs: list[dict[str, Any]], route: dict[str, Any] | None) -> str:
        from ahbo.core import numwords as nw
        if not evs:
            return "今日冇乜特別安排，可以慢慢嚟。"
        parts = []
        plan = next((e for e in evs if e["kind"] == "plan"), None)
        for e in evs:
            if e["kind"] == "plan":
                continue
            h, m = (int(x) for x in e["time"].split(":"))
            tw = nw.time_words(h, m)
            if e["kind"] == "medical":
                s = f"{tw}你要去{e['place']}{e['title']}"
                if route and route.get("ok") and plan:
                    ph, pm = (int(x) for x in plan["time"].split(":"))
                    where = (plan.get("place") or "").removesuffix("巴士站") or "樓下"
                    s += f"，我已經查好路線，{nw.time_words(ph, pm)}喺{where}搭巴士就得"
                parts.append(s + "。")
            else:
                parts.append(f"{tw}{e['title']}。")
        return "".join(parts)

    def _system(self, kind: str, body: str, R: dict[str, dict[str, Any]]) -> LLMResponse:
        """Injected system lines (§5.3): relay the content kindly and briefly."""
        content = body.split("。")[0].strip()
        if kind == "家人":
            return self._say(f"{content}。")
        if kind in ("背景結果", "行動結果", "提醒", "問候", "錄音"):
            return self._say(content + ("。" if not content.endswith(("。", "！", "？")) else ""))
        return self._say(content)

    def _utility(self, messages: list[dict[str, Any]], purpose: str) -> LLMResponse:
        """Extractor / intent tagger / digest stand-ins: valid JSON, deterministic."""
        last = str(messages[-1]["content"]) if messages else ""
        if purpose == "intent_tag":
            intent = ("ask_med_time" if "藥" in last else "ask_today" if _has(last, "安排", "今日做咩") else
                      "ask_date" if _has(last, "幾號", "星期幾") else
                      "ask_family_visit" if _has(last, "阿珍", "幾時嚟") else "other")
            return LLMResponse(text=json.dumps({"intent": intent}), model=self.model)
        if purpose == "extractor":
            return LLMResponse(text=json.dumps({"summary": ["同阿寶傾咗一陣偈。"], "facts": []}, ensure_ascii=False),
                               model=self.model)
        if purpose == "digest":
            return LLMResponse(text="今日媽媽食藥正常，心情平穩。", model=self.model)
        return LLMResponse(text="{}", model=self.model)
