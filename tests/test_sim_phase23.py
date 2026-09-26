"""Simulator phase 2 (history from HA statistics) and phase 3 (heat pump)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from test_simulator import DAYS, day_records, fake_api

from pe_core.heatpump import HeatPumpSettings, cop, day_heat, degree_hours, hlc_kw_per_k, schedule
from pe_core.planner import Params
from pe_core.simhistory import History, months_wanted, parse_upload
from pe_core.simjob import SimContext, SimStore, run
from pe_core.weather import Weather

LON = ZoneInfo("Europe/London")
UTC = timezone.utc


def test_months_wanted():
    assert months_wanted(date(2026, 9, 26), date(2026, 9, 11))[:2] == ["2025-09", "2025-10"]
    assert months_wanted(date(2026, 9, 26), date(2026, 9, 11))[-1] == "2026-09"
    assert len(months_wanted(date(2026, 9, 26), None)) == 13


def test_parse_upload_ms_and_iso_and_sums_plants():
    stats = {
        "sensor.a": [{"start": 1758326400000, "change": 1.5}, {"start": "2025-09-20T01:00:00+00:00", "change": 2}],
        "sensor.p1": [{"start": 1758326400000, "change": 0.5}],
        "sensor.p2": [{"start": 1758326400000, "change": 0.25}],
    }
    out = parse_upload(stats, {"house": ["sensor.a"], "solar": ["sensor.p1", "sensor.p2"]})
    assert out["house"] == {"2025-09-20T00": 1.5, "2025-09-20T01": 2.0}
    assert out["solar"] == {"2025-09-20T00": 0.75}


def _history(tmp_path, first: date, days: int, car_hour=2):
    hist = History(str(tmp_path / "history"))
    hours = {r: {} for r in ("house", "car", "solar", "grid_import", "grid_export")}
    t = datetime.combine(first, datetime.min.time(), tzinfo=UTC) - timedelta(days=1)
    for _ in range((days + 2) * 24):
        k = t.strftime("%Y-%m-%dT%H")
        hours["house"][k] = 0.8 + (7.0 if t.hour == car_hour else 0.0)
        hours["car"][k] = 7.0 if t.hour == car_hour else 0.0
        hours["solar"][k] = 1.0 if 9 <= t.hour < 15 else 0.0
        hours["grid_import"][k] = 0.8
        hours["grid_export"][k] = 0.0
        t += timedelta(hours=1)
    for m in sorted({k[:7] for k in hours["house"]}):
        hist.save_month(m, {r: {k: v for k, v in h.items() if k[:7] == m} for r, h in hours.items()}, datetime.now(UTC))
    return hist


def test_history_day_records_net_of_car(tmp_path):
    hist = _history(tmp_path, date(2026, 9, 1), 3)
    recs = hist.day_records(date(2026, 9, 2), LON, True)
    assert len(recs) == 48 and all(r["seconds"] == 1800 for r in recs)
    assert min(r["house"] for r in recs) == pytest.approx(0.4)
    assert sum(r["car"] for r in recs) == pytest.approx(7.0)


def test_heat_model():
    s = HeatPumpSettings(enabled=True, gas_kwh_year=12000, boiler_efficiency=85, hot_water_kwh_day=6)
    year = [5.0] * (24 * 365)  # a steady 5 C year
    hlc = hlc_kw_per_k(s, year)
    assert hlc == pytest.approx((12000 * 0.85 - 6 * 365) / degree_hours(year))
    assert cop(-3, s) == pytest.approx(2.5) and cop(12, s) == pytest.approx(4.5) and cop(5, s, True) < cop(5, s)
    assert hlc_kw_per_k(HeatPumpSettings(heat_loss_kw=6.0), []) == pytest.approx(0.25)
    assert HeatPumpSettings(enabled=True).problems()


def test_schedule_uses_cheap_hours_and_preheats():
    s = HeatPumpSettings(enabled=True, preheat_h=2, max_kw=10, hot_water_kwh_day=4)
    prices = [0.07] * 10 + [0.30] * 38
    temps = [5.0] * 48
    space, hw = day_heat([5.0] * 24, 0.1, s)
    elec, heat = schedule(prices, temps, space, hw, s)
    assert heat == pytest.approx(sum(space) + 4)
    assert sum(elec[:10]) > sum(elec[10:14])  # hot water + pre-heat land in the cheap hours
    assert all(e >= 0 for e in elec)


def test_weather_fill_and_cache(tmp_path):
    calls = []

    def get(url):
        calls.append(url)
        start = datetime(2026, 9, 1, tzinfo=UTC)
        times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(24 * 30)]
        return {"hourly": {"time": times, "temperature_2m": [10.0] * len(times)}}

    w = Weather(str(tmp_path / "w.json"), 51.75, -0.34)
    w.fill(date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 26), get)
    assert len(calls) == 1 and "archive" in calls[0]
    assert w.at(datetime(2026, 9, 5, 12, tzinfo=UTC)) == 10.0
    w.save()
    again = Weather(str(tmp_path / "w.json"), 51.75, -0.34)
    assert again.fill(date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 26), get) == 0


def test_run_with_history_and_heat_pump(tmp_path):
    store = SimStore(str(tmp_path / "sim"))
    hist = _history(tmp_path, date(2026, 9, 17), 3)

    def weather_get(url):
        start = datetime(2025, 9, 1, tzinfo=UTC)
        times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(24 * 400)]
        return {"hourly": {"time": times, "temperature_2m": [8.0] * len(times)}}

    hp = HeatPumpSettings(enabled=True, gas_kwh_year=10000, gas_price_p=6.0, install_cost=8000)
    ctx = SimContext(
        current={"supplier": "octopus", "product": "GO-VAR-22-10-14", "tariff": "E-1R-GO-VAR-22-10-14-A"},
        export_p=0.15,
        history=hist,
        hp=hp,
        weather=Weather(str(tmp_path / "w.json"), 51.75, -0.34),
        weather_get=weather_get,
    )
    now = datetime(2026, 9, 23, 1, 30, tzinfo=UTC)
    steps = list(
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
    assert steps[-1]["done"]
    s = store.summary
    assert s["imported_days"] >= 3 and s["windows"]["30"]["days"] == s["imported_days"] + 3
    assert s["windows"]["30"]["actual_estimated"]
    h = s["heat_pump"]["rows"]
    assert h and h[0]["hp_kwh"] > 0 and h[0]["scop"] and h[0]["gas_month"] > 0
    assert "hp|current" in store.results


def test_parse_upload_compact_pairs():
    out = parse_upload({"sensor.a": [[1758326400000, 1.25]]}, {"house": ["sensor.a"]})
    assert out["house"] == {"2025-09-20T00": 1.25}
