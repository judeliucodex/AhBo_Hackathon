"""12 Nov audit: a refused call or an iPad error must not be silent — 阿珍 gets a Telegram INFO."""
from ahbo.core.events import Event


async def test_refused_call_and_device_error_notify_caregiver(harness):
    k = harness.kernel
    await k.process(Event(type="phone.call_refused", source="twilio", payload={"reason": "unverified"}))
    await k.process(Event(type="device.error", source="device", payload={"code": "mic_denied"}))
    await k.process(Event(type="device.error", source="device", payload={"code": "something_minor"}))
    sends = k.db.query("SELECT payload FROM commands WHERE type='family.send' ORDER BY ts")
    texts = [s["payload"] for s in sends]
    assert len(texts) == 2
    assert "電話號碼未核實" in texts[0] and "冇咪高峰權限" in texts[1]
