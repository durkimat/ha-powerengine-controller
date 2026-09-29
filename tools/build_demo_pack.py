"""Build apps/powerengine/demo/pack.json from PowerEngine's half-hourly cost records.

    python tools/build_demo_pack.py COSTS_DIR [--out apps/powerengine/demo/pack.json] [--glob "2026-09-*.json"]

COSTS_DIR holds one JSON list per local day (<config>/powerengine/costs/YYYY-MM-DD.json). Four days are picked by
rule and the choice is printed; days are taken in this order and a day that already won an earlier rule is skipped,
so the next best takes the later rule:

    sunny   most solar          dull   least solar          axle   largest grid-services export
    car     most car kWh

Only complete days count: 48 records on consecutive half-hours from local midnight (Europe/London), at least 95 % of
the day's seconds covered, no duplicate starts.

Scrub rule: the pack keeps only numbers, times and flags. Every string in a source record must be a whitelisted field
holding an expected value; anything else (an entity id, account number, MPAN, serial, site id) stops the build. Never
commit a pack built from a run that was not scrubbed this way.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/London")
RULES = (  # key, title, what wins
    ("sunny", "Sunny day", "most solar"),
    ("dull", "Dull day", "least solar of the complete days"),
    ("axle", "<<event>> event day", "largest grid-services export"),
    ("car", "Car charging day", "most car charging"),
)
# string fields a source record may hold, and what each may say. Anything else refuses the build.
ALLOWED_STRINGS = {
    "start": lambda s: bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(\+00:00|Z)?", s)),
    "source": lambda s: s == "history",
    "fv": lambda s: bool(re.fullmatch(r"\d+-[0-9a-f]{8}", s)),        # a rates-version tag; dropped, not kept
    "v.event": lambda s: s in ("axle", "free_power"),
}
FORECAST_SCALE = (1.06, 0.94, 1.08, 0.93)   # per chosen day, so the derived forecast is a little off, like a real one
COLUMNS = ("house", "car", "solar", "forecast", "act", "std", "ovn", "exp", "standing", "soc", "slot", "axle", "free")


class ScrubError(ValueError):
    pass


def scrub_check(rec: dict, where: str) -> None:
    """Refuse a record with any string outside ALLOWED_STRINGS (checked recursively, dict and list values too)."""
    def walk(value, path):
        if isinstance(value, str):
            ok = ALLOWED_STRINGS.get(path)
            if not ok or not ok(value):
                raise ScrubError(f"{where}: unexpected string in field {path!r}: refusing to build")
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(value, list):
            for v in value:
                walk(v, path + "[]")
    walk(rec, "")


def _local(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(TZ)


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


def complete(records: list[dict]) -> str | None:
    """None if the day is complete, else why not."""
    if len(records) != 48:
        return f"{len(records)} records, not 48"
    if sum(r.get("seconds") or 0 for r in records) < 0.95 * 86400:
        return "under 95% coverage"
    starts = [_local(r["start"]) for r in records]
    if len(set(starts)) != 48:
        return "duplicate starts"
    first = starts[0]
    if first.hour or first.minute:
        return "does not start at local midnight"
    for i, s in enumerate(starts):
        if (s.hour, s.minute) != (i // 2, i % 2 * 30) or s.date() != first.date():
            return "half-hours are not consecutive from local midnight"
    return None


def select_days(days: dict[str, dict]) -> dict[str, str]:
    """days: name -> stats (complete days only). Returns rule key -> day name; ties go to the earliest date, and a day
    that has already won a rule is skipped so the next best takes the later one."""
    metric = {"sunny": ("solar", 1), "dull": ("solar", -1), "axle": ("axle_kwh", 1), "car": ("car", 1)}
    taken: dict[str, str] = {}
    for key, _title, _why in RULES:
        field, sign = metric[key]
        order = sorted(days, key=lambda n: (-sign * days[n][field], n))
        pick = next((n for n in order if n not in taken.values()), None)
        if pick is None:
            raise ValueError(f"not enough complete days to choose {key}")
        taken[key] = pick
    return taken


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


def build_day(records: list[dict], key: str, title: str, why: str, scale: float) -> dict:
    solar = [max(0.0, float(r["solar"])) for r in records]
    v = [r["v"] for r in records]
    return {
        "title": title, "rule": why, "recorded": _local(records[0]["start"]).date().isoformat(),
        "stats": day_stats(records),
        "house": [round(float(r["house"]), 4) for r in records],
        "car": [round(float(r["car"]), 4) for r in records],
        "solar": [round(x, 4) for x in solar],
        "forecast": forecast(solar, scale),
        "act": [round(float(x["act"]), 5) for x in v], "std": [round(float(x["std"]), 5) for x in v],
        "ovn": [round(float(x["ovn"]), 5) for x in v], "exp": [round(float(x["exp"]), 5) for x in v],
        "standing": [round(float(r["standing"]), 4) for r in records],
        "soc": [round(float(r["soc_start"]), 1) for r in records],
        "slot": [int(bool(x["slot"])) for x in v],
        "axle": [int(bool(r["axle"])) for r in records],
        "free": [int(bool(r["free"])) for r in records],
        "as_recorded": {k: [round(float(r[k]), 3) for r in records]
                        for k in ("grid_import", "grid_export", "battery_in", "battery_out")},
    }


def scrub_pack(pack: dict) -> None:
    """The finished pack may hold strings only in fixed metadata places."""
    fixed = {"source", "generated", "tz", "forecast", "note"}
    for name, day in pack["days"].items():
        for k, v in day.items():
            if isinstance(v, str) and k not in ("title", "rule", "recorded"):
                raise ScrubError(f"pack day {name}: string in {k!r}")
            if isinstance(v, list) and any(isinstance(x, str) for x in v):
                raise ScrubError(f"pack day {name}: strings in column {k!r}")
    for k, v in pack.items():
        if isinstance(v, str) and k not in fixed:
            raise ScrubError(f"pack: string in {k!r}")


def build(files: list[str], log=print) -> dict:
    loaded: dict[str, list[dict]] = {}
    for path in files:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            records = json.load(fh)
        if not isinstance(records, list):
            raise ScrubError(f"{name}: not a list of records")
        for rec in records:
            scrub_check(rec, name)
        why = complete(records)
        if why:
            log(f"skipped {name}: {why}")
            continue
        loaded[name] = records
    stats = {n: day_stats(r) for n, r in loaded.items()}
    picks = select_days(stats)
    out_days = {}
    for i, (key, title, why) in enumerate(RULES):
        name = picks[key]
        s = stats[name]
        log(f"{key:6} {name}: solar {s['solar']} house {s['house']} car {s['car']} axle {s['axle_kwh']} kWh  ({why})")
        out_days[key] = build_day(loaded[name], key, title, why, FORECAST_SCALE[i])
    pack = {
        "version": 1, "source": "recorded, scrubbed", "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tz": "Europe/London",
        "forecast": "derived: the recorded solar smoothed over 2 hours, scaled per day to within 10% of the actual",
        "note": "Times are half-hours from local midnight. Flows are kWh, rates GBP/kWh, standing GBP/day, soc %.",
        "days": out_days,
    }
    scrub_pack(pack)
    return pack


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("costs_dir")
    ap.add_argument("--glob", default="20*-*-*.json")
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "apps", "powerengine", "demo", "pack.json")
    ap.add_argument("--out", default=os.path.normpath(default_out))
    a = ap.parse_args(argv)
    day_file = re.compile(r"\d{4}-\d\d-\d\d\.json")
    files = sorted(f for f in glob.glob(os.path.join(a.costs_dir, a.glob)) if day_file.fullmatch(os.path.basename(f)))
    if not files:
        print("no day files found", file=sys.stderr)
        return 1
    try:
        pack = build(files)
    except (ScrubError, ValueError) as err:
        print("refused:", err, file=sys.stderr)
        return 2
    text = json.dumps(pack, separators=(",", ":"))
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"wrote {a.out}: {len(text) / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
