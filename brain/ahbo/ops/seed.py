"""Seed loader (§7.5, §26.6): loads seed/demo.yaml, anchoring relative times to the virtual clock's today.
Used by `ahbo seed` and the console's Reset demo."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ahbo.core.clock import HKT, Clock
from ahbo.core.config import load_seed
from ahbo.core.db import RESET_TABLES, Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps
from ahbo.core.settings import SEED_DIR, Settings


def load_demo(db: Database, clock: Clock, settings: Settings, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    seed = seed or load_seed("demo.yaml", settings.public_base_url)
    today = clock.today()
    at = lambda hhmm, d=today: datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time(), HKT)  # noqa: E731
    with db.transaction():
        for t in RESET_TABLES:
            db.execute(f"DELETE FROM {t}")
        r = seed["resident"]
        sh = dict(r.get("shareable") or {})
        if sh.pop("contact_phone_from", None):
            sh["contact_phone"] = settings.family_phone or "（示範號碼）"
        db.execute("INSERT INTO resident(id,name_zh,name_en,age,district,address,shareable,quiet_hours) VALUES (?,?,?,?,?,?,?,?)",
                   ("resident", r["name_zh"], r["name_en"], r.get("age"), r.get("district"), r.get("address"),
                    dumps(sh), dumps(r.get("quiet_hours") or {})))
        for c in seed.get("contacts", []):
            phone = getattr(settings, (c.get("phone_env") or "").lower(), "") if c.get("phone_env") else ""
            chat = settings.family_telegram_chat_ids.get(c.get("telegram_from_env") or "", None)
            db.execute("INSERT INTO contacts(id,name,role,relation,telegram_chat_id,phone,verified,priority) VALUES (?,?,?,?,?,?,?,?)",
                       (c["id"], c["name"], c["role"], c.get("relation"), chat, phone or None,
                        int(bool(c.get("verified")) and bool(phone or chat or True)), c.get("priority", 9)))
        meds = seed["meds"]
        for m in meds["catalogue"]:
            db.execute("INSERT INTO med_catalogue(id,yue,en,dose,pill) VALUES (?,?,?,?,?)",
                       (m["id"], m["yue"], m.get("en"), m.get("dose"), dumps(m.get("pill") or [])))
        for s in meds["slots"]:
            db.execute("INSERT INTO med_schedule(slot_id,time,med_names,active) VALUES (?,?,?,1)",
                       (s["id"], s["time"], dumps(s["meds"])))
        for d in meds.get("seeded_today", []):
            if at(d["at"]) > clock.now():      # never record a dose as taken in the future (QA 25 D1, INV-4)
                continue
            db.execute("INSERT INTO dose_events(id,slot_id,date,state,resolved_at,source,updated_at) VALUES (?,?,?,?,?,?,?)",
                       (new_id("dose"), d["slot"], today.isoformat(), d["state"], at(d["at"]).isoformat(), "seed",
                        clock.iso()))
        for e in seed.get("calendar", []):
            db.execute("INSERT INTO calendar_events(id,datetime,title,place,place_full,kind,route,status,source) VALUES (?,?,?,?,?,?,?,?,?)",
                       (new_id("cal"), at(e["time"]).isoformat(), e["title"], e.get("place"), e.get("place_full"),
                        e.get("kind", "other"), e.get("route"), "confirmed", "seed"))
        for f in seed.get("facts", []):
            db.execute("INSERT INTO facts(id,fact,category,importance,status,source,created_at) VALUES (?,?,?,?,?,?,?)",
                       (new_id("fact"), f["fact"], f["category"], f.get("importance", 3), "active", "seed", clock.iso()))
        for s in seed.get("life_stories", []):
            db.execute("INSERT INTO life_stories(id,title,era,text,ok_to_reminisce) VALUES (?,?,?,?,?)",
                       (new_id("story"), s["title"], s.get("era"), s["text"], int(s.get("ok_to_reminisce", True))))
        for s in seed.get("sessions", []):
            opened = (clock.now() - timedelta(days=s["days_ago"])).replace(hour=16, minute=0, second=0, microsecond=0)
            db.execute("INSERT INTO sessions(id,opened_at,closed_at,reason,mode,summary,summary_text,mood) VALUES (?,?,?,?,?,?,?,?)",
                       (new_id("sess"), opened.isoformat(), (opened + timedelta(minutes=10)).isoformat(), "seed",
                        "companion", dumps(s["summary"]), "".join(s["summary"]), s.get("mood")))
        for v in seed.get("voice_messages", []):
            path = SEED_DIR / "voice" / v["file"].split("/")[-1]
            db.execute("INSERT INTO voice_messages(id,direction,from_name,contact_id,file_path,mime,duration_s,transcript,received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (v["id"], "in", v["from"], "c_chun", str(path), "audio/mp4",
                        round(max(1.2, len(v.get("transcript", "")) / 5.2), 2), v.get("transcript"),
                        at(v["received"]).isoformat()))
        dp = seed.get("day_plan")
        if dp:
            db.execute("INSERT INTO day_plans(date,json,summary,generated_at) VALUES (?,?,?,?)",
                       (today.isoformat(), dumps(dp), dp.get("summary"), clock.iso()))
    return seed


def sync_contacts_from_env(db: Database, settings: Settings, seed: dict[str, Any] | None = None) -> list[str]:
    """Startup: copy Telegram chat ids and phone numbers from .env onto the seeded contacts WITHOUT a reset, so keys
    added after the first start take effect (found 11 Nov: 阿珍 had no chat id → the bot treated her as a stranger).
    Only fills values that are empty in the DB; never overwrites what the caregiver edited in Telegram."""
    seed = seed or load_seed("demo.yaml", settings.public_base_url)
    changed: list[str] = []
    for c in seed.get("contacts", []):
        row = db.one("SELECT * FROM contacts WHERE id=?", (c["id"],))
        if row is None:
            continue
        chat = settings.family_telegram_chat_ids.get(c.get("telegram_from_env") or "", None)
        phone = getattr(settings, (c.get("phone_env") or "").lower(), "") if c.get("phone_env") else ""
        if chat and not row.get("telegram_chat_id"):
            db.execute("UPDATE contacts SET telegram_chat_id=? WHERE id=?", (chat, c["id"]))
            changed.append(f"{c['id']}.telegram_chat_id")
        if phone and not row.get("phone"):
            db.execute("UPDATE contacts SET phone=? WHERE id=?", (phone, c["id"]))
            changed.append(f"{c['id']}.phone")
    return changed
