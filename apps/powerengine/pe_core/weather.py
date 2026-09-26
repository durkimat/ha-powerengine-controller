"""Hourly outside temperatures from Open-Meteo (free for personal use, no key): the Simulator's heat pump, and the
cold-battery caution (the last 3 days and the next 3).

Older days come from the historical archive (ERA5, a few days behind); the last few days from the forecast API's
recent past. Cached by UTC hour in one file, so each hour is fetched once.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date, datetime, timedelta, timezone

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
RECENT = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_LAG_DAYS = 6
TIMEOUT_S = 30


def _get(url: str, timeout: float = TIMEOUT_S) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "PowerEngine (Home Assistant; tariff simulator)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:            # noqa: S310 (fixed https hosts)
        return json.loads(resp.read().decode("utf-8"))


def _merge(cache: dict, data: dict) -> int:
    h = data.get("hourly") or {}
    n = 0
    for t, v in zip(h.get("time") or [], h.get("temperature_2m") or [], strict=False):
        if v is not None:
            cache[t[:13]] = round(float(v), 1)
            n += 1
    return n


class Weather:
    def __init__(self, path: str, lat: float, lon: float):
        self.path, self.lat, self.lon = path, round(lat, 2), round(lon, 2)
        try:
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            saved = {}
        same = saved.get("lat") == self.lat and saved.get("lon") == self.lon
        self.hours: dict[str, float] = saved.get("hours", {}) if same else {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"lat": self.lat, "lon": self.lon, "hours": self.hours}, fh, separators=(",", ":"))
        os.replace(tmp, self.path)

    def missing_days(self, first: date, last: date) -> list[date]:
        out, d = [], first
        while d <= last:
            if f"{d.isoformat()}T23" not in self.hours or f"{d.isoformat()}T00" not in self.hours:
                out.append(d)
            d += timedelta(days=1)
        return out

    def fill(self, first: date, last: date, today: date, get=_get) -> int:
        """Fetch any missing days in [first, last] (UTC dates). Returns hours added."""
        missing = self.missing_days(first, last)
        if not missing:
            return 0
        added = 0
        cutoff = today - timedelta(days=ARCHIVE_LAG_DAYS)
        old = [d for d in missing if d <= cutoff]
        if old:
            url = (f"{ARCHIVE}?latitude={self.lat}&longitude={self.lon}&start_date={old[0]}&end_date={old[-1]}"
                   "&hourly=temperature_2m&timezone=UTC")
            added += _merge(self.hours, get(url))
        if any(d > cutoff for d in missing):
            url = (f"{RECENT}?latitude={self.lat}&longitude={self.lon}&hourly=temperature_2m&past_days=14"
                   "&forecast_days=1&timezone=UTC")
            added += _merge(self.hours, get(url))
        return added

    def refresh_recent(self, get=None) -> int:
        """The last 3 days and the next 3 (forecast), replacing earlier forecasts. Returns hours merged.
        Short timeout: this runs in the control cycle once an hour."""
        url = (f"{RECENT}?latitude={self.lat}&longitude={self.lon}&hourly=temperature_2m&past_days=3"
               "&forecast_days=3&timezone=UTC")
        return _merge(self.hours, get(url) if get else _get(url, timeout=8))

    def series(self, start: datetime, end: datetime) -> dict[datetime, float]:
        """Hourly temperatures (UTC hour starts) from start to end, where known."""
        out: dict[datetime, float] = {}
        t = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        while t <= end:
            v = self.hours.get(t.strftime("%Y-%m-%dT%H"))
            if v is not None:
                out[t] = v
            t += timedelta(hours=1)
        return out

    def at(self, t: datetime) -> float | None:
        return self.hours.get(t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H"))

    def last_year(self, today: date) -> list[float]:
        start = today - timedelta(days=365)
        out, d = [], start
        while d < today:
            for h in range(24):
                v = self.hours.get(f"{d.isoformat()}T{h:02d}")
                if v is not None:
                    out.append(v)
            d += timedelta(days=1)
        return out
