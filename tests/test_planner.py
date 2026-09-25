from datetime import datetime, timedelta, timezone

import pytest

from pe_core.decide import FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE
from pe_core.forecast import SLOT, Slot
from pe_core.planner import Params, PlanSlot, headline, make_plan, simulate, step

T0 = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)
PEAK, CHEAP = 0.30, 0.07
P = Params(fill_when_cheap=False)         # the heuristic on its own; fill-when-cheap is tested separately
FILL = Params()


def day(n=48, load=0.5, solar=0.0, cheap_from=14, cheap_to=24, **flags):
    """n half-hours from 17:00 UTC; cheap between slot indexes [cheap_from, cheap_to) (00:00-05:00)."""
    out = []
    for i in range(n):
        price = CHEAP if cheap_from <= i < cheap_to else PEAK
        out.append(Slot(T0 + i * SLOT, price, 0.15, solar_kwh=solar, load_kwh=load,
                        **{k: (i in v) for k, v in flags.items()}))
    return out


def actions(plan):
    return [ps.action for ps in plan.slots]


def test_physics_self_use_discharges_to_floor_then_imports():
    ps = PlanSlot(Slot(T0, PEAK, 0.15, load_kwh=2.0), SELF_USE, "")
    end = step(ps, 13.0, P)       # only ~0.18 kWh above the 12% floor
    assert end == pytest.approx(12.0, abs=0.01)
    assert ps.grid_import > 1.7


def test_physics_grid_charge_respects_rate_and_target():
    ps = PlanSlot(Slot(T0, CHEAP, 0.15, load_kwh=0.2), GRID_CHARGE, "", target_soc=100)
    end = step(ps, 50.0, P)
    assert end == pytest.approx(50 + 4.8 * 0.5 * 0.95 / 18 * 100, abs=0.01)
    assert ps.grid_import == pytest.approx(0.2 + 2.4)


def test_physics_solar_surplus_charges_then_exports():
    ps = PlanSlot(Slot(T0, PEAK, 0.15, load_kwh=0.2, solar_kwh=2.0), SELF_USE, "")
    step(ps, 99.0, P)
    assert ps.grid_export > 1.5


def test_low_battery_gets_charged_in_the_cheap_window():
    plan = make_plan(day(), soc=20.0, p=P, now=T0)
    acts = actions(plan)
    assert GRID_CHARGE in acts
    charged = [i for i, a in enumerate(acts) if a == GRID_CHARGE]
    assert all(14 <= i < 24 for i in charged), charged        # only in the cheap window
    assert plan.cost < plan.baseline_cost


def test_cheap_window_holds_rather_than_discharges():
    plan = make_plan(day(), soc=90.0, p=P, now=T0)
    assert all(plan.slots[i].action in (HOLD, GRID_CHARGE) for i in range(14, 24))


def test_never_charges_above_cheap_cap_for_normal_use():
    plan = make_plan(day(cheap_from=99, cheap_to=99), soc=20.0, p=P, now=T0)   # no cheap slots at all
    assert GRID_CHARGE not in actions(plan)


def test_axle_event_forces_discharge_and_tops_up_beforehand():
    # 2-hour event at slots 30-33; start low; peak-price top-up is worth it for £1/kWh
    plan = make_plan(day(cheap_from=99, cheap_to=99, axle={30, 31, 32, 33}), soc=15.0, p=P, now=T0)
    acts = actions(plan)
    assert all(acts[i] == FORCE_DISCHARGE for i in range(30, 34))
    assert GRID_CHARGE in acts[:30]
    top_ups = [ps for ps in plan.slots[:30] if ps.action == GRID_CHARGE]
    assert "Axle" in top_ups[0].reason


def test_axle_disabled_is_ignored():
    p = Params(axle_enabled=False)
    assert FORCE_DISCHARGE not in actions(make_plan(day(axle={30}), soc=50.0, p=p, now=T0))


def test_free_power_fills_battery():
    plan = make_plan(day(free={5, 6}), soc=50.0, p=P, now=T0)
    assert plan.slots[5].action == GRID_CHARGE and plan.slots[5].target_soc == 100


def test_car_slot_holds():
    plan = make_plan(day(smart_slot={8}), soc=80.0, p=P, now=T0)
    assert plan.slots[8].action in (HOLD, GRID_CHARGE)
    assert "car" in plan.slots[8].reason or plan.slots[8].action == GRID_CHARGE


def test_windows_merge_and_headline():
    plan = make_plan(day(), soc=20.0, p=P, now=T0)
    assert len(plan.windows) < len(plan.slots)
    assert {"from", "to", "action", "reason", "price"} <= set(plan.windows[0])
    text = headline(plan)
    assert "grid-charge" in text.lower() and "saves" in text


def test_simulate_is_deterministic():
    a = make_plan(day(), soc=30.0, p=P, now=T0)
    b = make_plan(day(), soc=30.0, p=P, now=T0)
    assert actions(a) == actions(b) and a.cost == b.cost


def test_runs_fast_on_96_slots():
    import time
    t = time.perf_counter()
    make_plan(day(n=96, axle={40, 41, 42}), soc=12.0, p=P, now=T0)
    assert time.perf_counter() - t < 2.0


def test_simulate_total_cost_matches_slots():
    plan = [PlanSlot(s, SELF_USE, "") for s in day(n=4)]
    total = simulate(plan, 50.0, P)
    assert total == pytest.approx(sum(ps.cost for ps in plan))


def test_slot_end():
    assert Slot(T0, 0.1, 0.1).end == T0 + timedelta(minutes=30)


def test_charge_windows_merge_show_reached_level_and_name_the_day():
    plan = make_plan(day(n=72), soc=20.0, p=P, now=T0)
    charges = [w for w in plan.windows if w["action"] == GRID_CHARGE]
    assert len(charges) == 1                                  # back-to-back charge slots are one window
    w = charges[0]
    assert w["target_soc"] == round(w["soc_end"])             # the level reached, not the 100% ceiling
    assert w["day"] == "tomorrow"                             # 00:00 UTC on the 23rd
    assert "from" in w["reason"]
    assert plan.windows[-1]["to"].endswith("Thu")            # a window running past midnight says so


def test_day_labels():
    from pe_core.planner import _day
    assert _day(T0 + timedelta(hours=1), T0, timezone.utc) == ""
    assert _day(T0 + timedelta(hours=8), T0, timezone.utc) == "tomorrow"
    assert _day(T0 + timedelta(hours=40), T0, timezone.utc) == "Thu"
    assert _day(T0, None, timezone.utc) == ""


def test_fuse_limits_grid_charging_while_the_car_charges():
    from pe_core.planner import grid_charge_kw
    p60 = Params(fuse_kw=60 * 0.230 * 0.9, ev_charger_kw=7.4)           # 12.42 kW
    p80 = Params(fuse_kw=80 * 0.230 * 0.9, ev_charger_kw=7.4)           # 16.56 kW
    house_1kw = Slot(T0, CHEAP, 0.15, load_kwh=0.5, smart_slot=True)
    assert grid_charge_kw(house_1kw, p80) == pytest.approx(4.8)           # 1 + 7.4 + 4.8 = 13.2 < 16.56
    assert grid_charge_kw(house_1kw, p60) == pytest.approx(12.42 - 1 - 7.4)
    no_car = Slot(T0, CHEAP, 0.15, load_kwh=0.5)
    assert grid_charge_kw(no_car, p60) == pytest.approx(4.8)
    live = Slot(T0, CHEAP, 0.15, load_kwh=0.5, car_kw=11.0)               # live reading beats the assumption
    assert grid_charge_kw(live, p60) == pytest.approx(0.42)


def test_fill_when_cheap_tops_up_in_the_cheap_window_and_only_there():
    plan = make_plan(day(), soc=60.0, p=FILL, now=T0)
    assert all(plan.slots[i].action == GRID_CHARGE for i in range(14, 24))
    assert max(ps.soc_end for ps in plan.slots[14:24]) == pytest.approx(100, abs=0.1)
    assert all(plan.slots[i].action != GRID_CHARGE for i in range(0, 14))
    # without it, the heuristic buys only what the forecast needs
    lean = make_plan(day(), soc=60.0, p=P, now=T0)
    assert max(ps.soc_end for ps in lean.slots[14:24]) < 100


def test_charge_bars_count_only_energy_into_the_battery():
    from pe_core.planner import plan_entity_states
    plan = make_plan(day(load=0.3, solar=0.3), soc=100.0, p=FILL, now=T0)   # stays full: nothing to charge
    ser = plan_entity_states(plan)["plan"][1]["series"]
    assert sum(ser["charge_kwh"]) < 0.2


def test_charge_left_at_the_end_counts_in_the_saving():
    plan = make_plan(day(n=30), soc=60.0, p=FILL, now=T0)       # ends in the cheap window, freshly topped up
    assert plan.extra_kwh > 0 and plan.extra_value == pytest.approx(plan.extra_kwh * CHEAP)
    assert plan.saving == pytest.approx(plan.baseline_cost - plan.cost + plan.extra_value)
