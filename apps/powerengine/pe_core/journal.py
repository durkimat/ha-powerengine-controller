"""A journal of every inverter write PowerEngine makes (last 3 days), to see why writes add up."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

KEEP_DAYS = 3


class WriteJournal:
    def __init__(self, path: str):
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                self.entries: list[dict] = json.load(fh)
        except (OSError, ValueError):
            self.entries = []

    def add(self, now: datetime, entity: str, value, before, why: str) -> None:
        self.entries.append({"t": now.isoformat(timespec="seconds"), "entity": entity, "value": value,
                             "before": before, "why": why})

    def save(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
        self.entries = [e for e in self.entries if e["t"] >= cutoff]
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, separators=(",", ":"))
        os.replace(tmp, self.path)
