"""BMS charge/discharge limits in RAM remote control (issue #121)."""
import math
from datetime import datetime, timedelta, timezone

import pytest
from test_pause_guards import app  # noqa: F401  (fixture)
from test_ramcontrol import R, ramapp  # noqa: F401  (fixture)

from pe_core import bms
from pe_core.decide import EXPORT, GRID_CHARGE, Decision
from pe_core.ramcontrol import STEP_DOWN_FLOOR_W, Command, RamController

T = datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)
CH, DIS = Command("Force charge", 5000), Command("Force discharge", 5000)


# --- parsing ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [("100", 100.0), (40.5, 40.5), ("0", 0.0), (None, None), ("unavailable", None),
                                      ("unknown", None), ("", None), ("abc", None), ("-5", None), ("nan", None),
                                      ("inf", None), ("5000", None), (math.inf, None)])
def test_parse_amps_only_takes_sane_numbers(raw, want):
    assert bms.parse_amps(raw) == want


def test_volts_use_the_sensor_only_when_plausible():
    assert bms.parse_volts("51.2", 52.0) == (51.2, "sensor")
    for bad in (None, "unavailable", "0", "-50", "9999", "x"):
        assert bms.parse_volts(bad, 52.0) == (52.0, "nominal")


def test_limits_in_watts_round_down_to_100():
    lim = bms.read_limits("100", "100", None, 52.0)
    assert (lim.charge_w, lim.discharge_w, lim.charge_source) == (5200, 5200, "bms")
    lim = bms.read_limits("40", "30", "50.0", 52.0)
    assert (lim.charge_w, lim.discharge_w, lim.volts, lim.volts_source) == (2000, 1500, 50.0, "sensor")
    assert bms.read_limits("19.9", None, "51", 52.0).charge_w == 1000            # 1014.9 W rounds down


# --- capping ---------------------------------------------------------------------------------------

def test_a_limit_below_the_command_caps_it_and_a_limit_above_does_not():
    lim = bms.read_limits("40", "100", None, 52.0)                   # 2080 -> 2000 W charge
    cmd, why = bms.cap_command(CH, lim)
    assert cmd == Command("Force charge", 2000) and "2000 W" in why and "BMS" in why
    assert bms.cap_command(Command("Force charge", 1500), lim) == (Command("Force charge", 1500), None)
    assert bms.cap_command(DIS, lim) == (DIS, None)                  # 5200 W discharge limit: no cap on 5000
    assert bms.cap_command(Command("Off"), lim) == (Command("Off"), None)
    assert bms.cap_command(Command("Force charge", 0), lim)[0] == Command("Force charge", 0)


def test_no_sensors_means_no_change():
    lim = bms.read_limits(None, None, None, 52.0)
    assert lim == bms.Limits(volts=52.0) and lim.charge_w is None
    assert bms.cap_command(CH, lim) == (CH, None) and bms.cap_command(CH, None) == (CH, None)
    for garbage in ("unavailable", "unknown", "junk", "-3", "1e9"):
        assert bms.cap_command(CH, bms.read_limits(garbage, garbage, garbage, 52.0)) == (CH, None)


def test_a_zero_limit_holds_a_charge_and_switches_off_a_discharge():
    lim = bms.read_limits("0", "0", None, 52.0)
    assert bms.cap_command(CH, lim) == (Command("Force charge", 0), "no charge allowed by the battery (BMS)")
    assert bms.cap_command(DIS, lim)[0] == Command("Off", 0)
    assert bms.cap_command(Command("Force charge", 0), lim)[1] is None    # a hold stays a hold


def test_a_zero_limit_is_not_believed_while_the_battery_is_moving_that_way():
    assert bms.read_limits("0", "0", None, 52.0, battery_w=-2500).charge_w is None    # charging at 2.5 kW
    assert bms.read_limits("0", "0", None, 52.0, battery_w=2500).discharge_w is None
    assert bms.read_limits("0", "0", None, 52.0, battery_w=-100).charge_w == 0         # idle: believed


def test_cold_caution_caps_the_charge_only_when_the_bms_limit_is_unusable():
    cold = 2500.0
    lim = bms.read_limits(None, None, None, 52.0, cold_charge_w=cold)
    assert (lim.charge_w, lim.charge_source) == (2500, "cold caution") and lim.discharge_w is None
    assert bms.cap_command(CH, lim) == (Command("Force charge", 2500), "limited to 2500 W by the cold caution")
    assert bms.read_limits("unavailable", None, None, 52.0, cold_charge_w=cold).charge_source == "cold caution"
    lim = bms.read_limits("100", None, None, 52.0, cold_charge_w=cold)                # the BMS sensor wins
    assert (lim.charge_w, lim.charge_source) == (5200, "bms")
    assert bms.read_limits(None, None, None, 52.0, cold_charge_w=0).charge_w is None


# --- the follow check ------------------------------------------------------------------------------

def test_expected_power_is_the_lower_of_command_and_limit():
    lim = bms.read_limits("40", "30", None, 52.0)
    assert bms.expected_w(CH, lim) == 2000 and bms.expected_w(DIS, lim) == 1500
    assert bms.expected_w(Command("Force charge", 1000), lim) == 1000
    assert bms.expected_w(Command("Off"), lim) is None and bms.expected_w(None, lim) is None
    assert bms.expected_w(CH, None) == 5000


def test_a_bms_limited_charge_is_not_reported_as_not_following():
    c = RamController()
    c.done(T, "d", CH, "change")
    late = T + timedelta(minutes=10)
    assert c.check_following(late, -2000, 50, 12) == "waiting"                      # old rule: 2 kW of 5 kW is bad
    c2 = RamController()
    c2.done(T, "d", CH, "change")
    lim = bms.read_limits("40", None, None, 52.0)
    for minutes in (2, 4, 10):
        assert c2.check_following(T + timedelta(minutes=minutes), -2000, 50, 12, bms.expected_w(CH, lim)) == "ok"


def test_still_not_following_when_below_half_of_the_expected_power():
    c = RamController()
    c.done(T, "d", CH, "change")
    lim = bms.read_limits("40", None, None, 52.0)                                   # expect 2000 W
    exp = bms.expected_w(CH, lim)
    assert c.check_following(T + timedelta(minutes=2), -500, 50, 12, exp) == "waiting"
    assert c.check_following(T + timedelta(minutes=6), -500, 50, 12, exp) == "not following"


def test_a_zero_limit_expects_nothing():
    c = RamController()
    c.done(T, "d", CH, "change")
    lim = bms.read_limits("0", None, None, 52.0)
    assert c.check_following(T + timedelta(minutes=5), 0, 50, 12, bms.expected_w(CH, lim)) == "ok"


# --- step down -------------------------------------------------------------------------------------

def test_step_down_5000_4000_3000_then_stops():
    c = RamController()
    c.done(T, "d", CH, "change")
    assert c.step_down(T, 0) == 4000
    assert c.apply_ceiling(T, CH) == Command("Force charge", 4000)
    c.done(T, "d", Command("Force charge", 4000), "change")
    assert c.step_down(T, 0) == 3000
    c.done(T, "d", Command("Force charge", 3000), "change")
    assert c.step_down(T, 0) is None and c.ceiling_w == STEP_DOWN_FLOOR_W           # floor: reported, not chased
    assert c.apply_ceiling(T, Command("Force charge", 2000)) == Command("Force charge", 2000)


def test_the_ceiling_is_dropped_for_a_new_kind_of_command_or_after_an_hour():
    c = RamController()
    c.done(T, "d", CH, "change")
    c.step_down(T, 0)
    assert c.apply_ceiling(T + timedelta(minutes=10), CH).watts == 4000
    assert c.apply_ceiling(T + timedelta(minutes=10), DIS) == DIS                  # discharge: no ceiling, and gone
    assert c.ceiling_w is None
    c.step_down(T, 0)
    c.done(T, "d", CH, "change")
    c.step_down(T, 0)
    assert c.apply_ceiling(T + timedelta(minutes=61), CH) == CH
    c.done(T, "d", CH, "change")
    c.step_down(T, 0)
    c.forget()
    assert c.ceiling_w is None


def test_nothing_to_step_down_for_hold_off_or_a_low_command():
    c = RamController()
    assert c.step_down(T, 0) is None
    for cmd in (Command("Off"), Command("Force charge", 0), Command("Force charge", 3000)):
        c.done(T, "d", cmd, "change")
        assert c.step_down(T, 0) is None


def test_no_step_down_when_the_battery_is_doing_some_of_it_or_is_unknown():
    c = RamController()
    c.done(T, "d", CH, "change")
    assert c.step_down(T, None) is None
    assert c.step_down(T, -1500) is None                      # charging at 1.5 kW of 5: limited by itself
    assert c.ceiling_w is None
    assert c.step_down(T, -900) == 4000                       # under 20%: the write did not take
    c.done(T, "d", DIS, "change")
    assert c.step_down(T, 1200) is None and c.step_down(T, -300) == 4000     # wrong way round counts as not moving


# --- in the app ------------------------------------------------------------------------------------

BMS_C, BMS_D = "sensor.solis_bms_battery_charge_limit", "sensor.solis_bms_battery_discharge_limit"
CHARGE = "number.solis_inverter_battery_control_override_charge_power"


def with_bms(a, charge="40", discharge="100"):
    from pe_core.config import parse_config
    inputs = dict(a.cfg.raw["inputs"], battery_bms_charge_limit={"entity": BMS_C},
                  battery_bms_discharge_limit={"entity": BMS_D})
    a.cfg = parse_config(dict(a.cfg.raw, inputs=inputs))
    a.states.update({BMS_C: charge, BMS_D: discharge})
    return a


def test_the_command_is_capped_at_the_bms_limit(ramapp):  # noqa: F811
    a = with_bms(ramapp)                                           # 40 A x 52 V = 2080 W, rounded down to 2000
    a._control(R(T), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    assert a.states[CHARGE] == 2000
    assert a.states["select.solis_inverter_battery_control_override"] == "Force charge"
    a._control(R(T + timedelta(minutes=10), -1900), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    assert a._ramctl.follow == "ok"                                # 1.9 kW of an expected 2 kW


def test_limit_sensors_unavailable_means_the_old_behaviour(ramapp):  # noqa: F811
    a = with_bms(ramapp, charge="unavailable", discharge="unknown")
    a._control(R(T), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    assert a.states[CHARGE] == 4500


def test_a_zero_charge_limit_becomes_a_hold_and_zero_discharge_limit_off(ramapp):  # noqa: F811
    a = with_bms(ramapp, charge="0", discharge="0")
    a._control(R(T), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    assert a.states[CHARGE] == 0 and a.states["select.solis_inverter_battery_control_override"] == "Force charge"
    a._control(R(T + timedelta(minutes=1)), Decision(EXPORT, "plan", "sell", power_w=4000))
    assert a.states["select.solis_inverter_battery_control_override"] == "Off"


def test_not_following_steps_down_once_and_resends(ramapp):  # noqa: F811
    a = ramapp
    notes = []
    a._notify = lambda kind, msg: notes.append(msg)
    dec = Decision(GRID_CHARGE, "plan", "charge", power_w=4800)
    a._control(R(T, 0), dec)
    assert a.states[CHARGE] == 4800
    for s in range(30, 400, 30):                                   # the battery never responds
        a._control(R(T + timedelta(seconds=s), 0), dec)
    assert a.states[CHARGE] == 3800
    assert len(notes) == 1 and "stepped down" in notes[0][1]
    for s in range(400, 800, 30):
        a._control(R(T + timedelta(seconds=s), 0), dec)
    assert a.states[CHARGE] == 3000
    for s in range(800, 1400, 30):                                 # at the floor: the usual report, no more steps
        a._control(R(T + timedelta(seconds=s), 0), dec)
    assert a.states[CHARGE] == 3000
    keys = list(dict.fromkeys(n[0] for n in notes))                # the real notifier drops repeats of a key
    assert len(keys) == 2 and "stepdown" in keys[0] and "ram_follow" in keys[1]


def test_the_diagnostics_bundle_carries_the_bms_limits_and_recent_rows(ramapp):  # noqa: F811
    a = with_bms(ramapp)
    a._control(R(T, 0), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    a._control(R(T + timedelta(seconds=30), -1800), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    b = a._bms_bundle()
    assert b["mapped"] == {"battery_bms_charge_limit": True, "battery_bms_discharge_limit": True}
    assert b["sensors"]["battery_bms_charge_limit"] == {"entity": BMS_C, "state": "40"}
    row = b["recent"][0]
    assert row["command"] == "Force charge at 2000 W" and row["expected_w"] == 2000
    assert row["limits"]["charge_a"] == 40.0 and row["limits"]["volts"] == 52.0
    assert len(b["recent"]) == 1                                   # same command within 2 minutes: one row
    a._control(R(T + timedelta(minutes=3), -1800), Decision(GRID_CHARGE, "plan", "charge", power_w=4500))
    assert len(a._bms_bundle()["recent"]) == 2 and a._bms_bundle()["recent"][1]["battery_w"] == -1800


def test_the_bundle_works_with_nothing_mapped(ramapp):  # noqa: F811
    b = ramapp._bms_bundle()
    assert not any(b["mapped"].values()) and b["recent"] == []


def test_sample_ring_is_bounded():
    ring = bms.SampleRing(size=3)
    for i in range(10):
        ring.add(T + timedelta(minutes=i * 5), f"cmd {i}", {})
    assert [r["command"] for r in ring.as_list()] == ["cmd 7", "cmd 8", "cmd 9"]
