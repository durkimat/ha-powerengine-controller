"""Forecast against actual: what engine v2 expected at each re-plan, kept to be scored against what happened.

    <costs dir>/forecastlog/YYYY-MM-DD.jsonl   (kept 30 days, one JSON object a line)

Two kinds of row:

    plan    {"k": "p", "t": iso, "lvl": 87.0, "why": "...",
             "h": {"30": [low, mid, high], "60": [...], "120": [...], "240": [...]},      battery level expected then
             "sl": [[half-hour iso, odds, slot import p, usual import p], ...],            slots counted on (next 12 h)
             "su": [[half-hour iso, low, mid, high], ...],                                 solar kWh expected (next 4 h)
             "lo": [[half-hour iso, low, mid, high], ...]}                                 house kWh expected (next 4 h)
    actual  {"k": "a", "t": iso, "lvl": 87.0, "imp": 6.66, "sol": 1234, "hou": 980}      every 5 minutes: level, the
                                                                                          import price in force (p), W

`score` turns a run of rows into the figures worth looking at: how far the expected level was from the real one
at each distance ahead (and how often the real level fell outside the low-to-high range), how often a half-hour counted
on as a smart slot really had the slot price (by how far ahead it was counted, against the odds used), and how the
expected sun and house load compared with the real ones. Files are read only by `read_rows`; the rest is pure.
"""

from __future__ import annotations

import bisect
import json
import os
from datetime import date, datetime, timedelta, timezone

VERSION = 1
KEEP_DAYS = 30
HORIZONS_MIN = (30, 60, 120, 240)
SLOT_AHEAD_H = 12            # half-hours counted on as smart slots, this far ahead, are kept
ENERGY_AHEAD_H = 4           # expected sun and house load, this far ahead
ACTUAL_EVERY_S = 300
HALF = timedelta(minutes=30)
LEAD_BUCKETS = ((0.5, "next 30 min"), (1.0, "30-60 min"), (2.0, "1-2 h"), (4.0, "2-4 h"), (99.0, "over 4 h"))
HIT_MARGIN_P = 0.5           # the real price is the slot price if it is within this of the slot price
MIN_SAMPLES_HALF = 3         # actual rows needed in a half-hour to judge it
LEVEL_MARGIN = 1.0           # a real level this far outside low-high counts as outside (whole-percent readings)


def _half_start(t: datetime) -> datetime:
    return t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _at(path: dict, minutes: float, case: str) -> float | None:
    step = path.get("step_min") or 15
    series = path.get(case) or []
    i = round(minutes / step)
    return float(series[i]) if 0 <= i < len(series) else None


def plan_row(now: datetime, level: float, vr) -> dict:
    """One row for a re-plan. `vr` is the engine's ValueResult (its path and forecast segments)."""
    path = vr.path or {}
    row: dict = {"k": "p", "t": _iso(now), "lvl": round(level, 1), "why": str(vr.because)[:80], "h": {}, "sl": [],
                 "su": [], "lo": []}
    for h in HORIZONS_MIN:
        trio = [_at(path, h, c) for c in ("low", "mid", "high")]
        if None not in trio:
            row["h"][str(h)] = [round(x, 1) for x in trio]
    segs = vr.forecast.segments if vr.forecast else ()
    first = _half_start(now) + HALF
    for n in range(int(SLOT_AHEAD_H * 2)):
        hs = first + n * HALF
        seg = next((g for g in segs if g.start <= hs < g.end), None)
        if seg is not None and seg.slot_prob is not None and seg.slot_import_p is not None:
            row["sl"].append([_iso(hs), round(float(seg.slot_prob), 3), round(float(seg.slot_import_p), 2),
                              round(float(seg.import_p), 2)])
    for n in range(int(ENERGY_AHEAD_H * 2)):
        hs = first + n * HALF
        sun, load = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        for g in segs:
            lo, hi = max(g.start, hs), min(g.end, hs + HALF)
            if hi <= lo or g.end <= g.start:
                continue
            share = (hi - lo) / (g.end - g.start)
            for i, name in enumerate(("low", "mid", "high")):
                sun[i] += getattr(g.solar_kwh, name) * share
                load[i] += getattr(g.load_kwh, name) * share
        row["su"].append([_iso(hs), *[round(x, 3) for x in sun]])
        row["lo"].append([_iso(hs), *[round(x, 3) for x in load]])
    return row


def actual_row(now: datetime, readings) -> dict:
    imp = getattr(readings, "import_rate", None)
    return {"k": "a", "t": _iso(now), "lvl": getattr(readings, "battery_soc", None),
            "imp": None if imp is None else round(imp * 100, 2), "sol": getattr(readings, "solar_power", None),
            "hou": getattr(readings, "house_power", None)}


class ForecastLog:
    def __init__(self, folder: str, tz=None):
        self.folder, self.tz = folder, tz
        self._last_actual = 0.0
        os.makedirs(folder, exist_ok=True)

    def _path(self, day: str) -> str:
        return os.path.join(self.folder, f"{day}.jsonl")

    def _day(self, now: datetime) -> str:
        return (now.astimezone(self.tz) if self.tz else now).date().isoformat()

    def _append(self, now: datetime, row: dict) -> None:
        with open(self._path(self._day(now)), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def plan(self, now: datetime, level: float | None, vr) -> None:
        if level is not None and vr is not None:
            self._append(now, plan_row(now, level, vr))

    def actual(self, now: datetime, readings) -> bool:
        """Write an actual row if five minutes have passed since the last. Returns True when it wrote one."""
        ts = now.timestamp()
        if ts - self._last_actual < ACTUAL_EVERY_S or getattr(readings, "battery_soc", None) is None:
            return False
        self._last_actual = ts
        self._append(now, actual_row(now, readings))
        return True

    def prune(self, today: date, keep: int = KEEP_DAYS) -> int:
        gone = 0
        for name in os.listdir(self.folder):
            try:
                day = date.fromisoformat(name[:10])
            except ValueError:
                continue
            if (today - day).days > keep:
                os.remove(os.path.join(self.folder, name))
                gone += 1
        return gone

    def rows(self, days: int, today: date) -> list[dict]:
        return read_rows(self.folder, days, today)

    def score(self, days: int, today: date) -> dict:
        return score(self.rows(days, today))


def read_rows(folder: str, days: int, today: date) -> list[dict]:
    out: list[dict] = []
    for n in range(days - 1, -1, -1):
        path = os.path.join(folder, f"{(today - timedelta(days=n)).isoformat()}.jsonl")
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue                               # a half-written last line
        except OSError:
            continue
    return out


# ---- scoring (pure) -------------------------------------------------------------------------------------------
def _t(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


class _Actuals:
    def __init__(self, rows: list[dict]):
        self.rows = sorted(((_t(r["t"]), r) for r in rows if r.get("k") == "a"), key=lambda x: x[0])
        self.times = [t for t, _ in self.rows]

    def _slice(self, lo: float, hi: float) -> list[dict]:
        return [r for _, r in self.rows[bisect.bisect_left(self.times, lo):bisect.bisect_left(self.times, hi)]]

    def level_near(self, t: float, within_s: float = 420.0) -> float | None:
        """The level reading nearest to `t`, if one is within `within_s` of it."""
        i = bisect.bisect_left(self.times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.rows) and self.rows[j][1].get("lvl") is not None:
                gap = abs(self.times[j] - t)
                if gap <= within_s and (best is None or gap < best[0]):
                    best = (gap, float(self.rows[j][1]["lvl"]))
        return None if best is None else best[1]

    def half(self, start: float) -> list[dict]:
        return self._slice(start, start + HALF.total_seconds())

    def kwh(self, start: float, key: str) -> float | None:
        vals = [r[key] for r in self.half(start) if r.get(key) is not None]
        if len(vals) < MIN_SAMPLES_HALF:
            return None
        return sum(vals) / len(vals) * 0.5 / 1000.0

    def slot_hit(self, start: float, slot_p: float) -> bool | None:
        prices = [r["imp"] for r in self.half(start) if r.get("imp") is not None]
        if len(prices) < MIN_SAMPLES_HALF:
            return None
        cheap = sum(1 for p in prices if p <= slot_p + HIT_MARGIN_P)
        return cheap * 2 >= len(prices)


def _bucket(lead_h: float) -> str:
    return next(name for top, name in LEAD_BUCKETS if lead_h <= top)


def score(rows: list[dict]) -> dict:
    """The accuracy figures for the plan rows against the actual rows in `rows` (any number of days)."""
    act = _Actuals(rows)
    plans = [r for r in rows if r.get("k") == "p"]
    level: dict[int, dict] = {h: {"n": 0, "abs": 0.0, "bias": 0.0, "below": 0, "above": 0} for h in HORIZONS_MIN}
    slots: dict[str, dict] = {name: {"n": 0, "p": 0.0, "hit": 0} for _, name in LEAD_BUCKETS}
    energy = {key: {name: {"n": 0, "pred": 0.0, "act": 0.0, "inside": 0} for name in ("0-1 h", "1-2 h", "2-4 h")}
              for key in ("sun", "house")}
    for p in plans:
        t0 = _t(p["t"])
        for h, trio in (p.get("h") or {}).items():
            real = act.level_near(t0 + int(h) * 60)
            if real is None:
                continue
            low, mid, high = trio
            d = level[int(h)]
            d["n"] += 1
            d["abs"] += abs(mid - real)
            d["bias"] += mid - real
            d["below"] += real < low - LEVEL_MARGIN
            d["above"] += real > high + LEVEL_MARGIN
        for iso, odds, slot_p, _usual in p.get("sl") or []:
            hs = _t(iso)
            hit = act.slot_hit(hs, slot_p)
            if hit is None:
                continue
            d = slots[_bucket((hs - t0) / 3600)]
            d["n"] += 1
            d["p"] += odds
            d["hit"] += bool(hit)
        for key, field in (("sun", "su"), ("house", "lo")):
            for iso, low, mid, high in p.get(field) or []:
                hs = _t(iso)
                real = act.kwh(hs, "sol" if key == "sun" else "hou")
                if real is None:
                    continue
                lead = (hs - t0) / 3600
                d = energy[key]["0-1 h" if lead <= 1 else "1-2 h" if lead <= 2 else "2-4 h"]
                d["n"] += 1
                d["pred"] += mid
                d["act"] += real
                d["inside"] += low - 0.02 <= real <= high + 0.02
    out: dict = {"plans": len(plans), "actuals": len(act.rows), "level": {}, "slots": {}, "sun": {}, "house": {}}
    for h, d in level.items():
        if d["n"]:
            out["level"][f"+{h} min"] = {"n": d["n"], "mean_abs_error": round(d["abs"] / d["n"], 1),
                                         "bias": round(d["bias"] / d["n"], 1),
                                         "below_low_pct": round(100 * d["below"] / d["n"]),
                                         "above_high_pct": round(100 * d["above"] / d["n"])}
    for name, d in slots.items():
        if d["n"]:
            out["slots"][name] = {"n": d["n"], "assumed_pct": round(100 * d["p"] / d["n"]),
                                  "arrived_pct": round(100 * d["hit"] / d["n"])}
    for key in ("sun", "house"):
        for name, d in energy[key].items():
            if d["n"] and d["pred"] > 0:
                out[key][name] = {"n": d["n"], "actual_over_expected": round(d["act"] / d["pred"], 2),
                                  "inside_range_pct": round(100 * d["inside"] / d["n"])}
    return out


def summary(folder: str, today: date) -> dict:
    """Files kept, for the diagnostics bundle."""
    days = []
    try:
        for name in sorted(os.listdir(folder)):
            if name.endswith(".jsonl"):
                days.append({"day": name[:-6], "bytes": os.path.getsize(os.path.join(folder, name))})
    except OSError:
        pass
    return {"days_kept": len(days), "days": days[-8:], "score_7d": score(read_rows(folder, 7, today))}
