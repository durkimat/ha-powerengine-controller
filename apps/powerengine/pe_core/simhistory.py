"""A year of history for the Simulator (#54 phase 2), from Home Assistant's long-term statistics.

The app can't read HA's statistics itself (that needs an admin token, which it doesn't have). The Simulator
card, running in an admin's browser, reads them a month at a time (hourly energy per sensor) and hands them to
the app as an event. They're kept per month; each hourly figure is split evenly over its two half-hours, and a
day becomes a set of records shaped like the Costs tab's (house, car, solar, grid), minus prices.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

ROLES = ("house", "car", "solar", "grid_import", "grid_export")
MONTHS_BACK = 12
HALF = timedelta(minutes=30)


def months_wanted(today: date, first_recorded: date | None) -> list[str]:
    """YYYY-MM months from a year ago up to the month of the first recorded day (inclusive)."""
    start = date(today.year - 1, today.month, 1)
    end = first_recorded or today
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def month_range(month: str) -> tuple[datetime, datetime]:
    """UTC start/end covering the month (local days near the edges are handled by the per-hour keys)."""
    y, m = int(month[:4]), int(month[5:7])
    start = datetime(y, m, 1, tzinfo=timezone.utc) - timedelta(days=1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, datetime(ny, nm, 1, tzinfo=timezone.utc) + timedelta(days=1)


def _hour_key(t) -> str | None:
    """Statistics 'start' as ms since epoch (current HA) or an ISO string (older)."""
    try:
        if isinstance(t, (int, float)):
            dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return dt.strftime("%Y-%m-%dT%H")


def parse_upload(stats: dict, entities: dict[str, list[str]]) -> dict[str, dict[str, float]]:
    """{role: {utc hour: kWh}} from a recorder/statistics_during_period result, summing entities per role
    (e.g. several solar plants)."""
    out: dict[str, dict[str, float]] = {r: {} for r in ROLES}
    for role, eids in entities.items():
        if role not in out:
            continue
        for eid in eids:
            for row in stats.get(eid) or []:
                if isinstance(row, (list, tuple)) and len(row) >= 2:       # compact [start, change] from the card
                    start, v = row[0], row[1]
                else:
                    start, v = row.get("start"), row.get("change")
                k = _hour_key(start)
                if k is None or v is None:
                    continue
                try:
                    out[role][k] = round(out[role].get(k, 0.0) + max(0.0, float(v)), 4)
                except (TypeError, ValueError):
                    continue
    return out


class History:
    def __init__(self, folder: str):
        self.folder = folder
        self._cache: dict[str, dict] = {}

    def _path(self, month: str) -> str:
        return os.path.join(self.folder, f"{month}.json")

    def months(self) -> list[str]:
        try:
            return sorted(n[:7] for n in os.listdir(self.folder) if n.endswith(".json") and n[:4].isdigit())
        except OSError:
            return []

    def save_month(self, month: str, hours: dict, now: datetime) -> None:
        os.makedirs(self.folder, exist_ok=True)
        data = {"month": month, "imported": now.isoformat(timespec="seconds"), "hours": hours}
        tmp = self._path(month) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, self._path(month))
        self._cache.pop(month, None)

    def month(self, month: str) -> dict:
        if month not in self._cache:
            try:
                with open(self._path(month), encoding="utf-8") as fh:
                    self._cache[month] = json.load(fh).get("hours", {})
            except (OSError, ValueError):
                self._cache[month] = {}
        return self._cache[month]

    def value(self, role: str, key: str) -> float | None:
        """Each month's file also holds a day either side, so look in the hour's own month first."""
        y, m = int(key[:4]), int(key[5:7])
        prev = f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"
        nxt = f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"
        for month in (key[:7], prev, nxt):
            v = self.month(month).get(role, {}).get(key)
            if v is not None:
                return v
        return None

    def day_records(self, day: date, tz, house_includes_car: bool) -> list[dict]:
        """Half-hour records for a local day (house net of the car where the house figure includes it)."""
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        out, t = [], start
        while t < end:
            k = t.strftime("%Y-%m-%dT%H")
            house = self.value("house", k)
            vals = {r: (self.value(r, k) or 0.0) / 2 for r in ROLES}
            if house is not None:
                car = vals["car"]
                h = house / 2
                out.append({"start": t.isoformat(), "seconds": 1800.0,
                            "house": round(max(0.0, h - car) if house_includes_car else h, 4), "car": round(car, 4),
                            "solar": round(vals["solar"], 4), "grid_import": round(vals["grid_import"], 4),
                            "grid_export": round(vals["grid_export"], 4), "source": "statistics"})
            t += HALF
        return out
