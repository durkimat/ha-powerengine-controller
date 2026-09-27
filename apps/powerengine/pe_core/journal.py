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


STAGED_PARTS = ("_start_hour", "_start_minute", "_end_hour", "_end_minute")


def is_staged(name: str) -> bool:
    """Window start/end times (role or entity id). SolaX Modbus keeps these in HA and only sends them to the
    inverter when the update button is pressed (one block write), so setting them isn't an inverter write."""
    s = (name or "").lower()
    return any(part in s for part in STAGED_PARTS)


def _short(entity: str) -> str:
    name = entity.split(".", 1)[-1]
    for prefix in ("solis_inverter_", "solis_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.replace("_", " ")


def day_summary(entries: list[dict], since_iso: str, tz=None, recent: int = 30) -> dict:
    """Today's writes from the journal (entries at or after since_iso, UTC ISO strings).
    'writes' counts real inverter writes (button presses and directly written settings); staged window times are
    counted separately."""
    today = [e for e in entries if e.get("t", "") >= since_iso]
    real = [e for e in today if not is_staged(e.get("entity", ""))]
    reasons: dict[str, int] = {}
    for e in real:
        reasons[e.get("why") or "control"] = reasons.get(e.get("why") or "control", 0) + 1
    changes = len({(e["t"][:16], e.get("why")) for e in real})

    def fmt(e):
        t = datetime.fromisoformat(e["t"])
        if tz is not None:
            t = t.astimezone(tz)
        what = "pressed" if e.get("value") is None else f"{e.get('before')} → {e.get('value')}"
        return {"time": t.strftime("%H:%M:%S"), "setting": _short(e.get("entity", "")), "change": what,
                "why": e.get("why") or ""}
    return {"writes": len(real), "staged": len(today) - len(real), "changes": changes,
            "by_reason": sorted(({"why": k, "writes": n} for k, n in reasons.items()), key=lambda x: -x["writes"]),
            "recent": [fmt(e) for e in reversed(real[-recent:])]}
