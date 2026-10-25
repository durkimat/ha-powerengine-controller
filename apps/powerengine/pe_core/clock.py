"""Inverter clock drift, and when to sync it.

The Solis timed windows run on the inverter's own clock (local time, no daylight-saving change of its own), so a
drifting or un-adjusted clock shifts every window. SolaX Modbus exposes the clock as a sensor
("YYYY-MM-DD HH:MM:SS", local) and a button that sets it from HA's time; the button's state is when it was last
pressed (by anything).
"""

from __future__ import annotations

from datetime import datetime, timedelta

SYNC_DRIFT_S = 60          # sync as soon as the clock is this far out (at most once a day)
WARN_DRIFT_S = 120         # Health finding above this
ROUTINE_DAYS = 7           # and sync routinely this often anyway
DRIFT_RETRY = timedelta(days=1)
RETRY_AFTER = timedelta(minutes=30)   # a sync that did not take is tried again after this ...
MAX_RETRIES = 2                       # ... up to this many times, then left for DRIFT_RETRY


def _parse(value, tz) -> datetime | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    text = str(value).strip()
    for parse in (lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S"), datetime.fromisoformat):
        try:
            dt = parse(text)
        except ValueError:
            continue
        if dt.tzinfo is None:
            if tz is None:
                return None
            dt = dt.replace(tzinfo=tz)
        return dt
    return None


def drift_seconds(rtc_state, read_at: datetime | None, tz) -> float | None:
    """Inverter time minus HA time, from the sensor's value and when HA last received it (+ = inverter ahead).
    Includes the integration's polling delay (a few seconds)."""
    rtc = _parse(rtc_state, tz)
    if rtc is None or read_at is None:
        return None
    return round((rtc - read_at).total_seconds(), 1)


def last_sync(button_state, tz) -> datetime | None:
    return _parse(button_state, tz)


def sync_due(drift: float | None, synced: datetime | None, now: datetime, retries: int = 0) -> str | None:
    """Why a sync is due now ("drift" / "retry" / "routine"), or None. `retries`: syncs already repeated for this drift
    (a press the inverter did not take leaves the clock out, and waiting a day to try again was too long)."""
    if drift is not None and abs(drift) >= SYNC_DRIFT_S:
        if synced is None or now - synced >= DRIFT_RETRY:
            return "drift"
        if now - synced >= RETRY_AFTER and retries < MAX_RETRIES:
            return "retry"
    if synced is None or now - synced >= timedelta(days=ROUTINE_DAYS):
        return "routine"
    return None


def finding(drift: float | None, ram: bool = False, synced: datetime | None = None,
            now: datetime | None = None) -> dict | None:
    """The Health finding for a clock that is out. With RAM control nothing runs on the inverter's clock (no timed
    windows are set), so there is nothing to look at; the drift stays on its own sensor."""
    if drift is None or abs(drift) < WARN_DRIFT_S or ram:
        return None
    ahead = "ahead of" if drift > 0 else "behind"
    mins = abs(drift) / 60
    size = f"{mins:.0f} min" if mins >= 1 else f"{abs(drift):.0f} s"
    tried = ""
    if synced is not None and now is not None and now - synced < DRIFT_RETRY:
        tried = f" A sync was sent at {synced.astimezone(now.tzinfo):%H:%M} and the clock did not change."
    return {"level": "problem" if abs(drift) >= 600 else "warning",
            "title": f"Inverter clock {size} {ahead} HA",
            "detail": "The timed charge/discharge windows run on the inverter's clock, so they are shifted by this "
                      "much. In Active mode PowerEngine syncs it; otherwise press Sync inverter clock (e.g. "
                      "button.solis_sync_rtc)." + tried}
