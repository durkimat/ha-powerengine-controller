from datetime import datetime, timedelta

from pe_core.control import desired, window_end, writes_needed
from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE, Decision

NOW = datetime(2026, 9, 26, 23, 10)
CLOSED = {f"timed_{k}_{p}": 0 for k in ("charge", "discharge")
          for p in ("start_hour", "start_minute", "end_hour", "end_minute")}
HAVE = {**CLOSED, "timed_charge_current": 50, "timed_discharge_current": 50, "storage_mode": "Self-Use"}


def test_self_use_needs_no_writes_when_windows_are_closed():
    want = desired(Decision(SELF_USE, "x", "x"), NOW, None, 52, 4800, 4800)
    assert writes_needed(want, HAVE) == []


def test_grid_charge_opens_a_rolling_window_then_presses_the_button_once():
    end = window_end(NOW, None, "rolling", None)
    assert end == datetime(2026, 9, 26, 23, 45)
    want = desired(Decision(GRID_CHARGE, "plan", "x", target_soc=100), NOW, end, 52, 4800, 4800)
    w = writes_needed(want, HAVE)
    roles = [x.role for x in w]
    assert roles[-1] == "timed_update_button" and roles.count("timed_update_button") == 1
    assert {"timed_charge_start_hour", "timed_charge_end_minute", "timed_charge_current"} <= set(roles)
    assert want["timed_charge_current"] == 90                        # 4800 W / 52 V = 92 A -> 90 A
    assert want["timed_charge_start_hour"] == 23 and want["timed_charge_start_minute"] == 9


def test_windows_never_span_midnight_and_block_strategy_uses_the_plan():
    late = datetime(2026, 9, 26, 23, 50)
    assert window_end(late, None, "rolling", None) == datetime(2026, 9, 26, 23, 59)
    assert window_end(NOW, None, "block", datetime(2026, 9, 27, 5, 0)) == datetime(2026, 9, 26, 23, 59)
    morning = datetime(2026, 9, 27, 0, 5)
    assert window_end(morning, None, "block", datetime(2026, 9, 27, 5, 0)) == datetime(2026, 9, 27, 5, 0)


def test_rolling_window_is_kept_until_it_nears_its_end():
    end = datetime(2026, 9, 26, 23, 45)
    assert window_end(NOW + timedelta(minutes=10), end, "rolling", None) == end
    assert window_end(NOW + timedelta(minutes=31), end, "rolling", None) == datetime(2026, 9, 26, 23, 59)  # capped


def test_hold_is_zero_amps_and_export_uses_the_discharge_window():
    end = datetime(2026, 9, 26, 23, 45)
    hold = desired(Decision(HOLD, "x", "x"), NOW, end, 52, 4800, 4800)
    assert hold["timed_charge_current"] == 0 and hold["timed_discharge_end_hour"] == 0
    exp = desired(Decision(EXPORT, "plan", "x"), NOW, end, 52, 4800, 4800)
    assert exp["timed_discharge_end_minute"] == 45 and exp["timed_charge_end_hour"] == 0


def test_mode_is_corrected_and_only_differences_are_written():
    have = {**HAVE, "storage_mode": "Self-Use - No Grid Charging"}
    w = writes_needed(desired(Decision(SELF_USE, "x", "x"), NOW, None, 52, 4800, 4800), have)
    assert [(x.role, x.kind) for x in w] == [("storage_mode", "select")]
