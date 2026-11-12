"""Bot replies and inline keyboards (Traditional Chinese, HK Cantonese, short).

A handler returns `Reply` objects; `ahbo.links.telegram` sends them through the channel abstraction
(`channel.reply(chat_id, text, buttons=rows)`): the fake channel records them, the real one calls sendMessage.

Callback data (≤ 64 bytes) of the bot always starts with `m:` (the alert buttons keep `b:<button>:<outbox id>`):
`m:menu` · `m:v:<view>` · `m:f:<form>[:<id>]` · `m:a:<op>[:<arg>]` · `m:i:<value>` (form input) ·
`m:ok:<nonce>` / `m:no:<nonce>` (confirm / cancel a pending change) · `m:x` (leave the form).
"""
from __future__ import annotations

from dataclasses import dataclass, field

Button = tuple[str, str]           # (label, callback_data)


@dataclass
class Reply:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)

    def markup(self) -> list[list[dict[str, str]]]:
        return [[{"text": t, "callback_data": d} for t, d in row] for row in self.buttons if row]


def cb(*parts: object) -> str:
    data = ":".join(["m", *(str(p) for p in parts)])
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback data too long: {data!r}")
    return data


def rows(buttons: list[Button], per_row: int = 2) -> list[list[Button]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


MENU_ITEMS: list[tuple[str, str]] = [
    ("今日狀況", "today"), ("藥物", "meds"), ("日程", "cal"), ("設定", "set"),
    ("聯絡人", "contacts"), ("示範控制", "demo"), ("系統健康", "health"),
]

BACK = ("⬅️ 主選單", cb("menu"))


def back_row(*extra: Button) -> list[Button]:
    return [*extra, BACK]


def main_menu(name: str | None = None) -> Reply:
    hello = f"你好，{name}！" if name else ""
    text = (f"{hello}阿寶主選單：揀一樣嘢睇或者改。\n"
            "亦可以打指令：/today /meds /cal /settings /contacts /demo /health /alerts /help")
    return Reply(text, rows([(t, cb("v", v)) for t, v in MENU_ITEMS], 2))


def confirm_buttons(nonce: str) -> list[list[Button]]:
    return [[("✅ 確認", cb("ok", nonce)), ("❌ 取消", cb("no", nonce))]]


HELP = """阿寶指令（全部都可以直接打）：
/menu 主選單
/today 今日狀況（藥、約會、事故、iPad）
/meds 藥物時間表同藥物資料
/cal 日程（今日同之後七日）
/settings 設定（靜音時段、問候時間）
/contacts 聯絡人同致電次序
/alerts 最近十個通知
/health 系統健康
/demo 示範控制
/fall 模擬跌倒 · /sos 模擬求助 · /panic 全部停止 · /reset 重設示範
/scenario S1|S2|S3|S4|S4ok · /clock 07:58|+30|now · /mute · /unmute
/dose [時段] · /checkin morning|afternoon|evening · /say 一句說話
/thinking flash|sonnet 程度
/fact 記低一樣嘢 · /story 講個故事 · /whoami · /cancel 取消
每樣改動都會問你「確認／取消」先至做。"""
