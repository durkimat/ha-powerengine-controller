"""The manual override (pe_core/override.py): periods, expiry, storage and what the controller does under it."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pe_core import override as ov
from pe_core.config import parse_config
from pe_core.decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE, decide
from pe_core.readings import Readings

NOW = datetime(2026, 10, 4, 9, 40, tzinfo=timezone.utc)
CFG = parse_config({"inputs": {"battery_capacity": {"value": 18}, "battery_max_discharge_power": {"value": 4800}}})


def r(soc=84, **kw):
    base = dict(now=NOW, battery_soc=soc, import_rate=0.2884, export_rate=0.15, house_power=800, solar_power=400,
                ev_power=0, ev_plug="EV Disconnected")
    base.update(kw)
    return Readings(**base)


def mk(mode, **period):
    got, why = ov.parse({"mode": mode, **period}, NOW, window_end=NOW.replace(hour=11, minute=30))
    assert got is not None, why
    return got


def test_slots_count_the_half_hour_running():
    assert mk(HOLD, slots=1).until == datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    assert mk(HOLD, slots=3).until == datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc)


def test_window_until_and_permanent():
    assert mk(EXPORT, window=True).until.hour == 11
    assert mk(EXPORT, until="2026-10-04T12:00:00+00:00").until.hour == 12
    assert mk(GRID_CHARGE, permanent=True).until is None


@pytest.mark.parametrize("data", [
    {"mode": "nope", "slots": 1}, {"mode": HOLD}, {"mode": HOLD, "slots": 0}, {"mode": HOLD, "slots": 25},
    {"mode": HOLD, "until": "2026-10-04T12:10:00+00:00"}, {"mode": HOLD, "until": "2026-10-04T09:30:00+00:00"},
    {"mode": HOLD, "until": "2026-10-05T12:00:00+00:00"}, {"mode": HOLD, "until": "2026-10-04T12:00:00"},
])
def test_refused(data):
    got, why = ov.parse(data, NOW)
    assert got is None and why


def test_expiry_and_storage(tmp_path):
    path = str(tmp_path / "override.json")
    o = mk(HOLD, slots=2)
    ov.save(path, o)
    assert ov.load(path, NOW) == o
    assert ov.load(path, NOW + timedelta(hours=2)) is None          # expired
    ov.save(path, None)
    assert ov.load(path, NOW) is None
    ov.save(path, mk(HOLD, permanent=True))
    assert ov.load(path, NOW + timedelta(days=30)).until is None


def test_modes_in_decide():
    assert decide(r(), CFG, override=mk(HOLD, slots=2)).action == HOLD
    assert decide(r(), CFG, override=mk(SELF_USE, slots=2)).action == SELF_USE
    d = decide(r(), CFG, override=mk(EXPORT, slots=2))
    assert d.action == EXPORT and d.rule == "override"
    d = decide(r(), CFG, override=mk(GRID_CHARGE, slots=2))
    assert d.action == GRID_CHARGE and d.target_soc == CFG.safety["grid_charge_target_soc"]


def test_reserve_stops_export_and_self_use():
    floor = CFG.safety["min_reserve_soc"]
    assert decide(r(floor), CFG, override=mk(EXPORT, slots=2)).action == HOLD
    assert decide(r(floor), CFG, override=mk(SELF_USE, slots=2)).action == HOLD
    assert decide(r(floor), CFG, override=mk(GRID_CHARGE, slots=2)).action == GRID_CHARGE


def test_charge_holds_at_target_without_flapping():
    target = CFG.safety["grid_charge_target_soc"]
    o = mk(GRID_CHARGE, slots=2)
    held = decide(r(target), CFG, override=o)
    assert held.action == HOLD
    assert decide(r(target - 1), CFG, held, override=o).action == HOLD       # reads a point low while charging
    assert decide(r(target - 5), CFG, held, override=o).action == GRID_CHARGE


def test_a_grid_event_in_progress_wins():
    cfg = parse_config({"features": {"axle": True}})
    now = NOW
    rd = r(axle_active=True, axle_start=now - timedelta(minutes=5), axle_end=now + timedelta(hours=1))
    d = decide(rd, cfg, override=mk(HOLD, slots=2))
    assert d.action == FORCE_DISCHARGE and d.rule == "axle_active"


def test_describe():
    tz = ZoneInfo("Europe/London")
    assert ov.describe(mk(EXPORT, slots=1), tz) == "Export until 11:00"
    assert ov.describe(mk(HOLD, permanent=True)) == "Hold until cancelled"
