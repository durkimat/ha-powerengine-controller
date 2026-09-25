"""Smart-charge slot tracking: what EDF planned, and what the car actually did.

Every planned dispatch PowerEngine sees gets a record, updated each cycle:
    planned     announced, not started yet (or in progress)
    done        ran to its end (confirmed if EDF lists it as completed)
    cancelled   disappeared before it started
    cut_short   disappeared while it was running (e.g. car unplugged, or finished and EDF stopped it)
While a slot is running, the car's charging energy is added up, so each slot ends with how much the car really
drew. This is the history the slot-certainty score (backlog #14) will be built on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .readings import Window

KEEP_DAYS = 30


class SlotTracker:
    def __init__(self, path: str | None = None):
        self.path = path
        self.slots: dict[str, dict] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.slots = json.load(fh)
            except (OSError, ValueError):
                self.slots = {}

    def update(self, now: datetime, planned: list[Window], completed: list[Window], charging: bool,
               car_w: float | None, dt_s: float) -> bool:
        """Advance one cycle. Returns True if anything was added or changed status (worth saving)."""
        changed = False
        listed = {w.start.isoformat() for w in planned}
        done = {w.start.isoformat() for w in completed}
        for w in planned:
            key = w.start.isoformat()
            rec = self.slots.get(key)
            if rec is None:
                rec = self.slots[key] = {"start": key, "end": w.end.isoformat(),
                                         "planned_kwh": abs(w.value) if w.value is not None else None,
                                         "first_seen": now.isoformat(timespec="seconds"), "status": "planned",
                                         "car_kwh": 0.0, "charging_min": 0.0, "confirmed": False}
                changed = True
            rec["end"] = w.end.isoformat()
            if w.value is not None:
                rec["planned_kwh"] = abs(w.value)                     # EDF/Octopus report it as negative
        for key, rec in self.slots.items():
            start, end = datetime.fromisoformat(rec["start"]), datetime.fromisoformat(rec["end"])
            if key in done and not rec["confirmed"]:
                rec["confirmed"], changed = True, True
            if rec["status"] != "planned":
                continue
            if start <= now < end and charging and dt_s > 0:
                rec["car_kwh"] = round(rec["car_kwh"] + max(0.0, car_w or 0.0) * dt_s / 3.6e6, 4)
                rec["charging_min"] = round(rec["charging_min"] + dt_s / 60, 1)
            if now >= end:
                rec["status"], changed = "done", True
            elif key not in listed and key not in done:
                rec["status"] = "cut_short" if now >= start else "cancelled"
                rec["ended"] = now.isoformat(timespec="seconds")
                changed = True
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        for key in [k for k in self.slots if k < cutoff]:
            del self.slots[key]
            changed = True
        return changed

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.slots, fh)
        os.replace(tmp, self.path)

    def summary(self, now: datetime, days: int = 14, tz=None) -> dict:
        """Counts and recent slots for the Health tab."""
        since = (now - timedelta(days=days)).isoformat()
        recent = sorted((r for k, r in self.slots.items() if k >= since), key=lambda r: r["start"])
        finished = [r for r in recent if r["status"] != "planned"]
        used = [r for r in finished if r["status"] in ("done", "cut_short") and r["car_kwh"] >= 0.2]
        out = {
            "days": days, "slots": len(finished),
            "used": len(used),
            "done_no_car": len([r for r in finished if r["status"] == "done" and r["car_kwh"] < 0.2]),
            "cancelled": len([r for r in finished if r["status"] == "cancelled"]),
            "cut_short": len([r for r in finished if r["status"] == "cut_short"]),
            "planned_kwh": round(sum(r.get("planned_kwh") or 0.0 for r in finished), 1),
            "car_kwh": round(sum(r["car_kwh"] for r in finished), 1),
            "upcoming": len([r for r in recent if r["status"] == "planned"]),
        }
        rows = []
        for r in recent[-12:]:
            s, e = datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])
            ls, le = (s.astimezone(tz), e.astimezone(tz)) if tz else (s, e)
            rows.append({"day": ls.strftime("%a %d %b"), "time": f"{ls:%H:%M}–{le:%H:%M}", "status": r["status"],
                         "planned_kwh": r.get("planned_kwh"), "car_kwh": round(r["car_kwh"], 2),
                         "charging_min": r["charging_min"], "confirmed": r["confirmed"]})
        out["recent"] = rows
        return out
