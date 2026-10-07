"""Forecast snapshots: what PowerEngine could see of the future, as it was (docs/plans/engine-pages-and-comparison.md).

The cost history holds only what happened. To replay a day fairly the replay needs what was *forecast* at the time:
the solar forecast, the rates, the smart-charge dispatches, free-power and grid-event announcements, the house profile
in use, and when each smart slot was first seen. This module keeps them as a day file of deltas:

    <costs dir>/snapshots/YYYY-MM-DD.json
    {"version": 1, "day": "2026-10-07", "tz": "Europe/London",
     "roles": {"import_rates_today": "event.edf_..._current_day_rates", ...},
     "entries": [{"at": "2026-10-07T00:00:12+01:00",
                  "states": {"<entity id>": {"state": "...", "attributes": {...}}},
                  "profile": {"days": 14.0, "watts": {"0|0": 412.0, ...}},      # key "<weekend 0|1>|<half-hour 0-47>"
                  "first_seen": {"2026-10-07T01:30:00+01:00": "2026-10-06T19:02:00+01:00"}}]}

An entry holds only what changed since the last one (an entity at most once per 5 minutes), plus a full entry on the
first cycle of a local day. `profile` and `first_seen` appear only when they changed. Replaying the entries up to a time
gives every entity's state then: `states_at`, `profile_at`, `first_seen_at` (pure, used by the comparison runner).

The files hold entity ids and never leave the host; the diagnostics export carries only `summary`.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

VERSION = 1
KEEP_DAYS = 14
MIN_GAP_S = 300                  # one entry per entity per 5 minutes
# A real day is about 1 to 1.5 MB (6 Oct, from the owner's export: 470 KB by 06:46, the EDF dispatch list is 13 KB and
# changes ~40 times a day, each Solcast entity 12 KB). 800 KB / 1 MB (the first build) would have cut the evening off.
SOFT_BYTES = 3_000_000           # past this an entity is written at most every 30 minutes
HARD_BYTES = 5_000_000           # past this nothing more is written that day
SLOW_GAP_S = 1800
FIRST_SEEN_BEFORE = timedelta(days=1)       # smart slots near the day: from a day before ...
FIRST_SEEN_AFTER = timedelta(days=3)        # ... to three days after its start

# The roles an adapter reads future information from (forecast, rates, dispatches, free power, grid events).
ROLE_KEYS = (
    "solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3",
    "import_rate_now", "import_rates_today", "import_rates_tomorrow", "export_rate", "standing_charge",
    "smart_dispatches",
    "free_power_active", "free_power_next_start", "free_power_next_end",
    "axle_event_active", "axle_event_start", "axle_event_end", "axle_direction",
)


def snapshot_roles(cfg) -> dict[str, str]:
    """{role: entity id} of the mapped roles worth snapshotting (only the ones mapped to an entity)."""
    inputs = (getattr(cfg, "inputs", None) or {}) if cfg is not None else {}
    out = {}
    for role in ROLE_KEYS:
        eid = (inputs.get(role) or {}).get("entity")
        if eid:
            out[role] = eid
    return out


# ---- pure helpers: reading a snapshot -------------------------------------------------------------------------------
def _when(t) -> datetime:
    return t if isinstance(t, datetime) else datetime.fromisoformat(str(t))


def _entries_upto(snapshot: dict, when) -> list[dict]:
    t = _when(when)
    return [e for e in (snapshot or {}).get("entries", []) if _when(e["at"]) <= t]


def states_at(snapshot: dict, when) -> dict[str, dict]:
    """{entity id: {"state", "attributes"}} as the entries up to `when` (a datetime or ISO string) leave them. An
    entity with no entry yet is absent."""
    out: dict[str, dict] = {}
    for e in _entries_upto(snapshot, when):
        for eid, st in (e.get("states") or {}).items():
            out[eid] = {"state": st.get("state"), "attributes": dict(st.get("attributes") or {})}
    return out


def profile_at(snapshot: dict, when) -> dict | None:
    """The house profile in use at `when`: {"days": float, "watts": {"we|hh": W}}, or None before any was saved."""
    prof = None
    for e in _entries_upto(snapshot, when):
        if e.get("profile") is not None:
            prof = e["profile"]
    return None if prof is None else {"days": prof.get("days", 0.0), "watts": dict(prof.get("watts") or {})}


def first_seen_at(snapshot: dict, when) -> dict[str, str]:
    """{smart-slot start iso: when it was first seen} for the slots already known at `when`."""
    t = _when(when)
    seen: dict = {}
    for e in _entries_upto(snapshot, when):
        if e.get("first_seen") is not None:
            seen = e["first_seen"]
    return {k: v for k, v in seen.items() if _when(v) <= t}


def to_load_profile(profile: dict | None):
    """A saved profile as a forecast.LoadProfile (None stays None)."""
    if profile is None:
        return None
    from .forecast import LoadProfile
    watts = {}
    for k, w in (profile.get("watts") or {}).items():
        we, hh = k.split("|")
        watts[(we == "1", int(hh))] = float(w)
    return LoadProfile(watts=watts, days=float(profile.get("days") or 0.0))


def profile_dict(profile) -> dict | None:
    """A forecast.LoadProfile as the file stores it."""
    if profile is None:
        return None
    return {"days": round(float(profile.days), 2),
            "watts": {f"{int(we)}|{hh}": round(float(w), 1) for (we, hh), w in sorted(profile.watts.items())}}


def _norm(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def _plain(x):
    """JSON-safe copy (datetimes and the like become text), so what is compared is what is stored."""
    return json.loads(json.dumps(x, default=str))


def _write_json(path: str, data) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, path)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def summary(folder: str) -> dict:
    """For the diagnostics export: days kept, entries and bytes (no entity ids, no values)."""
    days = []
    try:
        names = sorted(n for n in os.listdir(folder) if n[:4].isdigit() and n.endswith(".json"))
    except OSError:
        names = []
    for n in names:
        path = os.path.join(folder, n)
        data = _read_json(path, {})
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        days.append({"day": n[:-5], "entries": len(data.get("entries", [])), "bytes": size})
    return {"days_kept": len(days), "entries": sum(d["entries"] for d in days),
            "bytes": sum(d["bytes"] for d in days), "days": days[-KEEP_DAYS:]}


# ---- the writer ----------------------------------------
class SnapshotWriter:
    """Appends delta entries to today's file. `observe` is called every cycle; it never raises (the caller warns)."""

    def __init__(self, folder: str, tz=None):
        self.folder, self.tz = folder, tz
        self._day: str | None = None
        self._data: dict | None = None
        self._last: dict[str, str] = {}           # entity id -> normalised last saved value
        self._at: dict[str, datetime] = {}        # entity id -> when it was last saved
        self._profile: str | None = None
        self._first_seen: str | None = None
        self._bytes = 0
        self.skipped = 0                          # updates left out by the size limits (today)

    def _path(self, day: str) -> str:
        return os.path.join(self.folder, f"{day}.json")

    def _open(self, day: str, now: datetime) -> bool:
        """Load or start the day's file. True when the day is new (the first entry must be a full one)."""
        self._day, self.skipped = day, 0
        os.makedirs(self.folder, exist_ok=True)
        data = _read_json(self._path(day), None)
        if not isinstance(data, dict) or data.get("version") != VERSION or not data.get("entries"):
            self._data = {"version": VERSION, "day": day, "tz": str(self.tz) if self.tz else "UTC",
                          "roles": {}, "entries": []}
            self._last, self._at, self._profile, self._first_seen, self._bytes = {}, {}, None, None, 0
            return True
        self._data = data                          # a restart in the day: carry on from what is saved
        self._last = {k: _norm(v) for k, v in states_at(data, "9999-01-01T00:00:00+00:00").items()}
        self._at = {}
        prof = profile_at(data, "9999-01-01T00:00:00+00:00")
        self._profile = _norm(prof) if prof is not None else None
        last_seen = next((e["first_seen"] for e in reversed(data["entries"]) if e.get("first_seen") is not None), None)
        self._first_seen = _norm(last_seen) if last_seen is not None else None
        try:
            self._bytes = os.path.getsize(self._path(day))
        except OSError:
            self._bytes = 0
        return False

    def observe(self, now: datetime, roles: dict[str, str], states: dict[str, dict | None], profile=None,
                first_seen: dict[str, str] | None = None) -> bool:
        """Compare with what was last saved and append an entry for what changed. `states` maps entity id to
        {"state", "attributes"} (None or missing: nothing read). Returns True when an entry was written."""
        local = now.astimezone(self.tz) if self.tz else now
        day = local.date().isoformat()
        full = False
        if day != self._day:
            full = self._open(day, now)
            self.prune(local.date())
        data = self._data
        new_states: dict[str, dict] = {}
        gap = SLOW_GAP_S if self._bytes > SOFT_BYTES else MIN_GAP_S
        for eid, st in states.items():
            if st is None:
                continue
            value = {"state": _plain(st.get("state")), "attributes": _plain(st.get("attributes") or {})}
            norm = _norm(value)
            if norm == self._last.get(eid):
                continue
            last_at = self._at.get(eid)
            if not full and last_at is not None and (now - last_at).total_seconds() < gap:
                continue
            new_states[eid] = value
        entry: dict = {"at": local.isoformat(timespec="seconds")}
        if new_states:
            entry["states"] = new_states
        prof = profile_dict(profile)
        if prof is not None and _norm(prof) != self._profile:
            entry["profile"] = prof
        if first_seen is not None:
            fs = {k: v for k, v in sorted(first_seen.items())}
            if _norm(fs) != self._first_seen and (fs or self._first_seen is not None):
                entry["first_seen"] = fs
        if len(entry) == 1:
            return False
        if self._bytes > HARD_BYTES:
            self.skipped += 1
            return False
        new_roles = {r: e for r, e in roles.items() if data["roles"].get(r) != e}
        data["roles"].update(new_roles)
        data["entries"].append(entry)
        try:
            _write_json(self._path(day), data)
            self._bytes = os.path.getsize(self._path(day))
        except OSError:
            data["entries"].pop()
            raise
        for eid, v in new_states.items():
            self._last[eid] = _norm(v)
            self._at[eid] = now
        if "profile" in entry:
            self._profile = _norm(prof)
        if "first_seen" in entry:
            self._first_seen = _norm(entry["first_seen"])
        return True

    def prune(self, today: date, keep: int = KEEP_DAYS) -> int:
        """Delete day files older than `keep` days. Returns how many."""
        cutoff = (today - timedelta(days=keep)).isoformat()
        n = 0
        try:
            names = os.listdir(self.folder)
        except OSError:
            return 0
        for name in names:
            if name.endswith(".json") and name[:4].isdigit() and name[:10] < cutoff:
                try:
                    os.remove(os.path.join(self.folder, name))
                    n += 1
                except OSError:
                    pass
        return n


def slot_first_seen(slots: dict, day: date, tz=None) -> dict[str, str]:
    """{slot start: first seen} from SlotTracker.slots for slots near `day` (bounds the entry's size)."""
    lo = datetime(day.year, day.month, day.day, tzinfo=tz or timezone.utc) - FIRST_SEEN_BEFORE
    hi = lo + FIRST_SEEN_BEFORE + FIRST_SEEN_AFTER
    out = {}
    for k, rec in (slots or {}).items():
        fs = rec.get("first_seen")
        if not fs:
            continue
        try:
            if lo <= datetime.fromisoformat(k) <= hi:
                out[k] = fs
        except ValueError:
            continue
    return out
