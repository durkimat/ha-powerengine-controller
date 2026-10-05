import time
from datetime import datetime, timezone

from pe_core.forecast import SLOT, Slot
from pe_core.optimiser import compare, optimise
from pe_core.planner import EXPORT, GRID_CHARGE, Params, make_plan

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


def test_arbitrage_band_is_a_guide_with_give_and_only_the_final_top_up_goes_to_full():
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
    assert max(soc[:16]) <= 95.5, soc                      # evening cycles, most of the night: band plus give
    assert min(soc[:7]) >= 69.5, soc                       # ...and the hard floor under its bottom (evening)
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
    assert min(optimise(slots, 80.0, p)["soc"][7:19]) >= 69.5          # the band (plus its give) holds overnight too
    p = Params(arbitrage=True, max_charge_kw=5.0, max_discharge_kw=5.0, capacity_kwh=18.0)
    assert min(optimise(slots, 80.0, p)["soc"][7:19]) < 60              # on (default): one deeper sale


def test_overnight_switches_cost_more_with_deeper_selling():
    from pe_core.optimiser import CHARGE_K, DISCHARGE_K, HOLD_K, switch_cost
    from pe_core.planner import Params
    p = Params(arbitrage=True, deep_overnight=True, switch_cost_p=0.5, overnight_switch_cost_p=3.0)
    assert switch_cost(CHARGE_K, DISCHARGE_K, p) == 0.005
    assert switch_cost(CHARGE_K, DISCHARGE_K, p, overnight=True) == 0.03
    assert switch_cost(HOLD_K, CHARGE_K, p, overnight=True) == 0.001          # hold <-> charge stays cheap
    off = Params(arbitrage=True, deep_overnight=False, switch_cost_p=0.5, overnight_switch_cost_p=3.0)
    assert switch_cost(CHARGE_K, DISCHARGE_K, off, overnight=True) == 0.005


def test_arbitrage_starts_selling_as_soon_as_it_can_not_after_an_idle_hour():
    # 22:00-06:00 cheap, battery fills to the band's top by 22:30. The sale-and-refill cycles tile the night in
    # threes, so where the spare half-hours go is a tie in cash: they must come first (bank the sale now, leave the
    # slack at the end where a replan can still use it), not sit as an hour of 90% to 90% before the first sale.
    t0 = T0.replace(hour=22)
    slots = [Slot(t0 + i * SLOT, CHEAP if i < 16 else PEAK, 0.15, load_kwh=0.25) for i in range(24)]
    p = Params(arbitrage=True, max_charge_kw=4.8, max_discharge_kw=4.8, capacity_kwh=18.0, switch_cost_p=0.5)
    opt = optimise(slots, 73.0, p, wear=0.02)
    first_sale = opt["actions"].index("export")
    assert first_sale <= 2, (opt["actions"], opt["soc"])
    idle = [i for i in range(first_sale) if abs(opt["soc"][i] - opt["soc"][i - 1 if i else 0]) < 0.05 and i > 1]
    assert not idle, opt["soc"]


def test_a_cycle_uses_whole_half_hours_with_no_charge_then_idle_sliver():
    """The band is a guide with give (4 Oct 2026): a sale of 15 points and a charge that stopped at the band's top
    left a charge slot mostly idle after every sale. With the give, a charge slot in the middle of a cycle runs the
    whole half-hour at full power, and a cycle sells on 15p and refills on 6.99p without idle half-hours."""
    p = Params(arbitrage=True, switch_cost_p=0.0)                 # remote control: a change of action costs no wear
    slots = [Slot(T0 + i * SLOT, CHEAP if i < 26 else PEAK, 0.15, load_kwh=0.3) for i in range(40)]
    opt = optimise(slots, 90.0, p, wear=p.wear_p / 100)
    soc, acts = opt["soc"], opt["actions"]
    prev, rises = 90.0, []
    for a, s in zip(acts[:26], soc[:26], strict=True):
        if a == GRID_CHARGE:
            rises.append(s - prev)
        prev = s
    assert acts[:8].count(EXPORT) >= 3, acts[:8]                  # it keeps cycling
    assert all(r > 10 or r < 0.05 for r in rises[:6]), rises      # no 1-3 point sliver of a charge slot in the cycles
    assert max(soc[:26]) <= 95.5 and min(soc[:26]) >= 69.5, soc   # the give is limited, the floor holds


def test_ram_control_switches_cost_the_configured_amount_not_the_overnight_minimum():
    from pe_core.optimiser import CHARGE_K, DISCHARGE_K, switch_cost
    ram = Params(arbitrage=True, deep_overnight=True, switch_cost_p=0.5, overnight_switch_cost_p=3.0, ram_control=True)
    assert switch_cost(CHARGE_K, DISCHARGE_K, ram, overnight=True) == 0.005          # no EEPROM write: 0.5p
    timed = Params(arbitrage=True, deep_overnight=True, switch_cost_p=0.5, overnight_switch_cost_p=3.0)
    assert switch_cost(CHARGE_K, DISCHARGE_K, timed, overnight=True) == 0.03         # timed windows: still 3p


def test_the_tie_break_biases_do_not_grow_with_how_far_off_the_night_is():
    from pe_core.optimiser import BIAS_CAP, bias_delay
    far = [Slot(T0 + i * SLOT, PEAK, 0.15, overnight=30 <= i < 44) for i in range(60)]
    d = bias_delay(far)
    assert d[30:44] == [min(i, BIAS_CAP) for i in range(14)]     # counted from the window's start, not from now
    assert max(d) == BIAS_CAP and d[59] == BIAS_CAP and d[5] == 5


def test_a_night_far_ahead_still_sells_and_refills_when_the_margin_pays():
    # 5 Oct 2026: the biases were counted from now, so a refill 24 half-hours off cost over 1p/kWh in tie-breakers
    # and a cycle worth about 4p/kWh was never planned. The night below is 30 half-hours away, as that one was.
    p = Params(arbitrage=True, max_charge_kw=4.8, max_discharge_kw=4.95, capacity_kwh=18.0, efficiency=0.889,
               switch_cost_p=0.5, ram_control=True)
    slots = [Slot(T0 + i * SLOT, CHEAP if 30 <= i < 44 else PEAK, 0.15, load_kwh=0.3, overnight=30 <= i < 44)
             for i in range(60)]
    opt = optimise(slots, 90.0, p, wear=p.wear_p / 100)
    assert opt["actions"][30:44].count(EXPORT) >= 2, opt["actions"][30:44]
    assert opt["soc"][43] >= 99                                 # and the battery is full when the cheap window closes
