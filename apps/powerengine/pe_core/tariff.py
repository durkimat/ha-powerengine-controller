"""Rates for cost accounting: actual, standard (no smart slot), overnight and peak.

EDF's rate list already prices a smart-charge slot at the cheap rate inside the peak rate. To value a slot we need
the *standard* rate: what that half-hour would have cost without the slot.

- The overnight rate is the day's lowest rate; the peak rate is its highest.
- The advertised overnight window is the set of half-hours (by time of day) that are at the lowest rate on *every*
  day seen so far. Smart slots move from day to day, so they drop out of that intersection.
- A cheap half-hour outside that window is a smart slot: its standard rate is the peak rate. Everything else's
  standard rate is its actual rate.

With only one day seen, a slot at the same time as the window can't be told apart; that errs towards a smaller
smart-charge saving, never a larger one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .readings import Window

EPS = 1e-6
HALF = timedelta(minutes=30)


def tod(t: datetime, tz=None) -> int:
    """Half-hour of the local day, 0..47."""
    lt = t.astimezone(tz) if tz else t
    return lt.hour * 2 + (1 if lt.minute >= 30 else 0)


def cheap_tods(rates: list[Window], tz=None) -> dict[str, set[int]]:
    """{local date iso: half-hours at that day's minimum rate}, only for days with a full set of rates."""
    by_day: dict[str, dict[int, float]] = {}
    for w in rates:
        if w.value is None:
            continue
        t = w.start
        while t < w.end:
            lt = t.astimezone(tz) if tz else t
            by_day.setdefault(lt.date().isoformat(), {})[tod(t, tz)] = w.value
            t += HALF
    out = {}
    for day, vals in by_day.items():
        if len(vals) < 46:                      # a partial list (or a DST day's odd count) can't define the window
            continue
        lo = min(vals.values())
        out[day] = {k for k, v in vals.items() if v <= lo + EPS}
    return out


def overnight_window(history: dict[str, list[int]] | dict[str, set[int]]) -> set[int]:
    """Intersection of the cheap half-hours over the days seen."""
    sets = [set(v) for v in history.values() if v]
    if not sets:
        return set()
    out = sets[0]
    for s in sets[1:]:
        out &= s
    return out


@dataclass(frozen=True)
class Rates:
    actual: float           # GBP/kWh charged
    standard: float         # without a smart slot
    overnight: float        # normal overnight rate
    export: float
    smart_slot: bool
    peak: float | None = None     # the day's highest rate


def rates_at(t: datetime, rates: list[Window], window: set[int], export: float, fallback: float | None,
             tz=None) -> Rates | None:
    """Rates for the half-hour starting at `t`, or None if no import rate is known."""
    lt = t.astimezone(tz) if tz else t
    day = [w for w in rates if w.value is not None and (w.start.astimezone(tz) if tz else w.start).date() == lt.date()]
    actual = next((w.value for w in rates if w.value is not None and w.start <= t < w.end), fallback)
    if actual is None:
        return None
    vals = [w.value for w in day] or [actual]
    lo, hi = min(vals), max(vals)
    in_window = tod(t, tz) in window
    slot = actual < hi - EPS and actual <= lo + EPS and not in_window and bool(window)
    return Rates(actual=actual, standard=hi if slot else actual, overnight=lo, export=export, smart_slot=slot, peak=hi)


def reclassify(start: datetime, v: dict, window: set[int], tz=None) -> Rates:
    """Rates for a stored half-hour, re-deciding 'smart slot' with today's (better) overnight window."""
    act, ovn, peak, exp = v["act"], v["ovn"], v.get("peak") or v["std"], v["exp"]
    slot = bool(window) and act < peak - EPS and act <= ovn + EPS and tod(start, tz) not in window
    return Rates(actual=act, standard=peak if slot else act, overnight=ovn, export=exp, smart_slot=slot, peak=peak)
