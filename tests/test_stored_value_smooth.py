"""The value of a stored kWh is read non-increasing in level (`value.MONOTONE_LINES`), from the floor up.

The plan's value curve is not smooth (the sawtooth is in the arithmetic, worst with `late_events` off), and read raw the
live value rose and fell while the battery drained steadily. Reading it non-increasing keeps it moving one way."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from pe_core.engine_v2 import value
from pe_core.engine_v2.publish import _curve


@pytest.fixture(autouse=True)
def _plain(monkeypatch):
    monkeypatch.setattr(value, "MONOTONE_LINES", True)
    monkeypatch.setattr(value, "MONOTONE_MEDIAN", 1)  # the tests that need the median set it


def test_monotone_never_rises_with_level_and_keeps_the_mean():
    row = [30.0, 20.0, 25.0, 10.0, 12.0, 11.0, 4.0, 0.0]
    out = value.monotone(row)
    assert all(a >= b for a, b in zip(out, out[1:], strict=False))
    assert sum(out) == pytest.approx(sum(row))
    assert value.monotone([5.0, 4.0, 3.0]) == [5.0, 4.0, 3.0]            # an ordered row is untouched


def test_levels_below_the_floor_are_left_alone_and_never_pooled_in():
    row = [0.0] * 6 + [28.0, 27.0, 28.0, 20.0, 10.0]                      # 6 unreachable levels, then the real curve
    out = value.read_row(row, floor_pct=60.0)                             # 11 entries: the floor is entry 6
    assert out[:6] == [0.0] * 6
    assert out[6] > 20.0 and all(a >= b for a, b in zip(out[6:], out[7:], strict=False))


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(value, "MONOTONE_LINES", False)
    row = [1.0, 5.0, 2.0]
    assert value.read_row(row, 0.0) is row


def test_a_spike_is_dropped_by_the_median_before_pooling(monkeypatch):
    monkeypatch.setattr(value, "MONOTONE_MEDIAN", 5)
    row = [20.0] * 5 + [900.0] + [20.0] * 5                               # one wild level in a flat stretch
    out = value.read_row(row, 0.0)
    assert max(out) == pytest.approx(20.0)


def _vr(rows, floor):
    start = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    segs = [SimpleNamespace(start=start + timedelta(minutes=30 * i), end=start + timedelta(minutes=30 * (i + 1)))
            for i in range(len(rows) - 1)]
    return SimpleNamespace(lam=rows, forecast=SimpleNamespace(segments=segs), step_kwh=0.18,
                           limits=tuple(SimpleNamespace(floor_soc=floor) for _ in segs), made_at=start), start


def test_the_live_value_moves_one_way_while_the_battery_drains():
    jagged = [30.0 - 0.15 * i + (4.0 if i % 7 == 3 else 0.0) for i in range(101)]        # a sloping curve with teeth
    vr, start = _vr([jagged, jagged, jagged], floor=12.0)
    seen = [value.value_at(vr, start + timedelta(minutes=5), soc / 2) for soc in range(180, 30, -1)]  # 90% to 15%
    assert all(b >= a - 1e-9 for a, b in zip(seen, seen[1:], strict=False))      # as the level falls it never falls


def test_the_published_curve_is_non_increasing_in_level():
    jagged = [50.0 - 0.3 * i + (6.0 if i % 5 == 2 else 0.0) for i in range(101)]
    vr, _ = _vr([jagged, jagged, jagged], floor=12.0)
    _state, attrs = _curve(vr, timezone.utc)
    floor_idx = next(i for i, p in enumerate(attrs["levels"]) if p >= 12)
    for row in attrs["values"]:
        part = row[floor_idx:]
        assert all(a >= b - 1e-9 for a, b in zip(part, part[1:], strict=False))
