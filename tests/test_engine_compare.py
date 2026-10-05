"""tools/engine_compare.py: a short closed-loop run of both engines on one demo day (2 hours, no cost backfill),
checking that the tool runs end to end and the numbers are sane. The full four-day, 24-hour comparison is the
tool's own job and is not part of the test suite."""
import json
import math
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import engine_compare as ec  # noqa: E402

START = datetime(2026, 10, 6, 0, 0, tzinfo=ec.BST)
HOURS = 2


@pytest.fixture(scope="module")
def result():
    return ec.compare_day("car", START, HOURS, backfill=False)


def test_all_four_runs_are_there_with_finite_numbers(result):
    runs = result["runs"]
    assert set(runs) == {"selfuse", "v1", "v2", "bound"}
    for name, run in runs.items():
        for key in ("cost_gbp", "end_soc", "min_soc", "endval_gbp", "adj_cost_gbp", "save_gbp", "gap_to_bound_gbp"):
            assert isinstance(run[key], float) and math.isfinite(run[key]), (name, key)
        assert 11.9 <= run["min_soc"] <= run["end_soc"] + 1e-6 <= 100.1, name
    assert runs["bound"]["gap_to_bound_gbp"] == 0 and runs["selfuse"]["save_gbp"] == 0
    assert runs["v1"]["engine"] == "v1" and runs["v2"]["engine"] == "v2"


def test_every_run_starts_at_the_packs_level_and_the_apps_are_closed_loop(result):
    runs = result["runs"]
    start = {name: run["start_soc"] for name, run in runs.items()}
    assert len(set(start.values())) == 1 and abs(start["v1"] - 68.0) < 0.5            # the car day at midnight
    for name in ("v1", "v2"):
        assert runs[name]["real_calls"] == 0                                         # the demo gate held
        assert runs[name]["failsafes"] == 0                                          # the refresh kept RC alive
        assert runs[name]["commands"] >= 1 and runs[name]["mode_changes"] >= 1      # and they did something
    assert runs["selfuse"]["commands"] == 0 and runs["selfuse"]["mode_changes"] == 0


def test_v2_ran_without_errors_and_counts_its_revalues_by_cause(result):
    v2 = result["runs"]["v2"]
    assert v2["errors"] == 0 and v2["revalues"] >= 1
    assert sum(v2["revalue_causes"].values()) == v2["revalues"] and "start" in v2["revalue_causes"]
    assert any(r["kind"] == "mode" for r in v2["journal"])


def test_the_bound_is_a_real_solve_and_no_dearer_than_self_use(result):
    bound, su = result["runs"]["bound"], result["runs"]["selfuse"]
    assert bound["exact"] and bound["check_total"] < 1e-6 and bound["timeline"]
    assert bound["adj_cost_gbp"] <= su["adj_cost_gbp"] + 1e-6            # foresight can always do what self-use does


def test_the_table_prints_and_the_json_is_serialisable(result):
    text = ec.table([result])
    assert "selfuse" in text and "bound" in text and "v2 revalues by cause" in text
    json.dumps(result, default=str)


def test_costing_a_world_with_no_commands_matches_its_own_counters():
    """The account hooks the world's steps: with nothing commanded, its import and export agree with the world's
    own day counters (which start from the day's record at midnight, so they are compared as differences)."""
    su = ec.run_selfuse("dull", START, 1, (100.0, True))
    assert su["import_kwh"] >= 0 and su["export_kwh"] >= 0
    assert su["cost_gbp"] == pytest.approx(su["import_kwh"] * 0.06993 - su["export_kwh"] * 0.15, abs=0.05)
