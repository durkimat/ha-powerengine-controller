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
