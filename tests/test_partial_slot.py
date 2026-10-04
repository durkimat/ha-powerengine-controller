"""A plan made part-way through a half-hour plans that half-hour for the rest of it (#168), and a charge's label
names where the run of charging ends."""

from datetime import datetime, timedelta, timezone

import pytest
from fixtures import BST, NOW

from pe_core.activity import ActivityLog
from pe_core.config import parse_config
from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, decide
from pe_core.forecast import SLOT, Slot
from pe_core.optimiser import MID_SLOT_STICK, optimise, slot_target
from pe_core.planner import DT_H, Params, Plan, PlanSlot, make_plan, step
from pe_core.readings import Readings

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
CHEAP, PEAK = 0.0699, 0.3028
P = Params(arbitrage=True)


def block(n_cheap=16, n=24, load=0.3):
    return [Slot(T0 + i * SLOT, CHEAP if i < n_cheap else PEAK, 0.15, load_kwh=load) for i in range(n)]


def first_action(soc, minutes_left, prev="export", stick=0.0):
    o = optimise(block(), soc, P, wear=P.wear_p / 100, prev_action=prev, stick=stick, first_h=minutes_left / 60)
    return o["actions"][0]


def test_a_running_export_carries_on_with_time_left():
    for soc in (89, 88):
        assert first_action(soc, 20) == EXPORT, soc
        assert first_action(soc, 20, stick=MID_SLOT_STICK) == EXPORT, soc


def test_the_sell_floor_still_holds_for_a_whole_half_hour():
    assert first_action(89, 30) == EXPORT                       # 14% off 89 ends 75: in the band
    assert first_action(76, 30) != EXPORT                       # 14% off 76 would end below the hard floor (70%)
    assert first_action(76, 30, stick=MID_SLOT_STICK) != EXPORT


def test_full_slot_is_the_default():
    a = optimise(block(), 89, P, wear=0.02, prev_action="export")
    b = optimise(block(), 89, P, wear=0.02, prev_action="export", first_h=DT_H)
    assert a == b
    pa, pb = make_plan(block(), 89, P, T0, strategy="optimiser"), make_plan(block(), 89, P, T0, strategy="optimiser",
                                                                            first_h=DT_H)
    assert [x.action for x in pa.slots] == [x.action for x in pb.slots] and pa.cost == pb.cost
    ps = PlanSlot(block()[0], EXPORT, "")
    assert step(ps, 90.0, P) == step(PlanSlot(block()[0], EXPORT, ""), 90.0, P, DT_H)


def test_a_partial_slot_counts_only_its_part():
    s = Slot(T0, PEAK, 0.15, load_kwh=1.0)
    whole, part = PlanSlot(s, "self_use", ""), PlanSlot(s, "self_use", "", hours=0.25)
    step(whole, 50.0, Params())
    step(part, 50.0, Params())
    assert part.grid_import == pytest.approx(whole.grid_import / 2) and part.soc_end > whole.soc_end
    plan = make_plan(block(), 50.0, P, T0 + timedelta(minutes=10), strategy="optimiser", first_h=20 / 60)
    assert plan.slots[0].hours == pytest.approx(20 / 60) and plan.slots[1].hours == DT_H
    assert plan.slots[0].slot.load_kwh == 0.3                     # the slot itself is untouched (for the history)


def test_charge_target_of_a_partial_slot_is_its_own_end_soc():
    plan = make_plan(block(), 60.0, P, T0 + timedelta(minutes=20), strategy="optimiser", first_h=10 / 60)
    ps = plan.slots[0]
    if ps.action == GRID_CHARGE:
        assert ps.target_soc == pytest.approx(min(100.0, -(-ps.soc_end // 1)))
        assert ps.soc_end - 60.0 < 10                              # ten minutes at 5 kW, not a half-hour's worth


def test_closed_loop_replans_do_not_flip_flop():
    """Re-plan every 5 minutes through two hours of flat cheap import, the battery moving as told at 5 kW."""
    slots = block(n_cheap=24, n=30)
    soc, prev, actions, last = 90.0, "grid_charge", [], None
    for minute in range(0, 120, 5):
        into = (minute % 30) * 60
        left = (1800 - into) / 3600
        stick = MID_SLOT_STICK if into > 120 and last is not None else 0.0
        sl = slots[minute // 30:]
        o = optimise(sl, soc, P, wear=P.wear_p / 100, prev_action=prev, stick=stick,
                     first_h=None if left >= DT_H else left)
        a = o["actions"][0]
        if a == GRID_CHARGE and soc >= slot_target(sl[0], P):
            a = HOLD                                                # what decide does at the target
        actions.append((minute, a))
        ps = PlanSlot(sl[0], a, "", target_soc=slot_target(sl[0], P), hours=5 / 60)
        soc = step(ps, soc, P)
        prev = last = a
    changes = sum(1 for (_, x), (_, y) in zip(actions, actions[1:], strict=False) if x != y)
    assert changes <= 4, actions                                   # at most one per half-hour on average
    runs = "".join("E" if a == EXPORT else "." for _, a in actions).split(".")
    assert all(len(r) % 6 == 0 for r in runs), actions            # a sale started runs its whole half-hour
    for half in range(4):
        seq = [a for m, a in actions if m // 30 == half and a in (EXPORT, GRID_CHARGE)]
        toggles = sum(1 for x, y in zip(seq, seq[1:], strict=False) if x != y)
        assert toggles <= 1, (half, actions)


# --- the label ------------------------------------------------------------------

CFG = parse_config({"inputs": {"battery_capacity": {"value": 18}, "battery_max_discharge_power": {"value": 4800}}})


def _charging_run():
    s0 = T0
    slots = [PlanSlot(Slot(s0 + i * SLOT, CHEAP, 0.15), GRID_CHARGE, "charge at 7p to sell at 15p from 02:00",
                      target_soc=t) for i, t in enumerate((64.0, 77.0, 90.0))]
    slots.append(PlanSlot(Slot(s0 + 3 * SLOT, CHEAP, 0.15), EXPORT, "sell"))
    slots.append(PlanSlot(Slot(s0 + 4 * SLOT, CHEAP, 0.15), GRID_CHARGE, "again", target_soc=50.0))
    return Plan(slots=slots, made_at=NOW)


def _readings(soc):
    return Readings(now=NOW, battery_soc=soc, import_rate=CHEAP, export_rate=0.15, house_power=500, solar_power=0,
                    ev_power=0, ev_plug="EV Disconnected")


def test_label_names_the_end_of_the_charging_run_but_control_keeps_the_slot_target():
    d = decide(_readings(40), CFG, plan=_charging_run())
    assert d.action == GRID_CHARGE and d.target_soc == 64.0 and d.label_target_soc == 90.0
    assert "Grid-charge to 90%" in d.sentence(False)
    held = decide(_readings(64), CFG, plan=_charging_run())         # reached the slot's own target: hold
    assert held.action == HOLD and held.target_soc == 64.0


def test_activity_logs_a_charge_again_only_when_its_destination_changes():
    log = ActivityLog()
    plan = _charging_run()
    assert log.record(decide(_readings(40), CFG, plan=plan), NOW, False, BST)
    plan.slots.pop(0)                                              # half an hour on: the slot target moves up
    assert log.record(decide(_readings(50), CFG, plan=plan), NOW + timedelta(minutes=30), False, BST) is None
    plan.slots[1].target_soc = 100.0                               # the run now ends higher
    entry = log.record(decide(_readings(60), CFG, plan=plan), NOW + timedelta(minutes=31), False, BST)
    assert entry and "to 100%" in entry["text"]
    assert ActivityLog(log.entries).record(decide(_readings(60), CFG, plan=plan), NOW, False, BST) is None
