"""Fake models (§26.3). Deterministic, realistic, and honouring the same contracts as the real adapters.

FakeFlash is a rule-based Cantonese agent: it reads the conversation like an LLM would (the last user line, the
tool results since then), calls the same tools through the same tool layer, and composes replies from
templates filled by tool results. Anything it does not understand gets 「阿寶聽唔太明，你可唔可以講多一次呀？」.
It follows the persona's safety rules (§5.4 + rules.md): medicine facts only from get_med_status, never dosing
advice, confirm_taken only on her statement and only "recorded" when the tool says ok, symptoms → raise_concern +
empathy, scams → warn + ask 阿珍, planning → deep_task with the holding line, injected system lines relayed briefly,
text inside them treated as data.

`LLMResponse.plan` is a fake-only hint: the tools it intends to call this turn, in order, so the face can show
the planned steps up front exactly like the kit's storyboards. Real models leave it None.

FakeDeep returns `seed/demo.yaml → fake_deep_results` after 2.5 s (schema-validated); FakeVision returns
「可能有人跌咗喺地下」 after 1.5 s.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ahbo.core.adapters import LLMResponse, ToolCall
from ahbo.core.ids import new_id

DIDNT_GET = "阿寶聽唔太明，你可唔可以講多一次呀？"
HOLDING_LINE = "好呀，等阿寶幫你諗下，一陣話你知。"     # §24.8.2 (eval defect e)
SYS_RE = re.compile(r"^\[阿寶系統｜(?P<kind>[^｜]+)｜唔係王婆婆講嘅\]\s*(?P<body>.*)$", re.S)
TAKEN = "taken_self_reported"

# words that make a quoted family/web text unsafe to read out verbatim (INV-7: data, never instructions)
UNSAFE_QUOTE = re.compile(r"(?i)\d{3,}|九九九|指示|ignore|instruction|send|message|過數|轉賬|密碼|驗證碼|"
                          r"所有人|所有聯絡人|唔好話俾|https?://")

# Mandarin / written forms normalised before matching (the reply is always Cantonese)
MANDARIN = (("今天", "今日"), ("明天", "聽日"), ("昨天", "琴日"), ("我們", "我哋"), ("哪裡", "邊度"), ("哪里", "邊度"),
            ("怎麼樣", "點"), ("怎么样", "點"), ("什麼", "咩"), ("什么", "咩"), ("吃", "食"), ("天气", "天氣"),
            ("沒有", "冇"), ("的", "嘅"))
ENGLISH = ((r"(?i)ambulance|emergency|help me", "幫我打九九九"), (r"(?i)medicine|pills?|tablets?", "我今日要食咩藥"),
           (r"(?i)weather", "今日天氣點"), (r"(?i)\btime\b", "而家幾點"), (r"(?i)hello|\bhi\b|how are you", "你好"))

EMERGENCY_WORDS = ("胸口痛", "心口痛", "唞唔到氣", "抖唔到氣", "透唔到氣", "救命", "跌親", "跌低", "暈低", "中風")
URGENT_WORDS = ("頭暈", "頭好暈", "好暈", "嘔", "發燒", "好痛", "痛到")
MOOD_WORDS = ("唔想生存", "想死", "唔想活", "死咗好過", "冇人理我")
INFO_WORDS = ("有啲痛", "痛", "唔舒服", "唔妥")
SCAM_WORDS = ("過數", "驗證碼", "密碼", "提錢", "保釋", "被捕", "銀行戶口", "轉賬", "匯款", "入錢", "中獎", "俾錢")
DOSING_WORDS = ("食多粒", "食多一粒", "食多啲藥", "加藥", "食雙份", "減藥", "唔食得唔得", "可唔可以唔食", "唔食藥得唔得",
                "停藥", "食少粒", "食半粒")
TOOK_QUESTION = ("食咗未", "食咗藥未", "有冇食", "食咗藥未呀", "食過未", "食咗未呀")
TOOK_STATEMENT = ("已經食咗", "食咗藥喇", "食咗藥啦", "食咗喇", "食咗啦", "食過喇", "食完喇", "食完藥")
SNOOZE_WORDS = ("等陣先食", "遲啲先食", "遲啲再提", "等陣再提", "一陣先食", "等陣食", "陣間先食", "等一陣先食")
DEEP_WORDS = ("計劃", "推介", "好去處", "比較", "幫我搵吓", "幫我搵下", "幫我諗吓", "幫我諗下", "一日遊", "點樣申請",
              "點樣去", "去邊度玩好", "去邊度好", "安排吓", "有咩好玩", "邊度好玩")
NO_WORDS = ("唔好喇", "唔想去", "唔使喇", "唔要喇", "唔好啦", "唔去喇", "算喇", "唔使啦", "唔好住")
YES_WORDS = ("好呀", "好啊", "好吖", "想去", "幫我報", "報名", "得呀", "係呀", "好喎", "可以呀")
WEATHER_WORDS = ("天氣", "落雨", "帶遮", "好凍", "好熱", "着多件衫", "落緊雨", "出面凍唔凍", "會唔會落雨")
TIME_WORDS = ("幾點", "咩時間", "而家幾")
DATE_WORDS = ("星期幾", "禮拜幾", "幾號", "咩日子", "幾月")
RECALL_WORDS = ("叫咩名", "幾年級", "做咩工作", "以前係做咩", "以前做咩", "最鍾意", "鍾意食咩", "鍾意聽咩", "鍾意飲咩",
                "幾時生日", "記唔記得", "我講過", "幾時嚟", "嚟探我", "住喺邊")
EPISODE_DAY = ("琴日", "前日", "上次", "尋日")
STORY_WORDS = ("講個故", "講故", "以前嘅故", "講吓以前", "講下以前")


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def join_names(names: list[str]) -> str:
    """血壓藥、糖尿藥、膽固醇藥，同埋薄血丸"""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return "、".join(names[:-1]) + "，同埋" + names[-1]


def normalise(text: str) -> str:
    t = text.replace(" ", "").replace("　", "")
    for a, b in MANDARIN:
        t = t.replace(a, b)
    if re.search(r"[A-Za-z]{3,}", t):
        for pat, rep in ENGLISH:
            if re.search(pat, text):
                return rep
    return t


def second_person(text: str) -> str:
    """Seed/memory text is about her in the third person: 佢/王婆婆 → 你."""
    return text.replace("王婆婆", "你").replace("婆婆", "你").replace("佢", "你")


def first_sentences(text: str, n: int = 1) -> str:
    parts = [p for p in re.split(r"(?<=[。！？])", text or "") if p.strip()]
    out = "".join(parts[:n]).strip()
    if out and out[-1] not in "。！？":
        out += "。"
    return out


class FakeFlash:
    model = "fake-flash"

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   purpose: str = "voice_turn", thinking: str | None = None,
                   correlation_id: str | None = None) -> LLMResponse:
        if purpose != "voice_turn":
            return self._utility(messages, purpose)
        user_idx = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        if user_idx < 0:
            return LLMResponse(text=DIDNT_GET, model=self.model)
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
        t = normalise(text)
        for rule in (self._scam, self._symptom, self._help_call, self._voice_notes, self._dosing, self._took_it,
                     self._snooze, self._meds, self._remember, self._deep, self._schedule, self._no, self._bored,
                     self._yes, self._weather, self._time, self._story, self._recall, self._small_talk):
            r = rule(t, text, R, history, allowed)
            if r is not None:
                return r
        return self._say(DIDNT_GET)

    # -- safety first
    def _scam(self, t, text, R, history, allowed):
        if not _has(t, *SCAM_WORDS) or "記住" in t:      # 「記住我密碼…」 is a (refused) memory request
            return None
        if "raise_concern" not in R and "raise_concern" in allowed:
            return self._tools(self.call("raise_concern", level="urgent", category="scam", reason=text[:200]))
        return self._say("王婆婆，呢啲好似係騙案呀，千祈唔好俾錢或者講密碼俾人知。我會話俾阿珍知，你先問吓阿珍好唔好？")

    def _help_call(self, t, text, R, history, allowed):
        """「打九九九」/ ambulance: Ah Bo cannot phone anyone itself (INV-9); ask what is wrong (symptoms escalate)."""
        if not (_has(t, "九九九", "999", "白車", "救護車", "叫醫生") or re.search(r"打\s*9\s*9\s*9", text)):
            return None
        return self._say("王婆婆，你係咪唔舒服呀？話俾阿寶知邊度唔妥，我即刻通知阿珍。")

    def _symptom(self, t, text, R, history, allowed):
        level = ("emergency" if _has(t, *EMERGENCY_WORDS) else "urgent" if _has(t, *MOOD_WORDS, *URGENT_WORDS)
                 else "info" if _has(t, *INFO_WORDS) else None)
        if level is None or _has(t, "唔痛", "冇痛"):
            return None
        category = "mood" if _has(t, *MOOD_WORDS) else "fall" if _has(t, "跌親", "跌低") else "symptom"
        if "raise_concern" not in R and "raise_concern" in allowed:
            return self._tools(self.call("raise_concern", level=level, category=category, reason=text[:200]))
        if category == "mood":
            return self._say("王婆婆，聽到你咁講阿寶好心痛。我會話俾阿珍知，佢好緊張你㗎。")
        if level == "emergency":
            return self._say("王婆婆，阿寶好擔心你，我而家即刻話俾人知。你坐定定，阿寶喺度陪住你。")
        return self._say("聽到你唔舒服，阿寶好擔心你。我會話俾阿珍知，你慢慢唞吓先好嗎？")

    # -- S1: did anyone look for me / any messages?
    def _voice_notes(self, t, text, R, history, allowed):
        if not _has(t, "搵過我", "有冇人搵", "有冇訊息", "有冇錄音", "有冇留言"):
            return None
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

    # -- medicine (INV-4)
    def _dosing(self, t, text, R, history, allowed):
        if not _has(t, *DOSING_WORDS):
            return None
        return self._say("食幾多藥要聽醫生講先得呀，阿寶唔可以話你知。我幫你問吓阿珍好唔好？")

    def _took_it(self, t, text, R, history, allowed):
        """Her statement 「我食咗藥喇」 → confirm_taken; only 'recorded' when the Kernel says ok."""
        if not _has(t, *TOOK_STATEMENT) or _has(t, *TOOK_QUESTION) or "？" in text or "?" in text:
            return None
        if "confirm_taken" not in allowed:
            return None
        if "confirm_taken" not in R:
            return self._tools(self.call("confirm_taken", utterance=text[:200]))
        r = R["confirm_taken"]
        if r.get("ok"):
            return self._say("好叻呀！阿寶記低咗喇。")
        if r.get("reason") == "no_dose_pending":
            return self._say("而家冇藥要食住呀，到時間阿寶會提你㗎。")
        return self._say("阿寶而家記唔到，等陣再話我知好嗎？")

    def _snooze(self, t, text, R, history, allowed):
        if not _has(t, *SNOOZE_WORDS) or "snooze_dose" not in allowed:
            return None
        if "snooze_dose" not in R:
            return self._tools(self.call("snooze_dose", minutes=10))
        r = R["snooze_dose"]
        if r.get("ok"):
            return self._say("好呀，阿寶十分鐘之後再提你食藥。")
        if r.get("reason") == "max_snoozes":
            return self._say("已經延遲咗兩次喇，不如而家食咗佢好嗎？")
        if r.get("reason") == "no_dose_pending":
            return self._say("而家冇藥要食住呀，到時間阿寶會提你㗎。")
        return self._say("阿寶而家改唔到提醒，不過到時間會再提你。")

    def _meds(self, t, text, R, history, allowed):
        if "藥" not in t:
            return None
        asked_taken = _has(t, *TOOK_QUESTION, "食咗", "食過")
        if "get_med_status" not in R:
            scope = "today" if asked_taken else "next"
            return self._tools(self.call("get_med_status", scope=scope), plan=["get_med_status"])
        r = R["get_med_status"]
        if not r.get("ok"):
            return self._say("阿寶而家睇唔到藥單，等陣再問我好嗎？")
        if asked_taken:
            ans = self._taken_answer(r)
            if "再食" in t:
                ans += "食藥嘅份量要聽醫生講呀。"
            return self._say(ans)
        n = r.get("next")
        if not n:
            return self._say("今日啲藥都食晒喇，好叻呀！")
        names = join_names([x["yue"] for x in n["meds"]])
        if n.get("due_now"):
            return self._say(f"而家就係食藥時間喇，有{names}。")
        return self._say(f"{n['rel_words']}，{n['time_words']}。有{names}。到時我會提你㗎。")

    # -- memory
    def _remember(self, t, text, R, history, allowed):
        if "記住" not in t or "記唔記得" in t:
            return None
        if "remember" not in R:
            fact = re.sub(r"^.*?記住(我)?", "", t).strip("，。！？ ")
            fact = fact.removeprefix("係").strip() or t
            cat = ("preference" if _has(fact, "鍾意", "最愛", "唔鍾意") else
                   "health" if _has(fact, "病", "藥", "痛", "敏感") else
                   "family" if _has(fact, "阿珍", "阿明", "個女", "個仔", "孫") else "other")
            return self._tools(self.call("remember", fact=fact[:200], category=cat))
        r = R["remember"]
        if r.get("ok"):
            if r.get("status") == "pending_review":
                return self._say("好呀，阿寶記低咗，會俾阿珍確認吓。")
            return self._say("好呀，阿寶記住咗喇！")
        if r.get("reason") == "pii":
            return self._say("呢啲私人資料阿寶唔會記㗎，你自己收好佢呀。")
        return self._say("阿寶今次記唔到，你遲啲再同我講一次好嗎？")

    def _story(self, t, text, R, history, allowed):
        if not _has(t, *STORY_WORDS):
            return None
        if "recall_life_story" not in R:
            return self._tools(self.call("recall_life_story"))
        s = (R["recall_life_story"] or {}).get("story")
        if not s:
            return self._say("阿寶想聽你講以前嘅事呀，你講俾我聽好唔好？")
        return self._say(f"我記得呀，{first_sentences(second_person(s['text']))}你仲記唔記得呀？")

    def _recall(self, t, text, R, history, allowed):
        if not (_has(t, *RECALL_WORDS) or (_has(t, *EPISODE_DAY) and _has(t, "講", "傾", "做咩", "咩"))):
            return None
        if "recall" not in allowed:
            return None
        if "recall" not in R:
            return self._tools(self.call("recall", query=text[:200]))
        r = R["recall"]
        facts, eps = r.get("facts") or [], r.get("episodes") or []
        if _has(t, *EPISODE_DAY) and eps:
            when = "琴日" if "琴日" in t or "尋日" in t else "前日" if "前日" in t else "上次"
            return self._say(f"你{when}同我講，{first_sentences(second_person(eps[0]['summary']))}")
        best = self._best_line(t, [f["fact"] for f in facts] + [s for e in eps for s in _split(e.get("summary"))])
        if best:
            return self._say(f"我記得呀，{second_person(best).rstrip('。')}。")
        return self._say("阿寶唔記得喇，你講多次俾我聽好唔好？")

    @staticmethod
    def _best_line(query: str, lines: list[str]) -> str | None:
        """The line sharing the most two-character pieces with the question (fake ranking, deterministic)."""
        grams = {query[i:i + 2] for i in range(len(query) - 1)} - {"我個", "咩名", "呀？", "幾時", "鍾意"}
        best, score = None, 0
        for ln in lines:
            s = sum(1 for g in grams if g in ln)
            if s > score:
                best, score = ln, s
        return best if best else (lines[0] if lines else None)

    # -- planning → deep_task (§24.8.2)
    def _deep(self, t, text, R, history, allowed):
        if not _has(t, *DEEP_WORDS) or "deep_task" not in allowed:
            return None
        if "deep_task" not in R:
            return self._tools(self.call("deep_task", goal=text[:200], constraints="行得慢，唔好太遠，喺觀塘附近"),
                               plan=["deep_task"])
        r = R["deep_task"]
        if r.get("ok"):
            return self._say(HOLDING_LINE)
        if r.get("status") == "busy":
            return self._say("阿寶仲諗緊上一樣嘢，諗好咗即刻話你知。")
        return self._say("阿寶而家諗唔到，遲啲再試下好嗎？")

    # -- S3 turn 1: today's schedule (+ the bus route for the appointment)
    def _schedule(self, t, text, R, history, allowed):
        if not _has(t, "有咩安排", "今日做咩", "有咩約", "今日有咩做", "今日要做咩", "幾點出門"):
            return None
        if "list_events" not in R:
            return self._tools(self.call("list_events"), plan=["list_events", "get_route"])
        evs = R["list_events"].get("events", [])
        med = next((e for e in evs if e["kind"] == "medical"), None)
        if med and "get_route" not in R:
            return self._tools(self.call("get_route", destination=med["place"]))
        return self._say(self._schedule_line(evs, R.get("get_route")))

    def _no(self, t, text, R, history, allowed):
        if not _has(t, *NO_WORDS):
            return None
        return self._say("好呀，唔緊要，有咩想做再話阿寶知。")

    # -- S3 turn 2: bored → a seeded local activity (quick tools, not deep_task)
    def _bored(self, t, text, R, history, allowed):
        if not _has(t, "好悶", "悶", "有咩好做", "有咩活動", "去邊度玩", "好無聊", "冇嘢做"):
            return None
        if "find_local_activities" not in R:
            return self._tools(self.call("find_local_activities", when="afternoon"),
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

    # -- S3 turn 3: she says yes to a proposed action
    def _yes(self, t, text, R, history, allowed):
        if not (_has(t, *YES_WORDS) and len(t) <= 12):
            return None
        action_id = self._last_action_question(history)
        if not action_id and self._offered_voice_note(history):
            return self._voice_notes("有冇錄音", text, R, history, allowed)
        if action_id and "confirm_action" in allowed:
            if "confirm_action" not in R:
                return self._tools(self.call("confirm_action", action_id=action_id, utterance=text),
                                   plan=["confirm_action"])
            r = R["confirm_action"]
            if r.get("ok"):
                return self._say("好呀，我而家幫你報名，報好咗再話你知。")
            if r.get("reason") == "low_confidence":
                return self._say("阿寶聽得唔係好清楚，你係咪想我幫你報名呀？")
            return self._say("報唔到名，我叫阿珍幫你睇下。")
        return self._say("好呀！")

    @staticmethod
    def _offered_voice_note(history: list[dict[str, Any]]) -> bool:
        """The last thing Ah Bo said announced a voice note (錄音 injection) that has not been played yet."""
        for m in reversed(history):
            if m["role"] == "assistant" and m.get("content"):
                return "錄音" in str(m["content"]) and "播" not in str(m["content"])
            if m["role"] == "tool":
                return False
        return False

    # -- quick facts
    def _weather(self, t, text, R, history, allowed):
        if not _has(t, *WEATHER_WORDS):
            return None
        if "get_weather" not in R:
            return self._tools(self.call("get_weather"), plan=["get_weather"])
        w = R["get_weather"]
        if not w.get("ok"):
            return self._say("阿寶而家睇唔到天氣，你出門前望吓窗外好嗎？")
        tip = "出門記得帶遮呀。" if w.get("rain_likely") else "今日唔使帶遮。"
        if _has(t, "好凍", "着多件衫", "凍唔凍") and (w.get("temp_c") or 99) <= 20:
            tip = "有啲涼，着多件衫呀。"
        return self._say(f"{w.get('now_words') or '而家天氣幾好'}。{tip}")

    def _time(self, t, text, R, history, allowed):
        if not _has(t, *TIME_WORDS, *DATE_WORDS):
            return None
        if "get_time" not in R:
            return self._tools(self.call("get_time"))
        r = R["get_time"]
        if not r.get("ok"):
            return self._say("阿寶而家睇唔到鐘，等陣再問我好嗎？")
        if _has(t, *DATE_WORDS):
            from ahbo.core import numwords as nw
            y, mo, d = (int(x) for x in r["date"].split("-"))
            return self._say(f"今日係{nw.int_words(mo)}月{nw.int_words(d)}號，{r['weekday_words']}。")
        return self._say(f"而家係{r['time_words']}，{r['weekday_words']}。")

    def _small_talk(self, t, text, R, history, allowed):
        rules = (
            (("我冇事", "冇事"), "你冇事就好，阿寶喺度陪住你。"),
            (("晚安", "瞓喇", "去瞓"), "晚安呀王婆婆，好好休息，聽朝見！"),
            (("拜拜", "再見", "傾住"), "好呀，拜拜王婆婆，有咩事隨時叫我呀！"),
            (("多謝", "唔該"), "唔使客氣呀王婆婆，阿寶好鍾意幫你㗎！"),
            (("你係邊個", "你叫咩名", "你係咩", "你係咪小狗"), "我係阿寶呀，陪住你嘅機械寵物！"),
            (("好唔好呀", "你好嗎", "你今日點"), "阿寶今日好好呀，見到你就開心！你呢？"),
            (("早晨",), "早晨呀王婆婆！今日精神嗎？"),
            (("好攰", "攰"), "辛苦你喇，坐低唞吓先，阿寶陪住你。"),
            (("驚",), "唔使驚呀，阿寶喺度陪住你。"),
            (("瞓得唔好", "瞓唔着", "失眠"), "瞓得唔好好辛苦㗎，今晚早啲休息，阿寶陪你傾吓偈。"),
            (("開心",), "聽到你咁開心，阿寶都好開心呀！"),
            (("肚餓",), "肚餓就食啲嘢先啦，阿寶陪你傾吓偈。"),
            (("唱歌", "唱首歌"), "阿寶唱得唔好聽㗎，不如你唱俾我聽吖？"),
            (("笑話",), "有隻豬行過，佢話自己好肥，阿寶話佢好可愛！"),
            (("飲茶",), "阿寶好鍾意陪你飲茶呀，你最鍾意食蝦餃㗎嘛！"),
            (("食唔食飯", "食飯"), "阿寶食電㗎，你記得食飽飯呀！"),
            (("數數字", "識唔識數"), "識呀，一、二、三、四、五，阿寶好叻㗎！"),
            (("靚唔靚", "好唔好睇"), "好靚呀，王婆婆着乜都咁好睇！"),
            (("電視",), "不如開電視睇吓粵曲節目，你鍾意㗎！"),
            (("落街", "行吓", "散步"), "好主意呀，出門慢慢行，小心啲呀。"),
            (("教書", "以前"), "王婆婆以前教書好叻㗎，講多啲俾我聽吖！"),
            (("帝女花", "任白", "粵曲"), "帝女花好好聽呀，阿寶都鍾意聽！"),
            (("生日", "送咩"), "生日禮物最緊要係心意，你想送咩俾佢呀？"),
            (("鎖匙", "眼鏡", "唔見咗", "擺咗喺邊", "放咗喺邊"), "唔使急，我哋慢慢諗，你最後喺邊度用過佢呀？"),
            (("你好", "喂阿寶", "阿寶", "午安"), "王婆婆你好呀！有咩可以幫你？"),
        )
        for words, reply in rules:
            if _has(t, *words) and (words[0] not in ("你好",) or len(t) <= 8):
                return self._say(reply)
        return None

    # ---------------------------------------------------------------- answers
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
        if last["state"] == TAKEN:
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
        """Injected system lines (§5.3): relay the content kindly and briefly. Quoted text is data (INV-7)."""
        sentences = [s for s in re.split(r"(?<=[。！？])", body) if s.strip()]
        instr = re.compile(r"(講俾佢知|話俾佢知|講俾佢聽|問佢|提佢|簡單|有需要|唔好|relay|instruction|needs_confirmation)")
        content = [s for s in sentences if not instr.search(s)] or sentences[:1]
        if kind == "提醒":
            meds = re.search(r"提佢食(?P<m>[^，。]+)", body)
            when = re.search(r"而家係(?P<w>[^，。]+)", body)
            if meds:
                head = f"王婆婆，{when.group('w')}喇，" if when else "王婆婆，夠鐘喇，"
                return self._say(f"{head}記得食{meds.group('m')}呀。食咗未呀？")
            return self._say(first_sentences("".join(content)))
        if kind == "家人":
            quoted = re.search(r"「(?P<q>[^」]*)」", body)
            if quoted:                                   # a family text: her family's words, quoted data (INV-7)
                who = re.match(r"^(?P<who>[^\s「：:，。]{1,6}?)(傳咗|話|留咗|send)", body)
                name = who.group("who") if who else "阿珍"
                if not UNSAFE_QUOTE.search(quoted.group("q")):
                    return self._say(f"{name}傳咗個訊息俾你，佢話：「{quoted.group('q')}」")
                return self._say(f"{name}傳咗個訊息俾你，等{name}得閒再同你詳細講啦。")
            line = first_sentences("".join(content), 2)     # the Kernel's own words (e.g. 「我已經話咗俾阿珍知」)
            if UNSAFE_QUOTE.search(line):
                return self._say("阿珍有嘢想同你講，等佢得閒再同你詳細講啦。")
            if "使唔使叫阿珍" in body:
                line = first_sentences(line) + "使唔使叫阿珍即刻打俾你呀？"
            return self._say(line)
        if kind == "背景結果":
            line = first_sentences("".join(content), 2)
            if "唔好意思" in line or not line:
                return self._say("唔好意思，我搵唔到，遲啲再試下好嗎？")
            return self._say(line)
        return self._say(first_sentences("".join(content)))

    def _utility(self, messages: list[dict[str, Any]], purpose: str) -> LLMResponse:
        """Extractor / intent tagger / digest stand-ins: valid JSON, deterministic."""
        last = str(messages[-1]["content"]) if messages else ""
        if purpose == "intent_tag":
            from ahbo.memory.repetition import keyword_intent
            return LLMResponse(text=json.dumps({"intent": keyword_intent(last)}), model=self.model)
        if purpose == "extractor":
            return LLMResponse(text=json.dumps({"summary": ["同阿寶傾咗一陣偈。"], "facts": []}, ensure_ascii=False),
                               model=self.model)
        if purpose == "digest":
            return LLMResponse(text="今日媽媽食藥正常，心情平穩。", model=self.model)
        return LLMResponse(text="{}", model=self.model)


def _split(summary: Any) -> list[str]:
    return [s for s in re.split(r"(?<=[。！？])", str(summary or "")) if s.strip()]


# ====================================================================== deep + vision fakes
GENERIC_DEEP = {
    "status": "done",
    "spoken_summary": "阿寶幫你諗好咗，張卡度有幾個建議。你睇吓啱唔啱？",
    "card": {"title": "阿寶嘅建議", "items": [
        {"time": "10:00", "activity": "去公園散步", "place": "觀塘海濱公園（示範）", "why": "行路唔遠，空氣好"},
        {"time": "15:00", "activity": "粵曲班", "place": "秀茂坪長者中心（示範）", "why": "你鍾意粵曲"}]},
    "sources": [], "signup_methods": [], "needs_confirmation": [],
}
ACTIVITY_WORDS = ("活動", "悶", "下晝", "粵曲", "玩", "去邊", "好去處", "報名", "興趣")


class FakeDeep:
    """§26.3: `fake_deep_results` from the seed after 2.5 s (scaled by `delay_s`), validated against §7."""
    model = "fake-deep"

    def __init__(self, delay_s: float = 2.5, seed: dict[str, Any] | None = None):
        self.delay_s, self._seed = delay_s, seed

    def _results(self) -> dict[str, Any]:
        if self._seed is None:
            from ahbo.core.config import load_seed
            self._seed = load_seed("demo.yaml") or {}
        return self._seed.get("fake_deep_results") or {}

    def pick(self, goal: str) -> dict[str, Any]:
        res = self._results()
        if res.get("afternoon_activity") and _has(goal or "", *ACTIVITY_WORDS):
            return json.loads(json.dumps(res["afternoon_activity"]))
        for key, val in res.items():
            if key in (goal or ""):
                return json.loads(json.dumps(val))
        return json.loads(json.dumps(GENERIC_DEEP))

    async def run(self, task_id: str, goal: str, constraints: str, context: dict[str, Any], *,
                  purpose: str = "deep_task") -> dict[str, Any]:
        from ahbo.deep.schema import finalize
        await asyncio.sleep(self.delay_s)
        return finalize({**self.pick(goal), "task_id": task_id}, task_id)


class FakeVision:
    """§26.3: always the same advisory note after 1.5 s (scaled by `delay_s`). Advisory only (INV-2)."""
    model = "fake-vision"
    NOTE = "可能有人跌咗喺地下"

    def __init__(self, delay_s: float = 1.5):
        self.delay_s = delay_s

    async def describe(self, photo_path: Path, *, correlation_id: str | None = None) -> str | None:
        await asyncio.sleep(self.delay_s)
        return self.NOTE
