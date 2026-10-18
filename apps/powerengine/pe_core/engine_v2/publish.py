"""The published sensors of engine v2: states and attributes (the card's contract, docs/plans/engine-v2-build.md).

`entity_states` returns `{publish key: (state, attributes)}` (the entity is `sensor.pe_<key>`); every attribute set is
kept under 15,000 bytes (HA's recorder skips more than 16 KB). Times are ISO 8601 with an offset, prices and values in
pence per kWh to 2 places, levels to 1.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta

from .execute import Executor, idle_action
from .forecast import DISPLAY_H
from .settings import SETTINGS, V2Settings, catalogue, profiles_catalogue
from .types import CHARGE, EXPORT, HOLD, MODE_LABEL, SELF_USE, ModeState, StepOutput, TimelineItem, ValueResult
from .value import _segment_index, lines, value_at

LIMIT = 14_500                       # below the app's 15,000 byte warning
CURVE_PCTS = tuple(range(0, 101, 10))
CURVE_MAX_ROWS = 48
PATH_VALUE_SMOOTH = 2        # points either side averaged in the value along the path (15 min each)
REASON_MAX = 220


def _iso(t: datetime | None, tz=None) -> str | None:
    if t is None:
        return None
    return (t.astimezone(tz) if tz else t).isoformat(timespec="seconds")


def _r(x, n: int = 2):
    return None if x is None else round(float(x), n)


def size(attrs: dict) -> int:
    return len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _fit(attrs: dict, reducers) -> dict:
    """Apply the reducers one after another until the attributes fit under LIMIT."""
    for fn in reducers:
        if size(attrs) <= LIMIT:
            break
        fn(attrs)
    return attrs


def _shown_power_w(m, engine) -> int | None:
    """The power the mode asks for, for the card's "Charging at" / "Exporting at" box. The mode itself carries a power
    only when something caps it (the battery's limit, the cold caution); with no cap the command is the full rate, so
    that is shown (display only: the decision sent to the inverter is unchanged)."""
    if m.power_w is not None:
        return int(round(m.power_w))
    f = getattr(engine, "last_facts", None)
    kw = None
    if f is not None and m.mode in ("charge", "free"):
        kw = getattr(f, "max_charge_kw", None)
    elif f is not None and m.mode in ("export", "event"):
        kw = getattr(f, "max_discharge_kw", None)
    return int(round(kw * 1000)) if kw else None


# ---- the mode ----------------------------------------------------------------------------------
def _mode(out: StepOutput, engine, tz, preview: bool = False) -> tuple[str, dict]:
    m: ModeState = out.mode
    exits = [{"kind": e.kind, "text": e.text, "expected_at": _iso(e.expected_at, tz), "first": False}
             for e in m.exits]
    timed = [(e.expected_at, i) for i, e in enumerate(m.exits) if e.expected_at is not None]
    car_end = next((i for i, e in enumerate(m.exits) if e.kind == "car" and m.mode == "hold"), None)
    if car_end is not None:                     # held for the car: it is the headline, whatever the clock says
        exits[car_end]["first"] = True
    elif timed:
        exits[min(timed)[1]]["first"] = True
    elif exits:
        exits[0]["first"] = True
    vr = engine.vr
    obs = out.observation
    attrs = {"label": MODE_LABEL.get(m.mode, m.mode), "since": _iso(m.since, tz), "why": m.why, "rule": m.rule,
             "chosen_by": m.chosen_by, "target_soc": _r(m.target_soc, 1), "power_w": _shown_power_w(m, engine),
             "exits": exits, "deadline": _iso(m.deadline, tz),
             "level_reported": _r(obs.level_reported, 1), "level_filtered": _r(obs.level_filtered, 1),
             "sending": bool(engine.sending), "preview": bool(preview), "not_sending_reason": engine.not_sending_reason,
             "values_at": _iso(vr.made_at, tz) if vr else None, "values_because": vr.because if vr else None}
    return m.mode, attrs


# ---- the value now -----------------------------------------------------------------------------
def scale_max(lines) -> int:
    """The top of the value bar: 40p, or more when the real prices need it (grid-event values are off the scale)."""
    top = max(lines.buy_line_p, lines.sell_line_p, lines.use_line_p, lines.store_sun_line_p, lines.import_p,
              lines.export_p, 30.0)
    return max(40, int(math.ceil(top * 1.25 / 10.0) * 10))


def _value(out: StepOutput) -> tuple[str, dict]:
    ln = out.mode.lines
    if ln is None:
        return "unknown", {}
    attrs = {"value_p": _r(ln.value_p), "buy_line_p": _r(ln.buy_line_p), "sell_line_p": _r(ln.sell_line_p),
             "use_line_p": _r(ln.use_line_p), "store_sun_line_p": _r(ln.store_sun_line_p),
             "import_p": _r(ln.import_p), "export_p": _r(ln.export_p), "charge_target_soc": _r(ln.charge_target_soc, 1),
             "sell_floor_soc": _r(ln.sell_floor_soc, 1), "level": _r(out.observation.level_filtered, 1),
             "scale_max_p": scale_max(ln)}
    return f"{ln.value_p:.2f}", attrs


# ---- the timeline ------------------------------------------------------------------------------
def _cutoff(vr: ValueResult) -> datetime:
    """The end of what the timeline shows: the plan is solved further than this (see forecast.DISPLAY_H)."""
    return vr.made_at + timedelta(hours=DISPLAY_H)


def _prices(vr: ValueResult, tz) -> list[dict]:
    rows: list[dict] = []
    cut = _cutoff(vr)
    for seg in vr.forecast.segments:
        if seg.start >= cut:
            break
        # a smart slot EDF is offering is shown at its offered price, certain or not: the known rate, never the
        # expected price the plan works with. The standard rate it replaces rides beside it as `standard_p`.
        offered = seg.slot_prob is not None and seg.slot_prob > 0 and seg.slot_import_p is not None
        row = {"start": seg.start, "end": min(seg.end, cut),
               "import_p": _r(seg.slot_import_p if offered else seg.import_p), "export_p": _r(seg.export_p),
               "standard_p": _r(seg.import_p) if offered else None,
               "slot_prob": _r(seg.slot_prob), "slot_import_p": _r(seg.slot_import_p) if seg.slot_prob is not None
               else None, "event": bool(seg.event), "free": bool(seg.free), "estimated": bool(seg.price_estimated)}
        last = rows[-1] if rows else None
        if last and all(last[k] == row[k] for k in ("import_p", "export_p", "slot_prob", "slot_import_p", "event",
                                                    "free", "estimated", "standard_p")) and last["end"] == row["start"]:
            last["end"] = row["end"]
        else:
            rows.append(row)
    out = []
    for r in rows:
        d = {"start": _iso(r["start"], tz), "end": _iso(r["end"], tz), "import_p": r["import_p"],
             "export_p": r["export_p"], "slot_prob": r["slot_prob"], "event": r["event"], "free": r["free"],
             "estimated": r["estimated"]}
        if r["slot_prob"] is not None:
            d["slot_import_p"] = r["slot_import_p"]
        if r["standard_p"] is not None:
            d["standard_p"] = r["standard_p"]
        out.append(d)
    return out


def _thin_path(a: dict) -> None:
    p = a["path"]
    for k in ("mid", "low", "high", "value"):
        if k in p:
            p[k] = p[k][::2]
    p["step_min"] = p["step_min"] * 2


def _short_reasons(n: int):
    def f(a: dict) -> None:
        for it in a["items"]:
            it["reason"] = it["reason"][:n]
    return f


def _cap_items(n: int):
    def f(a: dict) -> None:
        a["items"] = a["items"][:n]
    return f


SUN_STEP_MIN = 30


def _sun(vr: ValueResult, tz) -> dict | None:
    """The sun and the house the plan was made with, as average kW in half-hour buckets from the first segment's half
    hour: sun low / middle / high and the middle house load (the same spreads the value was worked out on, after any
    learned bias). The segments are cut where the data changes, so each is spread over the buckets it overlaps."""
    segs = vr.forecast.segments
    if not segs:
        return None
    step = timedelta(minutes=SUN_STEP_MIN)
    first = segs[0].start
    t0 = first.replace(minute=first.minute // SUN_STEP_MIN * SUN_STEP_MIN, second=0, microsecond=0)
    n = int(math.ceil((min(segs[-1].end, _cutoff(vr)) - t0) / step))
    keys = ("low", "mid", "high")
    acc = {k: [0.0] * n for k in keys}
    house = [0.0] * n
    cover = [0.0] * n                                  # hours of each bucket the forecast covers
    for sg in segs:
        span = (sg.end - sg.start).total_seconds()
        if span <= 0:
            continue
        i0 = max(0, int((sg.start - t0) / step))
        i1 = min(n - 1, int(math.ceil((sg.end - t0) / step)) - 1)
        for i in range(i0, i1 + 1):
            b0, b1 = t0 + i * step, t0 + (i + 1) * step
            share = (min(sg.end, b1) - max(sg.start, b0)).total_seconds() / span
            if share <= 0:
                continue
            for k in keys:
                acc[k][i] += getattr(sg.solar_kwh, k) * share
            house[i] += sg.load_kwh.mid * share
            cover[i] += share * span / 3600
    def kw(xs):
        return [_r(x / c, 2) if c > 1e-9 else 0.0 for x, c in zip(xs, cover, strict=True)]
    out = {"start": _iso(t0, tz), "step_min": SUN_STEP_MIN, **{k: kw(acc[k]) for k in keys}, "house": kw(house)}
    return out if any(acc["high"]) or any(house) else None


def _thin_sun(a: dict) -> None:
    sun = a.get("sun")
    if not sun:
        return
    sun.pop("house", None)
    for k in ("mid", "low", "high"):
        sun[k] = sun[k][::2]
    sun["step_min"] *= 2


def _hhmm(t: datetime, tz=None) -> str:
    return (t.astimezone(tz) if tz else t).strftime("%H:%M")


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def _run_reason(mode: str, ln_value: float | None, level_end: float) -> str:
    if mode == EXPORT:
        return (f"Sell: the plan has no run here, and a stored kWh is worth {ln_value:.1f}p, under what a sale "
                f"brings: the battery sells down to {level_end:.0f}% before the next step.")
    return (f"Charge: the plan has no run here, and a stored kWh is worth {ln_value:.1f}p, over the import price: "
            f"the battery charges to {level_end:.0f}% before the next step.")


def _as_run(vr: ValueResult, settings: V2Settings, tz=None,
            facts=None) -> tuple[list[TimelineItem], list[tuple[float, float]], list[tuple[float, ...]]]:
    """`as_run`'s items, the anchors that map the time they are shown at to the plan's own clock: (shown, plan) in
    seconds, in step with both clocks, and the moves: (start, end, level before, level after) in seconds and percent
    for a run the lines start where the plan has none (`idle_action`), which shifts the levels after it."""
    idle = HOLD if not settings.hold_only_car_reserve else SELF_USE
    out: list[TimelineItem] = []
    anchors: list[tuple[float, float]] = []
    moves: list[tuple[float, ...]] = []
    cursor = None
    shift = 0.0
    for it in vr.timeline:
        if it.remainder:
            continue
        plan_start, plan_end = it.start, it.end
        if shift:
            it = replace(it, level_start=_clamp(it.level_start + shift), level_end=_clamp(it.level_end + shift),
                         level_lo=None if it.level_lo is None else _clamp(it.level_lo + shift),
                         level_hi=None if it.level_hi is None else _clamp(it.level_hi + shift))
        if cursor is not None and cursor < it.start:
            if Executor._same_prices(vr, cursor, it.start):         # the next step starts as soon as the run is done
                span = it.end - it.start
                it = replace(it, start=cursor, end=cursor + span) if it.mode in (CHARGE, EXPORT) \
                    else replace(it, start=cursor)
            else:                                                   # the clock stands: the rest is what fills the wait
                level = out[-1].level_end if out else it.level_start
                at = cursor
                act = idle_action(vr, cursor, level, settings, facts) if facts is not None and idle == SELF_USE \
                    else None
                if act is not None:                                 # no run in the plan, but the lines start one
                    mode, goal = act
                    seg = vr.limits[_segment_index(vr, cursor)]
                    kw = (seg.charge_cap_kw if mode == CHARGE else seg.discharge_cap_kw)
                    kw = kw if kw is not None else (facts.max_charge_kw if mode == CHARGE else facts.max_discharge_kw)
                    rate = max(kw, 0.1) / max(facts.capacity_kwh, 1.0) * 100.0 \
                        * (facts.eta_charge if mode == CHARGE else 1.0)     # percent an hour
                    hours = abs(goal - level) / rate
                    run_end = min(it.start, cursor + timedelta(hours=hours))
                    reached = level + (rate if mode == CHARGE else -rate) * (run_end - cursor).total_seconds() / 3600
                    reached = _clamp(min(reached, goal) if mode == CHARGE else max(reached, goal))
                    seg_k = vr.forecast.segments[_segment_index(vr, cursor)]
                    ln = lines(vr, cursor, level, seg_k.import_p, seg_k.export_p, facts, settings)
                    out.append(TimelineItem(mode=mode, start=cursor, end=run_end, level_start=level,
                                            level_end=reached, until=f"until {reached:.0f}%",
                                            reason=_run_reason(mode, ln.value_p, reached)))
                    moves.append((cursor.timestamp(), run_end.timestamp(), level, reached))
                    shift += reached - level
                    it = replace(it, level_start=_clamp(it.level_start + reached - level),
                                 level_end=_clamp(it.level_end + reached - level))
                    level, at = reached, run_end
                if at < it.start:
                    out.append(TimelineItem(
                        mode=idle, start=at, end=it.start, level_start=level, level_end=level,
                        until=f"until {_hhmm(it.start, tz)}", remainder=True,
                        reason="The run has reached its target; the battery waits for the next step."))
        anchors.append((it.start.timestamp(), plan_start.timestamp()))
        anchors.append((it.end.timestamp(), plan_end.timestamp()))
        if out and out[-1].mode == it.mode and out[-1].end == it.start and it.mode in (CHARGE, EXPORT):
            last = out[-1]
            out[-1] = replace(last, end=it.end, level_end=it.level_end, until=it.until, level_lo=it.level_lo,
                              level_hi=it.level_hi)
        else:
            out.append(it)
        cursor = out[-1].end
    return out, anchors, moves


def as_run(vr: ValueResult, settings: V2Settings, tz=None, facts=None) -> list[TimelineItem]:
    """The plan's timeline as the plan executor will run it, for display only (the plan itself is not changed). The
    plan ends a charge or sale part-way through a segment and fills the rest with a hold (`remainder`), a way of saying
    how much; the executor never rests there: a run follows the plan by level, and the next step starts as soon as the
    run is done wherever the prices are those of now (`Executor._plan_candidate`). Where they differ the clock stands
    and the battery does what the rules allow in between (a hold only where one is allowed, else self-use)."""
    return _as_run(vr, settings, tz, facts)[0]


def _path_as_run(path: dict, anchors: list[tuple[float, float]]) -> dict:
    """The level path on the clock the items are shown at. The plan's own path runs on the plan's clock, where each
    run ends at its segment's end; the items are shown pulled forward (`as_run`), so a path left as it is draws a
    battery that is still selling where the items say it waits. Each path point is read at the plan's time that the
    shown time maps to (linear between the anchors, the same shift as the items)."""
    start = datetime.fromisoformat(path["start"]).timestamp()
    step = path["step_min"] * 60.0
    if not anchors:
        return path
    xs, ys = [], []
    for shown, plan in anchors:                       # shown time must not go back; the plan's time must not either
        if xs and shown < xs[-1]:
            shown = xs[-1]
        if ys and plan < ys[-1]:
            plan = ys[-1]
        xs.append(shown)
        ys.append(plan)
    out = dict(path)
    for k in ("mid", "low", "high"):
        series = path.get(k) or []
        if len(series) < 2:
            continue
        vals = []
        for i in range(len(series)):
            t = start + i * step
            if t <= xs[0] or t >= xs[-1]:                 # before the first run, or past the last: the plan's own time
                plan_t = t if t <= xs[0] else t - xs[-1] + ys[-1]
            else:
                j = bisect_right(xs, t)
                x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
                plan_t = y0 + (y1 - y0) * ((t - x0) / (x1 - x0) if x1 > x0 else 0.0)
            f = min(len(series) - 1.0, max(0.0, (plan_t - start) / step))
            lo = int(f)
            hi = min(lo + 1, len(series) - 1)
            vals.append(series[lo] + (series[hi] - series[lo]) * (f - lo))
        out[k] = vals
    return out


def _path_after_moves(path: dict, moves: list[tuple[float, ...]]) -> dict:
    """The level path with the runs the lines start where the plan has none: the points inside a run lie on its ramp,
    the points after it are lifted or lowered by what it moved (the plan's own path stays flat there)."""
    start = datetime.fromisoformat(path["start"]).timestamp()
    step = path["step_min"] * 60.0
    out = dict(path)
    for k in ("mid", "low", "high"):
        series = list(path.get(k) or [])
        for s0, e0, before, after in moves:
            for i in range(len(series)):
                t = start + i * step
                if s0 <= t < e0:
                    series[i] = before + (after - before) * (t - s0) / max(e0 - s0, 1.0)
                elif t >= e0:
                    series[i] = _clamp(series[i] + after - before)
        out[k] = series
    return out


def _path_value(vr: ValueResult, p: dict, mid: list) -> list[float]:
    """What a stored kWh is worth (p/kWh) at each point of the shown level path: `value_at`, the figure the controller
    reads at the battery's level, so the chart shows that and not a coarser copy of the curve. Empty with no curve."""
    if not vr.lam or not mid or not p.get("start"):
        return []
    start = datetime.fromisoformat(p["start"])
    step = timedelta(minutes=p.get("step_min", 15))
    raw = [value_at(vr, start + i * step, lvl) for i, lvl in enumerate(mid)]
    return [_r(x, 1) for x in _smoothed(raw)]


def _smoothed(xs: list[float], half: int = PATH_VALUE_SMOOTH) -> list[float]:
    """A centred moving average over 2 * half + 1 points (fewer at the two ends). The plan holds the battery on the
    edge of a step in the value (just enough for the evening peak, or the end of a charge it can only just finish), so
    the value read at the path flips between the two sides of the step from one point to the next; the average shows
    where it sits."""
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - half): i + half + 1]
        out.append(sum(w) / len(w))
    return out


def _timeline(vr: ValueResult | None, settings: V2Settings, hard_floor: float, tz,
              facts=None) -> tuple[str, dict]:
    if vr is None:
        return "unknown", {}
    items_run, anchors, moves = _as_run(vr, settings, tz, facts)
    p = _path_as_run(vr.path, anchors) if vr.path and vr.path.get("start") else (vr.path or {})
    if moves and p.get("start"):
        p = _path_after_moves(p, moves)
    cut = _cutoff(vr)
    step_min = p.get("step_min", 15)
    keep = int(DISPLAY_H * 60 // step_min) + 1                    # path points up to the end of what is shown
    path = {k: [_r(x, 1) for x in p.get(k, [])][:keep] for k in ("mid", "low", "high")}
    value = _path_value(vr, p, path["mid"])
    if value:
        path["value"] = value
    items = []
    for it in items_run:
        if it.start >= cut:
            break
        end, level_end = it.end, it.level_end
        if end > cut:                                             # runs past what is shown: stop it at the edge
            end = cut
            if len(path["mid"]) == keep:
                level_end = path["mid"][-1]
        items.append({"mode": it.mode, "start": _iso(it.start, tz), "end": _iso(end, tz),
                      "level_start": _r(it.level_start, 1), "level_end": _r(level_end, 1), "until": it.until,
                      "reason": it.reason[:REASON_MAX]})
    attrs = {"now": _iso(vr.made_at, tz), "because": vr.because, "floor_soc": _r(hard_floor, 1),
             "reserve_soc": _r(settings.reserve_soc, 1),
             "items": items,
             "path": {"start": p.get("start"), "step_min": step_min, **path},
             "prices": _prices(vr, tz), "sun": _sun(vr, tz),
             "cost_expected": _r(vr.cost_expected_p / 100), "cost_selfuse": _r(vr.cost_selfuse_p / 100),
             "comfort_given_up": _r(vr.comfort_given_up_p / 100) if vr.comfort_given_up_p is not None else None,
             "calc_s": _r(vr.calc_s)}
    _fit(attrs, [_short_reasons(120), _thin_sun, _thin_path, _short_reasons(60), _short_reasons(0), _cap_items(40),
                 _cap_items(24)])
    return _iso(vr.made_at, tz), attrs


# ---- the value curve ---------------------------------------------------------------------------
def _curve(vr: ValueResult | None, tz) -> tuple[str, dict]:
    if vr is None or not vr.lam or not vr.forecast.segments:
        return "unknown", {}
    segs = vr.forecast.segments
    start = vr.made_at.replace(minute=0, second=0, microsecond=0)
    values = []
    for i in range(CURVE_MAX_ROWS):
        t = max(start + timedelta(hours=i), segs[0].start)
        if t >= segs[-1].end:
            break
        k = next(j for j, g in enumerate(segs) if g.start <= t < g.end)
        row = vr.lam[k]
        n = len(row)
        values.append([_r(row[min(n - 1, int(round(p / 100 * (n - 1))))], 2) for p in CURVE_PCTS])
    attrs = {"start": _iso(start, tz), "step_min": 60, "levels": list(CURVE_PCTS), "values": values,
             "unit": "p/kWh"}
    return _iso(vr.made_at, tz), attrs


# ---- triggers and diagnostics ------------------------------------------------------------------
def _triggers(out: StepOutput, engine) -> tuple[str, dict]:
    now = out.observation.now
    today = engine.triggers.health(now)
    today.pop("day", None)
    today.pop("pending", None)
    attrs = {"recent": list(reversed(engine.triggers.recent))[:30], "today": today}
    return str(today["revalues"]), attrs


def _diag(out: StepOutput, engine) -> tuple[str, dict]:
    attrs = engine.learner.diag()
    attrs["filter_gap_max_today"] = round(engine.observer.gap_max_today, 2)
    attrs["comfort"] = engine.learner.comfort_rows()
    return ("learning" if engine.learner.days < 3 else "ok"), attrs


def entity_states(out: StepOutput, engine, settings: V2Settings, tz,
                  preview: bool = False) -> dict[str, tuple[str, dict]]:
    """Every engine v2 sensor for this tick: {publish key: (state, attributes)}. In preview (engine v1 is in control)
    `v2_mode` says so and `state_engine` is left out: the app's own cycle publishes it."""
    floor = engine.last_facts.hard_floor_soc if engine.last_facts is not None else 12.0
    res = {"state_engine": ("v2", {"v2_available": True}),
           "v2_mode": _mode(out, engine, tz, preview), "v2_value": _value(out),
           "v2_timeline": _timeline(out.value, settings, floor, tz, engine.last_facts),
           "v2_value_curve": _curve(out.value, tz),
           "v2_triggers": _triggers(out, engine), "diag_v2": _diag(out, engine)}
    if preview:
        res.pop("state_engine")
    return res


def settings_state(settings: V2Settings, profile: str | None = None,
                   user_profiles: dict | None = None) -> tuple[str, dict]:
    """sensor.pe_diag_v2_settings: the catalogue with the values in use (the config page's schema)."""
    return str(len(SETTINGS)), catalogue(settings, profile, user_profiles)


def profiles_state(profile: str | None = None, user_profiles: dict | None = None) -> tuple[str, dict]:
    """sensor.pe_diag_v2_profiles: the profile in use (the state) and the profiles on offer."""
    attrs = profiles_catalogue(profile, user_profiles)
    return attrs["profile"], attrs

