"""Sensor Ingest (§9.1): the beacon's events arrive at `POST /events/sensor`.

- `X-Beacon-Token` is checked against the sha256 hashes seeded from `BEACON_TOKENS` (`beacon1:<token>`,
  stored in `beacons` at wiring install). Unknown beacon or wrong token → 401.
- Body per CONTRACTS §8: `{beacon_id, type: fall_suspected|sos|heartbeat, ts, peak_g?, freefall_ms?,
  stillness_s?, battery?, rssi?, seq}`.
- **Idempotent** on `(beacon_id, seq)` via the `sensor_seen` table; a duplicate answers
  `202 {"ok": true, "duplicate": true}` without re-emitting.
- The response is always fast: the event is queued with `kernel.submit`, never processed inline.
- `fall_suspected` / `sos` / `heartbeat` → `sensor.fall_suspected` / `sensor.sos_pressed` / `sensor.heartbeat`
  (source `beacon`); `beacons.last_seen` / `battery` / `rssi` are updated either way.

`detect_fall(samples)` is the PRD's reference detection rule (§9.1) as a pure function, used by the firmware
team and by `sim/beacon.py`: free-fall |a| < 0.4 g for ≥ 60 ms → impact |a| > 2.5 g within 1 s → stillness
(|a| within ±0.15 g of 1 g) for ≥ 2 s.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ahbo.core.events import Event

from . import current_svc

log = logging.getLogger("ahbo.links.sensors")

EVENT_MAP = {"fall_suspected": "sensor.fall_suspected", "sos": "sensor.sos_pressed", "heartbeat": "sensor.heartbeat"}
PAYLOAD_KEYS = ("peak_g", "freefall_ms", "stillness_s", "battery", "rssi")

# Reference rule thresholds (§9.1, PROPOSED values)
FREEFALL_G = 0.4
FREEFALL_MS = 60.0
IMPACT_G = 2.5
IMPACT_WINDOW_S = 1.0
STILLNESS_BAND_G = 0.15
STILLNESS_S = 2.0

router = APIRouter()


# ==================================================================== the reference rule (pure function)
def detect_fall(samples: list[dict[str, float]] | list[tuple[float, float]]) -> dict[str, float] | None:
    """§9.1 on magnitude samples `{"t": seconds, "g": |a|}` (or `(t, g)` tuples): free-fall, then impact,
    then stillness. Returns `{"peak_g", "freefall_ms", "stillness_s"}` or None if the rule doesn't match."""
    s = _norm_samples(samples)
    if len(s) < 3:
        return None
    ff_start, ff_end = _freefall(s)
    if ff_start is None:
        return None
    peak_i = _impact(s, ff_end)
    if peak_i is None:
        return None
    still = _stillness(s, peak_i)
    if still < STILLNESS_S:
        return None
    return {"peak_g": round(s[peak_i][1], 3), "freefall_ms": round((s[ff_end][0] - s[ff_start][0]) * 1000, 1),
            "stillness_s": round(still, 2)}


def _norm_samples(samples: Any) -> list[tuple[float, float]]:
    out = []
    for x in samples or []:
        if isinstance(x, dict):
            out.append((float(x["t"]), float(x["g"])))
        else:
            out.append((float(x[0]), float(x[1])))
    out.sort(key=lambda p: p[0])
    return out


def _freefall(s: list[tuple[float, float]]) -> tuple[int | None, int | None]:
    """First run of |a| < 0.4 g lasting ≥ 60 ms: returns (start, end) indices, (None, None) if absent."""
    start: int | None = None
    for i, (t, g) in enumerate(s):
        if g < FREEFALL_G:
            if start is None:
                start = i
        else:
            if start is not None and (t - s[start][0]) * 1000.0 >= FREEFALL_MS:
                return start, i - 1
            start = None
    if start is not None and (s[-1][0] - s[start][0]) * 1000.0 >= FREEFALL_MS:
        return start, len(s) - 1
    return None, None


def _impact(s: list[tuple[float, float]], ff_end: int) -> int | None:
    """Index of the peak |a| > 2.5 g within 1 s after the free-fall ends; None if there is no impact."""
    t0 = s[ff_end][0]
    best, best_i = IMPACT_G, None
    for i in range(ff_end + 1, len(s)):
        t, g = s[i]
        if t - t0 > IMPACT_WINDOW_S:
            break
        if g > best:
            best, best_i = g, i
    return best_i


def _stillness(s: list[tuple[float, float]], peak_i: int) -> float:
    """Longest still window (|a| within ±0.15 g of 1 g) starting after the impact peak; seconds."""
    best_run = 0.0
    run_start: int | None = None
    for i in range(peak_i + 1, len(s)):
        if abs(s[i][1] - 1.0) <= STILLNESS_BAND_G:
            if run_start is None:
                run_start = i
            best_run = max(best_run, s[i][0] - s[run_start][0])
        else:
            run_start = None
    return best_run


# ==================================================================== install + route
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def install_beacons(svc: Any) -> None:
    """Seed the `beacons` table from `BEACON_TOKENS` (`beacon1:<token>`); tokens are stored hashed only."""
    for beacon_id, token in (getattr(svc.settings, "beacon_tokens", None) or {}).items():
        try:
            svc.db.execute("INSERT OR IGNORE INTO beacons(id, token_hash) VALUES (?,?)",
                           (str(beacon_id), token_hash(str(token))))
        except Exception:
            log.exception("seeding beacon %s failed", beacon_id)


@router.post("/events/sensor")
async def sensor_event(request: Request) -> JSONResponse:
    svc = current_svc(request)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "reason": "bad_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "reason": "bad_json"}, status_code=400)
    beacon_id = str(body.get("beacon_id") or "")
    if not beacon_id:
        return JSONResponse({"ok": False, "reason": "bad_beacon_id"}, status_code=400)
    row = svc.db.one("SELECT token_hash FROM beacons WHERE id=?", (beacon_id,))
    got = request.headers.get("x-beacon-token", "")
    if row is None or not got or not hmac.compare_digest(str(row["token_hash"]), token_hash(got)):
        return JSONResponse({"ok": False, "reason": "bad_token"}, status_code=401)
    etype = EVENT_MAP.get(str(body.get("type") or ""))
    if etype is None:
        return JSONResponse({"ok": False, "reason": "bad_type"}, status_code=400)
    now = svc.clock.iso()
    svc.db.execute("UPDATE beacons SET last_seen=?, battery=?, rssi=?, last_seq=COALESCE(?, last_seq) WHERE id=?",
                   (now, _num(body.get("battery")), _num(body.get("rssi")), _int(body.get("seq")), beacon_id))
    seq = _int(body.get("seq"))
    if seq is not None:
        cur = svc.db.execute("INSERT OR IGNORE INTO sensor_seen(beacon_id, seq) VALUES (?,?)", (beacon_id, seq))
        if cur.rowcount == 0:
            return JSONResponse({"ok": True, "duplicate": True}, status_code=202)
    payload = {k: body[k] for k in PAYLOAD_KEYS if body.get(k) is not None}
    payload["beacon_id"] = beacon_id
    await svc.kernel.submit(Event(type=etype, source="beacon", payload=payload))
    return JSONResponse({"ok": True, "queued": True}, status_code=202)


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None
