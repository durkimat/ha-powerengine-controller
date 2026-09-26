"""How likely a planned EDF smart-charge slot is to really happen (backlog #14).

Built from the slot history SlotTracker keeps. Each finished slot counts as delivered (1), half delivered (0.5)
or not delivered (0):
    done, and EDF lists it as completed or the car drew at least 0.2 kWh   -> 1
    done, but neither                                                     -> 0 (probably not billed as a slot)
    cut short while running (car unplugged, or the charge finished)       -> 0.5
    cancelled before it started                                           -> 0

Certainty is worked out for the whole history and for groups of similar slots: overnight (23:00-06:00) or
daytime, and announced well ahead (2 h or more) or at short notice. Each group is pulled towards the overall
figure until it has enough slots of its own, and the overall figure starts from a prior of 70% worth 4 slots,
so a few early results can't swing it to 0% or 100%.

The planner uses it as an expected price: certainty x slot price + (1 - certainty) x the price without the slot.
"""

from __future__ import annotations

from datetime import datetime, timedelta

PRIOR, PRIOR_WEIGHT = 0.7, 4.0
GROUP_WEIGHT = 4.0
SHORT_NOTICE = timedelta(hours=2)
MIN_CAR_KWH = 0.2


def outcome(rec: dict) -> float | None:
    status = rec.get("status")
    if status == "done":
        return 1.0 if rec.get("confirmed") or (rec.get("car_kwh") or 0.0) >= MIN_CAR_KWH else 0.0
    if status == "cut_short":
        return 0.5
    if status == "cancelled":
        return 0.0
    return None                                     # still planned


def group(start: datetime, first_seen: datetime | None, tz=None) -> tuple[str, str]:
    lt = start.astimezone(tz) if tz else start
    night = "overnight" if lt.hour >= 23 or lt.hour < 6 else "daytime"
    notice = "short notice" if first_seen is not None and start - first_seen < SHORT_NOTICE else "ahead"
    return night, notice


class Certainty:
    def __init__(self, slots: dict[str, dict], tz=None):
        self.tz = tz
        self.n = 0.0
        self.total = 0.0
        self.groups: dict[tuple[str, str], list[float]] = {}
        for rec in slots.values():
            o = outcome(rec)
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

    def score(self, start: datetime, first_seen: datetime | None) -> float:
        s, n = self.groups.get(group(start, first_seen, self.tz), (0.0, 0.0))
        return round((s + self.overall * GROUP_WEIGHT) / (n + GROUP_WEIGHT), 3)

    def summary(self) -> dict:
        return {"overall": round(self.overall, 3), "slots": int(self.n),
                "groups": [{"when": k[0], "notice": k[1], "slots": int(v[1]),
                            "certainty": round((v[0] + self.overall * GROUP_WEIGHT) / (v[1] + GROUP_WEIGHT), 3)}
                           for k, v in sorted(self.groups.items())]}


def expected_price(slot_price: float, standard_price: float, certainty: float) -> float:
    return certainty * slot_price + (1 - certainty) * standard_price
