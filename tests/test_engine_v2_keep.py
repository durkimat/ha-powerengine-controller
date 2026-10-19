"""The forecast caution (0.9.158): a sale while energy is dear keeps what the house may need until energy is certainly
cheap again; a smart slot that may not come does not count as cheap."""

from datetime import datetime, timedelta, timezone

import pytest

from pe_core.engine_v2 import value as V
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import CHARGE, EXPORT, HOLD, SELF_USE, BatteryFacts, Forecast, Limits, Segment, Spread

UTC = timezone.utc
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
FACTS = BatteryFacts()


def t(i):
    return T0 + timedelta(minutes=30 * i)


def seg(i, imp, load, **kw):
    return Segment(start=t(i), end=t(i + 1), import_p=imp, export_p=15.0, solar_kwh=Spread(0, 0, 0, (0.25, 0.5, 0.25)),
                   load_kwh=Spread(load, load, load, (0.25, 0.5, 0.25)), **kw)


def limits(sg):
    return Limits(allowed=frozenset({SELF_USE, HOLD, CHARGE, EXPORT}), forced=None, floor_soc=12.0, ceiling_soc=100.0)


def day():
    """A dear day with an uncertain smart slot early on and the cheap night at 10:00."""
    return ([seg(i, 28.84, 0.8) for i in range(6)]
            + [seg(6 + i, 28.84, 0.8, slot_prob=0.9, slot_import_p=6.66) for i in range(2)]
            + [seg(8 + i, 28.84, 0.8) for i in range(12)] + [seg(20 + i, 6.66, 0.3) for i in range(12)])


def solve(caution):
    s = V2Settings(arbitrage=True, range_firmness="off", switch_cost_p=0.0, forecast_caution=caution)
    return V.solve(Forecast(made_at=T0, segments=tuple(day())), 90.0, FACTS, s, limits, T0, "test", UTC)


def level_at(vr, i):
    """The expected level at the start of half-hour i (the path is in 15-minute steps from T0)."""
    return vr.path["mid"][i * 2]


@pytest.fixture(autouse=True)
def _refill_value(monkeypatch):
    monkeypatch.setattr(V, "TERMINAL_FIXED_P", None)


def test_the_keep_counts_the_house_until_energy_is_certainly_cheap():
    segs = day()
    need = V._keep_kwh(segs[8:], 1.0, 0.95, "mid")
    assert need == pytest.approx(12 * 0.8 / 0.95)                   # twelve dear half-hours to the cheap night
    assert V._keep_kwh(segs, 1.0, 0.95, "mid") == pytest.approx(20 * 0.8 / 0.95)   # the 90% slot is not counted
    certain = [seg(0, 28.84, 0.8), seg(1, 28.84, 0.8, slot_prob=1.0, slot_import_p=6.66)]
    assert V._keep_kwh(certain, 1.0, 0.95, "mid") == pytest.approx(0.8 / 0.95)


def test_optimistic_sells_down_to_the_floor_and_the_house_buys_at_the_dear_rate():
    vr = solve("optimistic")
    assert level_at(vr, 16) < 15                                        # at the floor by 08:00, two dear hours left


def test_balanced_sells_only_what_the_house_will_not_need_and_reaches_the_night_at_the_floor():
    vr = solve("balanced")
    assert level_at(vr, 10) > 55                                        # what the house needs is still there
    assert level_at(vr, 20) == pytest.approx(13, abs=2)                 # and used up just as the night begins
    after = [it for it in vr.timeline if it.end > t(10) and it.start < t(20)]
    assert after and all(it.mode == SELF_USE for it in after)          # the house runs on it, not on the grid
