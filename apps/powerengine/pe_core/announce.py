"""Spoken reminders for a grid event (Alexa announcements): when to speak, whether the house looks ready, what to say.

Pure decisions only. The app sends the text through the configured Alexa notify service and uses the Notifier so each
reminder is said once. Nothing is said outside the allowed hours (later than `earliest`, before `latest`).
"""

from __future__ import annotations

import re
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
    parts = ([f"{h} hour" + ("s" if h != 1 else "")] if h else []) + (
        [f"{m} minute" + ("s" if m != 1 else "")] if m else [])
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
        wait = _span(round(hours_before * 12) * 5) if hours_before < 1 else _span(round(hours_before * 2) * 30)
        out.append(f"The grid event starts in {wait}, at {_clock(ls)}.")
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


_SPEAKER = re.compile(r"^notify\.alexa_media_[a-z0-9_]+$")


def trial_speakers(services) -> list[str]:
    """The speakers a test may use: Alexa Media Player's notify services only (the reply-on-last-speaker one needs a
    command to have just been heard, so it is no use here), whatever the event carried."""
    out = []
    for s in services if isinstance(services, (list, tuple)) else []:
        if isinstance(s, str) and _SPEAKER.match(s) and s != "notify.alexa_media_last_called" and s not in out:
            out.append(s)
    return out


def trial_message(intro: str = "") -> str:
    """What the test button says: the opening words, then "Test message."."""
    return f"{intro.strip()} Test message.".strip()


# ---- when a grid event has finished ---------------------------------------------------------------------------------
def event_summary(records: list[dict], start: datetime, end: datetime) -> dict | None:
    """What the half-hours of a grid event recorded: house and car use, grid export, what the event pays. None when no
    record falls inside the event. `records` are cost records (`pe_core/energy.py` and `costs.py`), any days."""
    rows = [r for r in records if start <= datetime.fromisoformat(r["start"]) < end]
    if not rows:
        return None

    def total(key):
        return sum(float(r.get(key) or 0.0) for r in rows)

    def event(key):
        return sum(float((r.get("v") or {}).get(key) or 0.0) for r in rows)
    return {"house_kwh": total("house"), "car_kwh": total("car"), "export_kwh": total("grid_export"),
            "event_kwh": event("event_kwh"), "gross": event("event_gross"), "net": event("event_net"),
            "half_hours": len(rows)}


def _kwh(x: float) -> str:
    return f"{x:.1f} kilowatt hours"


HEAT = {
    "restored": "The heating set-back is back to off, as it was before, so the heating is back on its programme.",
    "already_on": "The heating set-back was already on before the event, so I've left it on.",
    "user_override": "The heating set-back was switched off during the event, so I've left it off.",
}


def finish_message(intro: str, start: datetime, end: datetime, now: datetime, tz: tzinfo | None, summary: dict | None,
                   heat: str | None, car: str | None, car_mode: str | None = None) -> str:
    """What is said when a grid event has finished: what the house used, what was exported, what it should pay, and
    anything PowerEngine switched for the event, with the state it is back to. `heat` and `car` are the notes of
    `setback.Setback` and `carstop.CarStop` for this event (None: PowerEngine did nothing there, so nothing is said)."""
    ls = start.astimezone(tz) if tz else start
    ln = now.astimezone(tz) if tz else now
    gap = (ln.date() - ls.date()).days
    day = "today" if gap == 0 else ("yesterday" if gap == 1 else ls.strftime("%A"))
    out = [intro.strip()] if intro.strip() else []
    out.append(f"The grid event at {_clock(ls)} {day} is over.")
    if summary:
        use = f"The house used {_kwh(summary['house_kwh'])}"
        if summary["car_kwh"] > 0.05:
            use += f", and the car {_kwh(summary['car_kwh'])}"
        out.append(f"{use}, and we exported {_kwh(summary['export_kwh'])}.")
        if summary["gross"] > 0.005:
            pay = f"It should pay about £{summary['gross']:.2f}"
            if summary["net"] > 0.005 and abs(summary["gross"] - summary["net"]) >= 0.01:
                pay += f", or £{summary['net']:.2f} after the battery energy it used"
            out.append(pay + ".")
    if heat in HEAT:
        out.append(HEAT[heat])
    if car == "restored":
        out.append(f"The car charger is back to {car_mode or 'its usual mode'}, as it was before.")
    elif car == "already_stopped":
        out.append("The car charger was already stopped before the event, so I've left it stopped.")
    elif car == "user_changed":
        out.append("The car charger mode was changed during the event, so I've left it as it is.")
    return " ".join(out)
