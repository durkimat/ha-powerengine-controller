import time
from datetime import datetime, timezone

from pe_core.forecast import SLOT, Slot
from pe_core.optimiser import compare, optimise
from pe_core.planner import Params, make_plan

T0 = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)
PEAK, CHEAP = 0.30, 0.07


def day(n=72, load=0.5, solar=0.0):
    return [Slot(T0 + i * SLOT, CHEAP if 14 <= i < 24 or 62 <= i < 72 else PEAK, 0.15, solar_kwh=solar,
                 load_kwh=load) for i in range(n)]


def test_optimiser_is_never_worse_than_the_heuristic():
    for soc in (15.0, 50.0, 95.0):
        for p in (Params(), Params(fill_when_cheap=False), Params(arbitrage=True)):
            plan = make_plan(day(), soc, p, T0)
            c = compare(plan, optimise(day(), soc, p), p)
            assert c["better_by"] >= -0.02, (soc, p, c)       # small rounding tolerance


def test_optimiser_charges_cheap_for_the_evening():
    opt = optimise(day(), 15.0, Params())
    acts = opt["actions"]
    assert "grid_charge" in acts[14:24] and "grid_charge" not in acts[:14]


def test_optimiser_runs_quickly():
    t = time.perf_counter()
    optimise(day(n=96), 50.0, Params(arbitrage=True))
    assert time.perf_counter() - t < 3.0



def test_arbitrage_sells_from_full_before_idling_there():
    # full at 23:00 in a cheap window with a later refill: sell the top now rather than park at 100% and sell later
    slots = []
    for i in range(24):
        s = Slot(T0.replace(hour=23) + i * SLOT, CHEAP if i < 12 else PEAK, 0.15, load_kwh=0.3)
        s.overnight = i < 12
        slots.append(s)
    opt = optimise(slots, 100.0, Params(arbitrage=True))
    assert opt["soc"][0] < 99.0 and opt["soc"][11] >= 99.0, opt["soc"]


def test_arbitrage_stays_in_the_band_and_only_the_final_top_up_goes_to_full():
    from pe_core.optimiser import final_topup
    # 20:00: a cheap smart slot all evening, then the fixed overnight window 23:30-05:30, export 15p
    t0 = T0.replace(hour=19)
    slots = []
    for i in range(24):
        s = Slot(t0 + i * SLOT, CHEAP, 0.15, load_kwh=0.35)
        s.overnight = 7 <= i < 19
        slots.append(s)
    p = Params(arbitrage=True, max_charge_kw=5.0, capacity_kwh=18.0)
    fin = final_topup(slots, p)
    assert fin[18] and fin[17] and not fin[15] and not fin[5]
    opt = optimise(slots, 80.0, p)
    soc = opt["soc"]
    assert max(soc[:16]) <= 90.5, soc                      # evening cycles and most of the night: within the band
    assert soc[18] >= 99.0, soc                            # full when the overnight window ends


def test_overnight_arbitrage_is_one_deeper_cycle_not_many_shallow_ones():
    t0 = T0.replace(hour=19)
    slots = []
    for i in range(24):
        s = Slot(t0 + i * SLOT, CHEAP if i < 19 else PEAK, 0.15, load_kwh=0.35)
        s.overnight = 7 <= i < 19
        slots.append(s)
    p = Params(arbitrage=True, max_charge_kw=5.0, max_discharge_kw=5.0, capacity_kwh=18.0, switch_cost_p=0.5)
    night = optimise(slots, 80.0, p)["actions"][7:19]
    assert sum(1 for x, y in zip(night, night[1:], strict=False) if x != y) <= 3, night


def test_deeper_overnight_selling_can_be_switched_off():
    t0 = T0.replace(hour=19)
    slots = []
    for i in range(24):
        s = Slot(t0 + i * SLOT, CHEAP if i < 19 else PEAK, 0.15, load_kwh=0.35)
        s.overnight = 7 <= i < 19
        slots.append(s)
    p = Params(arbitrage=True, max_charge_kw=5.0, max_discharge_kw=5.0, capacity_kwh=18.0, deep_overnight=False)
    assert min(optimise(slots, 80.0, p)["soc"][7:19]) >= 74.5          # the band's bottom holds overnight too
    p = Params(arbitrage=True, max_charge_kw=5.0, max_discharge_kw=5.0, capacity_kwh=18.0)
    assert min(optimise(slots, 80.0, p)["soc"][7:19]) < 60              # on (default): one deeper sale
