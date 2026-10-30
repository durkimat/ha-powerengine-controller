"""Spoken reminders for a grid event (Alexa announcements): when to speak, whether the house looks ready, what to say.

Pure decisions only. The app sends the text through the configured Alexa notify service and uses the Notifier so each
reminder is said once. Nothing is said outside the allowed hours (later than `earliest`, before `latest`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

GRACE_MIN = 20            # a reminder that is later than this after its time is dropped, not said late
READY, TIGHT, SHORT = "ready", "tight", "short"
MARGIN_SOC = 3.0          # points above the reserve the battery must still hold at the end of the event


def allowed(now: datetime, tz: tzinfo | None, earliest_weekday: int, earliest_weekend: int, latest: int) -> bool:
    """True when speaking is fine: from the weekday/weekend earliest time to the latest (minutes after midnight)."""
    local = now.astimezone(tz) if tz else now
    minute = local.hour * 60 + local.minute
    earliest = earliest_weekend if local.weekday() >= 5 else earliest_weekday
    return earliest <= minute < latest


def due(start: datetime, end: datetime | None, now: datetime, hours_before: list[float]) -> float | None:
    """The reminder (hours before the start; 0 = as it starts) whose time has just come, or None. At most one."""
    best = None
    for h in sorted(hours_before, reverse=True):
        at = start - timedelta(hours=h)
        if at <= now < at + timedelta(minutes=GRACE_MIN) and (end is None or now < end):
            best = h
    return best


def _level(series: list | None, step_min: float, t0: datetime, t: datetime) -> float | None:
    if not series or step_min <= 0:
        return None
    i = round((t - t0).total_seconds() / 60 / step_min)
    return float(series[i]) if 0 <= i < len(series) else None


def readiness(timeline: dict | None, start: datetime, end: datetime | None, soc_now: float | None,
              need_soc: float | None = None) -> dict:
    """How ready the battery is. With engine v2's timeline: the projected level at the start and where the event leaves
    it, in the middle and low cases, against the reserve. Without it: the level now against what the event needs."""
    path = (timeline or {}).get("path") or {}
    try:
        t0 = datetime.fromisoformat(path["start"])
        step = float(path["step_min"])
    except (KeyError, TypeError, ValueError):
        t0, step = None, 0.0
    floor = (timeline or {}).get("floor_soc")
    if t0 is not None and end is not None and floor is not None:
        stop = min(end, t0 + timedelta(minutes=step * (len(path.get("mid") or []) - 1)))
        mid_end = _level(path.get("mid"), step, t0, stop)
        low_end = _level(path.get("low"), step, t0, stop)
        if mid_end is not None and low_end is not None:
            lim = float(floor) + MARGIN_SOC
            state = READY if low_end > lim else (TIGHT if mid_end > lim else SHORT)
            return {"state": state, "at_start": _level(path.get("mid"), step, t0, start), "at_end": mid_end}
    if soc_now is not None and need_soc is not None:
        return {"state": READY if soc_now >= need_soc else TIGHT, "at_start": None, "at_end": None}
    return {"state": None, "at_start": None, "at_end": None}


def _clock(t: datetime) -> str:
    h = t.hour % 12 or 12
    suffix = "pm" if t.hour >= 12 else "am"
    return f"{h} {suffix}" if t.minute == 0 else f"{h}:{t.minute:02d} {suffix}"


def _span(minutes: float) -> str:
    m = int(round(minutes))
    h, m = divmod(m, 60)
    parts = ([f"{h} hour" + ("s" if h != 1 else "")] if h else []) + ([f"{m} minutes"] if m else [])
    return " ".join(parts) or "a short time"


def message(hours_before: float, start: datetime, end: datetime | None, now: datetime, tz: tzinfo | None,
            soc: float | None, ready: dict, intro: str = "") -> str:
    ls = start.astimezone(tz) if tz else start
    le = (end.astimezone(tz) if tz else end) if end else None
    ln = now.astimezone(tz) if tz else now
    gap = (ls.date() - ln.date()).days
    day = "today" if gap == 0 else ("tomorrow" if gap == 1 else ls.strftime("%A"))
    last = ready["state"]
    out = [intro.strip()] if intro.strip() else []
    if hours_before <= 0:
        out.append("The grid event is starting now" + (f" and runs until {_clock(le)}." if le else "."))
    elif hours_before >= 4:
        out.append(f"Heads up. There's a grid event {day} at {_clock(ls)}"
                   + (f", lasting {_span((le - ls).total_seconds() / 60)}." if le else "."))
    else:
        n = round(hours_before)
        out.append(f"The grid event starts in {n} hour{'s' if n != 1 else ''}, at {_clock(ls)}.")
    if soc is not None:
        level = f"The battery is at {soc:.0f} percent"
        if hours_before > 0 and ready["at_start"] is not None:
            level += f" and should be around {ready['at_start']:.0f} percent by then"
        out.append(level + ".")
    if last == READY:
        out.append("The battery is ready." if hours_before <= 0 else "We're looking well prepared.")
    elif last == TIGHT:
        out.append("We should be fine, but saving power would help.")
    elif last == SHORT:
        out.append("The battery may run short, so saving power would really help.")
    if hours_before <= 0:
        out.append("Please switch off what you can: computers, ovens, lights and anything else you don't need. "
                   "Every unit you save is exported at the grid event rate.")
    elif hours_before <= 2.5 and last in (TIGHT, SHORT):
        out.append("Please start turning off things you don't need.")
    return " ".join(out)
