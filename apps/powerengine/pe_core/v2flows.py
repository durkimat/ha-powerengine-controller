"""Engine v2 history: the grid and the car at the time they happened (sensor.pe_v2_flows).

The half-hour records of `v2history` say how much energy moved in each half-hour. This says when: readings taken every
few seconds (a replay of Home Assistant's own history of the grid and car power) averaged into 5-minute buckets. Values
are kWh per half-hour at that power (kW x 0.5), the chart's own scale, so a bar's area is the energy. Quiet buckets
(nothing moving) are left out, which keeps the attributes small; the chart draws a gap as nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

BUCKET_MIN = 5
FLOW_ROLES = ("grid_power", "grid_power_reference", "ev_charge_power", "ev_plug_status")


def real_flows(readings, tz, day: date, bucket_min: int = BUCKET_MIN) -> dict | None:
    """{"date", "start" (local midnight), "bucket" (minutes), "m" (minutes from start), "imp", "exp", "car"}, or None
    when no reading had a grid figure. `readings` have `now`, `grid_power` (W, + importing) and `ev_power` (W)."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    span = bucket_min * 60
    sums: dict[int, list[float]] = {}
    for r in readings:
        if r.grid_power is None:
            continue
        row = sums.setdefault(int((r.now - start).total_seconds() // span), [0, 0.0, 0.0, 0.0])
        row[0] += 1
        row[1] += max(r.grid_power, 0.0) / 1000
        row[2] += max(-r.grid_power, 0.0) / 1000
        row[3] += max(r.ev_power or 0.0, 0.0) / 1000
    if not sums:
        return None
    out = {"date": day.isoformat(), "start": start.astimezone(tz).isoformat(timespec="seconds"), "bucket": bucket_min,
           "m": [], "imp": [], "exp": [], "car": []}
    for k in sorted(sums):
        n = sums[k][0]
        vals = [round(sums[k][i] / n * 0.5, 2) for i in (1, 2, 3)]
        if k < 0 or not any(vals):
            continue
        out["m"].append(k * bucket_min)
        for key, v in zip(("imp", "exp", "car"), vals, strict=True):
            out[key].append(v)
    return out


def day_end(day: date, tz) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
