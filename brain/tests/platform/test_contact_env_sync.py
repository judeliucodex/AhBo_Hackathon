"""Keys added to .env after the first start must reach the seeded contacts without a reset (11 Nov bug)."""
from dataclasses import replace

from ahbo.core.clock import FakeClock
from ahbo.core.db import open_db
from ahbo.core.settings import load_settings
from ahbo.ops.seed import load_demo, sync_contacts_from_env


def test_chat_id_and_phone_fill_in_but_never_overwrite():
    db, clock = open_db(":memory:"), FakeClock()
    s0 = replace(load_settings(), family_telegram_chat_ids={}, family_phone="")
    load_demo(db, clock, s0)
    assert db.one("SELECT telegram_chat_id FROM contacts WHERE id='c_chun'")["telegram_chat_id"] is None
    s1 = replace(s0, family_telegram_chat_ids={"阿珍": "12345"}, family_phone="+85291234567")
    assert set(sync_contacts_from_env(db, s1)) == {"c_chun.telegram_chat_id", "c_chun.phone"}
    row = db.one("SELECT * FROM contacts WHERE id='c_chun'")
    assert row["telegram_chat_id"] == "12345" and row["phone"] == "+85291234567"
    db.execute("UPDATE contacts SET phone='+85298765432' WHERE id='c_chun'")      # edited in Telegram
    assert sync_contacts_from_env(db, s1) == []
    assert db.one("SELECT phone FROM contacts WHERE id='c_chun'")["phone"] == "+85298765432"
