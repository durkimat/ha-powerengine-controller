"""A minimal reader of the forecast snapshot (docs/plans/engine-pages-and-comparison.md, 2.2), for the replay.

A snapshot file is `{"version": 1, "day", "tz", "roles": {role: entity_id}, "entries": [{"at", "states": {entity_id:
{"state", "attributes"}}, "profile": {"days", "watts": {"we|hh": W}}, "first_seen": {slot start: first seen}}]}`:
a sequence of deltas, so the state of an entity at time t is the last entry at or before t that names it, the house
profile the last one that carries `profile`, and the first-seen times the entries' `first_seen` maps merged up to t.

Read only, and small on purpose: the recorder (pe_core/fcsnap.py) has its own reader.
"""

from __future__ import annotations

import bisect
import json
import os
from datetime import datetime, timedelta, timezone

from ..forecast import LoadProfile

# the world's roles whose entities the feed replaces: what an adapter reads future information from
FED_ROLES = ("solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3", "import_rate_now",
             "import_rates_today", "import_rates_tomorrow", "export_rate", "standing_charge", "smart_dispatches",
             "free_power_active", "free_power_next_start", "free_power_next_end", "axle_event_active",
             "axle_event_start", "axle_event_end")
START_BY = timedelta(minutes=30)          # a usable snapshot has an entry at or before 00:30 local


def _when(text) -> datetime:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def snapshot_path(costs_dir: str, day) -> str:
    return os.path.join(costs_dir, "snapshots", f"{day if isinstance(day, str) else day.isoformat()}.json")


def load_snapshot(costs_dir: str, day) -> dict | None:
    """The day's snapshot file, or None (missing, unreadable or not a snapshot)."""
    try:
        with open(snapshot_path(costs_dir, day), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("entries"), list) else None


class Snapshot:
    def __init__(self, data: dict):
        self.data = data
        self.day, self.tz = data.get("day"), data.get("tz")
        self.roles: dict[str, str] = {k: v for k, v in (data.get("roles") or {}).items() if isinstance(v, str)}
        entries = []
        for e in data.get("entries") or []:
            try:
                entries.append((_when(e["at"]), e))
            except (KeyError, TypeError, ValueError):
                continue
        entries.sort(key=lambda x: x[0])
        self.times = [t for t, _ in entries]
        self._states: list[dict] = []
        self._profile: list[LoadProfile | None] = []
        self._seen: list[dict] = []
        states: dict[str, dict] = {}
        profile: LoadProfile | None = None
        seen: dict[str, str] = {}
        for _, e in entries:
            states = {**states, **{k: v for k, v in (e.get("states") or {}).items() if isinstance(v, dict)}}
            if isinstance(e.get("profile"), dict):
                profile = profile_of(e["profile"])
            if isinstance(e.get("first_seen"), dict):
                seen = {**seen, **{str(k): str(v) for k, v in e["first_seen"].items()}}
            self._states.append(states)
            self._profile.append(profile)
            self._seen.append(seen)

    def index(self, when: datetime) -> int:
        """The last entry at or before `when`, -1 if there is none."""
        return bisect.bisect_right(self.times, when) - 1

    def states_at(self, when: datetime) -> dict[str, dict]:
        """{entity_id: {"state", "attributes"}} as the snapshot knew them at `when` (empty before the first entry)."""
        i = self.index(when)
        return self._states[i] if i >= 0 else {}

    def profile_at(self, when: datetime) -> LoadProfile | None:
        """The house profile in use at `when`; the same object until it changes."""
        i = self.index(when)
        return self._profile[i] if i >= 0 else None

    def first_seen_at(self, when: datetime) -> dict[str, str]:
        """{smart slot start (iso): when it was first seen} known at `when`."""
        i = self.index(when)
        return self._seen[i] if i >= 0 else {}

    def refusal(self, day_start: datetime) -> str | None:
        """Why this snapshot can't carry a replay of the day starting at `day_start` (aware), or None."""
        i = self.index(day_start + START_BY)
        if i < 0:
            return "no forecast record at the start of the day"
        if self._profile[i] is None:
            return "the forecast record has no house profile"
        return None


def profile_of(raw: dict) -> LoadProfile:
    """A LoadProfile from the snapshot's `{"days", "watts": {"we|hh": W}}`."""
    watts = {}
    for key, w in (raw.get("watts") or {}).items():
        try:
            we, hh = str(key).split("|")
            watts[(we.strip().lower() in ("1", "true"), int(hh))] = float(w)
        except (TypeError, ValueError):
            continue
    return LoadProfile(watts=watts, days=float(raw.get("days") or 0.0))


def states_at(snapshot, when: datetime) -> dict[str, dict]:
    """The state of every entity the snapshot knows at `when` (a Snapshot or the raw file's dict)."""
    return (snapshot if isinstance(snapshot, Snapshot) else Snapshot(snapshot)).states_at(when)


class SnapshotFeed:
    """What the demo world serves for the fed roles: the snapshot's state, at world time."""

    def __init__(self, snapshot: Snapshot, roles=FED_ROLES):
        self.snapshot = snapshot
        self.entity = {role: snapshot.roles[role] for role in roles if role in snapshot.roles}

    def get(self, role: str, when: datetime):
        eid = self.entity.get(role)
        if eid is None:
            return None
        st = self.snapshot.states_at(when).get(eid)
        if st is None:
            return None
        return st.get("state"), st.get("attributes") or {}
