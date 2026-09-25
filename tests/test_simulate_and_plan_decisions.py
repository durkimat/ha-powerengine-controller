from datetime import timedelta

import pytest
from fixtures import BST, NOW

from pe_core.config import parse_config
from pe_core.decide import GRID_CHARGE, HOLD, SELF_USE, Decision, decide
from pe_core.forecast import SLOT, Slot, slot_start
from pe_core.planner import Params, make_plan, params_from
from pe_core.readings import Readings
from pe_core.simulate import SimBattery

CFG = parse_config({"inputs": {"battery_capacity": {"value": 18}, "battery_max_discharge_power": {"value": 4800}}})


def R(t=NOW, **kw):
    base = dict(now=t, battery_soc=50, import_rate=0.30, export_rate=0.15, house_power=1000, solar_power=0,
                ev_power=0, ev_plug="EV Disconnected")
    base.update(kw)
    return Readings(**base)


def test_params_from_config():
    p = params_from(CFG)
    assert p.capacity_kwh == 18 and p.max_discharge_kw == 4.8 and p.axle_kw == 4.0
    assert p.cheap_cap_p == 10 and p.hold_for_car is True


def test_sim_battery_discharges_under_self_use_and_resets_at_midnight():
    sim, p = SimBattery(), Params()
    assert sim.update(None, R(), p, BST) == 50                  # first call syncs to the real battery
    d = Decision(SELF_USE, "default", "x")
    soc = sim.update(d, R(NOW + timedelta(minutes=15)), p, BST)
    assert soc == pytest.approx(50 - 0.25 / 0.95 / 18 * 100, abs=0.01)
    assert sim.update(d, R(NOW + timedelta(hours=7), battery_soc=80), p, BST) == 80   # next day: re-sync


def test_sim_battery_charges_when_decision_is_grid_charge():
    sim, p = SimBattery(), Params()
    sim.update(None, R(), p)
    soc = sim.update(Decision(GRID_CHARGE, "plan", "x", target_soc=100), R(NOW + timedelta(minutes=10)), p)
    assert soc > 50 and sim.cost_today > 0


def _plan_with_first(action_price):
    s0 = slot_start(NOW)
    slots = [Slot(s0 + i * SLOT, action_price if i == 0 else 0.30, 0.15, load_kwh=0.5) for i in range(48)]
    return make_plan(slots, 20.0, params_from(CFG), NOW)


def test_fill_when_cheap_is_on_by_default_and_can_be_turned_off():
    assert params_from(CFG).fill_when_cheap is True
    off = parse_config({"features": {"fill_when_cheap": False}})
    assert params_from(off).fill_when_cheap is False


def test_decision_follows_plan():
    plan = _plan_with_first(0.05)          # cheap now, expensive later -> charge now
    d = decide(R(battery_soc=20, import_rate=0.05), CFG, plan=plan)
    assert d.rule == "plan" and d.action == GRID_CHARGE and ("cheapest" in d.reason or "top up" in d.reason)


def test_live_car_charging_overrides_plan():
    plan = _plan_with_first(0.30)
    d = decide(R(ev_power=7000, ev_plug="Charging"), CFG, plan=plan)
    assert d.action == HOLD and d.rule == "car_charging"


def test_live_axle_overrides_plan():
    plan = _plan_with_first(0.30)
    assert decide(R(axle_active=True), CFG, plan=plan).action == "force_discharge"
