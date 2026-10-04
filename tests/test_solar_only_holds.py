"""4 Oct 2026: a hold that imports nothing at a dear rate becomes self-use.

The forecast said 2 kW of sun, the real figure was 0.4 kW, and the plan's hold (battery 84%) let the house buy at
28.84p. Self-use covers any shortfall from the battery."""
from datetime import datetime, timedelta, timezone

from pe_core.decide import HOLD, SELF_USE
from pe_core.forecast import SLOT, Slot
from pe_core.planner import Params, PlanSlot, _solar_only_holds

T0 = datetime(2026, 10, 4, 8, 30, tzinfo=timezone.utc)
DEAR = 0.2884


def slot(i, price=DEAR):
    return Slot(T0 + i * SLOT, price, 0.15, load_kwh=0.5, solar_kwh=1.0)


def test_dear_hold_with_no_import_becomes_self_use():
    out = [PlanSlot(slot(0), HOLD, "keep the charge for later")]
    assert _solar_only_holds(out, [False], Params()) is True
    assert out[0].action == SELF_USE


def test_cheap_hold_stays():
    out = [PlanSlot(slot(0, 0.0666), HOLD, "cheap import")]
    assert _solar_only_holds(out, [True], Params()) is False
    assert out[0].action == HOLD


def test_hold_that_imports_stays():
    out = [PlanSlot(slot(0), HOLD, "keep the charge for 18:00")]
    out[0].grid_import = 0.4
    assert _solar_only_holds(out, [False], Params()) is False
    assert out[0].action == HOLD


def test_short_data_gap_keeps_the_last_decision():
    from types import SimpleNamespace

    import powerengine as pe

    from pe_core.decide import NONE, Decision
    app = SimpleNamespace(_decision=Decision(HOLD, "plan", "x"), _no_data_since=None)
    gap = Decision(NONE, "no_data", "no reading for import rate")
    bridge = pe.PowerEngine._bridge_data_gap
    assert bridge(app, gap, T0) is app._decision
    assert bridge(app, gap, T0 + timedelta(seconds=120)) is app._decision
    assert bridge(app, gap, T0 + timedelta(seconds=200)) is gap
    ok = Decision(SELF_USE, "plan", "y")
    assert bridge(app, ok, T0 + timedelta(seconds=260)) is ok and app._no_data_since is None
