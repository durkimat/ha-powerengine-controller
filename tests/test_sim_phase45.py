"""Simulator phase 4 (equipment) and phase 5 (PowerEngine's planner against the best achievable)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from test_simulator import DAYS, day_records, fake_api

from pe_core.equipment import EquipmentSettings, adjust_slots, params_for
from pe_core.forecast import Slot
from pe_core.planner import Params
from pe_core.simjob import SimContext, SimStore, run

LON = ZoneInfo("Europe/London")
UTC = timezone.utc
T0 = datetime(2026, 9, 20, tzinfo=UTC)


def _slots():
    return [
        Slot(
            T0 + timedelta(minutes=30 * i),
            0.07 if i < 10 else 0.30,
            0.15,
            solar_kwh=1.0 if 20 <= i < 30 else 0,
            load_kwh=0.4,
        )
        for i in range(48)
    ]


def test_equipment_settings_and_kinds():
    e = EquipmentSettings.from_dict({"battery_enabled": True, "battery_kwh": "27", "battery_kw": 6, "junk": 1})
    assert e.battery_kwh == 27 and e.kinds() == ["battery"] and not e.problems()
    assert EquipmentSettings(solar_enabled=True).problems()
    both = EquipmentSettings(battery_enabled=True, battery_kwh=27, battery_kw=6, ev2_enabled=True, ev2_miles_year=7300)
    assert both.kinds() == ["battery", "ev2", "all"]
    assert params_for("battery", both, Params()).capacity_kwh == 27
    assert params_for("ev2", both, Params()).capacity_kwh == Params().capacity_kwh


def test_solar_scaled_and_second_car_in_cheapest_hours():
    e = EquipmentSettings(
        solar_enabled=True,
        solar_current_kwp=6,
        solar_extra_kwp=3,
        ev2_enabled=True,
        ev2_miles_year=7300,
        ev2_kwh_per_mile=0.25,
        ev2_charger_kw=7.4,
    )
    slots = _slots()
    info = adjust_slots("all", e, slots)
    assert sum(s.solar_kwh for s in slots) == pytest.approx(15.0)
    assert info["ev2_kwh"] == pytest.approx(5.0)
    assert sum(s.load_kwh for s in slots[:10]) == pytest.approx(4.0 + 5.0)  # all in the cheap half-hours


def test_run_adds_planner_and_equipment(tmp_path):
    store = SimStore(str(tmp_path))
    eq = EquipmentSettings(battery_enabled=True, battery_kwh=20, battery_kw=5, battery_cost=4000)
    ctx = SimContext(equipment=eq)
    now = datetime(2026, 9, 23, 1, 30, tzinfo=UTC)
    list(
        run(
            store,
            DAYS,
            day_records,
            Params(capacity_kwh=10.0),
            LON,
            now,
            7.4,
            fetch=fake_api,
            log=lambda m: None,
            ctx=ctx,
        )
    )
    s = store.summary
    rows = s["planner"]["rows"]
    assert rows[0]["id"] == "current" and "actual_month" in rows[0]
    assert all(r["planner_month"] >= r["best_month"] - 0.01 for r in rows)  # the optimiser is the floor
    eqr = s["equipment"]["rows"]
    assert {r["kind"] for r in eqr} == {"battery"} and eqr[0]["payback_years"] is None  # < a year of data
    assert all("|" not in r["id"] for r in s["windows"]["30"]["ranking"])
