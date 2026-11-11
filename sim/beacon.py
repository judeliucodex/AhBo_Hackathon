#!/usr/bin/env python3
"""Beacon simulator (§9.1): sends sensor events to a running Ah Bo api.

    python sim/beacon.py fall      --url http://127.0.0.1:8000 --token <BEACON_TOKEN>
    python sim/beacon.py sos       --url http://127.0.0.1:8000 --token <BEACON_TOKEN>
    python sim/beacon.py heartbeat --url http://127.0.0.1:8000 --token <BEACON_TOKEN> --battery 0.8

`--token` is the plain token from `BEACON_TOKENS=beacon1:<token>` (the api stores only its sha256). Standard
library only, so it runs anywhere (the VPS host has no third-party packages). The `detect` subcommand runs the
PRD's reference detection rule (§9.1) over a CSV (`t,g` per line) and prints what a compliant beacon would send:

    python sim/beacon.py detect samples.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "brain"), str(_ROOT)):      # make `ahbo` importable when run from a checkout
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

TYPES = {"fall": "fall_suspected", "sos": "sos", "heartbeat": "heartbeat"}


def _seq() -> int:
    return int(time.time() * 1000) & 0x7FFFFFFF


def build_body(kind: str, args: argparse.Namespace) -> dict:
    body: dict = {"beacon_id": args.beacon, "type": TYPES[kind], "ts": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                  "seq": args.seq if args.seq is not None else _seq(), "battery": args.battery, "rssi": args.rssi}
    if kind == "fall":
        body |= {"peak_g": args.peak_g, "freefall_ms": args.freefall_ms, "stillness_s": args.stillness_s}
    return body


def post(url: str, token: str, body: dict, timeout: float = 10.0) -> tuple[int, dict]:
    req = urllib.request.Request(url.rstrip("/") + "/events/sensor", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Beacon-Token": token},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except urllib.error.URLError as e:
        return 0, {"ok": False, "reason": f"unreachable: {e.reason}"}


def detect_rule(samples: list[tuple[float, float]]) -> dict | None:
    """The reference rule (§9.1), duplicated here only as a demo aid; the implementation under test is
    `ahbo.links.sensors.detect_fall` — this CLI prefers it when the brain package is importable."""
    try:
        from ahbo.links.sensors import detect_fall
        return detect_fall(samples)
    except Exception:
        pass
    ff_start = None
    ff_end = None
    peak_t = peak_g = None
    for i, (t, g) in enumerate(samples):
        if g < 0.4:
            if ff_start is None:
                ff_start = t
        elif ff_start is not None and (t - ff_start) * 1000 >= 60:
            ff_end = t
            break
        elif ff_start is not None:
            ff_start = None
    if ff_end is None:
        return None
    for t, g in samples:
        if t < ff_end:
            continue
        if t - ff_end > 1.0:
            break
        if g > (peak_g or 2.5):
            peak_g, peak_t = g, t
    if peak_g is None:
        return None
    run = best = 0.0
    run_start = None
    for t, g in samples:
        if t <= (peak_t or 0):
            continue
        if abs(g - 1.0) <= 0.15:
            if run_start is None:
                run_start = t
            run = t - run_start
            best = max(best, run)
        else:
            run_start = None
    if best < 2.0:
        return None
    return {"peak_g": peak_g, "freefall_ms": (ff_end - ff_start) * 1000, "stillness_s": best}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ah Bo beacon simulator (§9.1)")
    sub = ap.add_subparsers(dest="kind", required=True)
    for k in TYPES:
        s = sub.add_parser(k, help=f"send a {TYPES[k]} event")
        _common(s)
        if k == "fall":
            s.add_argument("--peak-g", type=float, default=3.1)
            s.add_argument("--freefall-ms", type=int, default=180)
            s.add_argument("--stillness-s", type=float, default=2.4)
    d = sub.add_parser("detect", help="run the reference rule over a CSV of t,g samples")
    d.add_argument("csv_path")
    a = ap.parse_args(argv)
    if a.kind == "detect":
        samples: list[tuple[float, float]] = []
        with open(a.csv_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and not row[0].startswith(("#", "t")):
                    samples.append((float(row[0]), float(row[1])))
        print(json.dumps(detect_rule(samples), ensure_ascii=False))
        return 0
    status, body = post(a.url, a.token, build_body(a.kind, a))
    print(json.dumps({"http": status, **body}, ensure_ascii=False))
    return 0 if status == 202 and body.get("ok") else 1


def _common(s: argparse.ArgumentParser) -> None:
    s.add_argument("--url", default="http://127.0.0.1:8000", help="api base URL")
    s.add_argument("--token", required=True, help="plain beacon token (BEACON_TOKENS)")
    s.add_argument("--beacon", default="beacon1")
    s.add_argument("--battery", type=float, default=0.85)
    s.add_argument("--rssi", type=float, default=-58.0)
    s.add_argument("--seq", type=int, default=None)


if __name__ == "__main__":
    sys.exit(main())
