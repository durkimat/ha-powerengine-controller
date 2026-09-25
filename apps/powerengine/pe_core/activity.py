"""A short, plain-English history of decision changes."""

from __future__ import annotations

from datetime import datetime, tzinfo

from .decide import Decision

MAX_ENTRIES = 20


class ActivityLog:
    def __init__(self, entries: list[dict] | None = None):
        self.entries: list[dict] = list(entries or [])[:MAX_ENTRIES]
        first = self.entries[0] if self.entries else None
        self._last_key: tuple | None = (first.get("action"), first.get("rule")) if first else None

    def record(self, decision: Decision, now: datetime, passive: bool, tz: tzinfo | None = None) -> dict | None:
        """Add an entry if the action or deciding rule changed. Returns the new entry, or None."""
        key = (decision.action, decision.rule)
        if key == self._last_key:
            return None
        self._last_key = key
        local = now.astimezone(tz) if tz else now
        entry = {"time": local.isoformat(timespec="seconds"), "hhmm": local.strftime("%H:%M"),
                 "action": decision.action, "rule": decision.rule, "text": decision.sentence(passive)}
        self.entries.insert(0, entry)
        del self.entries[MAX_ENTRIES:]
        return entry
