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
    assert p.capacity_kwh == 18 and p.max_discharge_kw == 4.8 and p.axle_kw == 4.8
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


def test_grid_charge_stops_at_the_slot_target():
    plan = _plan_with_first(0.05)
    ps = plan.slots[0]
    assert ps.action == GRID_CHARGE and ps.target_soc is not None
    below = decide(R(battery_soc=ps.target_soc - 1, import_rate=0.05), CFG, plan=plan)
    assert below.action == GRID_CHARGE
    reached = decide(R(battery_soc=ps.target_soc, import_rate=0.05), CFG, plan=plan)
    assert reached.action == HOLD and "target" in reached.reason


def test_live_car_charging_overrides_plan():
    plan = _plan_with_first(0.30)
    d = decide(R(ev_power=7000, ev_plug="Charging"), CFG, plan=plan)
    assert d.action == HOLD and d.rule == "car_charging"


def test_live_axle_overrides_plan():
    plan = _plan_with_first(0.30)
    assert decide(R(axle_active=True), CFG, plan=plan).action == "force_discharge"


def test_car_charging_cheaply_charges_the_battery_too_even_if_the_plan_holds():
    from dataclasses import replace
    plan = _plan_with_first(0.07)
    plan.slots[0] = replace(plan.slots[0], action=HOLD, target_soc=None)
    arb = parse_config({"features": {"arbitrage": True}, "inputs": {"battery_capacity": {"value": 18}}})
    d = decide(R(ev_power=7000, ev_plug="Charging", import_rate=0.07, battery_soc=60), arb, plan=plan)
    assert d.action == GRID_CHARGE and d.target_soc == 90 and "too" in d.reason
    full = decide(R(ev_power=7000, ev_plug="Charging", import_rate=0.07, battery_soc=95), arb, plan=plan)
    assert full.action == HOLD


def test_optimiser_charges_the_battery_in_a_cheap_car_slot():
    from pe_core.optimiser import optimise
    s0 = slot_start(NOW)
    slots = [Slot(s0 + i * SLOT, 0.07 if i < 4 else 0.30, 0.15, load_kwh=0.3, smart_slot=i < 4) for i in range(24)]
    p = params_from(parse_config({"features": {"arbitrage": True}, "inputs": {"battery_capacity": {"value": 18}}}))
    res = optimise(slots, 50.0, p)
    assert res["actions"][:4] == [GRID_CHARGE] * 4
    assert max(res["soc"][:4]) <= 90.5                                   # to the top-up level, not beyond


def test_axle_export_earns_axle_plus_the_export_rate():
    from pe_core.planner import axle_rate, axle_words
    p = params_from(CFG)
    assert p.axle_plus_export is True
    assert axle_rate(p, 0.15) == pytest.approx(1.15)
    assert axle_words(p, 0.15) == "£1.15/kWh (£1 Axle + 15p export)"
    off = params_from(parse_config({"features": {"axle_plus_export": False}}))
    assert axle_rate(off, 0.15) == 1.0 and axle_words(off, 0.15) == "£1/kWh"


def test_next_text_merges_windows_and_skips_the_running_one():
    from types import SimpleNamespace

    from pe_core.planner import next_text
    w = lambda a, f, t, day="": {"action": a, "from": f, "to": t, "day": day}  # noqa: E731
    plan = SimpleNamespace(windows=[w("hold", "09:00", "14:00"), w("export", "14:00", "15:00"),
                                    w("export", "15:00", "16:00"), w("self_use", "16:00", "23:30"),
                                    w("grid_charge", "23:30", "05:00", "tomorrow")])
    assert next_text(plan) == "sell 14:00–16:00, then self-use 16:00–23:30"
    assert next_text(plan, n=3).endswith("charge 23:30–05:00 tomorrow")
    assert next_text(None) == ""


def test_plan_windows_are_capped_to_a_byte_budget_soonest_first():
    import json

    from pe_core.planner import WINDOWS_BUDGET, windows_within
    ws = [{"start": f"2026-09-29T{h:02d}:00:00+00:00", "end": f"2026-09-29T{h:02d}:30:00+00:00", "action": "export",
           "reason": "sell at 15p: refilled at 6.99p from 13:30", "target_soc": None, "soc_start": 90, "soc_end": 80,
           "cost": -0.5, "estimated": False, "price": "6.99p", "from": "10:00", "to": "10:30", "day": ""}
          for h in range(24)] * 3
    shown, more = windows_within(ws)
    assert shown == ws[:len(shown)] and more == len(ws) - len(shown) > 0
    assert len(json.dumps(shown, separators=(",", ":"))) <= WINDOWS_BUDGET
    assert windows_within(ws[:3]) == (ws[:3], 0)
