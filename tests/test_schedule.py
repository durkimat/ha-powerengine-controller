"""Three-slot inverter programming, the overnight window and the window-change cost."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE
from pe_core.forecast import SLOT, Slot
from pe_core.optimiser import optimise
from pe_core.planner import Params, PlanSlot
from pe_core.schedule import CLOSED, Period, assign, desired_state, periods, slot_entities, writes_for

LON = ZoneInfo("Europe/London")
UTC = timezone.utc
NOW = datetime(2026, 9, 24, 21, 0, tzinfo=LON)                  # 21:00 local


def plan_of(actions, start=NOW):
    t0 = start.astimezone(UTC)
    return [PlanSlot(Slot(t0 + i * SLOT, 0.3, 0.15), a, "") for i, a in enumerate(actions)]


def test_periods_merge_split_at_midnight_and_use_the_live_action():
    acts = [SELF_USE, SELF_USE, EXPORT, EXPORT, GRID_CHARGE, GRID_CHARGE, HOLD, GRID_CHARGE] + [SELF_USE] * 4
    pers = periods(plan_of(acts), NOW, LON)
    assert [(p.kind, p.start.strftime("%H:%M"), p.end.strftime("%H:%M")) for p in pers] == [
        ("discharge", "22:00", "23:00"), ("charge", "23:00", "23:59"), ("charge", "00:00", "01:00")]
    live = periods(plan_of(acts), NOW, LON, current_action=HOLD)
    assert live[0].kind == "charge" and live[0].start.strftime("%H:%M") == "21:00"


def test_assign_keeps_matching_windows_and_reuses_free_slots():
    w1 = Period("charge", NOW.replace(hour=23), NOW.replace(hour=23, minute=59), GRID_CHARGE)
    programmed = [(23, 0, 23, 59), CLOSED, (7, 0, 8, 0)]
    assert assign([w1], programmed, NOW) == [(23, 0, 23, 59), CLOSED, CLOSED]
    far = Period("charge", NOW.replace(hour=23, minute=30), NOW.replace(hour=23, minute=59), GRID_CHARGE)
    assert assign([far], [(23, 0, 23, 59), CLOSED, CLOSED], NOW - timedelta(hours=3)) == [(23, 0, 23, 59), CLOSED,
                                                                                          CLOSED]   # within tolerance
    assert assign([far], [(23, 0, 23, 59), CLOSED, CLOSED], NOW.replace(hour=22)) == [(23, 30, 23, 59), CLOSED,
                                                                                        CLOSED]  # near: exact


def test_a_repeating_night_needs_no_writes():
    acts = [EXPORT] * 4 + [GRID_CHARGE] * 6 + [SELF_USE] * 10
    start = NOW.replace(hour=23)
    pers = periods(plan_of(acts, start), start, LON)
    have: dict = {}
    want = desired_state(pers, have, start, EXPORT, None, 52.0, 4800, 4800)
    first = writes_for(want, have)
    assert first and any(w.role.startswith("timed_update_button#") for w in first)
    have = {w.role: w.value for w in first if w.kind != "button"}
    again = writes_for(desired_state(pers, have, start, EXPORT, None, 52.0, 4800, 4800), have)
    assert again == []


def test_currents_follow_the_current_or_next_window():
    start = NOW
    pers = periods(plan_of([SELF_USE, HOLD, GRID_CHARGE]), start, LON)
    w = desired_state(pers, {}, start, SELF_USE, None, 52.0, 4800, 4800)
    assert w["timed_charge_current"] == 0 and "timed_discharge_current" not in w       # next up is a hold
    w = desired_state(pers, {}, start, GRID_CHARGE, 2600, 52.0, 4800, 4800)
    assert w["timed_charge_current"] == 50


def test_slot_entities_need_all_three():
    first = {"timed_charge_start_hour": "number.solis_timed_charge_start_hours",
             "timed_update_button": "button.solis_update_charge_discharge_times"}
    have = {"number.solis_timed_charge_start_hours_2", "number.solis_timed_charge_start_hours_3",
            "button.solis_update_charge_discharge_times_2", "button.solis_update_charge_discharge_times_3"}
    m = slot_entities(first, lambda e: e in have)
    assert m[3]["timed_update_button"] == "button.solis_update_charge_discharge_times_3"
    assert slot_entities(first, lambda e: False) is None


def _night(start_soc=100.0):
    """21:00 -> 21:00 next day: peak until 23:00, 7-hour cheap window 23:00-06:00, then peak; export 15p."""
    t0 = NOW.astimezone(UTC)
    out = []
    for i in range(48):
        local = (t0 + i * SLOT).astimezone(LON)
        night = local.hour >= 23 or local.hour < 6
        out.append(Slot(t0 + i * SLOT, 0.07 if night else 0.30, 0.15, load_kwh=0.3, overnight=night))
    return out


def test_window_change_cost_gives_one_deep_overnight_cycle():
    free = Params(arbitrage=True, switch_cost_p=0.0)
    costly = Params(arbitrage=True, switch_cost_p=5.0)
    a = optimise(_night(), 60.0, free, wear=0.02)
    b = optimise(_night(), 60.0, costly, wear=0.02)
    assert b["switches"] < a["switches"]
    assert b["switches"] <= 6                                        # fits the three charge + three sell windows
    ends = [i for i, s in enumerate(_night()) if s.overnight][-1]
    assert b["soc"][ends] >= 98.0                                   # full when the cheap window closes


def test_optimiser_sells_below_the_band_inside_the_overnight_window():
    b = optimise(_night(), 100.0, Params(arbitrage=True, switch_cost_p=5.0), wear=0.02)
    night = [i for i, s in enumerate(_night()) if s.overnight]
    assert min(b["soc"][i] for i in night) < 60                     # a deep cycle, not a 75-90% shuffle
    assert min(b["soc"]) >= Params().min_reserve_soc + Params().arbitrage_keep_soc - 1.0
