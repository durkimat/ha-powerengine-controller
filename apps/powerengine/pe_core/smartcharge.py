"""Smart-charge optimisation (FR-8): asking EDF for extra smart-charge slots by changing the car's ready-by time.

Changing the ready-by time makes EDF re-plan the car's charging, which often creates new slots (the whole house
gets the slot rate). What PowerEngine does, decided with Matthew on 26 Sep 2026:
  - Ask only when it's worth it: the car is plugged in and not charging, no slot is planned in the next 3 hours,
    import isn't already cheap, and either the battery has room or arbitrage is on and cheap power could be sold
    for more than it costs.
  - Pick the next nearest ready-by time (EDF currently offers morning times only); it must differ from the
    current one, since the change is what prompts EDF to re-plan. The charge target stays at 100%.
  - Check about 15 minutes later whether new slots appeared. If not, back off: 30, 60, 120, then 240 minutes
    before the next try; at most 6 requests a day and never within 20 minutes of the last one, so EDF's API is
    never hammered. Success or unplugging resets the back-off.
  - Changes made by anything else (e.g. the four "IO Schedule" automations) are logged with their outcome too,
    which gives the baseline to compare against.
In Passive mode nothing is sent: requests are recorded as "would" so the timing can be reviewed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

MIN_GAP = timedelta(minutes=20)
CHECK_AFTER = timedelta(minutes=15)
BACKOFF_MIN = (30, 60, 120, 240)
DAILY_CAP = 6
MIN_LEAD = timedelta(minutes=60)
LOOKAHEAD = timedelta(hours=3)
KEEP_DAYS = 30


def choose_time(options: list[str], current: str | None, now_local: datetime) -> str | None:
    """The next nearest ready-by option at least an hour ahead (tomorrow's first if today's have passed),
    moved on by one if it equals the current setting (the change is what makes EDF re-plan)."""
    opts = sorted(o[:5] for o in options if o and len(o) >= 5)
    if not opts:
        return None
    cur = (current or "")[:5]
    lead = (now_local + MIN_LEAD).strftime("%H:%M")
    later = [o for o in opts if o >= lead] if (now_local + MIN_LEAD).date() == now_local.date() else []
    pick = later[0] if later else opts[0]
    if pick == cur:
        i = opts.index(pick)
        pick = opts[(i + 1) % len(opts)]
    return pick if pick != cur else None


def worth_asking(ev_state: str, dispatches, now: datetime, cheap_now: bool, soc: float | None, target: float,
                 arbitrage: bool, export_p: float | None, cheap_p: float | None) -> tuple[bool, str]:
    if ev_state != "plugged_in":
        return False, "car not plugged in" if ev_state == "unplugged" else "car already charging"
    if any(w.start - now <= LOOKAHEAD and w.end > now for w in dispatches):
        return False, "a slot is already planned soon"
    if cheap_now:
        return False, "import is already cheap"
    if soc is not None and soc < target - 5:
        return True, f"battery at {soc:.0f}% has room for cheap energy"
    if arbitrage and export_p is not None and cheap_p is not None and export_p > cheap_p:
        return True, f"arbitrage is on: cheap power ({cheap_p:.2f}p) can be sold for {export_p:.0f}p"
    return False, "battery full and nothing to gain"


class SmartCharger:
    def __init__(self, path: str | None = None):
        self.path = path
        self.attempts: list[dict] = []
        self.backoff = 0
        self.next_allowed: str | None = None
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                self.attempts, self.backoff = raw.get("attempts", []), raw.get("backoff", 0)
                self.next_allowed = raw.get("next_allowed")
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"attempts": self.attempts, "backoff": self.backoff, "next_allowed": self.next_allowed}, fh)
        os.replace(tmp, self.path)

    def _record(self, now: datetime, by: str, frm: str | None, to: str, dispatches, why: str = "") -> dict:
        a = {"time": now.isoformat(timespec="seconds"), "by": by, "from": frm, "to": to, "why": why,
             "before": sorted(w.start.isoformat() for w in dispatches), "result": None, "gained": 0}
        if by == "would":
            a["result"] = "not sent (Passive)"
        self.attempts.append(a)
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        self.attempts = [x for x in self.attempts if x["time"] >= cutoff]
        return a

    def resolve(self, now: datetime, dispatches) -> bool:
        """Settle attempts whose check time has come. Returns True if any changed."""
        changed = False
        starts = {w.start.isoformat() for w in dispatches}
        for a in self.attempts:
            if a["result"] is None and now >= datetime.fromisoformat(a["time"]) + CHECK_AFTER:
                new = starts - set(a["before"])
                a["gained"], a["result"] = len(new), ("slots" if new else "none")
                if a["by"] == "powerengine":
                    self.backoff = 0 if new else min(self.backoff + 1, len(BACKOFF_MIN) - 1)
                changed = True
        return changed

    def observe_external(self, now: datetime, frm: str | None, to: str, dispatches) -> None:
        self._record(now, "other", frm, to, dispatches)

    def step(self, now: datetime, now_local: datetime, worth: tuple[bool, str], options: list[str],
             current: str | None, dispatches, active: bool) -> dict | None:
        """The request to make now, if any: {"to": "HH:MM", ...}. Records it (as "would" when not active)."""
        ok, why = worth
        if not ok:
            if why == "car not plugged in":
                self.backoff, self.next_allowed = 0, None
            return None
        today = now.date().isoformat()
        mine_today = [a for a in self.attempts if a["by"] in ("powerengine", "would") and a["time"][:10] == today]
        if len(mine_today) >= DAILY_CAP:
            return None
        last = max((datetime.fromisoformat(a["time"]) for a in self.attempts), default=None)
        if last and now - last < MIN_GAP:
            return None
        if self.next_allowed and now < datetime.fromisoformat(self.next_allowed):
            return None
        if any(a["result"] is None for a in self.attempts):
            return None                                       # still waiting to see if the last one worked
        to = choose_time(options, current, now_local)
        if not to:
            return None
        a = self._record(now, "powerengine" if active else "would", current, to, dispatches, why)
        # Passive can't see results, so its back-off just steps up through the day
        step = self.backoff if active else min(len(mine_today), len(BACKOFF_MIN) - 1)
        wait = BACKOFF_MIN[step]
        self.next_allowed = (now + timedelta(minutes=wait)).isoformat(timespec="seconds")
        return a

    def summary(self, now: datetime, days: int = 14, tz=None) -> dict:
        since = (now - timedelta(days=days)).isoformat()
        recent = [a for a in self.attempts if a["time"] >= since]
        def rate(by: str) -> dict:
            xs = [a for a in recent if a["by"] == by and a["result"] in ("slots", "none")]
            won = sum(1 for a in xs if a["result"] == "slots")
            return {"tries": len(xs), "won": won, "rate": round(won / len(xs) * 100) if xs else None}
        rows = []
        for a in recent[-12:]:
            t = datetime.fromisoformat(a["time"])
            lt = t.astimezone(tz) if tz else t
            rows.append({"when": lt.strftime("%a %d %b %H:%M"), "by": a["by"],
                         "change": f"{a['from'] or '?'} → {a['to']}", "result": a["result"] or "waiting",
                         "gained": a["gained"], "why": a.get("why", "")})
        return {"days": days, "powerengine": rate("powerengine"), "other": rate("other"),
                "would_today": sum(1 for a in recent if a["by"] == "would"
                                   and a["time"][:10] == now.date().isoformat()),
                "next_allowed": self.next_allowed, "recent": rows}
