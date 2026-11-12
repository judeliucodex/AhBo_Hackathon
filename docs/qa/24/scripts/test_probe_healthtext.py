"""QA24 probe 4b: full Telegram /health reply text (in-process, same as the live bot path) — secrets scan."""
import json, sys

sys.path.insert(0, "/repo/brain/tests/links")
sys.path.insert(0, "/repo/brain/tests")

from fastapi.testclient import TestClient          # noqa: E402
from ahbo.api.app import create_app                # noqa: E402
from ahbo.core.clock import FakeClock              # noqa: E402
from link_helpers import make_settings, svc_of, allow_chun  # noqa: E402

ENV = {}
for line in open("/scratch/env.txt"):
    k, _, v = line.strip().partition("=")
    ENV[k] = v
BEACON = "qa-beacon-token-1234567890"
NEEDLES = {k: ENV[k] for k in ("APP_SECRET", "INTERNAL_TOKEN", "SESSION_SECRET", "TELEGRAM_WEBHOOK_SECRET",
                               "TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID")}
NEEDLES["BEACON_TOKEN"] = BEACON


def test_health_reply_has_no_secret_material(tmp_path):
    s = make_settings(
        tmp_path,
        app_secret=ENV["APP_SECRET"], internal_token=ENV["INTERNAL_TOKEN"],
        session_secret=ENV["SESSION_SECRET"], telegram_webhook_secret=ENV["TELEGRAM_WEBHOOK_SECRET"],
        twilio_auth_token=ENV["TWILIO_AUTH_TOKEN"], twilio_account_sid=ENV["TWILIO_ACCOUNT_SID"],
        twilio_from=ENV.get("TWILIO_FROM", "+85290000001"),
        beacon_tokens={"beacon1": BEACON}, family_telegram_chat_ids={"阿珍": "987654321"})
    app = create_app(s, db_path=":memory:", clock=FakeClock("2026-11-20T15:00:00+08:00"), time_scale=0.05,
                     backups=False)
    with TestClient(app, base_url="https://test.invalid") as c:
        svc = svc_of(c)
        allow_chun(svc, "987654321")
        upd = {"update_id": 1, "message": {"message_id": 11, "date": 0, "text": "/health",
                                           "chat": {"id": 987654321, "type": "private"},
                                           "from": {"id": 987654321, "first_name": "阿珍"}}}
        r = c.post("/telegram/webhook", json=upd,
                   headers={"X-Telegram-Bot-Api-Secret-Token": ENV["TELEGRAM_WEBHOOK_SECRET"]})
        assert r.status_code == 200, r.text
        ch = svc.extra["telegram_channel"]
        texts = [x["text"] for x in ch.replies]
        full = "\n---\n".join(texts)
        open("/scratch/tg_health_full.txt", "w").write(full)
        print("reply length:", len(full))
        print(full[:400])
        hits = [label for label, val in NEEDLES.items() if val and len(val) > 4 and val[:-4] in full]
        print("secret material in /health reply:", hits or "NONE")
        assert not hits
        # also scan /status and /whoami style views? /health is the task's target; done.
