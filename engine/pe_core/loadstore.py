"""PowerEngine's own record of house-only load, one mean per half-hour.

Kept in a small JSON file next to config.yaml so the load forecast keeps
learning even if HA history can't be read, and survives restarts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .forecast import SLOT, slot_start

KEEP_DAYS = 15


class LoadStore:
    def __init__(self, path: str | None = None):
        self.path = path
        self.means: dict[datetime, float] = {}
        self._slot: datetime | None = None
        self._sum = self._secs = 0.0
        self._last: datetime | None = None
        self._last_w: float | None = None

    def load(self) -> int:
        if not self.path or not os.path.exists(self.path):
            return 0
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.means = {datetime.fromisoformat(k): float(v) for k, v in raw.items()}
        except (OSError, ValueError, TypeError):
            self.means = {}
        return len(self.means)

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k.isoformat(): round(v, 1) for k, v in sorted(self.means.items())}, fh)
        os.replace(tmp, self.path)

    def add(self, now: datetime, house_w: float | None) -> bool:
        """Accumulate a reading. Returns True when a half-hour has just been completed and stored."""
        done = False
        s = slot_start(now)
        if self._slot is not None and s != self._slot:
            if self._secs >= 600:                                   # at least 10 minutes of readings
                self.means[self._slot] = self._sum / self._secs
                done = True
            cutoff = s - timedelta(days=KEEP_DAYS)
            self.means = {k: v for k, v in self.means.items() if k >= cutoff}
            self._sum = self._secs = 0.0
        if self._slot != s:
            self._slot = s
        if house_w is not None and self._last is not None and self._last_w is not None:
            dt = min((now - self._last).total_seconds(), 300.0)     # ignore long gaps
            if dt > 0 and self._last >= s:
                self._sum += self._last_w * dt
                self._secs += dt
        self._last, self._last_w = now, house_w
        return done


__all__ = ["LoadStore", "SLOT"]
