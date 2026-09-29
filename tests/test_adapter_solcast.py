"""The Solcast adapter: points, day totals, raw reading, the slot builder taking raw dicts or points, protocol and
registry."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fixtures import BST, CONFIG, NOW, STATES, S, get_state

from pe_core.adapters import ForecastAdapter, ForecastPoint
from pe_core.adapters import get as get_adapter
from pe_core.adapters import names as adapter_names
from pe_core.adapters.solcast import ROLES, SolcastForecast
from pe_core.forecast import build_slots
from pe_core.readings import Readings, forecast_kwh, read

HERE = os.path.dirname(__file__)
ROLE_ENTITY = {"solar_forecast_today": "sensor.fc_today", "solar_forecast_tomorrow": "sensor.fc_tomorrow",
               "solar_forecast_day3": "sensor.fc_day3"}


def item(t, kw, **more):
    return {"period_start": t, "pv_estimate": kw, **more}


def real_forecast():
    with open(os.path.join(HERE, "replay", "day_2026_09_27.json"), encoding="utf-8") as fh:
        return json.load(fh)["solar_forecast"]


def old_forecast_kwh(items):
    """The pre-adapter implementation, verbatim."""
    from pe_core.readings import _num
    if not isinstance(items, list) or not items:
        return None
    return round(sum((_num(i.get("pv_estimate")) or 0.0) * 0.5 for i in items if isinstance(i, dict)), 3)


def old_solar_by_slot(solar):
    """The pre-adapter slot-map code, verbatim."""
    from pe_core.forecast import parse_time, slot_start
    out = {}
    for it in solar or []:
        t = parse_time(it.get("period_start"))
        try:
            kw = float(it.get("pv_estimate") or 0.0)
        except (TypeError, ValueError):
            continue
        if t:
            out[slot_start(t)] = kw * 0.5
    return out


def test_points_without_bands():
    pts = SolcastForecast().points([item("2026-09-22T12:00:00+01:00", 2.0)])
    assert pts == [ForecastPoint(datetime(2026, 9, 22, 12, 0, tzinfo=BST), 1.0, None, None)]


def test_points_with_bands():
    pts = SolcastForecast().points([item("2026-09-22T12:00:00+01:00", 2.0, pv_estimate10=1.0, pv_estimate90=3.0)])
    assert (pts[0].kwh, pts[0].low_kwh, pts[0].high_kwh) == (1.0, 0.5, 1.5)
    odd = SolcastForecast().points([item("2026-09-22T12:00:00+01:00", 2.0, pv_estimate10="x", pv_estimate90=None)])
    assert odd[0].kwh == 1.0 and odd[0].low_kwh is None and odd[0].high_kwh is None


def test_points_skip_bad_items_and_keep_order():
    items = [item("2026-09-22T13:00:00+01:00", 1.0), item("bad", 5.0), {"pv_estimate": 3.0},
             item("2026-09-22T12:00:00+01:00", "x"), "junk", None, item("2026-09-22T12:00:00+01:00", None),
             item("2026-09-22T12:30:00+01:00", 4.0)]
    pts = SolcastForecast().points(items)
    assert [(p.start.astimezone(BST).strftime("%H:%M"), p.kwh) for p in pts] == [("13:00", 0.5), ("12:00", 0.0),
                                                                                  ("12:30", 2.0)]
    assert SolcastForecast().points(None) == [] and SolcastForecast().points([]) == []


def test_day_kwh_matches_the_old_forecast_kwh():
    a = SolcastForecast()
    assert a.day_kwh([{"pv_estimate": 2.0}] * 4) == 4.0
    odd = [{"pv_estimate": "n/a"}, "junk", {"pv_estimate": float("nan")}, {"pv_estimate": 1.2345}]
    for case in (None, [], "x", {}, odd, [{"pv_estimate": 0.3333333}] * 3):
        assert a.day_kwh(case) == old_forecast_kwh(case) and forecast_kwh(case) == old_forecast_kwh(case)


def test_day_kwh_on_the_replay_fixtures_real_forecast():
    fc = real_forecast()
    assert len(fc) == 240
    days = sorted({it["period_start"][:10] for it in fc})
    for d in days:
        part = [it for it in fc if it["period_start"][:10] == d]
        assert SolcastForecast().day_kwh(part) == old_forecast_kwh(part)
    assert SolcastForecast().day_kwh(fc) == old_forecast_kwh(fc) > 0


def fixture_rates():
    from pe_core.readings import parse_windows
    with open(os.path.join(HERE, "replay", "day_2026_09_27.json"), encoding="utf-8") as fh:
        return parse_windows(json.load(fh)["rates"])


def slots_of(solar, now=datetime(2026, 9, 26, 12, 10, tzinfo=BST)):
    r = Readings(now=now, import_rate=0.2, export_rate=0.15, rates=fixture_rates())
    return build_slots(r, solar, None, BST)


def test_builder_gives_identical_slots_for_raw_dicts_and_points():
    fc = real_forecast()
    raw = slots_of(fc)
    pts = slots_of(SolcastForecast().points(fc))
    assert raw == pts
    assert len(raw) >= 36 and sum(s.solar_kwh for s in raw) > 0          # the forecast really lands in the slots


def test_builder_slot_map_matches_the_old_code_including_last_one_wins():
    from pe_core.forecast import slot_start
    items = [item("2026-09-22T12:10:00+01:00", 2.0), item("2026-09-22T12:20:00+01:00", 4.0),
             item("bad", 1.0), item("2026-09-22T12:30:00+01:00", "x"), item("2026-09-22T13:00:00+01:00", 0.6)]
    old = old_solar_by_slot(items)
    new = {}
    for p in SolcastForecast().points(items):
        new[slot_start(p.start)] = p.kwh
    assert new == old and old[slot_start(datetime(2026, 9, 22, 12, 0, tzinfo=BST))] == 2.0


def test_builder_uses_solar_from_points():
    start = datetime(2026, 9, 26, 13, 0, tzinfo=BST)
    slots = slots_of([ForecastPoint(start + timedelta(minutes=5), 0.75)])
    assert next(s for s in slots if s.start == start).solar_kwh == 0.75
    assert sum(s.solar_kwh for s in slots) == 0.75


class FakeHA:
    def __init__(self, states):
        self.states = states

    def get_state(self, entity_id=None, attribute=None):
        s = self.states.get(entity_id)
        return (s or {}).get("attributes", {}).get(attribute) if attribute else s

    def call_service(self, service, **data):
        pass


def test_read_joins_raw_items_in_order_and_skips_unmapped():
    states = {"sensor.fc_today": S("1", detailedForecast=[item("a", 1)]),
              "sensor.fc_tomorrow": S("1", detailedForecast=[item("b", 2)]), "sensor.fc_day3": S("1")}
    ha = FakeHA(states)
    items = SolcastForecast().read(lambda e, a: ha.get_state(e, attribute=a), [ROLE_ENTITY[r] for r in ROLES])
    assert [i["period_start"] for i in items] == ["a", "b"]
    assert SolcastForecast().read(lambda e, a: None, [None, "sensor.x"]) == []
    assert SolcastForecast().read(lambda e, a: [{"n": 1}], []) == []


def test_half_hourly_protocol_wrapper():
    states = {"sensor.fc_today": S("1", detailedForecast=[item("2026-09-22T13:00:00+01:00", 2.0),
                                                          item("2026-09-22T12:00:00+01:00", 1.0)]),
              "sensor.fc_tomorrow": S("1", detailedForecast=[item("2026-09-23T12:00:00+01:00", 3.0)])}
    a = SolcastForecast(ROLE_ENTITY.get, BST)
    pts = a.half_hourly(FakeHA(states), date(2026, 9, 22))
    assert [p.kwh for p in pts] == [0.5, 1.0] and pts[0].start < pts[1].start
    assert [p.kwh for p in a.half_hourly(FakeHA(states), date(2026, 9, 23))] == [1.5]
    with pytest.raises(RuntimeError):
        SolcastForecast().half_hourly(FakeHA(states), date(2026, 9, 22))


def test_readings_use_the_adapter_and_accept_another():
    r = read(CONFIG, get_state({**STATES, "sensor.fc_today": S("1", detailedForecast=[item("x", 2.0)] * 2)}), NOW)
    assert r.forecast_today_kwh == 2.0

    class Other(SolcastForecast):
        attribute = "other"
    r2 = read(CONFIG, get_state({**STATES, "sensor.fc_today": S("1", detailedForecast=[item("x", 2.0)],
                                                                other=[item("x", 8.0)])}), NOW, forecast=Other())
    assert r2.forecast_today_kwh == 4.0


def test_protocol_and_registry():
    a = get_adapter("forecast", "solcast")(ROLE_ENTITY.get)
    assert isinstance(a, SolcastForecast) and isinstance(a, ForecastAdapter)
    assert a.name == "solcast" and a.attribute == "detailedForecast" and "solcast" in adapter_names("forecast")
    assert timezone.utc == a.tz
