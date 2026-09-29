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

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[2] / "demo" / "pack.json"
SLOTS = 48
STEP = timedelta(minutes=30)
COLUMNS = ("house", "car", "solar", "forecast", "act", "std", "ovn", "exp", "standing", "soc", "slot", "axle", "free")
FLAGS = ("slot", "axle", "free")


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
