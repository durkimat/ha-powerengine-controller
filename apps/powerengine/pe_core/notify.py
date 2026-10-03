"""Phone notifications: what to say, and making sure each thing is said once.

The app calls the configured notify service (the HA companion app). This module only decides; it never calls HA.
Each message has a key; a key is sent once until it is cleared (e.g. an input recovers), and no more than
DAILY_CAP messages go out per day whatever happens.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from .names import N

DAILY_CAP = 10


class Notifier:
    def __init__(self, path: str | None = None):
        self.path = path
        self.sent: dict[str, str] = {}
        self.per_day: dict[str, int] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                self.sent, self.per_day = dict(raw.get("sent", {})), dict(raw.get("per_day", {}))
            except (OSError, ValueError):
                pass

    def should_send(self, key: str, now: datetime) -> bool:
        return key not in self.sent and self.per_day.get(now.date().isoformat(), 0) < DAILY_CAP

    def mark(self, key: str, now: datetime) -> None:
        day = now.date().isoformat()
        self.sent[key] = now.isoformat(timespec="seconds")
        self.per_day = {d: n for d, n in self.per_day.items() if d >= day} | {day: self.per_day.get(day, 0) + 1}
        if len(self.sent) > 500:                                  # keep the newest keys
            self.sent = dict(sorted(self.sent.items(), key=lambda kv: kv[1])[-300:])
        self.save()

    def clear(self, prefix: str) -> None:
        gone = [k for k in self.sent if k.startswith(prefix)]
        for k in gone:
            del self.sent[k]
        if gone:
            self.save()

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"sent": self.sent, "per_day": self.per_day}, fh)
        os.replace(tmp, self.path)


# --- messages (key, title, message) ------------------------------------------------------

def _when(t: datetime, now: datetime, tz=None) -> str:
    lt, ln = (t.astimezone(tz), now.astimezone(tz)) if tz else (t, now)
    gap = (lt.date() - ln.date()).days
    day = "today" if gap == 0 else ("tomorrow" if gap == 1 else lt.strftime("%a %d %b"))
    return f"{lt:%H:%M} {day}"


def health_message(health: dict) -> tuple[str, str, str] | None:
    problems = [f for f in health.get("findings", []) if f.get("level") == "problem"]
    if not problems:
        return None
    key = "health:" + "|".join(sorted(f["title"] for f in problems))
    title = "PowerEngine: " + ("a problem to look at" if len(problems) == 1 else f"{len(problems)} problems to look at")
    return key, title, " ".join(f"{f['title']}." for f in problems) + " See the Health tab."


def input_message(role: str, label: str, status: str, detail: str) -> tuple[str, str, str]:
    return (f"input:{role}", f"PowerEngine: {label} not working",
            f"{label} has been {status.replace('_', ' ')} for 15 minutes ({detail}). "
            "PowerEngine's decisions may be off.")


def axle_message(start: datetime, end: datetime | None, now: datetime, tz=None) -> tuple[str, str, str]:
    until = f" to {(end.astimezone(tz) if tz else end):%H:%M}" if end else ""
    return (f"axle:{start.isoformat()}", f"{N('event')} event scheduled",
            f"{N('event')} export event at {_when(start, now, tz)}{until}. "
            "PowerEngine will keep the battery ready for it.")


def free_message(start: datetime, end: datetime | None, now: datetime, tz=None) -> tuple[str, str, str]:
    until = f" to {(end.astimezone(tz) if tz else end):%H:%M}" if end else ""
    return (f"free:{start.isoformat()}", "Free electricity session",
            f"Free electricity from {_when(start, now, tz)}{until}.")


def daily_message(summary: dict, steps: list[dict] | None = None) -> tuple[str, str, str]:
    """Yesterday's costs in the Costs page's waterfall steps (`costs.waterfall`'s `steps`: dicts with label, kind,
    value; negative steps are savings). Without steps (no scenario figures for the day), just paid against no solar
    or battery."""
    s = summary
    def gbp(v: float) -> str:
        return f"−£{-v:.2f}" if v < -0.005 else f"£{abs(v) if abs(v) < 0.005 else v:.2f}"
    def signed(v: float) -> str:
        return f"+£{v:.2f}" if v > 0.005 else gbp(v)
    totals = [x for x in steps or [] if x["kind"] == "total"]
    if len(totals) == 2:
        base, paid = totals[0]["value"], totals[1]["value"]
        parts = ", ".join(f"{x['label']} {signed(x['value'])}" for x in steps if x["kind"] == "step")
        body = f"You paid {gbp(paid)} against {gbp(base)} with no solar or battery. {parts}."
    else:
        paid = s["actual"]
        body = f"You paid {gbp(paid)} against {gbp(s['s0'])} with no solar or battery."
    return (f"daily:{s['date']}", f"PowerEngine: yesterday {gbp(paid)}", body)
