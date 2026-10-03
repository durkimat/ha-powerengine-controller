"""tools/low_write_study.py: the study runs on the demo pack and leaves the optimiser as it found it."""

import importlib.util
import pathlib

import pe_core.optimiser as opt

spec = importlib.util.spec_from_file_location(
    "low_write_study", pathlib.Path(__file__).parent.parent / "tools" / "low_write_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def test_the_tiers_order_by_value_and_window_changes_and_the_optimiser_is_restored():
    before = opt._actions
    out = study.study([10.0], ["dull", "car"])[10.0]
    assert opt._actions is before
    assert out[0]["saving"] == 0.0
    assert 0 < out[1]["saving"] < out[4]["saving"]                      # overnight charging, then the full plan
    assert out[1]["full"] + out[1]["current_only"] < out[4]["full"] + out[4]["current_only"]
    assert set(out[4]["days"]) == {"dull", "car"} and len(out[4]["days"]["dull"]["acts"]) == 48
    assert out[1]["saving"] <= out[2]["saving"] + 1e-9 or out[2]["saving"] > 0       # overnight arbitrage never loses


def test_a_dearer_window_change_means_fewer_of_them():
    low, high = study.study([1.0, 40.0], ["sunny", "axle"]).values()
    changes = lambda r: r["full"] + r["current_only"]                   # noqa: E731
    assert changes(high[4]) < changes(low[4])
    assert high[4]["saving"] < low[4]["saving"]


def test_the_command_line_prints_each_tier(capsys):
    assert study.main(["x", "--switch-cost", "10", "--days", "dull"]) == 0
    text = capsys.readouterr().out
    assert "price per window change 10p" in text and "T4 + arbitrage anywhere (full plan)" in text


def test_a_daytime_hold_trades_money_for_fewer_full_window_changes():
    plan = study.study([10.0], ["dull", "sunny"])[10.0]
    hold = study.study([10.0], ["dull", "sunny"], daytime="hold")[10.0]
    assert hold[2]["saving"] < plan[2]["saving"]                          # the battery sits idle by day
    assert hold[2]["full"] < plan[2]["full"]                              # but fewer windows open and close
    assert study._daytime["policy"] == "self-use"                         # restored
