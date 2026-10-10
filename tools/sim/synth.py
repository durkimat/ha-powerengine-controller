"""Synthetic days: a day the archive does not have, built from a real one.

    synth.py sunny --from 2026-10-08 --date 2026-09-03 [--peak-kw 4] [--slots 09:00-12:00,13:00-16:00] [--slot-p 6.66]

takes the house load, the standard and overnight prices and the learned house profile of the `--from` day, and replaces
the sun (an east-facing array: an early, asymmetric curve peaking at `--peak-kw`), the smart slots (cheap windows at
`--slot-p`), the car (none) and the grid events (none). It writes `costs/<date>.json` (the day's records) and
`costs/snapshots/<date>.json` (the forecasts as they would have been: Solcast with a narrow spread, the rates, the
dispatches) under `--out` (default ~/pe-sim/synthetic), which the simulator reads beside the archive. The archive is
only read. A synthetic day carries `"synthetic": true` in its records and has no metered cost.

Pick a `--date` the archive does not have (the real archive begins on 11 Sep 2026): the simulator refuses a date in
both. Keep the template's weekday (8 Oct 2026 is a Thursday; 3 Sep 2026 is too), since the house profile is by weekday.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workspace  # noqa: E402

HALF = timedelta(minutes=30)


def solar_kw(
    hour: float, peak_kw: float, rise: float = 7.2, noon: float = 10.5, sig_a: float = 1.5, sig_b: float = 2.3
) -> float:
    """An east-facing array on a clear day: it starts early, peaks mid-morning and tails off through the afternoon. A
    half-gaussian either side of `noon` (the peak), narrower before it. Nothing before `rise` or below 30 W."""
    if hour < rise:
        return 0.0
    sig = sig_a if hour <= noon else sig_b
    kw = peak_kw * math.exp(-0.5 * ((hour - noon) / sig) ** 2)
    return round(kw, 4) if kw >= 0.03 else 0.0


def parse_slots(text: str) -> list[tuple[float, float]]:
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        (h1, m1), (h2, m2) = (tuple(int(x) for x in t.split(":")) for t in (a, b))
        out.append((h1 + m1 / 60, h2 + m2 / 60))
    return out


def in_slot(hour: float, slots) -> bool:
    return any(a <= hour < b for a, b in slots)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def make(
    template_day: str,
    new_day: str,
    data: Path,
    out: Path,
    peak_kw: float,
    slots: list,
    slot_p: float,
    tomorrow_scale: float,
) -> dict:
    src_costs = Path(data) / "costs"
    records = json.loads((src_costs / f"{template_day}.json").read_text(encoding="utf-8"))
    snap = json.loads((src_costs / "snapshots" / f"{template_day}.json").read_text(encoding="utf-8"))
    d0, d1 = date.fromisoformat(template_day), date.fromisoformat(new_day)
    if d0.weekday() != d1.weekday():
        raise SystemExit(f"{new_day} is a {d1:%A} but {template_day} is a {d0:%A}: the house profile is by weekday")
    if (src_costs / f"{new_day}.json").exists():
        raise SystemExit(f"{new_day} is a real day in the archive: choose another date")
    shift = d1 - d0

    # ---- the day's records: the template's house load and prices, the new sun, slots, no car, no event
    new = copy.deepcopy(records)
    for i, r in enumerate(new):
        r["start"] = _iso(datetime.fromisoformat(r["start"]) + shift)
        hour = i / 2 + 0.25
        kw = solar_kw(hour, peak_kw)
        r["solar"] = round(kw * 0.5, 5)
        r["car"], r["axle"], r["free"], r["live"] = 0.0, False, False, False
        r["source"], r["synthetic"] = "synthetic", True
        v = r["v"]
        slot = in_slot(i / 2, slots)
        v["slot"], v["event"], v["event_kwh"], v["event_gross"] = slot, None, 0.0, 0.0
        v["act"] = slot_p / 100 if slot else v["std"]
    metered_note = "grid and battery columns are the template's and mean nothing here"
    for r in new:
        r["note"] = metered_note

    # ---- the snapshot: forecasts, rates and dispatches as they would have been on the new day
    midnight = datetime.fromisoformat(snap["entries"][0]["at"]).replace(hour=0, minute=0, second=8) + shift
    roles = snap["roles"]
    first = copy.deepcopy(snap["entries"][0])
    states = first["states"]

    def day_rates(offset: int, with_slots: bool) -> list[dict]:
        day0 = midnight.replace(second=0) + timedelta(days=offset)
        rows = []
        for i, r in enumerate(new):
            a = day0 + i * HALF
            price = slot_p / 100 if with_slots and in_slot(i / 2, slots) else r["v"]["std"]
            rows.append({"start": _iso(a), "end": _iso(a + HALF), "value_inc_vat": round(price, 5), "is_capped": False})
        return rows

    def solcast(offset: int, scale: float) -> tuple[str, dict]:
        base = (
            states[roles["solar_forecast_today"]]["attributes"] if roles.get("solar_forecast_today") in states else {}
        )
        day0 = midnight.replace(second=0) + timedelta(days=offset)
        det, hourly, total, t10, t90, inter = [], [], 0.0, 0.0, 0.0, []
        for i in range(48):
            kw = solar_kw(i / 2 + 0.25, peak_kw) * scale
            a = day0 + i * HALF
            e10, e90 = round(kw * 0.9, 4), round(kw * 1.05, 4)
            det.append(
                {"period_start": _iso(a), "pv_estimate": round(kw, 4), "pv_estimate10": e10, "pv_estimate90": e90}
            )
            total, t10, t90 = total + kw * 0.5, t10 + e10 * 0.5, t90 + e90 * 0.5
            spread = round((e90 - e10) * 0.5, 4)
            inter.append(
                {
                    "period_start": _iso(a),
                    "spread_kwh": spread,
                    "confidence": 1.0 if kw <= 0 else round(max(0.0, 1 - spread / max(kw * 0.5, 0.05)), 4),
                }
            )
        for h in range(24):
            pair = det[2 * h : 2 * h + 2]
            hourly.append(
                {
                    "period_start": pair[0]["period_start"],
                    **{
                        k: round((pair[0][k] + pair[1][k]) / 2, 4)
                        for k in ("pv_estimate", "pv_estimate10", "pv_estimate90")
                    },
                }
            )
        attrs = {
            **{k: v for k, v in base.items() if k not in ("detailedForecast", "detailedHourly", "analysis", "dayname")},
            "estimate": round(total, 4),
            "estimate10": round(t10, 4),
            "estimate90": round(t90, 4),
            "dayname": (day0).strftime("%A"),
            "analysis": {
                "estimate10_kwh": round(t10, 4),
                "estimate90_kwh": round(t90, 4),
                "spread_kwh": round(t90 - t10, 4),
                "confidence": round(sum(x["confidence"] for x in inter) / 48, 4),
                "intervals": inter,
            },
            "detailedForecast": det,
            "detailedHourly": hourly,
        }
        for k in list(attrs):
            if k.startswith("estimate") and k.endswith("_0382_d0fa_324a_2586") or k == "0382_d0fa_324a_2586":
                attrs[k] = round(t10 if "10" in k else t90 if "90" in k else total, 4)
        return str(round(total, 4)), attrs

    for role, off, sc in (
        ("solar_forecast_today", 0, 1.0),
        ("solar_forecast_tomorrow", 1, tomorrow_scale),
        ("solar_forecast_day3", 2, tomorrow_scale),
    ):
        eid = roles.get(role)
        if eid in states:
            st, at = solcast(off, sc)
            states[eid] = {"state": st, "attributes": at}
    for role, offset, with_slots in (("import_rates_today", 0, True), ("import_rates_tomorrow", 1, False)):
        eid = roles.get(role)
        if eid in states:
            rows = day_rates(offset, with_slots)
            prices = [x["value_inc_vat"] for x in rows]
            at = {
                **states[eid]["attributes"],
                "rates": rows,
                "min_rate": min(prices),
                "max_rate": max(prices),
                "average_rate": round(sum(prices) / len(prices), 6),
            }
            states[eid] = {"state": _iso(midnight), "attributes": at}
    slot_windows = [
        (
            midnight.replace(hour=0, minute=0, second=0) + timedelta(hours=a),
            midnight.replace(hour=0, minute=0, second=0) + timedelta(hours=b),
        )
        for a, b in slots
    ]
    seen_at = _iso((midnight - timedelta(days=1)).replace(hour=18, minute=0, second=0))
    first["first_seen"] = {
        _iso(w0 + k * HALF): seen_at for w0, w1 in slot_windows for k in range(int((w1 - w0) / HALF))
    }

    def dispatch_state(now: datetime) -> tuple[str, dict]:
        planned = [
            {
                "start": _iso(a),
                "end": _iso(b),
                "charge_in_kwh": -1.0 * (b - a).total_seconds() / 3600,
                "source": "SMART",
            }
            for a, b in slot_windows
            if b > now
        ]
        cur = next(((a, b) for a, b in slot_windows if a <= now < b), None)
        nxt = next(((a, b) for a, b in slot_windows if a > now), None)
        base = states[roles["smart_dispatches"]]["attributes"] if roles.get("smart_dispatches") in states else {}
        return ("on" if cur else "off"), {
            **base,
            "planned_dispatches": planned,
            "completed_dispatches": [],
            "started_dispatches": [],
            "current_start": _iso(cur[0]) if cur else None,
            "current_end": _iso(cur[1]) if cur else None,
            "next_start": _iso(nxt[0]) if nxt else None,
            "next_end": _iso(nxt[1]) if nxt else None,
        }

    if roles.get("smart_dispatches") in states:
        st, at = dispatch_state(midnight)
        states[roles["smart_dispatches"]] = {"state": st, "attributes": at}

    today_rows = day_rates(0, True)

    def rate_state(now: datetime) -> dict | None:
        """The current-rate sensor: the price now, over the run of equal-priced half-hours around now."""
        eid = roles.get("import_rate_now")
        if eid not in states:
            return None
        k = next(
            n
            for n, x in enumerate(today_rows)
            if datetime.fromisoformat(x["start"]) <= now < datetime.fromisoformat(x["end"])
        )
        lo = hi = k
        while lo > 0 and today_rows[lo - 1]["value_inc_vat"] == today_rows[k]["value_inc_vat"]:
            lo -= 1
        while hi < 47 and today_rows[hi + 1]["value_inc_vat"] == today_rows[k]["value_inc_vat"]:
            hi += 1
        return {
            "state": str(today_rows[k]["value_inc_vat"]),
            "attributes": {**states[eid]["attributes"], "start": today_rows[lo]["start"], "end": today_rows[hi]["end"]},
        }

    first["at"] = _iso(midnight)
    rs = rate_state(midnight)
    if rs:
        states[roles["import_rate_now"]] = rs
    entries = [first]
    changes = [
        datetime.fromisoformat(today_rows[k]["start"])
        for k in range(1, 48)
        if today_rows[k]["value_inc_vat"] != today_rows[k - 1]["value_inc_vat"]
    ]
    for t in sorted(set(changes) | {w for pair in slot_windows for w in pair}):
        if t <= midnight:
            continue
        s_ = {}
        rs = rate_state(t + timedelta(seconds=1))
        if rs:
            s_[roles["import_rate_now"]] = rs
        if roles.get("smart_dispatches") in states:
            st, at = dispatch_state(t + timedelta(seconds=1))
            s_[roles["smart_dispatches"]] = {"state": st, "attributes": at}
        entries.append({"at": _iso(t + timedelta(seconds=1)), "states": s_})
    snap_new = {**snap, "day": new_day, "entries": entries, "synthetic": True}

    (out / "costs" / "snapshots").mkdir(parents=True, exist_ok=True)
    (out / "costs" / f"{new_day}.json").write_text(json.dumps(new, separators=(",", ":")), encoding="utf-8")
    (out / "costs" / "snapshots" / f"{new_day}.json").write_text(
        json.dumps(snap_new, separators=(",", ":")), encoding="utf-8"
    )
    kwh = sum(r["solar"] for r in new)
    return {
        "day": new_day,
        "solar_kwh": round(kwh, 2),
        "house_kwh": round(sum(r["house"] for r in new), 2),
        "peak_kw": max(r["solar"] for r in new) * 2,
        "slot_half_hours": sum(1 for r in new if r["v"]["slot"]),
        "entries": len(entries),
        "out": str(out),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["sunny"])
    ap.add_argument(
        "--from", dest="template", default="2026-10-08", help="a real day to take the house load and prices from"
    )
    ap.add_argument(
        "--date", default="2026-09-03", help="the synthetic day's date (same weekday as --from, not in the archive)"
    )
    ap.add_argument("--data", default=str(workspace.default_data()))
    ap.add_argument("--out", default=str(workspace.default_work() / "synthetic"))
    ap.add_argument("--peak-kw", type=float, default=4.0)
    ap.add_argument("--slots", default="09:00-12:00,13:00-16:00", help="smart slot windows, local time")
    ap.add_argument("--slot-p", type=float, default=6.66, help="smart slot price, p/kWh")
    ap.add_argument(
        "--tomorrow-scale", type=float, default=1.0, help="the next two days' forecast as a fraction of today's"
    )
    a = ap.parse_args(argv)
    info = make(
        a.template, a.date, Path(a.data), Path(a.out), a.peak_kw, parse_slots(a.slots), a.slot_p, a.tomorrow_scale
    )
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
