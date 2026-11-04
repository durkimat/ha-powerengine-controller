"""A short, plain-English history of decision changes."""

from __future__ import annotations

from datetime import datetime, tzinfo

from .decide import Decision

MAX_ENTRIES = 48                 # a busy arbitrage day has 30-40 changes; the Monitoring page shows today's


class ActivityLog:
    def __init__(self, entries: list[dict] | None = None):
        self.entries: list[dict] = list(entries or [])[:MAX_ENTRIES]
        first = self.entries[0] if self.entries else None
        self._last_key: tuple | None = (first.get("action"), first.get("rule"), first.get("label")) if first else None

    def record(self, decision: Decision, now: datetime, passive: bool, tz: tzinfo | None = None) -> dict | None:
        """Add an entry if the action, deciding rule or (for a charge) the level it is heading for changed. Returns
        the new entry, or None."""
        key = (decision.action, decision.rule, decision.label_target_soc)
        if key == self._last_key:
            return None
        self._last_key = key
        local = now.astimezone(tz) if tz else now
        entry = {"time": local.isoformat(timespec="seconds"), "hhmm": local.strftime("%H:%M"),
                 "action": decision.action, "rule": decision.rule, "text": decision.sentence(passive),
                 "label": decision.label_target_soc}
        self.entries.insert(0, entry)
        del self.entries[MAX_ENTRIES:]
        return entry
