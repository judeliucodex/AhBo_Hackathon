"""HK Observatory open data (§24.8.5): current conditions (`rhrread`) + local forecast (`flw`), simplified into spoken
Cantonese words. Free, no key. Cached 10 minutes. When models are faked (`AHBO_FAKE` contains `models`) or the
network fails, a deterministic fake is returned (`source: "fake"`), so a demo never depends on the network.

Not a tool module itself: `quick.get_weather` and the deep agent's `get_weather_forecast` use it.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ahbo.core import numwords as nw

log = logging.getLogger("ahbo.tools.weather")

HKO_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
CACHE_S = 600.0
TIMEOUT_S = 3.0
HOME_STATIONS = ("觀塘", "啟德跑道公園", "香港天文台")
SHORT_MAX = 14

# HKO weather icon codes → short spoken words (https://www.hko.gov.hk/textonly/v2/explain/wxicon_c.htm)
ICON_WORDS = {
    50: "天晴", 51: "間中有陽光", 52: "短暫時間有陽光", 53: "間中有陽光，有幾陣驟雨", 54: "短暫時間有陽光，有驟雨",
    60: "多雲", 61: "密雲", 62: "微雨", 63: "落雨", 64: "落大雨", 65: "行雷", 70: "天色好", 71: "天色好",
    72: "天色好", 73: "天色好", 74: "天色好", 75: "天色好", 76: "大致多雲", 77: "天色大致好", 80: "大風",
    81: "乾燥", 82: "潮濕", 83: "有霧", 84: "有薄霧", 85: "有煙霞", 90: "好熱", 91: "暖", 92: "涼", 93: "凍",
}
RAIN_ICONS = {53, 54, 62, 63, 64, 65}

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    _cache.clear()


def fake_weather() -> dict[str, Any]:
    """Deterministic stand-in (a mild, dry HK November day)."""
    temp = 23
    return {"now_words": f"而家{nw.int_words(temp)}度，天晴", "temp_c": temp,
            "forecast_words": "今日大致天晴，日間乾燥，最高二十六度", "weather_short": f"{nw.int_words(temp)}度，天晴",
            "rain_likely": False, "source": "fake"}


def short_words(temp: int | None, icon_words: str) -> str:
    """Step label (≤ 14 characters): 「二十三度，天晴」."""
    head = f"{nw.int_words(temp)}度" if temp is not None else ""
    out = f"{head}，{icon_words}" if head and icon_words else (head or icon_words or "天氣已查好")
    if len(out) > SHORT_MAX:
        out = out.split("，")[0] if head else out[:SHORT_MAX]
    return out[:SHORT_MAX]


def summarise(rhr: dict[str, Any], flw: dict[str, Any] | None) -> dict[str, Any]:
    """Raw HKO JSON → the tool's fields (numbers as words; the forecast sentence trimmed)."""
    temps = {d.get("place"): d.get("value") for d in (rhr.get("temperature") or {}).get("data") or []}
    temp = next((temps[s] for s in HOME_STATIONS if isinstance(temps.get(s), (int, float))), None)
    if temp is None:
        vals = [v for v in temps.values() if isinstance(v, (int, float))]
        temp = round(sum(vals) / len(vals)) if vals else None
    temp = int(round(temp)) if temp is not None else None
    icons = [int(i) for i in rhr.get("icon") or [] if str(i).isdigit()]
    icon_words = ICON_WORDS.get(icons[0], "") if icons else ""
    now = "而家" + "，".join(x for x in (f"{nw.int_words(temp)}度" if temp is not None else "", icon_words) if x)
    fc = ""
    if flw:
        fc = str(flw.get("forecastDesc") or "").strip()
        fc = fc.split("。")[0][:60]
    return {"now_words": now if now != "而家" else "而家睇唔到氣溫", "temp_c": temp,
            "forecast_words": nw.digits_to_words(fc) if fc else "", "weather_short": short_words(temp, icon_words),
            "rain_likely": bool(set(icons) & RAIN_ICONS) or ("雨" in fc), "source": "hko"}


async def fetch_hko(data_type: str, *, client: Any = None) -> dict[str, Any]:
    import httpx
    params = {"dataType": data_type, "lang": "tc"}
    if client is not None:
        r = await client.get(HKO_URL, params=params, timeout=TIMEOUT_S)
    else:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
            r = await c.get(HKO_URL, params=params)
    r.raise_for_status()
    return r.json()


async def current(settings: Any, *, client: Any = None, force_real: bool = False) -> dict[str, Any]:
    if settings is not None and settings.is_fake("models") and not force_real:
        return fake_weather()
    hit = _cache.get("now")
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        rhr = await fetch_hko("rhrread", client=client)
        try:
            flw = await fetch_hko("flw", client=client)
        except Exception:
            flw = None
        out = summarise(rhr, flw)
    except Exception as e:  # network down, HKO changed: never fail the turn over the weather
        log.warning("HKO weather unavailable (%r); using the fake", e)
        return fake_weather()
    _cache["now"] = (time.monotonic(), out)
    return out


async def forecast_9day(settings: Any, *, client: Any = None) -> list[dict[str, Any]]:
    """For the deep agent: the 9-day forecast (`fnd`), trimmed to date, words, min/max."""
    if settings is not None and settings.is_fake("models"):
        return [{"date": "today", "weather": "大致天晴", "min_c": 19, "max_c": 26}]
    hit = _cache.get("fnd")
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]["days"]
    try:
        raw = await fetch_hko("fnd", client=client)
    except Exception as e:
        log.warning("HKO forecast unavailable (%r)", e)
        return []
    days = [{"date": d.get("forecastDate"), "weather": d.get("forecastWeather"),
             "min_c": (d.get("forecastMintemp") or {}).get("value"), "max_c": (d.get("forecastMaxtemp") or {}).get("value")}
            for d in raw.get("weatherForecast") or []][:9]
    _cache["fnd"] = (time.monotonic(), {"days": days})
    return days
