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
CHEAP_BAND = 1.15       # a half-hour within 15% of the day's lowest rate counts as that day's cheap rate. EDF priced
                        # its smart slots at the new 6.66p while that night still ran at the old 6.99p (30 Sep 2026):
                        # with an exact match the night dropped out of the day's cheap set, the intersection emptied
                        # the overnight window, and deep overnight selling stopped.
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
        out[day] = {k for k, v in vals.items() if v <= lo * CHEAP_BAND + EPS}
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


def fixed_window(start_h: float, end_h: float) -> set[int]:
    """The half-hours (0..47) of a window the owner typed in: hours since midnight in half-hour steps, so 23.5 is 23:30
    and 5.5 is 05:30. The end is when it stops (05:30 means the 05:00 half-hour is the last). A start after the end
    runs over midnight (23.5 to 5.5 is 23:30 to 05:30); 0 to 24 is the whole day. Equal times, or a value that is not
    a half-hour step in 0..24, make an empty window."""
    s, e = round(start_h * 2), round(end_h * 2)
    if abs(start_h * 2 - s) > 1e-6 or abs(end_h * 2 - e) > 1e-6 or not (0 <= s <= 48 and 0 <= e <= 48) or s == e:
        return set()
    if e < s:
        e += 48
    return {t % 48 for t in range(s, e)}


def chosen_window(history: dict[str, list[int]] | dict[str, set[int]], fixed: set[int] | None = None) -> set[int]:
    """The overnight window in use: the owner's fixed one when they set it, else the one learned from the rates."""
    return set(fixed) if fixed else overnight_window(history)


def describe_window(window: set[int]) -> str:
    """'23:30–05:30' for a window of half-hours (several ranges are joined with ', '); 'none yet' when empty."""
    if not window:
        return "none yet"
    if len(window) >= 48:
        return "all day"

    def hhmm(x: int) -> str:
        return f"{x // 2:02d}:{30 * (x % 2):02d}"
    runs = []
    for start in sorted(w for w in window if (w - 1) % 48 not in window):
        end = start
        while (end + 1) % 48 in window:
            end = (end + 1) % 48
        runs.append(f"{hhmm(start)}–{hhmm((end + 1) % 48)}")
    return ", ".join(runs)


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


BAND = 0.2      # "cheap" = within the bottom fifth of the day's price range


def cheap_threshold(prices: list[float | None], cap_p: float, rte: float = 0.9, wear_p: float = 2.0) -> float:
    """The price (p/kWh) at or below which import counts as cheap, worked out from the prices ahead.

    The lowest of:
      - the bottom fifth of the price range (so only genuinely cheap slots count, however the tariff is shaped);
      - what stored energy is worth later: the average of the other prices x round-trip efficiency, less battery
        wear (so buying is never 'cheap' if storing it can't pay);
      - the configured maximum (cap_p).
    A flat tariff has nothing cheap. Free or negative prices always count as cheap.
    """
    ps = [p * 100 for p in prices if p is not None]
    if not ps:
        return cap_p
    lo, hi = min(ps), max(ps)
    if hi - lo < 1.0:                                        # flat: storing gains nothing
        return 0.0 if lo <= 0 else lo - 0.01
    band = lo + BAND * (hi - lo)
    rest = [p for p in ps if p > band]
    worth = (sum(rest) / len(rest)) * rte - wear_p if rest else band
    t = min(band, worth, cap_p)
    if lo <= 0:
        return round(max(t, 0.0), 2)
    return round(t, 2) if t >= lo else round(lo - 0.01, 2)      # below the cheapest price: nothing is cheap
