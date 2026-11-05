"""How likely a planned EDF smart-charge slot is to really happen (backlog #14).

Built from the slot history SlotTracker keeps. Each finished slot counts as delivered (1), half delivered (0.5)
or not delivered (0):
    done, and EDF lists it as completed or the car charged for the minimum run  -> 1
    done, but neither                                                     -> 0 (probably not billed as a slot)
    cut short while running (car unplugged, or the charge finished)       -> 0.5
    cancelled before it started                                           -> 0

Certainty is worked out for the whole history and for groups of similar slots: overnight (23:00-06:00) or
daytime, and announced well ahead (2 h or more) or at short notice. Each group is pulled towards the overall
figure until it has enough slots of its own, and the overall figure starts from a prior of 70% worth 4 slots,
so a few early results can't swing it to 0% or 100%.

A window that has already started is a different question: the half-hour in progress is certain (its price is on the
tariff now), but the half-hours still to come in it are not, because EDF re-lists, shortens or drops a running dispatch.
`hold()` is the share of those later half-hours that stood, from every window that began (a window that ran to its end
counts all its later half-hours as held, one cut short counts those before the cut), pulled towards the overall figure
until there are enough of them. It is a chance like any other, so the plan prices those half-hours as the slot price
with that chance and the usual price otherwise, and the battery is sold down less deeply on a window that often shrinks.

The planner uses it as an expected price: certainty x slot price + (1 - certainty) x the price without the slot.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .slots import DEFAULT_MIN_CHARGE_MIN, drew

PRIOR, PRIOR_WEIGHT = 0.7, 4.0
GROUP_WEIGHT = 4.0
SHORT_NOTICE = timedelta(hours=2)
HALF = timedelta(minutes=30)


def outcome(rec: dict, min_charge_min: float = DEFAULT_MIN_CHARGE_MIN) -> float | None:
    status = rec.get("status")
    if status == "done":
        return 1.0 if rec.get("confirmed") or drew(rec, min_charge_min) else 0.0
    if status == "cut_short":
        return 0.5
    if status == "cancelled":
        return 0.0
    return None                                     # still planned


def continued(rec: dict, starts: list[datetime]) -> bool:
    """A 'cut short' slot that another began within 10 minutes of (EDF re-listing a running dispatch from the
    current half-hour, recorded before 0.8.10 as cut short): it carried on, so it was delivered."""
    ended, start = datetime.fromisoformat(rec["ended"]), datetime.fromisoformat(rec["start"])
    return any(start < s <= ended + timedelta(minutes=10) and s >= ended - timedelta(minutes=10) for s in starts)


def group(start: datetime, first_seen: datetime | None, tz=None) -> tuple[str, str]:
    lt = start.astimezone(tz) if tz else start
    night = "overnight" if lt.hour >= 23 or lt.hour < 6 else "daytime"
    notice = "short notice" if first_seen is not None and start - first_seen < SHORT_NOTICE else "ahead"
    return night, notice


class Certainty:
    def __init__(self, slots: dict[str, dict], tz=None, min_charge_min: float = DEFAULT_MIN_CHARGE_MIN):
        self.tz = tz
        self.n = 0.0
        self.total = 0.0
        self.groups: dict[tuple[str, str], list[float]] = {}
        self.held = self.later = 0.0                    # later half-hours of started windows: held, counted
        starts = [datetime.fromisoformat(r["start"]) for r in slots.values()]
        for rec in slots.values():
            o = outcome(rec, min_charge_min)
            if rec.get("status") == "cut_short" and rec.get("ended") and continued(rec, starts):
                o = 1.0 if drew(rec, min_charge_min) or rec.get("confirmed") else 0.5
            self._note_run(rec, starts)
            if o is None:
                continue
            start = datetime.fromisoformat(rec["start"])
            seen = datetime.fromisoformat(rec["first_seen"]) if rec.get("first_seen") else None
            g = self.groups.setdefault(group(start, seen, tz), [0.0, 0.0])
            g[0] += o
            g[1] += 1
            self.total += o
            self.n += 1
        self.overall = (self.total + PRIOR * PRIOR_WEIGHT) / (self.n + PRIOR_WEIGHT)

    def _note_run(self, rec: dict, starts: list[datetime]) -> None:
        """Count the half-hours after the first of a window that began: how many of them it held."""
        status = rec.get("status")
        if status not in ("done", "cut_short"):
            return
        start, end = datetime.fromisoformat(rec["start"]), datetime.fromisoformat(rec["end"])
        later = max(0, round((end - start) / HALF) - 1)
        if later == 0:
            return
        if status == "cut_short" and rec.get("ended") and not continued(rec, starts):
            ran = math.ceil((datetime.fromisoformat(rec["ended"]) - start) / HALF)   # half-hours begun before the cut
            held = min(later, max(0, ran - 1))
        else:
            held = later
        self.held += held
        self.later += later

    def hold(self) -> float:
        """The chance that a half-hour still to come in a window that has started stands (see the module text)."""
        return round((self.held + self.overall * GROUP_WEIGHT) / (self.later + GROUP_WEIGHT), 3)

    def score(self, start: datetime, first_seen: datetime | None) -> float:
        s, n = self.groups.get(group(start, first_seen, self.tz), (0.0, 0.0))
        return round((s + self.overall * GROUP_WEIGHT) / (n + GROUP_WEIGHT), 3)

    def summary(self) -> dict:
        return {"overall": round(self.overall, 3), "slots": int(self.n),
                "running": {"later_half_hours": int(self.later), "certainty": self.hold()},
                "groups": [{"when": k[0], "notice": k[1], "slots": int(v[1]),
                            "certainty": round((v[0] + self.overall * GROUP_WEIGHT) / (v[1] + GROUP_WEIGHT), 3)}
                           for k, v in sorted(self.groups.items())]}


def expected_price(slot_price: float, standard_price: float, certainty: float) -> float:
    return certainty * slot_price + (1 - certainty) * standard_price
