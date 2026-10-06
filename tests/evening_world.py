"""A small closed loop for engine v2 (the real forecast, value and executor layers), built like 6 Oct 2026's evening.

The car's smart slot opens at 19:12 and runs to 04:00 at 6.66p while the standard rate is 28.84p and export pays 15p, so
buying and selling both pay for hours; a grid event runs 19:30 to 20:30; the morning is dear. The battery is simulated
(18 kWh, 95% each way, 4.8 kW, 12% hard floor) and follows each decision the way the inverter does; the house draws a
flat 0.6 kW and there is no sun. `simulate` steps the engine every `STEP_S` seconds and returns what happened: the
mode over time, the battery level, every mode change, the value results (so the plan's expected cost can be compared)
and the real cash cost of the run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pe_core.engine_v2.engine import EngineV2
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import CHARGE, EVENT, EXPORT, FREE, HOLD, BatteryFacts, Situation, StepInput
from pe_core.forecast import LoadProfile
from pe_core.parsing import Window
from pe_core.readings import Readings

BST = timezone(timedelta(hours=1))
DAY = datetime(2026, 10, 6, tzinfo=BST)
STEP_S = 30
HOUSE_W = 600.0
STD_P, SLOT_P, EXPORT_P, EVENT_P = 28.84, 6.66, 15.0, 100.0
FACTS = BatteryFacts()


def at(h: int, m: int = 0, day: int = 0) -> datetime:
    return DAY.replace(hour=h, minute=m) + timedelta(days=day)


SLOT_START, SLOT_END = at(19, 12), at(4, 0, 1)
EVENT_START, EVENT_END = at(19, 30), at(20, 30)


def rates() -> list[Window]:
    """Half-hour rates for 6 and 7 Oct: the smart slot's half-hours cheap, everything else standard."""
    out = []
    t = DAY
    while t < DAY + timedelta(days=3):
        nxt = t + timedelta(minutes=30)
        cheap = SLOT_START <= t and nxt <= SLOT_END
        out.append(Window(t, nxt, (SLOT_P if cheap else STD_P) / 100))
        t = nxt
    return out


def price_at(t: datetime) -> float:
    return SLOT_P if SLOT_START <= t < SLOT_END else STD_P


def simulate(settings: V2Settings, start: datetime | None = None, end: datetime | None = None, soc0: float = 66.0,
             event: bool = True, on_step=None) -> dict:
    start, end = start or at(19, 0), end or at(4, 30, 1)
    eng = EngineV2(settings)
    profile = LoadProfile(watts={(wk, h): HOUSE_W for wk in (True, False) for h in range(48)}, days=7)
    rate_list = rates()
    dispatch = [Window(SLOT_START, SLOT_END)]
    cap, eta, floor = FACTS.capacity_kwh, FACTS.eta_charge, FACTS.hard_floor_soc / 100 * FACTS.capacity_kwh
    e = soc0 / 100 * cap
    t = start
    mode, power_w = HOLD, 0.0
    log, changes, vrs = [], [], []
    cash = 0.0                                   # pence: import and export, the event's pay on the sold energy
    last_mode = None
    while t <= end:
        dt_h = STEP_S / 3600
        in_event = event and EVENT_START <= t < EVENT_END
        kw = 0.0
        if mode in (CHARGE, FREE):
            kw = -min(FACTS.max_charge_kw, (power_w or 5000.0) / 1000.0, (cap - e) / eta / dt_h)
        elif mode in (EXPORT, EVENT):
            kw = min(FACTS.max_discharge_kw, (power_w or 5000.0) / 1000.0, max(0.0, e - floor) * eta / dt_h)
        elif mode == "self_use":
            kw = min(HOUSE_W / 1000, max(0.0, e - floor) * eta / dt_h)
        batt_w = kw * 1000                       # + discharging, - charging (the card's convention)
        e += (-kw * dt_h * eta) if kw < 0 else (-kw * dt_h / eta)
        e = min(cap, max(0.0, e))
        grid_kw = HOUSE_W / 1000 - kw                            # import (+) / export (-) at the meter
        if grid_kw > 0:
            cash += grid_kw * dt_h * price_at(t)
        else:
            sold = -grid_kw * dt_h
            cash -= sold * EXPORT_P
            if mode == EVENT or in_event:
                cash -= max(0.0, min(sold, max(0.0, kw) * dt_h)) * EVENT_P
        r = Readings(now=t, battery_soc=e / cap * 100, battery_power=batt_w,
                     grid_power=grid_kw * 1000, house_power=HOUSE_W, solar_power=0.0,
                     import_rate=price_at(t) / 100, export_rate=EXPORT_P / 100, rates=rate_list, dispatches=dispatch,
                     ev_plug="Connected", ev_power=0.0,
                     axle_active=in_event, axle_start=EVENT_START if event else None,
                     axle_end=EVENT_END if event else None)
        out = eng.step(StepInput(now=t, readings=r, facts=FACTS, situation=Situation(active=True), tz=BST,
                                 load_profile=profile, overnight=set()))
        d = out.decision
        mode = (d.details or {}).get("mode", HOLD)
        power_w = d.power_w or 0.0
        if mode != last_mode:
            changes.append((t, mode, e / cap * 100))
            last_mode = mode
        if out.revalued and out.value is not None:
            vrs.append(out.value)
        log.append((t, mode, e / cap * 100))
        if on_step:
            on_step(t, out)
        t += timedelta(seconds=STEP_S)
    return {"log": log, "changes": changes, "values": vrs, "cash_p": cash, "engine": eng, "end_level": e / cap * 100}


def reversals(changes: list, min_leg_min: float = 25.0) -> list:
    """Every turn from a charge to a sale (or back), a hold between them allowed and no grid event between, where the
    leg that was turned had run for less than `min_leg_min` minutes (from its start to the next change). Returns
    (time of the turn, from, to, minutes the leg ran)."""
    bad = []
    leg = None                                   # index in `changes` of the last charge or sale
    for i, (t, mode, _lvl) in enumerate(changes):
        if mode == EVENT:
            leg = None
        elif mode in (CHARGE, EXPORT):
            if leg is not None and changes[leg][1] != mode:
                ran = (changes[leg + 1][0] - changes[leg][0]).total_seconds() / 60
                if ran < min_leg_min:
                    bad.append((t, changes[leg][1], mode, round(ran, 1)))
            leg = i
    return bad


def since_last_change(changes: list) -> list:
    """The literal check: every turn between a charge and a sale (a hold between allowed, no event between) with the
    minutes since the change just before it."""
    out = []
    leg = None
    for i, (t, mode, _lvl) in enumerate(changes):
        if mode == EVENT:
            leg = None
        elif mode in (CHARGE, EXPORT):
            if leg is not None and leg != mode and i > 0:
                out.append((t, leg, mode, round((t - changes[i - 1][0]).total_seconds() / 60, 1)))
            leg = mode
    return out
