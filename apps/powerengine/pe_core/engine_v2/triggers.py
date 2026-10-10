"""When to look again, and when to work the values out again (docs/plans/engine-v2.md, section 10).

Two reactions at very different costs: a re-check (layer 5 against the values already worked out; every tick does it)
and a revalue (layers 2 and 3; seconds). `Triggers.due` says when a revalue should run: at once for an urgent event,
otherwise `revalue_coalesce_s` after the first request so a burst runs once, and as a backstop when no revalue has run
for `max_value_age_min`. It also keeps the day's counts and the last events for the health sensor.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .settings import V2Settings
from .types import Event

RECENT = 30
TEXT_MAX = 120
# what a revalue reads as in "values worked out because ..."
BECAUSE = {
    "start": "The engine started", "prices_published": "New prices published", "slots_changed": "Smart slots changed",
    "event_changed": "A grid event or free-power session changed", "event_start": "A grid event started",
    "event_end": "A grid event ended", "free_start": "A free-power session started",
    "free_end": "A free-power session ended", "car_start": "The car started charging",
    "car_stop": "The car stopped charging", "drift": "The house drifted from the forecast",
    "band_exit": "The battery left its expected range", "forecast_update": "The solar forecast changed",
    "deadline": "A charge or sale ran past its expected end", "override": "The manual override changed",
    "settings": "The settings were saved", "replan": "You asked for a re-plan", "learned": "Something learned changed",
    "data_back": "The readings came back",
    "backstop": "The backstop timer", "retry": "Trying again after a problem",
}


LEARNED_REL = 0.01            # a battery or supply fact must move by this share of itself to count as learned
LEARNED_DAYS = 1.0            # the house profile must have gained or lost this many days to count


def _moved(old, new) -> bool:
    """True when `new` differs from `old` by LEARNED_REL of itself (numbers; tuples element by element, a changed
    length or a change between a number and None counts)."""
    if isinstance(old, (tuple, list)) and isinstance(new, (tuple, list)):
        return len(old) != len(new) or any(_moved(a, b) for a, b in zip(old, new, strict=True))
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return abs(new - old) > LEARNED_REL * max(abs(old), abs(new), 1e-9)
    return old != new


def learned_moved(old: tuple | None, new: tuple) -> bool:
    """Have the learned facts moved enough to work the values out again? Each signature is (facts, days): the battery
    and supply facts (capacity, efficiency, powers, tapers, floor, limits) and the days the house profile holds. The
    facts count at a 1% relative change, the profile at a whole day, so rounding in a learned figure doesn't cause a
    revalue (17 of 45 on 6 Oct 2026). No `old` (the first look) is not a change. The caller keeps `new` as the next
    `old` only when this says True (or when there was none), so a slow drift adds up instead of slipping past."""
    if old is None:
        return False
    facts0, days0 = old
    facts1, days1 = new
    if (days0 is None) != (days1 is None) or (days1 is not None and abs(days1 - days0) >= LEARNED_DAYS):
        return True
    return _moved(facts0, facts1)


def _day(now: datetime, tz) -> str:
    return (now.astimezone(tz) if tz else now).date().isoformat()


def _fresh_today(day: str) -> dict:
    return {"day": day, "causes": {}, "revalues": 0, "mode_changes": 0, "flip_flops": 0, "deadlines_missed": 0,
            "backstop": 0, "longest_calc_s": 0.0}


class Triggers:
    def __init__(self, settings: V2Settings, state: dict | None = None):
        self.s = settings
        st = dict(state or {})
        self.pending: list = list(st.get("pending") or [])          # [[kind, text], ...] waiting to be coalesced
        self.pending_since: float | None = st.get("pending_since")
        self.today: dict = dict(st.get("today") or {})
        self.recent: list = list(st.get("recent") or [])
        self.last_causes: list = list(st.get("last_causes") or [])  # the kinds behind the revalue now running
        self.tz = None

    # ---- when -----------------------------------------------------------------------------------
    def due(self, now: datetime, events: tuple[Event, ...], last_value_at: datetime | None) -> str | None:
        """The "because" text when a revalue should run now, else None. Returning text consumes what was pending."""
        ts = now.timestamp()
        urgent = False
        for e in events:
            if e.revalue:
                if not any(k == e.kind for k, _ in self.pending):
                    self.pending.append([e.kind, e.text])
                if self.pending_since is None:
                    self.pending_since = ts
                urgent = urgent or e.urgent
        if self.pending and (urgent or ts - (self.pending_since or ts) >= self.s.revalue_coalesce_s):
            return self._take()
        if last_value_at is not None and now - last_value_at >= timedelta(minutes=self.s.max_value_age_min):
            self.pending.append(["backstop", ""])
            return self._take()
        return None

    def cause(self, kind: str, text: str = "") -> str:
        """A revalue the engine asked for itself (a retry after an error): counted under `kind`."""
        self.last_causes = [kind]
        return text or BECAUSE.get(kind, kind.replace("_", " ").capitalize())

    def _take(self) -> str:
        kinds = [k for k, _ in self.pending]
        self.last_causes = kinds
        self.pending, self.pending_since = [], None
        phrases = []
        for k in kinds:
            p = BECAUSE.get(k, k.replace("_", " ").capitalize())
            if p not in phrases:
                phrases.append(p)
        text = " and ".join(phrases[:2])
        if len(phrases) > 2:
            text += f" (and {len(phrases) - 2} more)"
        return text

    # ---- counts ---------------------------------------------------------------------------------
    def _roll(self, now: datetime) -> dict:
        day = _day(now, self.tz)
        if self.today.get("day") != day:
            self.today = _fresh_today(day)
        return self.today

    def note(self, now: datetime, events: tuple[Event, ...], revalued: bool, mode_changed: bool,
             calc_s: float | None, flip_flop: bool = False) -> None:
        t = self._roll(now)
        mark = None
        for e in events:
            if e.kind == "deadline":
                t["deadlines_missed"] += 1
            if mark is None or (e.urgent and not events[mark].urgent):
                mark = events.index(e)
            self.recent.append({"at": now.isoformat(timespec="seconds"), "kind": e.kind,
                                "text": e.text[:TEXT_MAX], "effect": "revalue" if e.revalue else "recheck"})
        if mode_changed and mark is not None:
            self.recent[len(self.recent) - len(events) + mark]["effect"] = "mode_change"
        self.recent = self.recent[-RECENT:]
        if mode_changed:
            t["mode_changes"] += 1
        if flip_flop:
            t["flip_flops"] += 1
        if revalued:
            t["revalues"] += 1
            for k in self.last_causes or ["start"]:
                t["causes"][k] = t["causes"].get(k, 0) + 1
            if "backstop" in self.last_causes:
                t["backstop"] += 1
            self.last_causes = []
            if calc_s is not None:
                t["longest_calc_s"] = round(max(t["longest_calc_s"], calc_s), 2)

    def health(self, now: datetime) -> dict:
        t = dict(self._roll(now))
        t["causes"] = dict(t["causes"])
        t["pending"] = [k for k, _ in self.pending]
        return t

    def state(self) -> dict:
        return {"pending": [list(p) for p in self.pending], "pending_since": self.pending_since,
                "today": dict(self.today), "recent": list(self.recent), "last_causes": list(self.last_causes)}

