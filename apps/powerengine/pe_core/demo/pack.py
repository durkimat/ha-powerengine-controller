"""The demo data pack: four recorded (scrubbed) days that demo mode replays on any date.

Pure module, no AppDaemon. The pack is built by tools/build_demo_pack.py (see CLAUDE.md, "Demo mode plan"). Per day
it holds 48 half-hours, column by column: house, car and solar kWh, the derived solar forecast, the rates (act, std,
ovn, exp in GBP/kWh, standing in GBP/day), the recorded SoC at the start, and the smart-slot, axle and free flags.

Times are wall-clock offsets from local midnight (half-hour i is at 00:00 + 30 min x i), so a day can be shifted to
any date. On a daylight-saving change the wall-clock times are mapped, not the elapsed hours: a 23-hour day skips
the wall-clock half-hours that don't exist (46 rows), a 25-hour day plays the repeated hour twice (50 rows, the same
data both times). Every row's start and end are timezone-aware and consecutive rows abut exactly.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[2] / "demo" / "pack.json"
SLOTS = 48
STEP = timedelta(minutes=30)
COLUMNS = ("house", "car", "solar", "forecast", "act", "std", "ovn", "exp", "standing", "soc", "slot", "axle", "free")
FLAGS = ("slot", "axle", "free")
TZ = ZoneInfo("Europe/London")


def load_pack(path=DEFAULT_PATH) -> dict:
    """Read and check a pack file. Raises ValueError if it isn't a pack (wrong version, missing or short columns)."""
    with open(path, encoding="utf-8") as fh:
        pack = json.load(fh)
    if not isinstance(pack, dict) or pack.get("version") != 1 or not pack.get("days"):
        raise ValueError("not a demo pack (version 1)")
    for name, day in pack["days"].items():
        for col in COLUMNS:
            if len(day.get(col, [])) != SLOTS:
                raise ValueError(f"demo pack day {name!r}: column {col!r} is not {SLOTS} long")
    return pack


def days(pack: dict) -> list[str]:
    """The day names, in the pack's order."""
    return list(pack["days"])


def _as_date(today) -> date:
    return today.date() if isinstance(today, datetime) else today


def _exists(naive: datetime, tz, fold: int) -> bool:
    """Does this wall-clock time exist on this fold (it doesn't inside a spring-forward gap)?"""
    aware = naive.replace(tzinfo=tz, fold=fold)
    return aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == naive


def _end(start: datetime, tz) -> datetime:
    return (start.astimezone(timezone.utc) + STEP).astimezone(tz)


def _row(day: dict, i: int, start: datetime, tz) -> dict:
    row = {"index": i, "start": start, "end": _end(start, tz)}
    for col in COLUMNS:
        row[col] = bool(day[col][i]) if col in FLAGS else day[col][i]
    return row


def day_at(pack: dict, name: str, today, tz) -> list[dict]:
    """The day's half-hours on the date `today` (a date or datetime), in `tz`, in time order.

    46 rows on a spring-forward date, 50 on an autumn one, else 48."""
    day = pack["days"][name]
    midnight = datetime.combine(_as_date(today), time(0))
    rows = []
    for i in range(SLOTS):
        naive = midnight + STEP * i
        first = naive.replace(tzinfo=tz, fold=0)
        second = naive.replace(tzinfo=tz, fold=1)
        if not _exists(naive, tz, 0):
            continue                                         # inside the gap
        rows.append(_row(day, i, first, tz))
        if second.utcoffset() != first.utcoffset():          # the repeated hour: play it again
            rows.append(_row(day, i, second, tz))
    rows.sort(key=lambda r: r["start"].astimezone(timezone.utc))
    return rows


def row_at(pack: dict, name: str, when: datetime, tz) -> dict:
    """The row covering `when` (must be timezone-aware). The day loops: any date maps onto the same 48 half-hours."""
    local = when.astimezone(tz)
    start = local.replace(minute=local.minute // 30 * 30, second=0, microsecond=0)
    i = start.hour * 2 + start.minute // 30
    return _row(pack["days"][name], i, start, tz)


def _windows(rows: list[dict], flag: str) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for r in rows:
        if not r[flag]:
            continue
        if out and out[-1][1] == r["start"]:
            out[-1] = (out[-1][0], r["end"])
        else:
            out.append((r["start"], r["end"]))
    return out


def smart_slots(rows: list[dict]) -> list[tuple[datetime, datetime]]:
    """Merged (start, end) windows of consecutive half-hours flagged as smart-charge slots."""
    return _windows(rows, "slot")


def axle_events(rows: list[dict]) -> list[tuple[datetime, datetime]]:
    """Merged (start, end) windows of consecutive half-hours flagged as grid-services (Axle) events."""
    return _windows(rows, "axle")


# --- a day from cost records (the pack builder and the engine comparison both use these) ---

def _local(iso: str, tz=TZ) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(tz)


def day_stats(records: list[dict]) -> dict:
    r2 = lambda x: round(x, 2)  # noqa: E731
    return {
        "solar": r2(sum(r["solar"] for r in records)),
        "house": r2(sum(r["house"] for r in records)),
        "car": r2(sum(r["car"] for r in records)),
        "axle_kwh": r2(sum(r["v"].get("event_kwh") or 0.0 for r in records if r.get("axle"))),
        "grid_import": r2(sum(r["grid_import"] for r in records)),
        "grid_export": r2(sum(r["grid_export"] for r in records)),
    }


def day_complete(records: list[dict], tz=TZ) -> str | None:
    """None if the day is complete, else why not: 48 records on consecutive half-hours from local midnight, at least
    95 % of the day's seconds covered, no duplicate starts."""
    if len(records) != 48:
        return f"{len(records)} records, not 48"
    if sum(r.get("seconds") or 0 for r in records) < 0.95 * 86400:
        return "under 95% coverage"
    starts = [_local(r["start"], tz) for r in records]
    if len(set(starts)) != 48:
        return "duplicate starts"
    first = starts[0]
    if first.hour or first.minute:
        return "does not start at local midnight"
    for i, s in enumerate(starts):
        if (s.hour, s.minute) != (i // 2, i % 2 * 30) or s.date() != first.date():
            return "half-hours are not consecutive from local midnight"
    return None


def forecast(solar: list[float], scale: float) -> list[float]:
    """Derived forecast: the actual solar smoothed over 2 hours (a centred window of 4 half-hours), scaled so the day's
    total is `scale` times the actual total."""
    n = len(solar)
    smooth = []
    for i in range(n):
        window = solar[max(0, i - 2):min(n, i + 2)]
        smooth.append(sum(window) / 4.0)          # /4 even at the edges: no solar outside the day
    total, actual = sum(smooth), sum(solar)
    k = scale * actual / total if total else 0.0
    return [round(x * k, 4) for x in smooth]


def _levels(records: list[dict]) -> list[float]:
    """The level at each half-hour's start; a record without one takes the previous level (the first, 50 %)."""
    out, last = [], 50.0
    for r in records:
        v = r.get("soc_start")
        last = float(v) if v is not None else last
        out.append(last)
    return out


def day_from_records(records: list[dict], title: str = "", rule: str = "", scale: float = 1.0, tz=TZ) -> dict:
    """One pack day from a complete local day's cost records (see `day_complete`)."""
    solar = [max(0.0, float(r["solar"])) for r in records]
    v = [r["v"] for r in records]
    return {
        "title": title, "rule": rule, "recorded": _local(records[0]["start"], tz).date().isoformat(),
        "stats": day_stats(records),
        "house": [round(float(r["house"]), 4) for r in records],
        "car": [round(float(r["car"]), 4) for r in records],
        "solar": [round(x, 4) for x in solar],
        "forecast": forecast(solar, scale),
        "act": [round(float(x["act"]), 5) for x in v], "std": [round(float(x["std"]), 5) for x in v],
        "ovn": [round(float(x["ovn"]), 5) for x in v], "exp": [round(float(x["exp"]), 5) for x in v],
        "standing": [round(float(r.get("standing") or 0.0), 4) for r in records],
        "soc": [round(x, 1) for x in _levels(records)],
        "slot": [int(bool(x["slot"])) for x in v],
        "axle": [int(bool(r["axle"])) for r in records],
        "free": [int(bool(r["free"])) for r in records],
        "as_recorded": {k: [round(float(r.get(k) or 0.0), 3) for r in records]
                        for k in ("grid_import", "grid_export", "battery_in", "battery_out")},
    }
