"""Half-hourly forecasts for the planner: prices, solar, house load, events.

All slots are 30 minutes, aligned to :00/:30 UTC (which is also UK local time).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .certainty import expected_price
from .readings import Readings, Window, parse_time
from .tariff import cheap_tods, overnight_window, tod

SLOT = timedelta(minutes=30)
DEFAULT_LOAD_W = 500.0          # steady assumption until a load profile exists
HALF_LIFE_DAYS = 7.0            # recency weighting for the load profile


def slot_start(t: datetime) -> datetime:
    t = t.astimezone(timezone.utc)
    return t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)


@dataclass
class Slot:
    start: datetime
    price: float | None                 # import GBP/kWh
    export: float | None                # export GBP/kWh
    solar_kwh: float = 0.0
    load_kwh: float = 0.0               # house only (no car)
    price_estimated: bool = False       # beyond published prices
    smart_slot: bool = False            # planned EDF smart-charge dispatch
    axle: bool = False                  # Axle event in this slot
    free: bool = False                  # free-electricity session
    car_kw: float | None = None         # known car draw (live); None = assume the charger rating in smart slots
    certainty: float | None = None      # smart slot: how likely it really happens (price is then the expected one)
    slot_price: float | None = None     # smart slot: the published slot price, before weighting by certainty
    overnight: bool = False             # in the tariff's fixed overnight window (cheap every day)
    charge_factor: float = 1.0          # cold-battery caution: fraction of the normal charge rate expected

    @property
    def end(self) -> datetime:
        return self.start + SLOT


# --- load profile -----------------------------------------------------------------

@dataclass
class LoadProfile:
    """Expected house-only load (W) by (weekday/weekend, half-hour of day)."""
    watts: dict[tuple[bool, int], float] = field(default_factory=dict)
    days: float = 0.0                   # how much history it's built from

    def expected_w(self, t: datetime, tz) -> float:
        local = t.astimezone(tz) if tz else t
        key = (local.weekday() >= 5, local.hour * 2 + local.minute // 30)
        if key in self.watts:
            return self.watts[key]
        other = (not key[0], key[1])     # fall back to the other day type
        return self.watts.get(other, DEFAULT_LOAD_W)


def half_hour_means(samples: list[tuple[datetime, float]], end: datetime) -> dict[datetime, float]:
    """Time-weighted mean power per half-hour from state-change samples (value holds until next change)."""
    samples = sorted((t.astimezone(timezone.utc), v) for t, v in samples if v is not None)
    if not samples:                                      # e.g. no car charging in the period
        return {}
    energy: dict[datetime, float] = defaultdict(float)   # W*s
    covered: dict[datetime, float] = defaultdict(float)  # s
    for (t0, v), nxt in zip(samples, samples[1:] + [(end, None)], strict=True):
        t1 = min(nxt[0], end)
        while t0 < t1:
            s = slot_start(t0)
            seg_end = min(s + SLOT, t1)
            dt = (seg_end - t0).total_seconds()
            energy[s] += v * dt
            covered[s] += dt
            t0 = seg_end
    return {s: energy[s] / covered[s] for s in energy if covered[s] >= 600}   # need 10 min of data


def house_only_means(house: list[tuple[datetime, float]], car: list[tuple[datetime, float]] | None,
                     now: datetime, subtract_car: bool = True) -> dict[datetime, float]:
    """Half-hour mean house-only load (W) from raw history samples."""
    h = half_hour_means(house, now)
    c = half_hour_means(car or [], now) if subtract_car else {}
    return {s: max(0.0, w - max(0.0, c.get(s, 0.0))) for s, w in h.items()}


def build_load_profile(house: list[tuple[datetime, float]], car: list[tuple[datetime, float]] | None,
                       now: datetime, tz, subtract_car: bool = True) -> LoadProfile:
    """Recency-weighted weekday/weekend half-hour profile of house-only load."""
    return profile_from_means(house_only_means(house, car, now, subtract_car), now, tz)


def profile_from_means(means: dict[datetime, float], now: datetime, tz) -> LoadProfile:
    sums: dict[tuple[bool, int], float] = defaultdict(float)
    weights: dict[tuple[bool, int], float] = defaultdict(float)
    for s, house_only in means.items():
        age_days = (now - s).total_seconds() / 86400
        weight = math.pow(0.5, age_days / HALF_LIFE_DAYS)
        local = s.astimezone(tz) if tz else s
        key = (local.weekday() >= 5, local.hour * 2 + local.minute // 30)
        sums[key] += house_only * weight
        weights[key] += weight
    return LoadProfile({k: sums[k] / weights[k] for k in sums}, len(means) / 48)


def flatten_history(result) -> list[dict]:
    """AppDaemon's get_history result in whatever shape it comes (list of lists, list, or dict) -> rows."""
    if not result:
        return []
    if isinstance(result, dict):
        rows = []
        for v in result.values():
            rows += flatten_history(v)
        return rows
    if isinstance(result, list) and result and all(isinstance(x, list) for x in result):
        return [row for sub in result for row in sub if isinstance(row, dict)]
    return [row for row in result if isinstance(row, dict)]


def parse_history(rows: list | None, unit: str | None = None) -> list[tuple[datetime, float]]:
    """AppDaemon/HA history rows ([{'state', 'last_changed'}...]) -> (time, W).

    `unit` is the entity's unit when the rows carry no attributes (no_attributes=True).
    """
    out = []
    for row in flatten_history(rows):
        unit = (row.get("attributes") or {}).get("unit_of_measurement") or unit   # minimal responses carry it once
        t = parse_time(row.get("last_changed") or row.get("last_updated"))
        try:
            v = float(row.get("state"))
        except (TypeError, ValueError):
            continue
        out.append((t, v * 1000 if unit == "kW" else v))
    return [x for x in out if x[0] is not None]


# --- building the slot list ----------------------------------------------------------

def _in(windows: list[Window], s: datetime) -> bool:
    return any(w.start < s + SLOT and w.end > s for w in windows)


def build_slots(r: Readings, solar: list[dict] | None, profile: LoadProfile | None, tz,
                horizon_h: float = 48, min_h: float = 36, certainty=None,
                first_seen: dict[str, str] | None = None, overnight: set[int] | None = None) -> list[Slot]:
    """Half-hour slots from the current half-hour to the end of known prices (24-48 h).

    `certainty` (a certainty.Certainty) turns each future smart slot's price into an expected price, using when
    the slot was first announced (`first_seen`: slot start iso -> iso) for its group."""
    start = slot_start(r.now)
    rates = sorted(r.rates, key=lambda w: w.start)
    last_known = max((w.end for w in rates), default=start)
    end = min(start + timedelta(hours=horizon_h), max(last_known, start + timedelta(hours=min_h)))

    solar_by_slot: dict[datetime, float] = {}
    for item in solar or []:
        t = parse_time(item.get("period_start"))
        try:
            kw = float(item.get("pv_estimate") or 0.0)
        except (TypeError, ValueError):
            continue
        if t:
            solar_by_slot[slot_start(t)] = kw * 0.5

    axle = [Window(r.axle_start, r.axle_end)] if r.axle_start and r.axle_end else []
    free = [Window(r.free_start, r.free_end)] if r.free_start and r.free_end else []

    window = overnight_window(cheap_tods(rates, tz))

    def day_range(t: datetime) -> tuple[float, float] | None:
        d = (t.astimezone(tz) if tz else t).date()
        vals = [w.value for w in rates
                if w.value is not None and (w.start.astimezone(tz) if tz else w.start).date() == d]
        return (min(vals), max(vals)) if vals else None

    def price_at(s: datetime) -> tuple[float | None, bool]:
        for w in rates:
            if w.start <= s < w.end:
                return w.value, False
        y = s - timedelta(days=1)
        for w in rates:                     # beyond published prices: same time yesterday...
            if w.start <= y < w.end:
                rng = day_range(y)
                if (w.value is not None and rng and window and tod(y, tz) not in window
                        and w.value <= rng[0] + 1e-6 < rng[1]):
                    return rng[1], True     # ...but not a smart slot's cheap price: those move from day to day
                return w.value, True
        return r.import_rate, True

    slots, s = [], start
    while s < end:
        price, est = price_at(s)
        load_w = profile.expected_w(s, tz) if profile and profile.watts else DEFAULT_LOAD_W
        slot = Slot(start=s, price=price, export=r.export_rate, solar_kwh=solar_by_slot.get(s, 0.0),
                    load_kwh=load_w / 1000 * 0.5, price_estimated=est, smart_slot=_in(r.dispatches, s),
                    axle=_in(axle, s), free=_in(free, s), overnight=tod(s, tz) in (overnight or set()))
        if slot.smart_slot and certainty is not None and s > start and price is not None:
            rng = day_range(s)
            std = rng[1] if rng else price
            win = next((w for w in r.dispatches if w.start <= s < w.end), None)
            seen = (first_seen or {}).get(win.start.isoformat()) if win else None
            c = certainty.score(win.start if win else s, datetime.fromisoformat(seen) if seen else r.now)
            slot.certainty, slot.slot_price = c, price
            slot.price = expected_price(price, std, c)
        slots.append(slot)
        s += SLOT
    return slots
