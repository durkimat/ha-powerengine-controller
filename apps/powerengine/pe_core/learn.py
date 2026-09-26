"""Limits learned from real use, and the cold-battery caution.

Every recorded half-hour carries what PowerEngine asked the inverter to do (``cmd``: the action held for the whole
half-hour, with ``cmd_kw``, the power asked for) and the outside temperature (``temp_c``) with the estimated battery
temperature (``tb_c``). From those:

- **Charge / discharge rate:** the median power actually reached when the full rate was asked for (below 90% charge,
  and not while the battery was cold). Before any such half-hours exist, the 98th percentile of all half-hours.
- **Charge taper:** how much of that rate is reached from 90% and from 95% charge.
- **Reserve:** the charge at which the battery stops supplying the house even though the house needs power.
- **Export limit:** only when selling hits a ceiling below the battery's own rate (the grid limit, not the battery).
- **Car charge rate:** the typical kW of a half-hour the car charged throughout.
- **Cold:** the battery-temperature threshold below which charging slows, and by how much.

The battery temperature is estimated from the outside temperature: it follows it with a lag (about a day in a
garage), so a cold spell chills it slowly and it stays cold until the weather has been warmer for a while.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

MIN_RATE_SAMPLES = 6
MIN_FALLBACK_SAMPLES = 20
MIN_TAPER_SAMPLES = 3
MIN_RESERVE_SAMPLES = 4
MIN_CAR_SAMPLES = 4
MIN_COLD_SAMPLES = 3
SLOW_RATIO = 0.8            # a charge half-hour at under 80% of the normal rate counts as slowed
FULL_ASK = 0.9              # "the full rate was asked for": at least 90% of the configured rate
TAPER_BANDS = (90.0, 95.0)


def _kw(kwh: float | None, seconds: float) -> float:
    return (kwh or 0.0) * 3600 / seconds if seconds else 0.0


def _full(h: dict) -> bool:
    return (h.get("seconds") or 0) >= 1500 and h.get("soc_start") is not None and h.get("soc_end") is not None


def _p(values: list[float], q: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def _asked_full(h: dict, action: str, rated_kw: float) -> bool:
    return h.get("cmd") == action and (h.get("cmd_kw") or 0.0) >= FULL_ASK * rated_kw


@dataclass
class ColdSettings:
    threshold_c: float = 4.0        # caution below this battery temperature (estimated)
    factor: float = 0.5             # charge rate while cautious
    release_c: float = 3.0          # stay cautious until the battery is this much warmer than the threshold
    lag_h: float = 24.0             # how long the battery takes to follow the outside temperature (time constant)


@dataclass
class Learned:
    max_charge_kw: float | None = None
    max_discharge_kw: float | None = None
    charge_samples: int = 0
    discharge_samples: int = 0
    taper: tuple[tuple[float, float], ...] = ()      # ((soc_from, fraction of the rate), ...)
    taper_samples: int = 0
    reserve_soc: float | None = None
    reserve_samples: int = 0
    export_kw: float | None = None
    export_samples: int = 0
    car_kw: float | None = None
    car_samples: int = 0
    cold_threshold_c: float | None = None
    cold_factor: float | None = None
    cold_slow: int = 0              # slowed charge half-hours seen (while cold)
    cold_fast: int = 0              # full-rate charge half-hours seen below the configured threshold
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (list(map(list, v)) if k == "taper" else v) for k, v in self.__dict__.items()}


def learn(halves: list[dict], rated_charge_kw: float, rated_discharge_kw: float, reserve_soc: float,
          export_limit_kw: float, cold: ColdSettings) -> Learned:
    """Everything that can be learned from these recorded half-hours (oldest first)."""
    out = Learned()
    full = [h for h in halves if _full(h)]

    def warm(h: dict) -> bool:
        tb = h.get("tb_c")
        return tb is None or tb >= cold.threshold_c + cold.release_c

    # --- charge rate: full rate asked, below the taper zone, battery warm
    asked = [_kw(h.get("battery_in"), h["seconds"]) for h in full
             if _asked_full(h, "grid_charge", rated_charge_kw) and h["soc_end"] < TAPER_BANDS[0] and warm(h)]
    asked = [v for v in asked if v > 0.1]
    if len(asked) >= MIN_RATE_SAMPLES:
        out.max_charge_kw, out.charge_samples = round(median(asked), 2), len(asked)
    else:
        seen = [_kw(h.get("battery_in"), h["seconds"]) for h in full if h["soc_end"] < TAPER_BANDS[0]]
        seen = [v for v in seen if v > 0.1]
        if len(seen) >= MIN_FALLBACK_SAMPLES:
            out.max_charge_kw, out.charge_samples = round(_p(seen, 0.98), 2), len(seen)
    # --- discharge rate: full rate asked (selling), well above the reserve
    asked = [_kw(h.get("battery_out"), h["seconds"]) for h in full
             if _asked_full(h, "export", rated_discharge_kw) and h["soc_end"] > reserve_soc + 5]
    asked = [v for v in asked if v > 0.1]
    if len(asked) >= MIN_RATE_SAMPLES:
        out.max_discharge_kw, out.discharge_samples = round(median(asked), 2), len(asked)
    else:
        seen = [_kw(h.get("battery_out"), h["seconds"]) for h in full if h["soc_end"] > reserve_soc + 5]
        seen = [v for v in seen if v > 0.1]
        if len(seen) >= MIN_FALLBACK_SAMPLES:
            out.max_discharge_kw, out.discharge_samples = round(_p(seen, 0.98), 2), len(seen)

    base = out.max_charge_kw or rated_charge_kw
    # --- taper: fraction of the normal rate reached when starting a half-hour at 90-95% and 95%+
    bands = []
    for i, lo in enumerate(TAPER_BANDS):
        hi = TAPER_BANDS[i + 1] if i + 1 < len(TAPER_BANDS) else 100.0
        v = [_kw(h.get("battery_in"), h["seconds"]) / base for h in full
             if _asked_full(h, "grid_charge", rated_charge_kw) and lo <= h["soc_start"] < hi and warm(h)
             and h["soc_end"] < 99.5]                     # still charging at the end: the rate wasn't cut short
        if len(v) >= MIN_TAPER_SAMPLES:
            bands.append((lo, round(min(1.0, max(0.1, median(v))), 2)))
            out.taper_samples += len(v)
    out.taper = tuple(bands)

    # --- reserve: the house needed power, the battery gave (almost) none, near the bottom, not held on purpose
    stops = [h["soc_end"] for h in full
             if (h.get("battery_out") or 0.0) < 0.05 and (h.get("battery_in") or 0.0) < 0.05
             and (h.get("house") or 0.0) - (h.get("solar") or 0.0) > 0.15
             and h["soc_end"] <= reserve_soc + 15 and h.get("cmd") in (None, "self_use")]
    if len(stops) >= MIN_RESERVE_SAMPLES:
        out.reserve_soc, out.reserve_samples = round(median(stops), 1), len(stops)

    # --- export limit: selling at the full rate but the grid export tops out below the battery's own rate
    sells = [_kw(h.get("grid_export"), h["seconds"]) for h in full if _asked_full(h, "export", rated_discharge_kw)]
    if len(sells) >= MIN_RATE_SAMPLES:
        top = round(_p(sells, 0.98), 2)
        battery_top = out.max_discharge_kw or rated_discharge_kw
        if top < export_limit_kw - 0.3 and top < battery_top - 0.3:
            out.export_kw, out.export_samples = top, len(sells)

    # --- car: half-hours the car charged throughout (over 1 kW on average)
    car = [_kw(h.get("car"), h["seconds"]) for h in full]
    car = [v for v in car if v > 1.0]
    if len(car) >= MIN_CAR_SAMPLES:
        out.car_kw, out.car_samples = round(_p(car, 0.9), 2), len(car)

    # --- cold: charge half-hours at the full rate asked, below the taper zone, with a battery temperature estimate
    samples = [(h["tb_c"], _kw(h.get("battery_in"), h["seconds"]) / base) for h in full
               if _asked_full(h, "grid_charge", rated_charge_kw) and h["soc_end"] < TAPER_BANDS[0]
               and h.get("tb_c") is not None]
    slow = [(t, r) for t, r in samples if r < SLOW_RATIO and t < cold.threshold_c + 8]
    fast = [t for t, r in samples if r >= SLOW_RATIO and t < cold.threshold_c]
    out.cold_slow, out.cold_fast = len(slow), len(fast)
    if len(slow) >= MIN_COLD_SAMPLES:
        # slowed at these temperatures: be cautious up to just above the warmest of them
        out.cold_threshold_c = round(min(cold.threshold_c + 8, max(t for t, _ in slow) + 0.5), 1)
        out.cold_factor = round(min(0.9, max(0.2, median(r for _, r in slow))), 2)
    elif len(fast) >= MIN_COLD_SAMPLES:
        # charged normally below the threshold: only be cautious below the coldest temperature proven fine
        out.cold_threshold_c = round(max(cold.threshold_c - 8, min(fast)), 1)
    return out


# --- battery temperature and caution ---------------------------------------------------------------------------

def battery_temps(outside: dict[datetime, float], lag_h: float) -> dict[datetime, float]:
    """Estimated battery temperature each hour: it follows the outside temperature with a time constant of lag_h.

    Starts from the average of the first day (so the estimate is sensible after a restart with 3 days of history).
    """
    hours = sorted(outside)
    if not hours:
        return {}
    first_day = [outside[h] for h in hours[:24]]
    tb = sum(first_day) / len(first_day)
    out: dict[datetime, float] = {}
    prev = hours[0]
    for h in hours:
        dt = (h - prev).total_seconds() / 3600
        k = 1 - math.exp(-dt / lag_h) if lag_h > 0 else 1.0
        tb += (outside[h] - tb) * k
        out[h] = round(tb, 2)
        prev = h
    return out


def caution_by_hour(tb: dict[datetime, float], threshold_c: float, release_c: float) -> dict[datetime, bool]:
    """Cautious from when the battery falls below the threshold until it is back above threshold + release."""
    on = False
    out = {}
    for h in sorted(tb):
        if tb[h] < threshold_c:
            on = True
        elif tb[h] > threshold_c + release_c:
            on = False
        out[h] = on
    return out


def hour_of(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def slot_factors(starts: list[datetime], caution: dict[datetime, bool], factor: float) -> list[float]:
    """Charge-rate factor per slot start (1.0 when not cautious or no temperature known)."""
    return [factor if caution.get(hour_of(t)) else 1.0 for t in starts]


def cold_summary(starts: list[datetime], factors: list[float], tb: dict[datetime, float], now: datetime,
                 threshold_c: float, factor: float, learned: bool, tz=None) -> dict:
    """For the plan and the Health tab: estimated battery temperature now and the cautious periods ahead."""
    periods: list[list[datetime]] = []
    for t, f in zip(starts, factors, strict=False):
        if f < 1.0:
            if periods and periods[-1][1] == t:
                periods[-1][1] = t + timedelta(minutes=30)
            else:
                periods.append([t, t + timedelta(minutes=30)])

    def fmt(t: datetime) -> str:
        loc = t.astimezone(tz) if tz else t
        return loc.strftime("%a %H:%M")
    return {"battery_c_now": tb.get(hour_of(now)), "threshold_c": threshold_c, "factor": factor,
            "learned": learned, "periods": [{"from": fmt(a), "to": fmt(b)} for a, b in periods],
            "active_now": bool(factors) and factors[0] < 1.0}
