"""The comparison runner on a day built from a demo pack day: both engines, self-use, the bound, the calibration, the
days it refuses, and what the replayed app is given (the owner's settings and learned state, the snapshot's forecasts,
profile and first-seen times). Short windows: the full day takes minutes (see the result's `took_s`)."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml
from compare_support import DAY, TEMPLATE, make_save_dir, snapshot_from_pack

from pe_core.compare import day as daymod
from pe_core.compare import replay, results, run
from pe_core.compare.snapfeed import Snapshot, SnapshotFeed
from pe_core.demo import pack as packmod

PACK = packmod.load_pack()
LONDON = ZoneInfo("Europe/London")
APP = os.path.join(os.path.dirname(__file__), "..", "apps", "powerengine")


def tree(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder) for d, _, files in os.walk(folder) for f in files)


def test_the_runner_compares_a_day_and_writes_the_result_file(tmp_path):
    owner = yaml.safe_load(TEMPLATE.read_text())
    owner["inputs"]["battery_capacity"] = {"value": 14.3}
    owner["safety"]["min_reserve_soc"] = 15
    save = make_save_dir(tmp_path / "pe", PACK, "car", owner_config=owner, engine="v1", live=True)
    before = {p: open(save / p, "rb").read() for p in tree(save)}
    messages = []
    assert run.run_day(str(save), DAY, hours=2, say=messages.append) == 0
    path = save / "costs" / "compare" / "2026-10-06.json"
    r = json.loads(path.read_text())
    assert set(r) == {"version", "day", "status", "reason", "took_s", "made_at", "in_control", "live", "selfuse", "v1",
                      "v2", "bound", "metered", "calibration"}
    assert (r["version"], r["day"], r["status"], r["reason"], r["in_control"], r["live"]) == (
        1, "2026-10-06", "ok", "", "v1", True)
    assert set(r["selfuse"]) == {"cost", "end_soc"}
    for engine in ("v1", "v2"):
        assert set(r[engine]) == {"cost", "end_soc", "saving", "flips", "modes"}
        assert r[engine]["modes"] >= 1 and 12 <= r[engine]["end_soc"] <= 100
        assert r[engine]["saving"] == round(r[engine]["saving"], 3)
    assert set(r["bound"]) == {"cost", "saving"} and r["bound"]["saving"] >= r["v1"]["saving"] - 0.01
    # the same arithmetic the file states: saving = self-use cost - engine cost + end level adjustment
    cheapest = min(PACK["days"]["car"]["act"])
    for engine in ("v1", "v2"):
        adj = (r[engine]["end_soc"] - r["selfuse"]["end_soc"]) / 100 * 14.3 * cheapest      # the owner's battery size
        assert abs(r[engine]["saving"] - (r["selfuse"]["cost"] - r[engine]["cost"] + adj)) < 0.005
    cal = r["calibration"]                                                 # v1 was live: its replay against the meter
    assert cal["engine"] == "v1" and cal["replay"] == r["v1"]["cost"] and cal["metered"] == r["metered"]["cost"]
    assert abs(cal["diff"] - (cal["replay"] - cal["metered"])) < 0.002
    assert isinstance(r["took_s"], int) and r["took_s"] >= 1 and r["made_at"].endswith("+00:00")
    assert any("engine v1" in m for m in messages) and any("engine v2" in m for m in messages)
    # nothing of the owner's was touched: only the result was added, nothing was written to Home Assistant
    after = tree(save)
    assert after == sorted(set(before) | {"costs/compare/2026-10-06.json"})
    assert all(open(save / p, "rb").read() == before[p] for p in before)


def test_the_replayed_app_gets_the_snapshots_profile_and_slot_times_and_the_owners_state(tmp_path):
    owner = yaml.safe_load(TEMPLATE.read_text())
    owner["inputs"]["battery_capacity"] = {"value": 14.3}
    profile = {"days": 11.0, "watts": {f"{we}|{hh}": 1234.5 for we in (0, 1) for hh in range(48)}}
    save = make_save_dir(tmp_path / "pe", PACK, "car", owner_config=owner, profile=profile)
    snap = json.loads((save / "costs" / "snapshots" / "2026-10-06.json").read_text())
    slot_rows = [r for r in packmod.day_at(PACK, "car", DAY, LONDON) if r["slot"]]
    key = slot_rows[0]["start"].isoformat()
    snap["entries"][0]["first_seen"] = {key: "2026-10-05T19:02:00+01:00"}
    (save / "costs" / "snapshots" / "2026-10-06.json").write_text(json.dumps(snap))
    (save / "engine_v2_state.json").write_text(json.dumps({"learned": "yes"}))
    old = {"2026-10-03T18:00:00+01:00": {"start": "2026-10-03T18:00:00+01:00", "end": "2026-10-03T19:00:00+01:00",
                                         "status": "done", "first_seen": "2026-10-03T12:00:00+01:00"},
           key: {"start": key, "end": slot_rows[-1]["end"].isoformat(), "status": "done", "first_seen": "x"}}
    (save / "costs" / "slots.json").write_text(json.dumps(old))

    day_input = daymod.load_day(str(save / "costs"), DAY)
    seen = {}

    def look(app, world, t):
        if "state" not in seen:
            seen["state"] = os.path.exists(os.path.join(os.path.dirname(app._save_path()), "engine_v2_state.json"))
            seen["capacity"] = (world.capacity_kwh, app._params().capacity_kwh, app.cfg.safety["min_reserve_soc"])
            seen["learn_timers"] = [getattr(x[1], "__name__", "") for x in app.timers]
        seen["profile"] = (app.profile.days, set(app.profile.watts.values())) if app.profile else None
        seen["hist"] = dict(app._hist_means)
        seen["slots"] = {k: dict(v) for k, v in app.slots.slots.items()}

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sc = run.scenario_for(day_input, str(save), tmp, hours=0.5)
        res = replay.run_app(sc, "v1", on_tick=look)
    assert res["real_calls"] == 0 and res["failsafes"] == 0
    assert seen["state"] is True                                       # the owner's learned engine state was copied in
    assert seen["capacity"][0] == 14.3 and seen["capacity"][1] == 14.3             # the world and the app: his battery
    assert seen["profile"] == (11.0, {1234.5})                                     # from the snapshot, never learned
    assert seen["hist"] == {}                                                      # no history was read from the world
    assert res["profile_days"] == 11.0
    assert seen["slots"][key]["first_seen"] == "2026-10-05T19:02:00+01:00"        # announced the evening before
    assert seen["slots"][key]["status"] == "planned"                               # the day's own record was dropped
    assert seen["slots"]["2026-10-03T18:00:00+01:00"]["first_seen"] == "2026-10-03T12:00:00+01:00"   # history kept


def test_a_profile_that_changes_during_the_day_is_followed(tmp_path):
    save = make_save_dir(tmp_path / "pe", PACK, "car")
    path = save / "costs" / "snapshots" / "2026-10-06.json"
    snap = json.loads(path.read_text())
    snap["entries"].append({"at": "2026-10-06T00:20:00+01:00", "states": {},
                            "profile": {"days": 15.0, "watts": {f"{we}|{hh}": 777.0 for we in (0, 1)
                                                                for hh in range(48)}}})
    path.write_text(json.dumps(snap))
    day_input = daymod.load_day(str(save / "costs"), DAY)
    seen = []

    def look(app, world, t):
        seen.append((t.strftime("%H:%M"), app.profile.days))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        replay.run_app(run.scenario_for(day_input, str(save), tmp, hours=0.4), "v1", on_tick=look)
    assert ("00:10", 14.0) in seen and ("00:19", 14.0) in seen and ("00:21", 15.0) in seen
    assert ("00:23", 14.0) not in seen


def test_days_that_cannot_be_replayed_are_refused_with_a_reason(tmp_path):
    for name, kw, status, fragment in (
            ("a", {"snapshot": False}, "no_snapshot", "forecast record"),
            ("b", {"records": False}, "incomplete", "no cost records"),
    ):
        save = make_save_dir(tmp_path / name, PACK, "car", **kw)
        assert run.run_day(str(save), DAY, hours=2) == 2
        r = json.loads((save / "costs" / "compare" / "2026-10-06.json").read_text())
        assert r["status"] == status and fragment in r["reason"] and "v1" not in r
    save = make_save_dir(tmp_path / "c", PACK, "car")
    p = save / "costs" / "2026-10-06.json"
    p.write_text(json.dumps(json.loads(p.read_text())[:30]))
    assert run.run_day(str(save), DAY, hours=2) == 2
    r = json.loads((save / "costs" / "compare" / "2026-10-06.json").read_text())
    assert r["status"] == "incomplete" and "30 records" in r["reason"]
    save = make_save_dir(tmp_path / "d", PACK, "car")
    s = save / "costs" / "snapshots" / "2026-10-06.json"
    snap = json.loads(s.read_text())
    snap["entries"] = [e for e in snap["entries"] if e["at"] > "2026-10-06T06"]     # first record after 06:00: too late
    s.write_text(json.dumps(snap))
    assert run.run_day(str(save), DAY, hours=2) == 2
    assert json.loads((save / "costs" / "compare" / "2026-10-06.json").read_text())["status"] == "no_snapshot"


def test_a_failure_is_written_and_exits_1(tmp_path, monkeypatch):
    save = make_save_dir(tmp_path / "pe", PACK, "car")

    def boom(*a, **k):
        raise RuntimeError("the world fell over")
    monkeypatch.setattr(run, "compare_day", boom)
    assert run.run_day(str(save), DAY, hours=2) == 1
    r = json.loads((save / "costs" / "compare" / "2026-10-06.json").read_text())
    assert r["status"] == "failed" and "the world fell over" in r["reason"]


def test_the_command_line_entry_point_exits_2_for_a_refused_day(tmp_path):
    save = make_save_dir(tmp_path / "pe", PACK, "car", snapshot=False)
    proc = subprocess.run([sys.executable, "-m", "pe_core.compare.run", "--save-dir", str(save), "--day", "2026-10-06"],
                          cwd=APP, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stderr
    assert json.loads((save / "costs" / "compare" / "2026-10-06.json").read_text())["status"] == "no_snapshot"
    out = tmp_path / "elsewhere.json"
    proc = subprocess.run([sys.executable, "-m", "pe_core.compare.run", "--save-dir", str(save), "--day", "2026-10-06",
                           "--out", str(out)], cwd=APP, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2 and json.loads(out.read_text())["status"] == "no_snapshot"


def test_the_runner_never_calls_home_assistant():
    """The replayed app runs against the fake AppDaemon: its Hass records every call it makes, and the runner treats any
    as a failure. Nothing in the comparison package may reach for the network or the real AppDaemon."""
    import pathlib
    for path in pathlib.Path(APP, "pe_core", "compare").glob("*.py"):
        text = path.read_text()
        for bad in ("import requests", "urllib", "import socket", "import appdaemon", "http.client"):
            assert bad not in text, (path.name, bad)
    assert results.VERSION == 1 and SnapshotFeed and Snapshot and datetime and timedelta and snapshot_from_pack
