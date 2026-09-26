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


def test_cheap_threshold_follows_the_prices():
    from pe_core.tariff import cheap_threshold
    edf = [0.0699] * 14 + [0.3028] * 34
    assert cheap_threshold(edf, cap_p=10) == 10                       # the cap: 6.99p counts, 30p doesn't
    assert cheap_threshold(edf, cap_p=50) == pytest.approx(6.99 + 0.2 * (30.28 - 6.99), abs=0.01)
    agile = [0.05 + 0.005 * i for i in range(48)]                     # 5p .. 28.5p
    t = cheap_threshold(agile, cap_p=50)
    assert 9 < t < 10                                                 # the bottom fifth of the range
    assert cheap_threshold([0.25] * 48, cap_p=50) < 25                # flat: nothing is cheap
    assert cheap_threshold([-0.02] + [0.25] * 47, cap_p=10) >= 0      # negative prices always count
    close = [0.20] * 24 + [0.22] * 24                                 # storing at 20p to save 22p doesn't pay
    assert cheap_threshold(close, cap_p=50, rte=0.9, wear_p=2) < 20


def test_auto_threshold_is_used_by_the_plan():
    plan = make_plan(day(), soc=60.0, p=Params(cheap_cap_p=50, fill_when_cheap=False), now=T0, auto_cheap=True)
    assert plan.cheap_p == pytest.approx(7 + 0.2 * 23, abs=0.01)


def test_arbitrage_sells_surplus_just_before_the_cheap_refill():
    from pe_core.decide import EXPORT
    arb = Params(arbitrage=True)
    plan = make_plan(day(), soc=90.0, p=arb, now=T0)
    exports = [i for i, ps in enumerate(plan.slots) if ps.action == EXPORT]
    assert exports and max(exports) == 13 and all(i < 14 for i in exports)   # just before 00:00
    assert plan.slots[13].soc_end >= arb.min_reserve_soc + arb.arbitrage_keep_soc - 0.01   # the house stays covered
    assert all(plan.slots[i].grid_import < 0.01 for i in range(0, 14) if plan.slots[i].action != EXPORT)
    without = make_plan(day(), soc=90.0, p=Params(), now=T0)
    assert plan.cost < without.cost and "refilled at 7p" in plan.slots[13].reason


def test_no_arbitrage_when_it_does_not_pay_or_is_off():
    from pe_core.decide import EXPORT
    low_export = [replace_export(s, 0.08) for s in day()]
    assert not any(ps.action == EXPORT for ps in make_plan(low_export, 90.0, Params(arbitrage=True), T0).slots)
    assert not any(ps.action == EXPORT for ps in make_plan(day(), 90.0, Params(arbitrage=False), T0).slots)


def replace_export(s, value):
    import dataclasses
    return dataclasses.replace(s, export=value)


def test_export_split_solar_when_battery_full():
    ps = PlanSlot(Slot(T0, PEAK, 0.15, load_kwh=0.3, solar_kwh=2.0), SELF_USE, "")
    step(ps, 100, P)
    assert ps.battery_export == 0 and ps.grid_export == pytest.approx(1.7)


def test_export_split_battery_and_solar_when_selling():
    from pe_core.decide import EXPORT
    ps = PlanSlot(Slot(T0, PEAK, 0.15, load_kwh=0.3, solar_kwh=1.0), EXPORT, "")
    step(ps, 90, P)
    assert ps.battery_export > 0
    assert ps.grid_export - ps.battery_export == pytest.approx(0.7, abs=0.01)


def test_plan_series_has_solar_export_and_fits_ha_limit():
    import json

    from pe_core.planner import plan_entity_states
    plan = make_plan(day(n=72, solar=1.5, load=0.3), soc=95.0, p=P, now=T0)
    attrs = plan_entity_states(plan)["plan"][1]
    ser = attrs["series"]
    assert len(ser["solar_export_kwh"]) == len(ser["t"]) and max(ser["solar_export_kwh"]) > 0
    assert all(d == 0 for d in ser["discharge_kwh"])
    assert len(json.dumps(attrs, separators=(",", ":"))) < 15500


@pytest.mark.parametrize("p", [P, FILL])
def test_horizon_end_does_not_change_what_happens_now(p):
    """#15: the 36-hour minimum horizon means the end of the plan never changes the current half-hour's action
    (compared with a 48-hour horizon), over every start time of day, several charge levels, with and without sun."""
    def slots(n, off, solar):
        out = []
        for i in range(n):
            k = (i + off) % 48
            out.append(Slot(T0 + (i + off) * SLOT, CHEAP if 14 <= k < 24 else PEAK, 0.15,
                            solar_kwh=solar if 30 <= k <= 40 else 0.0, load_kwh=0.5))
        return out
    for off in range(0, 48, 3):
        for soc in (15.0, 60.0, 95.0):
            for solar in (0.0, 1.5):
                now = T0 + off * SLOT
                short = make_plan(slots(72, off, solar), soc=soc, p=p, now=now)
                long = make_plan(slots(96, off, solar), soc=soc, p=p, now=now)
                assert short.slots[0].action == long.slots[0].action, (off, soc, solar)


def test_arbitrage_band_is_a_guide():
    from pe_core.decide import EXPORT
    arb = Params(arbitrage=True, arbitrage_min_soc=75, arbitrage_max_soc=90)
    assert arb.buffer_target == 90 and Params().buffer_target == 100
    # selling at 15p after buying at 7p clears the 2p outside-band cost, so it may go below 75% while covered
    plan = make_plan(day(load=0.1), soc=100.0, p=arb, now=T0)
    assert min(ps.soc_end for ps in plan.slots[:14]) < 75
    assert plan.slots[13].soc_end >= arb.min_reserve_soc + arb.arbitrage_keep_soc - 0.01
    # a high outside-band cost keeps it in the band
    strict = Params(arbitrage=True, arbitrage_band_penalty_p=10.0)
    tight = make_plan(day(load=0.1), soc=100.0, p=strict, now=T0)
    assert any(ps.action == EXPORT for ps in tight.slots)
    assert min(ps.soc_end for ps in tight.slots[:14]) >= 75 - 1.0
    # routine cheap top-up stops at the band top
    assert max(ps.soc_end for ps in plan.slots[14:24]) <= 90 + 0.01


def test_optimiser_band_penalty():
    from pe_core.decide import EXPORT, GRID_CHARGE
    from pe_core.optimiser import band_penalty
    arb = Params(arbitrage=True, capacity_kwh=10.0)
    assert band_penalty(EXPORT, 80, 70, arb) == pytest.approx(0.5 * 0.02)      # 5% of 10 kWh below 75%
    assert band_penalty(GRID_CHARGE, 85, 95, arb) == pytest.approx(0.5 * 0.02)  # 5% above 90%
    assert band_penalty(EXPORT, 80, 70, Params()) == 0


def test_config_arbitrage_band_validated():
    from pe_core.config import ConfigError, parse_config
    from pe_core.planner import params_from
    p = params_from(parse_config({"features": {"arbitrage": True}}))
    assert (p.arbitrage_min_soc, p.arbitrage_max_soc) == (75, 90)
    with pytest.raises(ConfigError, match="lowest charge"):
        parse_config({"safety": {"arbitrage_min_soc": 90, "arbitrage_max_soc": 80}})
