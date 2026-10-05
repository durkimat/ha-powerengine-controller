"""Engine v2, layer 4: rules and limits (docs/plans/engine-v2.md section 7)."""

from datetime import timedelta

from test_engine_v2_observe import SETTINGS, T0, readings, segment

from pe_core.engine_v2 import rules
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import (
    CHARGE,
    EVENT,
    EXPORT,
    FREE,
    HOLD,
    SELF_USE,
    BatteryFacts,
    Observation,
    Situation,
)
from pe_core.override import Override


def obs(level=50.0, car=False):
    return Observation(now=T0, level_reported=level, level_filtered=level, net_load_kw=0.5, car_charging=car,
                       data_ok=True)


def now_limits(level=50.0, car=False, facts=None, settings=SETTINGS, situation=None, seg=None, **rd):
    situation = situation or Situation(active=True)
    return rules.limits_now(situation, obs(level, car), seg, facts or BatteryFacts(), settings,
                            readings(T0, **rd))


def test_normal_allows_self_use_hold_charge_and_export_only_with_arbitrage():
    lim = now_limits()
    assert lim.allowed == {SELF_USE, HOLD, CHARGE} and lim.forced is None and lim.rule == "normal"
    assert now_limits(settings=V2Settings(arbitrage=True)).allowed == {SELF_USE, HOLD, CHARGE, EXPORT}


def test_not_active_is_still_decided_in_the_same_way():
    assert now_limits(situation=Situation(active=False, mode_reason="Passive")).allowed == {SELF_USE, HOLD, CHARGE}


def test_a_grid_event_forces_export_down_to_the_hard_floor_plus_margin():                # B14a
    facts = BatteryFacts(hard_floor_soc=10.0)
    s = V2Settings(reserve_soc=25.0, hard_floor_margin_pct=1.0)
    lim = now_limits(level=30.0, facts=facts, settings=s, axle_active=True)
    assert lim.forced == EVENT and lim.floor_soc == 11.0 and lim.rule == "event"
    assert "25%" in lim.reason and "11%" in lim.reason
    assert rules.event_floor(facts, s) == 11.0


def test_events_off_means_no_forced_event():
    lim = now_limits(settings=V2Settings(events=False), axle_active=True)
    assert lim.forced is None


def test_the_reserve_removes_every_discharge_mode_outside_events():                     # B14
    s = V2Settings(reserve_soc=20.0, arbitrage=True)
    lim = now_limits(level=20.0, settings=s)
    assert lim.allowed == {HOLD, CHARGE} and lim.rule == "reserve" and "20%" in lim.reason
    assert now_limits(level=21.0, settings=s).allowed == {SELF_USE, HOLD, CHARGE, EXPORT}


def test_reserve_latch_needs_the_level_band_above_it_before_discharging_again():
    s = V2Settings(reserve_soc=20.0, level_band_pct=1.0)
    assert now_limits(level=20.5, settings=s).allowed >= {SELF_USE}
    lim = rules.limits_now(Situation(active=True), obs(20.5), None, BatteryFacts(), s, readings(T0),
                           reserve_latched=True)
    assert SELF_USE not in lim.allowed
    lim = rules.limits_now(Situation(active=True), obs(21.2), None, BatteryFacts(), s, readings(T0),
                           reserve_latched=True)
    assert SELF_USE in lim.allowed


def test_the_floor_is_the_higher_of_reserve_and_hard_floor():
    assert now_limits(facts=BatteryFacts(hard_floor_soc=15.0), settings=V2Settings(reserve_soc=12.0)).floor_soc == 15.0
    assert now_limits(settings=V2Settings(reserve_soc=30.0)).floor_soc == 30.0


def test_car_charging_allows_only_hold_and_charge():                                    # B7
    lim = now_limits(car=True, ev_plug="Charging", ev_power=7000.0)
    assert lim.allowed == {HOLD, CHARGE} and lim.rule == "car" and "never feeds the car" in lim.reason
    lim = now_limits(car=True, situation=Situation(active=True, house_load_includes_ev=False))
    assert lim.allowed == {SELF_USE, HOLD, CHARGE}


def test_car_and_reserve_together_still_hold():
    lim = now_limits(level=12.0, car=True)
    assert lim.allowed == {HOLD, CHARGE} and lim.rule == "car"


def test_override_forces_its_mode_and_keeps_the_reserve_for_discharge():                 # B19
    for v1_mode, mode in (("self_use", SELF_USE), ("hold", HOLD), ("grid_charge", CHARGE), ("export", EXPORT)):
        sit = Situation(active=True, override=Override(v1_mode, T0 + timedelta(hours=1), T0))
        lim = now_limits(situation=sit, settings=V2Settings(reserve_soc=20.0))
        assert lim.forced == mode and lim.allowed == {mode} and lim.floor_soc == 20.0 and lim.rule == "override"
        assert lim.ceiling_soc == 100.0 and "override" in lim.reason.lower()


def test_override_charge_takes_the_charge_ceiling():
    sit = Situation(active=True, override=Override("grid_charge", None, T0))
    assert now_limits(situation=sit, settings=V2Settings(charge_ceiling_soc=90)).ceiling_soc == 90


def test_a_grid_event_beats_an_override_and_free_power_comes_after_it():
    sit = Situation(active=True, override=Override("hold", None, T0))
    assert now_limits(situation=sit, axle_active=True).forced == EVENT
    assert now_limits(situation=sit, free_active=True).forced == HOLD
    lim = now_limits(free_active=True)
    assert lim.forced == FREE and lim.ceiling_soc == 100.0
    assert now_limits(free_active=True, settings=V2Settings(free_power=False)).forced is None


def test_caps_only_when_below_the_batterys_own_limit():
    assert now_limits().charge_cap_kw is None and now_limits().discharge_cap_kw is None
    lim = now_limits(facts=BatteryFacts(bms_charge_kw=2.0, bms_discharge_kw=1.5))
    assert lim.charge_cap_kw == 2.0 and lim.discharge_cap_kw == 1.5
    assert now_limits(facts=BatteryFacts(bms_charge_kw=4.8)).charge_cap_kw is None
    assert now_limits(facts=BatteryFacts(charge_factor=0.5)).charge_cap_kw == 2.4


def test_fuse_headroom_is_live_house_and_car_less_the_sun():
    facts = BatteryFacts(fuse_kw=8.0)
    lim = now_limits(facts=facts, house_power=6000.0, solar_power=1000.0)
    assert abs(lim.charge_cap_kw - 3.0) < 1e-9
    lim = now_limits(facts=facts, car=True, house_power=500.0, solar_power=0.0, ev_plug="Charging", ev_power=5000.0)
    assert abs(lim.charge_cap_kw - 2.5) < 1e-9
    assert now_limits(facts=facts, house_power=500.0).charge_cap_kw is None


def test_limits_for_matches_limits_now_for_the_same_situations():
    t = T0
    facts = BatteryFacts(hard_floor_soc=10.0)
    s = V2Settings(reserve_soc=25.0, arbitrage=True)
    normal = rules.limits_for(segment(t), facts, s)
    assert normal.allowed == {SELF_USE, HOLD, CHARGE, EXPORT} and normal.floor_soc == 25.0
    ev = rules.limits_for(segment(t, event=True), facts, s)
    assert ev.forced == EVENT and ev.floor_soc == 11.0
    assert rules.limits_for(segment(t, event=True), facts, V2Settings(events=False)).forced is None
    assert rules.limits_for(segment(t, free=True), facts, s).forced == FREE
    manual = rules.limits_for(segment(t, manual=CHARGE), facts, s)
    assert manual.forced == CHARGE and manual.allowed == {CHARGE}
    car = rules.limits_for(segment(t, car_kw=7.0), facts, s)
    assert car.allowed == {HOLD, CHARGE}
    assert rules.limits_for(segment(t, car_kw=7.0), facts, s, house_load_includes_ev=False).allowed >= {SELF_USE}
    # the same answers from the live side
    live = now_limits(facts=facts, settings=s, axle_active=True)
    assert (live.forced, live.floor_soc) == (ev.forced, ev.floor_soc)
    assert now_limits(facts=facts, settings=s).allowed == normal.allowed


def test_limits_for_fuse_cap_uses_the_forecast_net_house_load():
    facts = BatteryFacts(fuse_kw=12.0)
    seg = segment(T0, hours=0.5, load=3.0, solar=0.0, car_kw=7.0)         # 6 kW house + 7 kW car in the half-hour
    assert rules.limits_for(seg, facts, SETTINGS).charge_cap_kw == 0.0
    seg = segment(T0, hours=0.5, load=1.0, solar=0.0)
    assert rules.limits_for(seg, facts, SETTINGS).charge_cap_kw is None       # 10 kW of room: no cap


def test_limits_for_cold_segment_caps_the_charge():
    seg = segment(T0, charge_factor=0.5)
    assert rules.limits_for(seg, BatteryFacts(), SETTINGS).charge_cap_kw == 2.4


def test_the_ceiling_is_the_setting():
    assert now_limits(settings=V2Settings(charge_ceiling_soc=88)).ceiling_soc == 88
    assert rules.limits_for(segment(T0), BatteryFacts(), V2Settings(charge_ceiling_soc=88)).ceiling_soc == 88
