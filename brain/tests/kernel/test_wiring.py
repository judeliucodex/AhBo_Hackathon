"""kernel.wiring.install: idempotent reducer registration, scheduler lifecycle, queued arbiter items survive a restart."""
from __future__ import annotations

from types import SimpleNamespace

from kt import ev

from ahbo.kernel import arbiter, wiring


async def test_install_is_idempotent_and_starts_the_scheduler(kh):
    h = kh
    n = len(h.kernel.reducers)
    svc = SimpleNamespace(kernel=h.kernel, on_start=[], on_stop=[], reducers=[])
    wiring.install(svc)
    assert len(h.kernel.reducers) == n and len(svc.on_start) == 1 and len(svc.on_stop) == 1
    svc.on_start[0](svc)
    assert h.kernel.scheduler.task is not None
    await svc.on_stop[0](svc)
    assert h.kernel.scheduler.task is None


async def test_arbiter_queue_persists(kh):
    h = kh
    await h.say("你好呀阿寶", wait=False)
    h.kernel.db.execute(
        "INSERT OR REPLACE INTO voice_messages (id, direction, from_name, contact_id, file_path, "
        "transcript, received_at) VALUES ('vm_demo', 'in', '阿珍', 'c_chun', "
        "'seed/voice/demo_daughter.m4a', '媽，記得食飯呀！星期日我帶阿明嚟探你。', '2026-11-14T07:42:00+08:00')")
    await ev(h, "family.voice_note", "telegram", contact_id="c_chun", voice_id="vm_demo")
    assert h.kernel.db.kv_get(arbiter.KV)
    h.kernel.state.arbiter = {}
    h.kernel.load()
    item = arbiter.slice_(h.kernel.state)["queue"]["2"]
    assert item["kind"] == "錄音" and item["status"] == "next_session"
