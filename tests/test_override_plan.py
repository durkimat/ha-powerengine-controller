"""The plan is made around an override's half-hours (docs/plans/mode-override.md, O3)."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from pe_core.decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE
from pe_core.forecast import SLOT, Slot
from pe_core.planner import Params, make_plan, plan_entity_states

T0 = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)
PEAK, CHEAP = 0.30, 0.07


def day(n=48, manual=None, span=(4, 8), **over):
    return [Slot(T0 + i * SLOT, CHEAP if 14 <= i < 24 else PEAK, 0.15, load_kwh=0.5,
                 manual=manual if span[0] <= i < span[1] else None, **over) for i in range(n)]


@pytest.mark.parametrize("strategy", ["rules", "optimiser"])
@pytest.mark.parametrize("mode", [SELF_USE, HOLD, GRID_CHARGE, EXPORT])
def test_manual_slots_keep_the_owners_action(strategy, mode):
    plan = make_plan(day(manual=mode), 60.0, Params(arbitrage=True), T0, strategy=strategy)
    assert [ps.action for ps in plan.slots[4:8]] == [mode] * 4
    assert all(ps.reason.startswith("manual override") for ps in plan.slots[4:8])
    assert not any(ps.slot.manual for ps in plan.slots[:4] + plan.slots[8:])


def test_the_plan_around_it_changes():
    free = make_plan(day(), 60.0, Params(), T0)
    held = make_plan(day(manual=EXPORT), 60.0, Params(), T0)
    assert held.slots[8].soc_start < free.slots[8].soc_start        # the export emptied the battery first


def test_a_charge_heads_for_the_charge_target():
    plan = make_plan(day(manual=GRID_CHARGE), 30.0, Params(), T0)
    assert plan.slots[4].target_soc == Params().target_soc
    assert plan.slots[7].soc_end > plan.slots[4].soc_start


def test_windows_and_series_mark_the_override():
    plan = make_plan(day(manual=HOLD), 60.0, Params(), T0)
    manual = [w for w in plan.windows if w.get("manual")]
    assert len(manual) == 1 and manual[0]["action"] == HOLD and manual[0]["reason"].startswith("manual override")
    series = plan_entity_states(plan)["plan"][1]["series"]
    assert series["manual"][3:9] == [0, 100, 100, 100, 100, 0]
    plain = plan_entity_states(make_plan(day(), 60.0, Params(), T0))["plan"][1]["series"]
    assert "manual" not in plain and all("manual" not in w for w in make_plan(day(), 60.0, Params(), T0).windows)


def test_a_grid_event_slot_keeps_the_event():
    slots = [replace(s, axle=(i == 5)) for i, s in enumerate(day(manual=HOLD))]
    plan = make_plan(slots, 60.0, Params(axle_enabled=True), T0)
    assert plan.slots[5].action == FORCE_DISCHARGE
    assert not any(w.get("manual") and w["from"] == plan.windows[0]["from"] and w["action"] == FORCE_DISCHARGE
                   for w in plan.windows)


def test_export_runs_to_the_reserve_not_below():
    plan = make_plan(day(manual=EXPORT, span=(0, 12)), 40.0, Params(), T0, strategy="optimiser")
    assert min(ps.soc_end for ps in plan.slots) >= Params().min_reserve_soc - 0.01


def _app(ov, axle=False):
    from types import SimpleNamespace
    return SimpleNamespace(_active_override=lambda: ov, cfg=SimpleNamespace(features={"axle": axle}))


def test_mark_manual_covers_now_to_the_end_and_skips_events(monkeypatch):
    from replay_harness import import_powerengine
    pe = import_powerengine(monkeypatch)

    from pe_core.override import Override
    now = T0 + 2 * SLOT + SLOT / 3                                # part-way through slot 2
    ov = Override(HOLD, T0 + 6 * SLOT, now)
    slots = [replace(s, axle=(i == 4)) for i, s in enumerate(day(span=(0, 0)))]
    out = pe.PowerEngine._mark_manual(_app(ov, axle=True), slots, now)
    assert [bool(s.manual) for s in out[:8]] == [False, False, True, True, False, True, False, False]
    perm = pe.PowerEngine._mark_manual(_app(Override(EXPORT, None, now)), slots, now)
    assert all(s.manual == EXPORT for s in perm[2:]) and not any(s.manual for s in perm[:2])
    assert pe.PowerEngine._mark_manual(_app(None), slots, now) is slots
