"""Engine v2 history: the day as engine v2 ran it (docs/plans/engine-pages-and-comparison.md section 3).

    <costs dir>/v2history/YYYY-MM-DD.json   (kept 30 days)
    {"version": 1, "day": "2026-10-07", "tz": "Europe/London",
     "expected": {"made_at": iso, "day": [[iso, level], ...],            # the first timeline after local midnight,
                  "hours": {"HH:00": [[iso, level], ...]}},              # and the first one seen in each hour
     "ran": [{"start": iso, "mode": "self_use", "level_end": 54.2, "import_p": 7.0, "export_p": 15.0,
              "value_p": 9.55, "sent": true, "preview": false}],        # one per half-hour, written as it ends
     "changes": [{"at": iso, "mode": "grid_charge", "reason": "..."}]}   # capped at 200 a day

The expected points are the engine's expected level at each half-hour boundary of the day (00:00 to 24:00, the ones
the timeline covers). A series row for the half-hour starting at t compares the level at its end with the expected
level at t + 30 minutes. Recorded whenever engine v2 is stepped, in control or preview (`sent` / `preview` say which).
`attributes` builds the sensor.pe_v2_history attributes (under 12 KB).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

VERSION = 1
KEEP_DAYS = 30
CHANGES_CAP = 200
CHANGES_SHOWN = 60
REASON_MAX = 90
ATTR_TARGET = 12_000
MAX_GAP_S = 120.0            # a longer gap between ticks counts as this much in a half-hour's mix
HALF = timedelta(minutes=30)

NOTE = ("What engine v2 did, half-hour by half-hour, against the level it expected at the start of the day. "
        "Half-hours marked preview were worked out but nothing was sent.")


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


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def half_start(local: datetime) -> datetime:
    return local.replace(minute=local.minute - local.minute % 30, second=0, microsecond=0)


def day_bounds(day: date, tz) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)


def half_hours(day: date, tz) -> list[datetime]:
    """The day's half-hour starts as UTC-offset-aware times, in order (46 or 50 on a clock-change day)."""
    start, end = day_bounds(day, tz)
    t, stop, out = start.astimezone(timezone.utc), end.astimezone(timezone.utc), []
    while t < stop:
        out.append(t.astimezone(tz))
        t += HALF
    return out


def expected_points(path: dict | None, day: date, tz) -> list[list]:
    """The engine's expected level (path["mid"], from path["start"] every path["step_min"] minutes) at each half-hour
    boundary of the day that the path covers, as [[iso, level], ...]."""
    if not path or not path.get("mid") or not path.get("start"):
        return []
    mid = path["mid"]
    step = timedelta(minutes=float(path.get("step_min") or 15))
    t0 = datetime.fromisoformat(path["start"])
    start, end = day_bounds(day, tz)
    bounds = [h.astimezone(timezone.utc) for h in half_hours(day, tz)] + [end.astimezone(timezone.utc)]
    out = []
    for b in bounds:
        k = (b - t0) / step
        if k < 0 or k > len(mid) - 1:
            continue
        i = int(k)
        frac = k - i
        a = mid[i]
        v = a if (frac == 0 or i + 1 >= len(mid)) else a + (mid[i + 1] - a) * frac
        out.append([b.astimezone(tz).isoformat(timespec="seconds"), round(v, 1)])
    return out


class _Acc:
    """The half-hour running now: how long each mode, sending and preview lasted, and the last readings."""

    def __init__(self, start: datetime):
        self.start = start
        self.modes: dict[str, float] = {}
        self.sent = self.preview = self.total = 0.0
        self.level = self.import_p = self.export_p = self.value_p = None

    def add(self, dt: float, mode: str, sent: bool, preview: bool, level, imp, exp, val) -> None:
        self.modes[mode] = self.modes.get(mode, 0.0) + dt
        self.total += dt
        self.sent += dt if sent else 0.0
        self.preview += dt if preview else 0.0
        if level is not None:
            self.level = level
        if imp is not None:
            self.import_p, self.export_p = imp, exp
        if val is not None:
            self.value_p = val

    def record(self) -> dict:
        mode = max(self.modes.items(), key=lambda kv: kv[1])[0]
        half = self.total / 2
        return {"start": self.start.isoformat(timespec="seconds"), "mode": mode, "level_end": _r(self.level, 1),
                "import_p": _r(self.import_p), "export_p": _r(self.export_p), "value_p": _r(self.value_p),
                "sent": self.sent > half, "preview": self.preview > half}


class V2History:
    def __init__(self, folder: str, tz=None):
        self.folder, self.tz = folder, tz or timezone.utc
        self._cache: dict[str, dict] = {}
        self._acc: _Acc | None = None
        self._last_t: datetime | None = None

    # --- files ----------------------------------------
    def _path(self, day: str) -> str:
        return os.path.join(self.folder, f"{day}.json")

    def load(self, day: date | str) -> dict:
        key = day if isinstance(day, str) else day.isoformat()
        if key in self._cache:
            return self._cache[key]
        data = _read_json(self._path(key), None)
        if not isinstance(data, dict) or data.get("version") != VERSION:
            data = {"version": VERSION, "day": key, "tz": str(self.tz), "expected": {"made_at": None, "day": [],
                                                                                    "hours": {}},
                    "ran": [], "changes": []}
        self._cache[key] = data
        for old in sorted(self._cache)[:-2]:
            del self._cache[old]
        return data

    def _save(self, data: dict) -> None:
        os.makedirs(self.folder, exist_ok=True)
        _write_json(self._path(data["day"]), data)

    def days(self) -> list[str]:
        try:
            return sorted(n[:10] for n in os.listdir(self.folder) if n[:4].isdigit() and n.endswith(".json"))
        except OSError:
            return []

    def prune(self, today: date, keep: int = KEEP_DAYS) -> int:
        cutoff = (today - timedelta(days=keep)).isoformat()
        n = 0
        for d in self.days():
            if d < cutoff:
                try:
                    os.remove(self._path(d))
                    self._cache.pop(d, None)
                    n += 1
                except OSError:
                    pass
        return n

    # --- recording ----------------------------------------
    def tick(self, now: datetime, *, mode: str, why: str = "", level=None, import_p=None, export_p=None,
             value_p=None, sent: bool = False, preview: bool = False, path: dict | None = None) -> bool:
        """One engine step. Returns True when a file was written."""
        local = now.astimezone(self.tz)
        day = local.date()
        start = half_start(local)
        wrote = False
        if self._acc is not None and self._acc.start != start:
            wrote = self._finish() or wrote
        if self._acc is None:
            self._acc = _Acc(start)
        dt = 0.0 if self._last_t is None else min(MAX_GAP_S, max(0.0, (now - self._last_t).total_seconds()))
        self._last_t = now
        self._acc.add(dt or 1.0, mode, sent, preview, level, import_p, export_p, value_p)
        data = self.load(day)
        dirty = False
        last = data["changes"][-1]["mode"] if data["changes"] else None
        if mode != last and len(data["changes"]) < CHANGES_CAP:       # the day's opening mode counts as a change
            data["changes"].append({"at": local.isoformat(timespec="seconds"), "mode": mode,
                                    "reason": (why or "")[:REASON_MAX * 2]})
            dirty = True
        if path:
            pts = None
            if not data["expected"]["day"]:
                pts = expected_points(path, day, self.tz)
                if pts:
                    data["expected"]["day"], data["expected"]["made_at"] = pts, local.isoformat(timespec="seconds")
                    dirty = True
            hour = f"{local.hour:02d}:00"
            if hour not in data["expected"]["hours"]:
                pts = pts if pts is not None else expected_points(path, day, self.tz)
                if pts:
                    data["expected"]["hours"][hour] = pts
                    dirty = True
        if dirty:
            self._save(data)
            wrote = True
        return wrote

    def _finish(self) -> bool:
        """Write the finished half-hour's record into its own day's file."""
        acc, self._acc = self._acc, None
        if acc is None or acc.total <= 0:
            return False
        data = self.load(acc.start.date())
        rec = acc.record()
        data["ran"] = sorted([x for x in data["ran"] if x["start"] != rec["start"]] + [rec],
                             key=lambda x: x["start"])
        self._save(data)
        return True

    # --- the sensor ----------------------------------------
    def attributes(self, day: date, today: date, in_control: str | None, live: bool | None = None) -> dict:
        data = self.load(day)
        days = self.days()
        return attributes(data, day, today, self.tz, in_control, live, days[0] if days else None)


def attributes(data: dict, day: date, today: date, tz, in_control: str | None, live: bool | None = None,
               earliest: str | None = None) -> dict:
    """The attributes of sensor.pe_v2_history for one day, under ATTR_TARGET bytes."""
    ran = {x["start"]: x for x in data.get("ran", [])}
    exp = {datetime.fromisoformat(t).astimezone(timezone.utc): v
           for t, v in (data.get("expected") or {}).get("day", [])}
    series = []
    for h in half_hours(day, tz):
        key = h.isoformat(timespec="seconds")
        x = ran.get(key)
        e = exp.get((h + HALF).astimezone(timezone.utc))
        if x is None and e is None:
            continue
        row = {"t": key, "level": x["level_end"] if x else None, "expected": e,
               "mode": x["mode"] if x else None, "import_p": x["import_p"] if x else None,
               "export_p": x["export_p"] if x else None, "value_p": x["value_p"] if x else None,
               "sent": bool(x["sent"]) if x else False}
        if x and x.get("preview"):
            row["preview"] = True
        series.append(row)
    changes = [{"at": c["at"], "mode": c["mode"], "reason": (c.get("reason") or "")[:REASON_MAX]}
               for c in data.get("changes", [])][-CHANGES_SHOWN:]
    attrs = {"date": day.isoformat(), "earliest": min(earliest, day.isoformat()) if earliest else day.isoformat(),
             "latest": today.isoformat(), "in_control": in_control, "live": live,
             "preview_only": bool(ran) and all(x.get("preview") for x in ran.values()),
             "series": series, "changes": changes, "note": NOTE}
    for width, keep in ((60, CHANGES_SHOWN), (40, CHANGES_SHOWN), (40, 40), (30, 30), (20, 20), (10, 10), (0, 5)):
        if _size(attrs) <= ATTR_TARGET:
            break
        attrs["changes"] = [{**c, "reason": c["reason"][:width]} for c in attrs["changes"][-keep:]]
    return attrs


def _size(attrs: dict) -> int:
    return len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def summary(folder: str, tz=None, today: date | None = None) -> dict:
    """For the diagnostics export: days kept, and per day the half-hours, mode changes and bytes."""
    h = V2History(folder, tz)
    rows = []
    for d in h.days():
        data = _read_json(h._path(d), {})
        try:
            size = os.path.getsize(h._path(d))
        except OSError:
            size = 0
        ran = data.get("ran", [])
        rows.append({"day": d, "half_hours": len(ran), "changes": len(data.get("changes", [])),
                     "sent": sum(1 for x in ran if x.get("sent")), "preview": sum(1 for x in ran if x.get("preview")),
                     "has_expected": bool((data.get("expected") or {}).get("day")), "bytes": size})
    return {"days_kept": len(rows), "bytes": sum(r["bytes"] for r in rows), "days": rows[-7:]}
