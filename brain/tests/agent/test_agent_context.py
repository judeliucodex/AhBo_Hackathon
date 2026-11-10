"""Context assembly (§5.2), injection line format (§5.3), intent tagging (§7.4)."""
from __future__ import annotations

from ahbo.agent import context, intent, prompts
from ahbo.agent.context import build_blocks, build_instructions, estimate_tokens


def test_persona_is_verbatim_and_first(harness):
    text = build_instructions(harness.kernel, None)
    assert text.startswith(prompts.persona())
    assert prompts.persona().startswith("你係「阿寶」，一隻陪住王婆婆嘅機械寵物。")
    assert "Lines starting with [阿寶系統｜…] were not said by her" in text
    assert estimate_tokens(text) <= context.TOTAL_BUDGET


def test_blocks_come_from_kernel_and_memory(harness):
    b = build_blocks(harness.kernel, None)
    assert "星期五" in b["now"] and "十點" in b["now"] and "天氣" in b["now"]
    assert "女兒叫阿珍" in b["profile"]
    assert "覆診" in b["today"] and "聯合醫院" in b["today"]
    assert "朝早八點（糖尿藥）：佢話食咗" in b["today"]          # med status from dose_events (INV-4)
    assert "中午十二點" in b["today"] and "未到" in b["today"]
    assert "琴日" in b["episodic"] and "膝頭" in b["episodic"]
    assert "未聽" in b["pending"]                                # the seeded voice note is unplayed
    for name, budget in context.BUDGETS.items():
        assert estimate_tokens(b[name]) <= budget, name


def test_repetition_hint_appears(harness):
    db, day = harness.kernel.db, harness.kernel.clock.today().isoformat()
    db.execute("INSERT INTO repetition(date, intent, count) VALUES (?,?,3)", (day, "ask_med_time"))
    db.kv_set("repetition_last", {"date": day, "intent": "ask_med_time", "count": 3, "ts": harness.kernel.clock.iso()})
    assert "第3次" in build_instructions(harness.kernel, None) or "第三次" in build_instructions(harness.kernel, None)


def test_budget_cap_trims_big_blocks():
    huge = {"now": "x", "profile": "\n".join(f"- 事實{i}" * 20 for i in range(400)), "episodic": "",
            "today": "", "pending": "", "repetition": ""}
    assert estimate_tokens(context.assemble(huge)) <= context.TOTAL_BUDGET + 200


def test_system_line_format():
    assert prompts.system_line("背景結果", "附近有粵曲班", "簡單講俾佢知") == \
        "[阿寶系統｜背景結果｜唔係王婆婆講嘅] 附近有粵曲班。簡單講俾佢知。"
    assert prompts.system_line("亂嚟", "x。").startswith("[阿寶系統｜提醒｜唔係王婆婆講嘅] x。")


async def test_intent_tag_closed_set():
    assert await intent.tag("我幾時食藥呀？") == "ask_med_time"
    assert await intent.tag("今日幾號呀？") == "ask_date"
    assert await intent.tag("我今日有咩安排呀？") == "ask_today"
    assert await intent.tag("阿珍幾時嚟探我？") == "ask_family_visit"
    assert await intent.tag("[阿寶系統｜提醒｜唔係王婆婆講嘅] 食藥") == "other"
    assert intent.parse('{"intent": "ask_today"}') == "ask_today"
    assert intent.parse('{"intent": "hack"}') is None


async def test_intent_tag_uses_flash_when_real_and_falls_back():
    from ahbo.core.adapters import LLMResponse

    class F:
        def __init__(self, text=None, boom=False):
            self.text, self.boom = text, boom

        async def chat(self, *a, **k):
            if self.boom:
                raise RuntimeError("down")
            assert k["purpose"] == "intent_tag"
            return LLMResponse(text=self.text)

    assert await intent.tag("隨便講吓", flash=F('{"intent":"ask_where_item"}')) == "ask_where_item"
    assert await intent.tag("我幾時食藥呀？", flash=F(boom=True)) == "ask_med_time"
    assert await intent.tag("我幾時食藥呀？", flash=F("nonsense")) == "ask_med_time"
