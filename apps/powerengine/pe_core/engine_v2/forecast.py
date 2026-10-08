"""Engine v2, layer 2: the forecast (docs/plans/engine-v2.md section 5, build plan "A: forecast and value").

`build` describes the next 48 hours as segments: stretches of time in which prices, events and the car are
constant and the sun and the house change little. Each segment carries a low / middle / high figure for the sun and the
house, and a smart slot is kept as two outcomes (the slot happens at its price, or does not and the standard price
applies) with a probability: it is never blended into one price.

The half-hourly data assembly (prices, estimated prices beyond the published ones, the sun, the house profile) is v1's
`forecast.build_slots` (shared data, not v1's planning); this module cuts it at the real minutes of dispatches, grid
events and free-power sessions and turns it into pence per kWh.

`learned` (the Learner's state) is read where present and ignored where not. Accepted shapes:

* solar weights: `learned["solar_weights"]` or `learned["weights"]["solar"]`, `{"morning"|"midday"|"afternoon":
  [low, mid, high]}` (morning before 10:00, midday 10:00 to 14:00, afternoon after);
* load weights: `learned["load_weights"]` or `learned["weights"]["load"]`, `[low, mid, high]`;
* solar bias: `learned["solar_bias"]`, either `{"by_hour": {hour: ratio}}` or `{hour: ratio}` (local hour; 0.5 to 1.5),
  used only when `learn_solar_bias` is on;
* load spread: `learned["load_spread"]`, `{half_hour_of_day: (p20, p80)}`, the residual of the actual house load around
  the profile in kWh per half-hour (so p20 is usually negative).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .. import decide as v1d
from ..forecast import SLOT, _solar_points, build_slots, slot_start
from ..names import N
from ..parsing import Window
from ..tariff import tod
from .settings import V2Settings
from .types import CHARGE, EXPORT, HOLD, SELF_USE, Forecast, Segment, Spread, StepInput

LOW_FACTOR, HIGH_FACTOR = 0.7, 1.2          # sun band when the forecast gives none (of the middle figure)
LOAD_LOW_FACTOR, LOAD_HIGH_FACTOR = 0.8, 1.3  # house band when nothing is learned (of the profile)
FALLBACK_IMPORT_P = 30.0                    # no price at all: assume dear, never cheap
BIAS_RANGE = (0.5, 1.5)
HORIZON_H = 48          # always solved this far (prices past the last published ones are estimated)
DISPLAY_H = 36          # what the timeline shows: the last hours before the end of the look-ahead are where the
                        # value of the energy left over bends the plan, so they are solved but not shown (0.9.122)
_OVERRIDE_MODE = {v1d.SELF_USE: SELF_USE, v1d.HOLD: HOLD, v1d.GRID_CHARGE: CHARGE, v1d.EXPORT: EXPORT}


def part_of_day(local_hour: int) -> str:
    return "morning" if local_hour < 10 else "midday" if local_hour < 14 else "afternoon"


def _weights3(v, default: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        a, b, c = (float(x) for x in v)
    except (TypeError, ValueError):
        return default
    t = a + b + c
    return (a / t, b / t, c / t) if t > 0 else default


def _learned_weights(learned: dict | None, key: str, part: str = ""):
    """The learned weights for "solar" (for a part of the day) or "load", or None."""
    if not learned:
        return None
    src = learned.get(f"{key}_weights")
    if src is None:
        src = (learned.get("weights") or {}).get(key)
    if key == "solar":
        return src.get(part) if isinstance(src, dict) else None
    return src if isinstance(src, (list, tuple)) else None


def _bias(learned: dict | None, hour: int) -> float:
    b = (learned or {}).get("solar_bias")
    if not isinstance(b, dict):
        return 1.0
    table = b.get("by_hour", b)
    v = table.get(hour, table.get(str(hour))) if isinstance(table, dict) else None
    try:
        return min(BIAS_RANGE[1], max(BIAS_RANGE[0], float(v)))
    except (TypeError, ValueError):
        return 1.0


def _spread_of_load(learned: dict | None, hh: int):
    t = (learned or {}).get("load_spread")
    if not isinstance(t, dict):
        return None
    v = t.get(hh, t.get(str(hh)))
    try:
        return float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError):
        return None


def _local(t: datetime, tz) -> datetime:
    return t.astimezone(tz) if tz else t


def _hhmm(t: datetime, tz) -> str:
    return f"{_local(t, tz):%H:%M}"


def _in(w: Window | None, t: datetime) -> bool:
    return w is not None and w.start <= t < w.end


def _day_max(rates, t: datetime, tz) -> float | None:
    d = _local(t, tz).date()
    vals = [w.value for w in rates if w.value is not None and _local(w.start, tz).date() == d]
    return max(vals) if vals else None


def _cuts(now: datetime, end: datetime, windows: list[Window], max_min: float) -> list[datetime]:
    """Segment boundaries: now, every half-hour, the exact ends of every window, split to at most max_min."""
    pts = {now, end}
    t = slot_start(now) + SLOT
    while t < end:
        pts.add(t)
        t += SLOT
    for w in windows:
        for t in (w.start, w.end):
            if now < t < end:
                pts.add(t)
    ordered = sorted(pts)
    out = [ordered[0]]
    for a, b in zip(ordered, ordered[1:], strict=False):
        minutes = (b - a).total_seconds() / 60
        n = max(1, math.ceil(minutes / max_min - 1e-9))
        out += [a + (b - a) * i / n for i in range(1, n)] + [b]
    return out


def build(inp: StepInput, settings: V2Settings, learned: dict | None = None) -> Forecast:
    r, tz, now = inp.readings, inp.tz, inp.now
    facts = inp.facts
    tz_ = tz or timezone.utc
    slots = build_slots(r, inp.solar_points, inp.load_profile, tz, horizon_h=HORIZON_H, min_h=HORIZON_H,
                        certainty=None, first_seen=None, overnight=inp.overnight, whole_house=True)
    if not slots:
        return Forecast(made_at=now, segments=(), notes=("No prices are known yet.",))
    by_start = {s.start: s for s in slots}
    end = slots[-1].end
    sun = {}
    for pt in _solar_points(inp.solar_points):
        mid = pt.kwh
        lo = pt.low_kwh if pt.low_kwh is not None else LOW_FACTOR * mid
        hi = pt.high_kwh if pt.high_kwh is not None else HIGH_FACTOR * mid
        sun[slot_start(pt.start)] = (lo, mid, hi)

    axle = Window(r.axle_start, r.axle_end) if r.axle_start and r.axle_end else None
    free = Window(r.free_start, r.free_end) if r.free_start and r.free_end else None
    dispatches = sorted(r.dispatches, key=lambda w: w.start)
    windows = list(dispatches) + [w for w in (axle, free) if w]
    cuts = _cuts(now, end, windows, settings.max_segment_min)

    rates = sorted(r.rates, key=lambda w: w.start)
    hh_end = slot_start(now) + SLOT
    car_now = r.ev_state() == "charging"
    car_kw = (r.ev_power / 1000 if r.ev_power else facts.ev_charger_kw) if car_now else 0.0
    ov = inp.situation.override
    ov_mode = _OVERRIDE_MODE.get(getattr(ov, "mode", None)) if ov is not None else None
    ov_until = getattr(ov, "until", None)
    export_default = (r.export_rate or 0.0) * 100

    segments: list[Segment] = []
    first_estimated = None
    for a, b in zip(cuts, cuts[1:], strict=False):
        mid_t = a + (b - a) / 2
        hs = slot_start(a)
        slot = by_start.get(hs)
        if slot is None:
            continue
        frac = (b - a) / SLOT
        local = _local(a, tz_)
        hh = tod(a, tz_)

        # prices
        price = slot.price * 100 if slot.price is not None else None
        if price is None:
            price = r.import_rate * 100 if r.import_rate is not None else FALLBACK_IMPORT_P
        export_p = slot.export * 100 if slot.export is not None else export_default
        disp = next((w for w in dispatches if _in(w, mid_t)), None)
        flagged = slot.smart_slot and not slot.overnight
        std = _day_max(rates, a, tz_)
        std = std * 100 if std is not None else price
        import_p, slot_import, prob = price, None, None
        if flagged and price < std - 1e-6:
            import_p = std
            if disp is not None and inp.slots_whole_house:
                slot_import = price
                if disp.start <= now:
                    prob = 1.0
                else:
                    seen = (inp.slot_first_seen or {}).get(disp.start.isoformat())
                    try:
                        seen_t = datetime.fromisoformat(seen) if seen else now
                    except ValueError:
                        seen_t = now
                    prob = inp.slot_certainty(disp.start, seen_t) if inp.slot_certainty else 1.0
                    prob = min(1.0, max(0.0, float(prob)))
        is_event = bool(settings.events and _in(axle, mid_t))
        is_free = bool(settings.free_power and _in(free, mid_t))
        if is_free:
            import_p, slot_import, prob = 0.0, None, None
        event_p = settings.event_value_p + (export_p if settings.event_plus_export else 0.0) if is_event else 0.0

        # sun
        lo, md, hi = sun.get(hs, (0.0, 0.0, 0.0))
        k = frac * (_bias(learned, local.hour) if settings.learn_solar_bias else 1.0)
        sw = _learned_weights(learned, "solar", part_of_day(local.hour))
        solar = Spread(lo * k, md * k, hi * k, _weights3(sw, settings.solar_weights) if sw else settings.solar_weights)

        # house
        base = slot.load_kwh
        sp = _spread_of_load(learned, hh)
        if sp is not None:
            l_lo, l_hi = max(0.0, base + sp[0]), max(0.0, base + sp[1])
        else:
            l_lo, l_hi = LOAD_LOW_FACTOR * base, LOAD_HIGH_FACTOR * base
        lw = _learned_weights(learned, "load")
        load = Spread(min(l_lo, base) * frac, base * frac, max(l_hi, base) * frac,
                      _weights3(lw, settings.load_weights) if lw else settings.load_weights)

        manual = None
        if ov_mode and not is_event and (ov_until is None or a < ov_until):
            manual = ov_mode
        if slot.price_estimated and first_estimated is None:
            first_estimated = a
        segments.append(Segment(
            start=a, end=b, import_p=import_p, export_p=export_p, solar_kwh=solar, load_kwh=load,
            slot_prob=prob, slot_import_p=slot_import, event=is_event, event_p=event_p, free=is_free,
            car_kw=car_kw if (car_now and a < hh_end) else 0.0, overnight=slot.overnight,
            price_estimated=slot.price_estimated, charge_factor=facts.charge_factor, manual=manual))

    notes = []
    if first_estimated is not None:
        day = _local(first_estimated, tz_)
        notes.append(f"Prices after {day:%a %H:%M} are estimated from the same time yesterday.")
    if not sun:
        notes.append("There is no solar forecast: the sun is taken as none.")
    if not (inp.load_profile and inp.load_profile.watts):
        notes.append("There is no house load history yet: the house is taken as a flat guess.")
    if any(sg.slot_prob is not None and sg.slot_prob < 1 for sg in segments):
        notes.append(f"{N('dispatch')}s that have not started are counted as a chance, not a certainty.")
    return Forecast(made_at=now, segments=tuple(segments), notes=tuple(notes))


__all__ = ["build", "part_of_day"]
