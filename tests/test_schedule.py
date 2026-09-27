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
    # 07:00 is 10 h away: left alone for now (closed or reused once it's within 4 h)
    assert assign([w1], programmed, NOW) == [(23, 0, 23, 59), CLOSED, (7, 0, 8, 0)]
    assert assign([], programmed, NOW.replace(hour=23, minute=30) + timedelta(hours=4)) == \
        [(23, 0, 23, 59), CLOSED, CLOSED]              # 03:30: 07:00 is near and unwanted; 23:00 isn't near yet
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


def test_a_running_window_is_not_moved_forward_each_half_hour():
    run = Period("charge", NOW.replace(hour=22, minute=30), NOW.replace(hour=23, minute=59), GRID_CHARGE)
    at = NOW.replace(hour=22, minute=40)
    assert assign([run], [(21, 30, 23, 59), CLOSED, CLOSED], at) == [(21, 30, 23, 59), CLOSED, CLOSED]
    # but a changed end is written
    shorter = Period("charge", run.start, NOW.replace(hour=23, minute=30), GRID_CHARGE)
    assert assign([shorter], [(21, 30, 23, 59), CLOSED, CLOSED], at)[0] == (22, 30, 23, 30)


def test_far_periods_wait_until_they_are_near():
    far = Period("discharge", (NOW + timedelta(hours=16)).replace(minute=30), NOW + timedelta(hours=17),
                 EXPORT)                                                  # 13:30 tomorrow, from 21:00
    assert assign([far], [CLOSED, CLOSED, CLOSED], NOW) == [CLOSED, CLOSED, CLOSED]
    soon = NOW + timedelta(hours=13)                                      # 10:00: now within 4 h
    assert assign([far], [CLOSED, CLOSED, CLOSED], soon)[0] == (13, 30, 14, 0)


def test_a_night_with_a_moving_plan_writes_little():
    """Simulate a night where the plan is remade every half-hour: running windows keep their start, so the only
    writes are real changes."""
    acts = [GRID_CHARGE] * 6 + [EXPORT] * 4 + [GRID_CHARGE] * 4 + [SELF_USE] * 20
    have: dict = {f"{r}#{n}": 0 for k in ("charge", "discharge") for r in
                  (f"timed_{k}_start_hour", f"timed_{k}_start_minute", f"timed_{k}_end_hour", f"timed_{k}_end_minute")
                  for n in (1, 2, 3)}
    have.update({"timed_charge_current": 90, "timed_discharge_current": 90, "storage_mode": "Self-Use"})
    total = 0
    start = NOW.replace(hour=23)
    for step in range(12):                        # 23:00 .. 04:30, remade each half-hour
        now = start + timedelta(minutes=30 * step)
        pers = periods(plan_of(acts[step:], now), now, LON)
        ws = writes_for(desired_state(pers, have, now, None, None, 52.0, 4800, 4800), have)
        total += len(ws)
        have.update({w.role: w.value for w in ws if w.kind != "button"})
    assert total <= 15, total                 # programming the night once, plus one real change


def test_later_changes_wait_for_the_plan_to_settle():
    from pe_core.control import Write
    from pe_core.schedule import settled, urgent
    at = NOW.replace(hour=3, minute=40)
    have = {"timed_discharge_start_hour#2": 0, "timed_discharge_start_minute#2": 0,
            "timed_discharge_end_hour#2": 0, "timed_discharge_end_minute#2": 0}
    want = dict(have, **{"timed_discharge_start_hour#2": 8, "timed_discharge_end_hour#2": 10})
    ws = [Write("timed_discharge_start_hour#2", 8, "number"), Write("timed_discharge_end_hour#2", 10, "number")]
    assert not urgent(ws, want, have, at)
    ok, pend = settled(None, want, at)
    assert not ok
    ok, pend = settled(pend, want, at + timedelta(minutes=5))
    assert not ok
    ok, pend = settled(pend, want, at + timedelta(minutes=11))
    assert ok
    soon = dict(have, **{"timed_discharge_start_hour#2": 4, "timed_discharge_end_hour#2": 5})
    assert urgent([Write("timed_discharge_start_hour#2", 4, "number")], soon, have, at)      # due in 20 min
    assert urgent([Write("timed_charge_current", 0, "number")], {}, {}, at)


def test_selling_stays_in_the_band_unless_the_refill_is_guaranteed():
    from pe_core.optimiser import sell_floor
    t0 = NOW.astimezone(UTC)
    p = Params(arbitrage=True)
    # daytime: a long optional smart slot at 6.99p with 15p export, then peak
    day = [Slot(t0 + i * SLOT, 0.0699 if i < 16 else 0.30, 0.15, load_kwh=0.3, smart_slot=i < 16,
                car_expected=False) for i in range(24)]
    res = optimise(day, 90.0, p, wear=0.02)
    assert min(res["soc"][:16]) >= 74.5, res["soc"]
    assert sell_floor(day[0], p) == 75
    night = [Slot(t0 + i * SLOT, 0.0699, 0.15, load_kwh=0.3, overnight=True) for i in range(14)]
    assert sell_floor(night[0], p) == p.min_reserve_soc + p.arbitrage_keep_soc
    res = optimise(night + day[16:], 90.0, p, wear=0.02)
    assert min(res["soc"][:14]) < 70                                      # guaranteed refill: deeper is allowed


def test_one_button_press_per_slot_and_new_windows_share_a_pressed_slot():
    """Each update button sends its slot's charge AND discharge times in one write, so a charge and a discharge
    change in the same slot cost one press, and a new window goes into a slot that's being pressed anyway."""
    start = NOW                                                   # 21:00
    acts = [EXPORT, EXPORT, GRID_CHARGE, GRID_CHARGE] + [SELF_USE] * 8
    pers = periods(plan_of(acts, start), start, LON)
    # charge slot 2 already has an unwanted later window; discharge slots 1 and 3 are free
    have: dict = {}
    for kind in ("charge", "discharge"):
        for n in (1, 2, 3):
            for role in ("start_hour", "start_minute", "end_hour", "end_minute"):
                have[f"timed_{kind}_{role}#{n}"] = 0
    for role, v in zip(("start_hour", "start_minute", "end_hour", "end_minute"), (21, 30, 22, 0), strict=True):
        have[f"timed_charge_{role}#2"] = v                     # due within AHEAD and no longer wanted: closed
    want = desired_state(pers, have, start, EXPORT, None, 52.0, 4800, 4800)
    ws = writes_for(want, have)
    buttons = sorted(w.role for w in ws if w.kind == "button")
    assert len(buttons) == len(set(buttons))                   # never twice for one slot
    changed = {int(w.role.split("#")[1]) for w in ws if "#" in w.role and w.kind == "number"}
    assert buttons == [f"timed_update_button#{n}" for n in sorted(changed)]
    assert len(buttons) == 2, buttons                          # slot 2 (charge closes) + the new pair's slot


def test_new_discharge_window_prefers_a_slot_being_pressed():
    near = Period("discharge", NOW.replace(hour=21, minute=30), NOW.replace(hour=22), EXPORT)
    assert assign([near], [CLOSED, CLOSED, CLOSED], NOW, pressing={3}) == [CLOSED, CLOSED, (21, 30, 22, 0)]
    assert assign([near], [CLOSED, CLOSED, CLOSED], NOW) == [(21, 30, 22, 0), CLOSED, CLOSED]


def test_forecast_writes_counts_real_writes_per_half_hour():
    from pe_core.schedule import forecast_writes
    start = NOW                                                    # 21:00: sell 22:00-23:00, charge 23:00-00:00
    acts = [SELF_USE, SELF_USE, EXPORT, EXPORT, GRID_CHARGE, GRID_CHARGE] + [SELF_USE] * 6
    plan = plan_of(acts, start)
    have = {f"timed_{k}_{r}#{n}": 0 for k in ("charge", "discharge") for n in (1, 2, 3)
            for r in ("start_hour", "start_minute", "end_hour", "end_minute")}
    have.update({"timed_charge_current": 0, "timed_discharge_current": 0, "storage_mode": "Self-Use"})
    f = forecast_writes(plan, start, LON, have, 52.0, 4800, 4800)
    assert f and all(n > 0 for n in f.values())
    first = min(f)
    assert first.startswith("2026-09-24T20:00")                  # 21:00 local: programme the coming windows
    assert sum(f.values()) <= 8                                  # a simple evening: a handful, not dozens
    assert forecast_writes(plan, start, LON, have, 52.0, 4800, 4800) == f
