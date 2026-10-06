"""The cross-check (design section 5): the nightly runner on a demo pack day, with a snapshot synthesised from the
pack's own forecast, gives the same savings as tools/engine_compare.py on that day, within 1p. A short window keeps it
quick. (The snapshot's house profile is what the tool's app learned from the world's history, when it learned it.)"""
import json
import pathlib
import sys
from datetime import datetime

from compare_support import DAY, make_save_dir

from pe_core.compare import run
from pe_core.demo import pack as packmod

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import engine_compare as ec  # noqa: E402

PACK = packmod.load_pack()
HOURS = 2
START = datetime(DAY.year, DAY.month, DAY.day, 0, 0, tzinfo=ec.BST)


def test_the_runner_agrees_with_the_tool_within_a_penny(tmp_path):
    changes, last = [], [None]

    def capture(app, world, t):                      # the profile the tool's app has, and when it changed
        if app.profile is not last[0]:
            last[0] = app.profile
            if app.profile is not None:
                changes.append((t, {"days": app.profile.days,
                                    "watts": {f"{int(we)}|{hh}": w for (we, hh), w in app.profile.watts.items()}}))
    v1 = ec.run_app("car", "v1", START, HOURS, False, on_tick=capture)
    assert changes and changes[0][1]["watts"]
    v2 = ec.run_app("car", "v2", START, HOURS, False)
    su = ec.run_selfuse("car", START, HOURS, v1["event_pay"])
    bound = ec.perfect_bound("car", START, HOURS, v2["facts"], v2["settings"])

    save = make_save_dir(tmp_path / "pe", PACK, "car", engine="v1", live=True)
    path = save / "costs" / "snapshots" / "2026-10-06.json"
    snap = json.loads(path.read_text())
    del snap["entries"][0]["profile"]                # the tool's app had none until its history was read
    for t, prof in changes:
        snap["entries"].append({"at": t.isoformat(), "states": {}, "profile": prof})
    snap["entries"].sort(key=lambda e: e["at"])
    path.write_text(json.dumps(snap))
    assert run.run_day(str(save), DAY, hours=HOURS) == 0
    got = json.loads((save / "costs" / "compare" / "2026-10-06.json").read_text())
    assert got["status"] == "ok"

    cap = 18.0
    cheapest = min(PACK["days"]["car"]["act"])
    for name, run_ in (("v1", v1), ("v2", v2)):
        tool_saving = su["cost_gbp"] - run_["cost_gbp"] + (run_["end_soc"] - su["end_soc"]) / 100 * cap * cheapest
        print(f"cross-check {name}: runner {got[name]['saving']:+.4f} tool {tool_saving:+.4f} GBP")
        assert abs(got[name]["saving"] - tool_saving) < 0.01, (name, got[name], tool_saving)
        assert abs(got[name]["cost"] - run_["cost_gbp"]) < 0.01, name
        assert abs(got[name]["end_soc"] - run_["end_soc"]) < 0.5, name
    tool_bound = su["cost_gbp"] - bound["cost_gbp"] + (bound["end_soc"] - su["end_soc"]) / 100 * cap * cheapest
    assert abs(got["bound"]["saving"] - tool_bound) < 0.01
    assert abs(got["selfuse"]["cost"] - su["cost_gbp"]) < 0.01
    assert abs(got["selfuse"]["end_soc"] - su["end_soc"]) < 0.1
