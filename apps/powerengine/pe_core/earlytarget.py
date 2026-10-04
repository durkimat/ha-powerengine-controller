"""A charge target reached early in its half-hour (#175): what the app did about it, kept for review.

When the plan's charge target for the running half-hour is reached with time to spare, the controller used to hold
until the half-hour ended. Now it replans at once from the live battery level for the rest of the slot, so the best
action takes over (keep charging if more is wanted, start the next slot's sale early, or hold). This module keeps the
record of every such moment, whether the replan ran and what it chose, or why it did not and the hold stayed
(too little time left, no plan, an error). The records are saved beside the config and go into the diagnostics export,
so the effect (extra energy moved, churn avoided, anything left to optimise) can be reviewed after a week.

Pure: no Home Assistant. The app calls `evaluate` and `record`, and `note_decision` on every cycle.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

SLOT = timedelta(minutes=30)
MIN_LEFT_MIN = 5.0          # less time left than this in the half-hour: not worth a replan, the hold stays
KEEP = 300                  # records kept (about a week of busy days)
AFTER_KEEP = 4              # decision changes noted after a replan, for the rest of that half-hour


def minutes_left(slot_start: datetime, now: datetime) -> float:
    return max(0.0, ((slot_start + SLOT) - now).total_seconds() / 60)


def evaluate(left_min: float, have_plan: bool) -> str | None:
    """None: replan now. Otherwise why the hold stays."""
    if not have_plan:
        return "no_plan"
    if left_min < MIN_LEFT_MIN:
        return "late"
    return None


class EarlyTargets:
    def __init__(self, path: str | None = None):
        self.path = path
        self.records: list[dict] = []
        self.seen: set[str] = set()                # half-hours already looked at (one look per half-hour)
        self._watch: dict | None = None            # the last replan record: what happened after it
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    self.records = list(json.load(fh).get("records", []))[-KEEP:]
            except (OSError, ValueError, AttributeError):
                self.records = []

    def first_look(self, slot: str) -> bool:
        """True the first time a half-hour's early target is seen (later cycles of the same hold are silent)."""
        if slot in self.seen:
            return False
        self.seen.add(slot)
        if len(self.seen) > 200:
            self.seen = set(sorted(self.seen)[-100:])
        return True

    def record(self, rec: dict, replanned: bool) -> None:
        self.records = (self.records + [rec])[-KEEP:]
        self._watch = rec if replanned else None
        self.save()

    def note_decision(self, now: datetime, action: str, rule: str, slot_end: datetime | None) -> None:
        """After a replan: the decisions that followed in the rest of that half-hour (churn, for the review)."""
        w = self._watch
        if w is None:
            return
        end = datetime.fromisoformat(w["slot_end"])
        if now >= end:
            self._watch = None
            return
        after = w.setdefault("after", [])
        last = after[-1]["action"] if after else w.get("new_action")
        if action != last and len(after) < AFTER_KEEP:
            after.append({"t": now.isoformat(timespec="seconds"), "action": action, "rule": rule})
            self.save()

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"records": self.records}, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def summary(self) -> dict:
        """Counts by outcome and what the replans chose, for the diagnostics export and the review."""
        by: dict[str, dict] = {}
        chose: dict[str, int] = {}
        for r in self.records:
            o = by.setdefault(r["outcome"], {"count": 0, "minutes_left": 0.0})
            o["count"] += 1
            o["minutes_left"] = round(o["minutes_left"] + r.get("left_min", 0.0), 1)
            if r["outcome"].startswith("replanned"):
                chose[r.get("new_action") or "?"] = chose.get(r.get("new_action") or "?", 0) + 1
        return {"count": len(self.records), "since": self.records[0]["t"] if self.records else None,
                "by_outcome": by, "replan_chose": chose,
                "settings": {"min_left_min": MIN_LEFT_MIN}}
