from datetime import date, datetime, timedelta, timezone

from pe_core.decide import FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE, Decision
from pe_core.eeprom import WriteLog, WriteModel

T0 = datetime(2026, 9, 26, 23, 0, tzinfo=timezone.utc)


def run(model, decisions, minutes):
    """decisions: list of (from_minute, Decision); step every minute."""
    events = []
    for m in range(minutes):
        d = [dec for start, dec in decisions if m >= start][-1]
        events += model.step(T0 + timedelta(minutes=m), d)
    return events


def test_overnight_charge_opens_rolls_and_closes():
    charge = Decision(GRID_CHARGE, "plan", "x", target_soc=100)
    ev = run(WriteModel(), [(0, charge), (360, Decision(SELF_USE, "plan", "x"))], 400)
    # open + current, then a roll every 30 minutes for 6 hours, then close
    assert ev[:2] == ["charge_window", "charge_current"]
    assert ev.count("charge_window") == 13                    # open, 11 rolls (every 30 min), close
    assert ev[-1] == "charge_window" and ev.count("charge_current") == 1


def test_hold_is_a_zero_amp_charge_window_and_switching_kind_closes_first():
    m = WriteModel()
    assert m.step(T0, Decision(HOLD, "x", "x")) == ["charge_window", "charge_current"]
    assert m.current["charge"] == 0
    ev = m.step(T0 + timedelta(minutes=1), Decision(FORCE_DISCHARGE, "axle", "x", power_w=4000))
    assert ev == ["charge_window", "discharge_window", "discharge_current"]
    assert m.current["discharge"] == 75                       # 4000 W / 52 V = 77 A, in 5 A steps
    assert m.step(T0 + timedelta(minutes=2), Decision(SELF_USE, "x", "x")) == ["discharge_window"]
    assert m.step(T0 + timedelta(minutes=3), Decision(SELF_USE, "x", "x")) == []


def test_log_and_lifespan(tmp_path):
    log = WriteLog(str(tmp_path / "w.json"))
    start = date(2026, 9, 1)
    for i in range(15):
        log.would(start + timedelta(days=i), 20)
        for _ in range(4):
            log.observed(start + timedelta(days=i), "button.solis_update_charge_discharge_times")
    log.save()
    s = WriteLog(str(tmp_path / "w.json")).summary(start + timedelta(days=15))
    assert s["would"]["per_day"] == 20 and s["would"]["years"] == round(100_000 / (20 * 365), 1)
    assert s["observed"]["per_day"] == 4 and s["by_entity"]["button.solis_update_charge_discharge_times"] == 60
    assert s["would"]["days"] == 14                             # the first (partial) day is left out
