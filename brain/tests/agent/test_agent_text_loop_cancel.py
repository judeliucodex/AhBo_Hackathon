"""Real-model eval run (12 Nov) crashed: waiting on a turn that a newer line cancelled re-raised CancelledError."""
import asyncio


async def test_wait_idle_after_turn_cancelled(harness):
    loop = harness.loop
    loop.task = asyncio.ensure_future(asyncio.sleep(10))
    await asyncio.sleep(0)
    loop.task.cancel()
    await loop.wait_idle(timeout=1)        # must return, not raise
