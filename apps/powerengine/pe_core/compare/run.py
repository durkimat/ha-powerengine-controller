"""The comparison runner: `python -m pe_core.compare.run --save-dir <dir> --day YYYY-MM-DD [--out <file>]`.

Replays one finished local day through the whole app in the demo world (docs/plans/engine-pages-and-comparison.md, 2.4):
plain self-use, engine v1, engine v2 (Active, RAM remote control, the same start level and learned state) and the
perfect-foresight bound, fed with what happened (house, car, sun, prices charged, smart slots that ran, grid events)
and the forecasts as they were (the day's snapshot). It runs in its own process with the fake AppDaemon, in a temp
folder, with the owner's settings copied in: it can't reach Home Assistant, and nothing it does touches the owner's
files except the result it writes to `<save dir>/costs/compare/YYYY-MM-DD.json`.

Exit code 0: compared; 2: the day was refused (the reason is in the file); 1: failure.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback
from datetime import date, datetime

import yaml

from . import results, settings
from .day import KEY, Refused, load_day, metered_cost
from .replay import Scenario, compare_day
from .snapfeed import SnapshotFeed

TEMPLATE = pathlib.Path(__file__).resolve().parents[2] / "demo" / "config.template"
STATE_FILE = "engine_v2_state.json"
SLOTS_FILE = "slots.json"


def _read_yaml(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def slots_before(slots: dict, day_start: datetime) -> dict:
    """The smart-slot history as it stood at the start of the day: slots that began before it and ended by it. The day's
    own slots are the ones being replayed (the feed announces them again, with their first-seen times)."""
    out = {}
    for key, rec in (slots or {}).items():
        try:
            if datetime.fromisoformat(rec["end"]) <= day_start and datetime.fromisoformat(rec["start"]) < day_start:
                out[key] = rec
        except (KeyError, ValueError, TypeError):
            continue
    return out


def scenario_for(day_input, save_dir: str, tmp: str, hours: float = 24.0) -> Scenario:
    """The scenario for a loaded day: the owner's settings remapped, the owner's learned state and slot history copied
    (the slot history cut at the start of the day), the snapshot's forecasts, profile and first-seen times."""
    owner = _read_yaml(os.path.join(save_dir, "config.yaml"))
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    texts = {e: settings.replay_config(owner, template, e) for e in ("v1", "v2")}
    extra = {}
    state = os.path.join(save_dir, STATE_FILE)
    if os.path.isfile(state):
        extra[STATE_FILE] = state
    try:
        with open(os.path.join(save_dir, "costs", SLOTS_FILE), encoding="utf-8") as fh:
            slots = json.load(fh)
    except (OSError, ValueError):
        slots = {}
    cut = os.path.join(tmp, SLOTS_FILE)
    with open(cut, "w", encoding="utf-8") as fh:
        json.dump(slots_before(slots if isinstance(slots, dict) else {}, day_input.start), fh)
    extra[f"costs/{SLOTS_FILE}"] = cut
    snap = day_input.snapshot
    return Scenario(pack=day_input.pack, day=KEY, tz=day_input.tz, start=day_input.start, hours=hours, backfill=False,
                    config_text=lambda engine: texts[engine], battery=settings.battery_of(texts["v2"]),
                    feed=SnapshotFeed(snap), profile_at=snap.profile_at, first_seen_at=snap.first_seen_at,
                    extra_files=extra)


def run_day(save_dir: str, day: date, hours: float = 24.0, out: str | None = None, say=lambda *a: None) -> int:
    costs = os.path.join(save_dir, "costs")
    t0 = time.monotonic()
    path = out or results.result_path(costs, day)

    def save(result):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(result, fh, separators=(",", ":"))
        os.replace(tmp, path)
    try:
        day_input = load_day(costs, day)
    except Refused as why:
        say(f"{day}: not compared: {why.reason}")
        save(results.refused_result(day, why.status, why.reason, time.monotonic() - t0))
        return 2
    except Exception as err:
        save(results.refused_result(day, "failed", f"could not read the day: {err!r}", time.monotonic() - t0))
        return 1
    if hours < 24:                                   # a short test window: the metered cost of the same hours
        day_input.metered_gbp = metered_cost(day_input.records[:round(hours * 2)])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sc = scenario_for(day_input, save_dir, tmp, hours)
            outcome = compare_day(sc, say)
        for name in ("v1", "v2"):
            if outcome["runs"][name].get("real_calls"):
                raise RuntimeError(f"the {name} replay reached for Home Assistant")
        save(results.build_result(day_input, outcome, time.monotonic() - t0))
    except Exception as err:
        say(traceback.format_exc())
        save(results.refused_result(day, "failed", f"replay failed: {err!r}", time.monotonic() - t0))
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--save-dir", required=True, help="the folder holding config.yaml, costs/ and engine_v2_state.json")
    ap.add_argument("--day", required=True, help="the local day to replay, YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="result file (default: <save dir>/costs/compare/<day>.json)")
    ap.add_argument("--hours", type=float, default=24.0, help="replay only this many hours from midnight (tests)")
    a = ap.parse_args(argv)
    return run_day(a.save_dir, date.fromisoformat(a.day), a.hours, a.out,
                   lambda *m: print(*m, file=sys.stderr, flush=True))


if __name__ == "__main__":
    sys.exit(main())
