"""0.9.64: the flap at the charge target, the price shown for a charge, "waiting for inputs", smart-slot energy.

Each case comes from the 30 Sep 2026 diagnostics (29 Sep 2026 evening and morning)."""
from datetime import datetime, timedelta, timezone

from test_pause_guards import GUARDED, app  # noqa: F401  (fixture for ramapp)
from test_ramcontrol import ramapp  # noqa: F401  (fixture)

from pe_core.config import parse_config
from pe_core.decide import GRID_CHARGE, HOLD, SELF_USE, Decision, decide
from pe_core.forecast import SLOT, Slot
from pe_core.modes import effective_mode
from pe_core.planner import Params, Plan, PlanSlot, make_plan
from pe_core.readings import Readings, Window
from pe_core.slots import SlotTracker
from pe_core.status import summary

CFG = parse_config({"inputs": {"battery_capacity": {"value": 18}, "battery_max_discharge_power": {"value": 4800}}})
T0 = datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc)
CHEAP, PEAK = 0.0699, 0.3028


def readings(soc, now=T0 + timedelta(minutes=8), rate=CHEAP, **kw):
    base = dict(now=now, battery_soc=soc, import_rate=rate, export_rate=0.15, house_power=500, solar_power=0,
                ev_power=0, ev_plug="EV Disconnected")
    base.update(kw)
    return Readings(**base)


def charging_plan(target=94.0, start=T0, rate=CHEAP):
    slots = [PlanSlot(Slot(start, rate, 0.15), GRID_CHARGE, "charge at 6.99p to sell at 15p from 00:00 tomorrow",
                      target_soc=target),
             PlanSlot(Slot(start + SLOT, rate, 0.15), GRID_CHARGE, "again", target_soc=100.0)]
    return Plan(slots=slots, made_at=start)


# --- 1. flapping at the target --------------------------------------------------------------------------

def inverter_soc(action, true_soc=93.6):
    """The Solis reports a whole number that reads a point lower while it charges than while it holds."""
    return float(int(true_soc) if action == GRID_CHARGE else round(true_soc))


def run_loop(plan_for, cycles, true_soc=93.6, car=False):
    prev, out = None, []
    for i in range(cycles):
        soc = inverter_soc(prev.action, true_soc) if prev else 94.0         # it has just charged up to the target
        kw = dict(ev_power=7000, ev_plug="Charging") if car else {}
        prev = decide(readings(soc, T0 + timedelta(minutes=8, seconds=30 * i), **kw), CFG, prev, plan=plan_for(i))
        out.append(prev)
    return out


def test_at_the_target_it_holds_instead_of_flapping_every_30_seconds():
    ds = run_loop(lambda i: charging_plan(94.0), 24)
    actions = [d.action for d in ds]
    switches = sum(1 for a, b in zip(actions, actions[1:], strict=False) if a != b)
    assert switches <= 1, actions                       # was: a switch every 30 s (Force charge <-> Hold)
    assert actions[-1] == HOLD and "reached the 94% charge target" in ds[-1].reason


def test_the_same_flap_with_the_car_charging_in_and_out_of_the_reading():
    """09:55-09:57: the Zappi's trickle power came and went, so the car rule and the plan took turns."""
    ds = run_loop(lambda i: charging_plan(94.0), 12, car=False)
    held = ds[-1]
    assert held.action == HOLD
    car = decide(readings(94.0, ev_power=7000, ev_plug="Charging"), CFG, held, plan=charging_plan(94.0))
    assert car.action == HOLD and "reached the 94% charge target" in car.reason


def test_it_charges_again_when_the_charge_really_drops_two_points_below_the_target():
    held = decide(readings(94.0), CFG, None, plan=charging_plan(94.0))
    assert held.action == HOLD
    assert decide(readings(93.0), CFG, held, plan=charging_plan(94.0)).action == HOLD          # reading noise
    assert decide(readings(92.0), CFG, held, plan=charging_plan(94.0)).action == GRID_CHARGE   # 2 points down


def test_it_charges_again_when_the_plan_wants_a_clearly_higher_target():
    held = decide(readings(94.0), CFG, None, plan=charging_plan(94.0))
    assert decide(readings(94.0), CFG, held, plan=charging_plan(95.0)).action == HOLD          # one point: still held
    assert decide(readings(94.0), CFG, held, plan=charging_plan(100.0)).action == GRID_CHARGE


def test_the_hold_ends_with_the_half_hour_and_with_the_plans_action():
    held = decide(readings(94.0), CFG, None, plan=charging_plan(94.0))
    nxt = charging_plan(94.0, start=T0 + SLOT)                                # the next half-hour's slot 0
    assert decide(readings(93.0, T0 + SLOT + timedelta(minutes=1)), CFG, held, plan=nxt).action == GRID_CHARGE
    other = Plan(slots=[PlanSlot(Slot(T0, CHEAP, 0.15), SELF_USE, "x")], made_at=T0)
    assert decide(readings(94.0), CFG, held, plan=other).action == SELF_USE      # not held: the plan says self-use


def test_other_holds_are_not_latched():
    prev = Decision(HOLD, "plan", "keep the charge for later")
    assert decide(readings(93.0), CFG, prev, plan=charging_plan(94.0)).action == GRID_CHARGE


# --- 2. the price a charge is shown at ------------------------------------------------------------------

def solar_day(soc_price=PEAK, solar0=1.0):
    t0 = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(60):
        t = t0 + i * SLOT
        h = (t + timedelta(hours=1)).hour
        s = Slot(t, CHEAP if h < 5 else soc_price, 0.15, load_kwh=0.35,
                 solar_kwh=solar0 if i < 8 else (1.0 if 7 <= h < 18 else 0.0))
        s.overnight = h < 5
        slots.append(s)
    return t0, slots


def test_a_charge_that_needs_no_grid_energy_at_the_peak_rate_is_not_a_grid_charge():
    """29 Sep 10:00: plan 'charge at 30.28p to sell at 15p' was a solar-surplus charge in the forecast; forced at full
    power under a cloudier sky it imported about 3 kW at 30.28p."""
    t0, slots = solar_day()
    plan = make_plan(slots, 92.0, Params(arbitrage=True, max_charge_kw=5.0, capacity_kwh=18.0), t0,
                     strategy="optimiser")
    first = plan.slots[0]
    assert first.action != GRID_CHARGE and "30.28p" not in first.reason and "sell at" not in first.reason
    for ps in plan.slots:                              # and no charge anywhere is bought at the peak to sell lower
        if ps.action == GRID_CHARGE and ps.slot.price > 0.2:
            assert ps.grid_import > 0.05 or "30.28p to sell" not in ps.reason


def test_with_that_plan_a_car_charging_at_the_peak_holds_and_never_force_charges():
    t0, slots = solar_day()
    plan = make_plan(slots, 92.0, Params(arbitrage=True, max_charge_kw=5.0, capacity_kwh=18.0), t0,
                     strategy="optimiser")
    d = decide(readings(92.0, t0 + timedelta(seconds=12), rate=PEAK, ev_power=220, ev_plug="Charging"), CFG, None,
               plan=plan)
    assert d.action == HOLD and d.rule == "car_charging" and "30.28p" in d.reason


def test_cheap_grid_charging_is_untouched():
    slots = [Slot(T0 + i * SLOT, CHEAP if i < 6 else PEAK, 0.15, load_kwh=0.4) for i in range(40)]
    plan = make_plan(slots, 20.0, Params(arbitrage=True, max_charge_kw=5.0, capacity_kwh=18.0), T0,
                     strategy="optimiser")
    assert plan.slots[0].action == GRID_CHARGE and plan.slots[0].grid_import > 1.0


def test_a_charge_that_really_buys_at_a_dear_rate_stays_a_grid_charge():
    """No solar, everything dear now, dearer later: the plan weighs the price it pays and charges at it."""
    slots = [Slot(T0 + i * SLOT, 0.25 if i < 4 else 0.40, 0.05, load_kwh=1.0) for i in range(40)]
    plan = make_plan(slots, 20.0, Params(max_charge_kw=5.0, capacity_kwh=18.0), T0, strategy="optimiser")
    charges = [ps for ps in plan.slots[:4] if ps.action == GRID_CHARGE]
    assert charges and all(ps.grid_import > 0.5 for ps in charges)


def test_a_car_slot_whose_rate_is_not_yet_the_slot_rate_says_so():
    """29 Sep 21:17: 'car smart-charge slot (30.28p)': EDF had planned the slot, the tariff still showed the peak rate
    (6.99p half a minute later). The battery only holds there, so no price was used, and none is claimed."""
    def car_plan(price, strategy):
        slots = [Slot(T0 + i * SLOT, price if i == 0 else PEAK, 0.15, load_kwh=0.4, smart_slot=i == 0,
                      car_expected=True) for i in range(6)]
        return make_plan(slots, 60.0, Params(fill_when_cheap=False), T0, strategy=strategy).slots[0]
    for strategy in ("rules", "optimiser"):
        ps = car_plan(PEAK, strategy)
        assert ps.action == HOLD and "car smart-charge slot (tariff still 30.28p)" in ps.reason, ps.reason
        cut = car_plan(CHEAP, strategy)
        assert "car smart-charge slot (6.99p)" in cut.reason or cut.action == GRID_CHARGE, cut.reason


# --- 3. waiting for inputs ------------------------------------------------------------------------------

def test_missing_inputs_read_as_waiting_but_the_mode_key_is_unchanged():
    cfg = parse_config(GUARDED)
    waiting = effective_mode(cfg, build_supports_active=True, missing_required=["battery_soc"])
    assert waiting.effective == "unconfigured" and waiting.configured == "active"      # keys other code relies on
    assert waiting.label == "waiting for inputs"
    assert effective_mode(None).label == "unconfigured" and effective_mode(cfg, config_error="x").label == \
        "unconfigured"
    assert effective_mode(cfg, build_supports_active=True).label == "active"


def test_the_summary_says_waiting_for_inputs():
    cfg = parse_config(GUARDED)
    waiting = effective_mode(cfg, build_supports_active=True, missing_required=["battery_soc", "grid_power"])
    assert summary(None, waiting).startswith("WAITING FOR INPUTS. 2 required input(s) not ready")
    assert summary(None, effective_mode(None)).startswith("UNCONFIGURED.")


def test_leaving_active_for_missing_inputs_logs_waiting(ramapp):  # noqa: F811
    a, logs = ramapp, []
    a.log = lambda msg, *args, **kw: logs.append(msg)
    a._ram_was_on = True
    a.cfg_error = None
    broken = effective_mode(a.cfg, build_supports_active=True, missing_required=["battery_soc"])
    a._leave_active(a.mode, broken)
    assert "RAM remote control Off (leaving Active (waiting for inputs))" in logs
    assert not any("unconfigured" in m for m in logs)


# --- 4. smart-slot energy -------------------------------------------------------------------------------

M = timedelta(minutes=1)
D0 = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)


def W(a_min, b_min, kwh):
    return Window(D0 + a_min * M, D0 + b_min * M, kwh)


def test_a_half_hour_slot_is_the_charger_rate_for_half_an_hour_not_the_whole_dispatch():
    """29 Sep: 22:30-23:00 showed 38.5 kWh: it was first listed as 22:30-04:00 (7 kW x 5.5 h), then continued."""
    tr = SlotTracker()
    for m in range(0, 241):
        tr.update(D0 + m * M, [W(210, 540, -38.5)], [], m >= 210, 7000, 60)      # listed 22:30-04:00, then running
    for m in range(241, 300):                                                    # EDF re-lists it from the current time
        tr.update(D0 + m * M, [W(m, 540, -7.0 * (540 - m) / 60)], [], True, 7000, 60)
    rec = tr.slots[(D0 + 210 * M).isoformat()]
    assert rec["status"] == "done" and rec["continued"] and rec["planned_kwh"] == 38.5
    assert abs(SlotTracker.planned_kwh(rec) - 3.6) < 0.1                         # not 38.5
    rows = tr.summary(D0 + 300 * M, days=1)["recent"]
    assert all(r["planned_kwh"] <= 7.4 * 0.6 for r in rows if r["time"].startswith("22:30"))


def test_a_slot_cut_short_or_continued_is_scaled_to_the_time_it_lasted():
    rec = {"start": D0.isoformat(), "end": (D0 + 30 * M).isoformat(), "planned_kwh": 63.0, "listed_h": 9.0}
    assert SlotTracker.planned_kwh(rec) == 3.5                              # 63 kWh over 9 h, for half an hour
    whole = dict(rec, end=(D0 + 540 * M).isoformat())
    assert SlotTracker.planned_kwh(whole) == 63.0


def test_old_records_without_the_listing_length_are_capped_at_the_charger_rate():
    rec = {"start": D0.isoformat(), "end": (D0 + 30 * M).isoformat(), "planned_kwh": 38.5}
    assert SlotTracker.planned_kwh(rec, 7.4) == 3.7 and SlotTracker.planned_kwh(rec, 7.0) == 3.5
    small = {"start": D0.isoformat(), "end": (D0 + 60 * M).isoformat(), "planned_kwh": 3.0}
    assert SlotTracker.planned_kwh(small) == 3.0                            # already under the cap: untouched


def test_the_summary_counts_planned_energy_only_for_slots_that_ran_and_never_beyond_the_rate():
    tr = SlotTracker()

    def add(a, b, kwh, status, listed=None):
        rec = {"start": (D0 + a * M).isoformat(), "end": (D0 + b * M).isoformat(), "planned_kwh": kwh,
               "status": status, "car_kwh": 0.0, "charging_min": 0.0, "confirmed": False}
        if listed:
            rec["listed_h"] = listed
        tr.slots[rec["start"]] = rec
    add(-600, -60, 63.0, "cancelled", 9.0)                    # withdrawn: never ran, not counted
    add(0, 30, 38.5, "done")                                  # old-style record of a slot that ran: 7.4 kW x 0.5 h
    add(30, 60, 35.0, "done")
    add(60, 300, 31.5, "cut_short")                           # capped: 4 h at 7.4 kW is 29.6
    add(300, 330, 3.5, "planned", 0.5)
    s = tr.summary(D0 + 400 * M)
    assert s["planned_kwh"] == 37.0, s["planned_kwh"]        # was 5345 over 14 days
    rows = {r["time"]: r["planned_kwh"] for r in s["recent"]}
    assert rows["19:00–19:30"] == 3.7 and rows["19:30–20:00"] == 3.7 and rows["09:00–18:00"] == 63.0
    assert rows["20:00–00:00"] == 29.6 and rows["00:00–00:30"] == 3.5
