"""QA23 check 4 (INV-9, S1 if broken): phone numbers 阿珍 might type — nothing an emergency number can ever
be stored or called; `verified` only after 確認; numbers shown masked.

Layer 1: the contact_add form (what 阿珍 actually does). Layer 2: the dial guard, fed a hand-seeded
emergency-number row (as if the DB had been edited elsewhere) — a phone.call through the real Kernel dispatcher
must refuse and write no outbox row.
"""
from __future__ import annotations

import pytest
from qa_helpers import (bot_tasks, confirm, events_of, make_svc_fixture, say, settle, stop_all, tap,
                        commands_of)
from bot_helpers import CHUN_CHAT

CHAT = "8925008896"

# (raw input, expected: "refused" | "stored", the reason keyword when refused)
CASES = [
    ("999", "refused"), ("112", "refused"), ("911", "refused"),
    ("+852 999", "refused"), ("(999)", "refused"), ("９９９", "refused"), ("00852 999", "refused"),
    ("12345999", "refused"),                    # 8 digits ending 999, HK service prefix 1 → never stored
    ("9999 9999", "stored"),                    # 8-digit local (999-tail rule is pre-E.164; see report)
    ("9123456", "refused"),                     # 7 digits: short, refused
    ("9123 4567", "stored"),                    # a genuine 8-digit HK mobile
    ("91235999", "stored"),                     # a genuine 8-digit HK number that happens to end 999
    ("+1 416 555 0123", "stored"),              # overseas with country code
    ("abc", "refused"), ("打俾警察啦", "refused"),  # letters / no digits at all
]


@pytest.fixture
async def svc(tmp_path):
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    yield s
    await stop_all(made)


def _texts(replies):
    return [r["text"] for r in replies]


def _btn(replies, prefix):
    for t, d in {b["text"]: b["callback_data"] for r in replies for row in r["buttons"] for b in row}.items():
        if t.startswith(prefix):
            return d
    raise AssertionError(f"no button {prefix!r} in {list(_texts(replies))}")


async def _try_number(svc, raw: str) -> tuple[str, str | None]:
    """Walk contact_add up to the phone step with `raw`. Returns ("refused", reason) — the form re-asked —
    or ("stored", e164-ish) — the flow moved past the phone step (confirmed by caller)."""
    r = await tap(svc, "m:f:contact_add")
    assert "【加聯絡人】第 1/4 步" in r[-1]["text"]
    r = await say(svc, "測試人")
    r = await say(svc, raw)
    text = r[-1]["text"]
    if "第 3/4 步" in text:
        return "stored", None
    assert "⚠️" in text, f"no explanation for refusing {raw!r}: {text!r}"
    return "refused", text


async def test_emergency_and_bad_numbers_never_stored_good_numbers_stored(svc):
    results = {}
    for raw, expect in CASES:
        kind, why = await _try_number(svc, raw)
        assert kind == expect, f"{raw!r}: expected {expect}, got {kind} ({why})"
        results[raw] = (kind, why)
        if kind == "refused":
            # the form is still on the phone step: a later 取消 leaves nothing behind
            r = await say(svc, "取消")
            assert any("已取消" in t for t in _texts(r))
        else:
            r = await say(svc, "2")               # priority
            r = await say(svc, "-")               # no chat id
            await confirm(svc, r)
    rows = svc.db.query("SELECT name, phone FROM contacts")
    stored = {r["phone"] for r in rows if r["name"] == "測試人"}
    assert stored == {"+85291234567", "+85291235999", "+14165550123", "+85299999999"}, stored
    # 9999 9999 is an 8-digit LOCAL number (+852 9999 9999): dialling it is a normal call, never the 999 line
    banned = {"999", "112", "911", "+852999", "852999", "12345999", "85212345999"}
    for phone in stored:
        from ahbo.links.twilio import only_digits
        d = only_digits(phone)
        assert d not in banned, f"emergency-looking {phone} stored!"
        assert len(d) >= 11, f"short/partial number stored: {phone}"   # short codes are never stored
    print("PHONE CASES:")
    for raw, (kind, why) in results.items():
        print(f"  {raw!r:20} -> {kind}" + (f": {why.splitlines()[0][:60]}" if why else ""))


async def test_verified_only_after_confirm_and_only_by_confirm(svc):
    # nothing exists before 確認 — the whole edit is one pending change
    r = await tap(svc, "m:f:contact_add")
    r = await say(svc, "家姐")
    r = await say(svc, "9123 4567")
    r = await say(svc, "1")
    r = await say(svc, "-")
    assert svc.db.one("SELECT 1 FROM contacts WHERE name='家姐'") is None, "row written before 確認"
    r = await confirm(svc, r)
    row = svc.db.one("SELECT * FROM contacts WHERE name='家姐'")
    assert row and row["verified"] == 1 and row["phone"] == "+85291234567", row
    ev = events_of(svc, "settings.changed")[-1]["payload"]
    assert ev["after"]["verified"] == 1, ev


async def test_numbers_shown_masked_everywhere(svc):
    from ahbo.core.jsonutil import loads
    r = await tap(svc, "m:f:contact_add")
    r = await say(svc, "家姐")
    r = await say(svc, "9123 4567")
    r = await say(svc, "1")
    r = await say(svc, "-")
    summary = r[-1]["text"]
    r = await confirm(svc, r)
    cid = svc.db.one("SELECT id FROM contacts WHERE name='家姐'")["id"]
    views = []
    views += _texts(await say(svc, "/contacts"))
    views += _texts(await tap(svc, f"m:v:contact:{cid}"))
    views.append(summary)
    for t in views:
        for full in ("91234567", "+85291234567", "9123 4567"):
            assert full not in t, f"full number visible: {t!r}"
        assert "••••4567" in t, f"masked form missing: {t!r}"
    ev = events_of(svc, "settings.changed")[-1]["payload"]
    dumped = str(loads.__self__ if hasattr(loads, "__self__") else ev)
    assert "91234567" not in dumped and "••••4567" in str(ev), f"settings.changed leaks the number: {ev}"


async def test_dial_guard_refuses_hand_seeded_emergency_rows(svc):
    """Layer 2: even if an emergency number got into `contacts` some other way, the Kernel's phone.call
    dispatcher refuses it, writes no outbox row, and the fall flow never picks it."""
    from ahbo.core.events import cmd, P0

    for raw in ("999", "+852 999", "911", "112", "８５２９９９", "24999"):   # 24999: ends 999, ≤5 extra digits
        svc.db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES (?,?,?,?,1,1)",
                       (f"c_bad_{raw.replace(' ', '')}", "壞人", "family", raw))
    # 阿珍's real contact also present (verified) so call_order is non-empty otherwise
    for i, raw in enumerate(("999", "+852 999", "911", "112", "２４９９９", "24999")):
        cid = f"c_bad_{i}"
        n_out_before = len(svc.db.query("SELECT 1 FROM outbox WHERE channel='twilio'"))
        await svc.kernel.emit(cmd("phone.call", P0, contact_id=cid))
        await settle(svc)
        await bot_tasks(svc)
        n_out_after = len(svc.db.query("SELECT 1 FROM outbox WHERE channel='twilio'"))
        assert n_out_after == n_out_before, f"{raw}: refused call still wrote an outbox row"
        notes = events_of(svc, "phone.call_refused")
        assert notes, f"{raw}: no phone.call_refused recorded"
    from ahbo.kernel import common
    bad = svc.db.query("SELECT * FROM contacts WHERE id LIKE 'c_bad_%' AND phone IS NOT NULL")
    assert bad, "seed rows missing"
    for c in bad:
        assert not common.callable_contact(c), f"fall flow could pick {c['phone']}"
        assert c not in common.call_order(svc.kernel.ctx()), f"call_order contains {c['phone']}"
    # and 阿珍 herself remains callable (control)
    chun = svc.db.one("SELECT * FROM contacts WHERE id='c_chun'")
    chun = dict(chun, phone="+85291230000", verified=1)
    assert common.callable_contact(chun)
