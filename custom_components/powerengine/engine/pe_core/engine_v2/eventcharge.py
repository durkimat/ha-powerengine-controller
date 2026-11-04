"""When the highest-price rule steps aside for an announced grid event.

`charge_max_import_p` stops the plan buying grid energy at a dear rate. A grid event pays far more than that rate costs
(115p a kWh against about 34p to put a kWh in the battery and take it out), so when an announced event would find the
battery short of what it can sell, the rule is lifted, but only for the stretch in which the charge has to happen.

The stretch is worked out when the event is announced and again at every revalue, from the plan as it stands (with the
rule in force): how much the event takes from the battery, the level the plan reaches at its start, and how long the
charger needs to make up the difference. The window opens one margin before the latest safe start, so the battery is not
charged hours early and the house run through the charger and back for nothing. Once a window has opened it stays open
(`latch`) so a small change of forecast cannot close it half way through a charge."""
from __future__ import annotations

from datetime import datetime, timedelta

from .types import BatteryFacts, Forecast, Segment

EVENT_MARGIN_PTS = 5.0            # extra level over the event's needs, points (as the v1 `axle_margin_soc`)
LEAD_MARGIN_MIN = 30              # the window opens this long before the latest safe start
CHARGE_RATE_SHARE = 0.85          # of the charger's full power, to allow for the house load, the taper and the BMS
STEP_MIN = 15


def _groups(segments: tuple[Segment, ...], now: datetime) -> list[tuple[int, int]]:
    """(first, last) segment index of each announced event that has not started."""
    out, i, n = [], 0, len(segments)
    while i < n:
        if segments[i].event:
            j = i
            while j + 1 < n and segments[j + 1].event:
                j += 1
            if segments[i].start > now:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def event_draw_kwh(segments: tuple[Segment, ...], first: int, last: int, facts: BatteryFacts) -> float:
    """What the event takes out of the battery: sales at full power (house load included) until it ends."""
    dkw = facts.max_discharge_kw
    if facts.bms_discharge_kw is not None:
        dkw = min(dkw, facts.bms_discharge_kw)
    total = 0.0
    for sg in segments[first:last + 1]:
        h = sg.hours
        net_kw = (sg.load_kwh.mid - sg.solar_kwh.mid) / h if h else 0.0
        kw = max(0.0, min(dkw, facts.export_limit_kw + net_kw))
        total += kw * h / facts.eta_discharge
    return total


def _level_at(path: dict, t: datetime) -> float | None:
    """The plan's level (percent) at t from its 15-minute path, None outside it."""
    vals = path.get("mid") or []
    if not vals:
        return None
    start = datetime.fromisoformat(path["start"])
    i = int(round((t - start).total_seconds() / (60 * path.get("step_min", STEP_MIN))))
    return vals[i] if 0 <= i < len(vals) else None


def windows(fc: Forecast, path: dict, now: datetime, facts: BatteryFacts, floor_pct: float,
            ceiling_pct: float) -> dict[datetime, tuple[datetime, float]]:
    """{event start: (when the highest-price rule steps aside, the level in percent it may charge to)}, for the
    announced events the plan would reach short of what they can sell. `path` is the plan's level (percent) every
    15 minutes, made with the rule in force."""
    out: dict[datetime, tuple[datetime, float]] = {}
    segs = fc.segments
    cap = facts.capacity_kwh
    rate_pct = CHARGE_RATE_SHARE * facts.max_charge_kw * facts.eta_charge / cap * 100.0       # points an hour
    for first, last in _groups(segs, now):
        t_ev = segs[first].start
        need_pct = floor_pct + event_draw_kwh(segs, first, last, facts) / cap * 100.0 + EVENT_MARGIN_PTS
        need_pct = min(need_pct, ceiling_pct)
        at_event = _level_at(path, t_ev)
        if at_event is None or at_event >= need_pct:
            continue
        latest = None
        t = t_ev
        while t > now:
            t -= timedelta(minutes=STEP_MIN)
            lvl = _level_at(path, max(t, now))
            if lvl is None:
                break
            if lvl + rate_pct * (t_ev - t).total_seconds() / 3600 >= need_pct:
                latest = t
                break
        start = now if latest is None else max(now, latest - timedelta(minutes=LEAD_MARGIN_MIN))
        out[t_ev] = (start, need_pct)
    return out


def latch(found: dict, held: dict, now: datetime) -> dict:
    """Keep a window that has opened open: its start does not move later once `now` has passed it, and its level
    does not fall. Windows of events that are over are dropped."""
    out = dict(found)
    for t_ev, (start, target) in held.items():
        if t_ev > now and start <= now:
            if t_ev in out:
                out[t_ev] = (min(out[t_ev][0], start), max(out[t_ev][1], target))
            else:
                out[t_ev] = (start, target)          # the plan now reaches the level, but the charge has begun
    return out


def mark(fc: Forecast, wins: dict) -> Forecast:
    """The forecast with `event_charge_to` set on the segments inside a window (they end after its start, by the
    event's)."""
    if not wins:
        return fc
    from dataclasses import replace
    segs = []
    for sg in fc.segments:
        to = None
        if not sg.event:
            for t_ev, (start, target) in wins.items():
                if start < sg.end <= t_ev:
                    to = target if to is None else max(to, target)
        segs.append(replace(sg, event_charge_to=to) if to is not None else sg)
    return replace(fc, segments=tuple(segs))
