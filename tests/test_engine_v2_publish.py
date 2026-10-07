"""Engine v2: the published sensors, the card's contract (docs/plans/engine-v2-build.md)."""

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from test_engine_v2_observe import T0, forecast, inp, segment, value_result

from pe_core.engine_v2 import engine as engine_mod
from pe_core.engine_v2 import publish
from pe_core.engine_v2.engine import EngineV2
from pe_core.engine_v2.settings import SETTINGS, V2Settings
from pe_core.engine_v2.types import CHARGE, EXPORT, HOLD, SELF_USE, Lines, TimelineItem

LOCAL = timezone(timedelta(hours=1))
LIMIT = 15_000


def big_value_result(now, reason_len=220, items=24):
    """A realistic 48 h result: 96 half-hour segments, 181 levels, 24 timeline items, a path every 15 minutes."""
    segs = []
    for i in range(96):
        t = now + timedelta(minutes=30 * i)
        night = (t.hour >= 23 or t.hour < 5)
        segs.append(segment(t, import_p=7.0 if night else 30.28, export_p=15.0, solar=0.5 if 8 <= t.hour < 16 else 0.0,
                            load=0.3, overnight=night, price_estimated=i > 60,
                            slot_prob=0.8 if i in (3, 4) else None, slot_import_p=6.99 if i in (3, 4) else None))
    lam = tuple(tuple(round(30 - 0.12 * j + (k % 7) * 0.31, 3) for j in range(181)) for k in range(96))
    modes = [CHARGE, SELF_USE, HOLD, EXPORT]
    tl = [TimelineItem(modes[i % 4], now + timedelta(hours=2 * i), now + timedelta(hours=2 * i + 2), 30.0 + i,
                       40.0 + i, "until 88%", "R" * reason_len) for i in range(items)]
    path = {"start": now.isoformat(), "step_min": 15, "mid": [50.123456 + i * 0.1 for i in range(192)],
            "low": [45.123456 + i * 0.1 for i in range(192)], "high": [55.123456 + i * 0.1 for i in range(192)]}
    return value_result(now, forecast(now, segs), path=path, timeline=tl, lam=lam, step_kwh=0.1,
                        because="New prices published",
                        cost_expected_p=123.4, cost_selfuse_p=234.5, comfort_given_up_p=6.0, calc_s=1.83)


class BigStubForecast:
    def build(self, inp_, settings, learned=None):
        return forecast(inp_.now, [segment(inp_.now)])


class BigStubValue:
    def __init__(self, reason_len=220):
        self.reason_len = reason_len

    def solve(self, fc, start_soc, facts, settings, limits_for, now, because, tz=None):
        return _with(big_value_result(now, self.reason_len), because=because)

    def lines(self, vr, t, soc, import_p, export_p, facts, settings):
        return Lines(value_p=30.0, buy_line_p=import_p / 0.95, sell_line_p=export_p * 0.95, use_line_p=import_p * 0.95,
                     store_sun_line_p=export_p / 0.95, import_p=import_p, export_p=export_p, charge_target_soc=88.0,
                     sell_floor_soc=None)


def _with(vr, **kw):
    from dataclasses import replace
    return replace(vr, **kw)


@pytest.fixture
def made(monkeypatch):
    monkeypatch.setattr(engine_mod, "forecast", BigStubForecast())
    monkeypatch.setattr(engine_mod, "value", BigStubValue())
    eng = EngineV2(V2Settings())
    out = None
    for s in range(0, 400, 10):
        out = eng.step(inp(T0 + timedelta(seconds=s), import_rate=0.07, battery_soc=50.0,
                           ev_plug="Charging" if s > 200 else "Connected", ev_power=7000.0 if s > 200 else 0.0))
    for i in range(40):                                    # a busy day for the triggers sensor
        event = engine_mod.Event(T0, "price", "x" * 90)
        eng.triggers.note(T0 + timedelta(seconds=400 + i), (event,), False, False, None)
    return eng, out


def size(attrs):
    return len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode())


def test_the_keys_and_the_engine_state(made):
    eng, out = made
    states = publish.entity_states(out, eng, eng.s, LOCAL)
    assert set(states) == {"state_engine", "v2_mode", "v2_value", "v2_timeline", "v2_value_curve", "v2_triggers",
                           "diag_v2"}
    assert states["state_engine"] == ("v2", {"v2_available": True})
    for state, attrs in states.values():
        assert isinstance(state, str) and isinstance(attrs, dict)


def test_every_attribute_set_is_json_and_well_under_the_limit(made):
    eng, out = made
    for key, (_state, attrs) in publish.entity_states(out, eng, eng.s, LOCAL).items():
        text = json.dumps(attrs, allow_nan=False)
        assert size(attrs) < LIMIT, (key, size(attrs))
        assert json.loads(text) == attrs


def test_timeline_sizes_for_48_hours_with_long_reasons_still_fit(monkeypatch):
    for reason_len in (60, 220, 400):
        monkeypatch.setattr(engine_mod, "forecast", BigStubForecast())
        monkeypatch.setattr(engine_mod, "value", BigStubValue(reason_len))
        eng = EngineV2(V2Settings())
        out = eng.step(inp(T0, import_rate=0.07))
        state, attrs = publish.entity_states(out, eng, eng.s, LOCAL)["v2_timeline"]
        assert size(attrs) < LIMIT, (reason_len, size(attrs))
        assert len(attrs["items"]) == 24 and attrs["path"]["mid"]


def test_the_timeline_with_extreme_input_is_cut_down_to_fit(monkeypatch):
    monkeypatch.setattr(engine_mod, "forecast", BigStubForecast())
    monkeypatch.setattr(engine_mod, "value", BigStubValue(5000))
    eng = EngineV2(V2Settings())
    big = _with(big_value_result(T0, 5000, items=60), because="x")
    _, attrs = publish._timeline(big, eng.s, 12.0, LOCAL)
    assert size(attrs) < LIMIT


def test_the_mode_sensor_has_the_contract_attributes(made):
    eng, out = made
    state, a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_mode"]
    assert state == out.mode.mode
    assert set(a) == {"label", "since", "why", "rule", "chosen_by", "target_soc", "power_w", "exits", "deadline",
                      "level_reported", "level_filtered", "sending", "preview", "not_sending_reason", "values_at",
                      "values_because"}
    assert a["rule"].startswith("v2_") and a["sending"] is True and a["not_sending_reason"] is None
    assert a["values_because"] == "The car started charging"
    for e in a["exits"]:
        assert set(e) == {"kind", "text", "expected_at", "first"}
    assert sum(e["first"] for e in a["exits"]) == 1
    assert size(a) < 3000


def test_times_are_iso_with_an_offset(made):
    eng, out = made
    states = publish.entity_states(out, eng, eng.s, LOCAL)
    a = states["v2_mode"][1]
    for key in ("since", "values_at", "deadline"):
        if a[key]:
            assert datetime.fromisoformat(a[key]).utcoffset() == timedelta(hours=1)
    tl = states["v2_timeline"]
    assert datetime.fromisoformat(tl[0]).utcoffset() is not None
    assert datetime.fromisoformat(tl[1]["items"][0]["start"]).utcoffset() == timedelta(hours=1)


def test_the_value_sensor_has_the_contract_attributes(made):
    eng, out = made
    state, a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_value"]
    assert state == "30.00"
    assert set(a) == {"value_p", "buy_line_p", "sell_line_p", "use_line_p", "store_sun_line_p", "import_p",
                      "export_p", "charge_target_soc", "sell_floor_soc", "level", "scale_max_p"}
    assert a["buy_line_p"] == round(7.0 / 0.95, 2) and a["sell_floor_soc"] is None and a["scale_max_p"] == 40
    assert size(a) < 800


def test_scale_max_grows_with_the_real_prices_and_ignores_the_value():
    base = dict(value_p=300.0, buy_line_p=7.0, sell_line_p=14.0, use_line_p=6.0, store_sun_line_p=15.8,
                import_p=7.0, export_p=15.0)
    assert publish.scale_max(Lines(**base)) == 40
    assert publish.scale_max(Lines(**{**base, "import_p": 30.28, "use_line_p": 28.8, "buy_line_p": 31.9})) == 40
    assert publish.scale_max(Lines(**{**base, "import_p": 40.0, "use_line_p": 38.0, "buy_line_p": 42.1})) == 60
    assert publish.scale_max(Lines(**{**base, "import_p": 80.0})) == 100


def test_the_timeline_sensor_has_the_contract_attributes(made):
    eng, out = made
    state, a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_timeline"]
    assert datetime.fromisoformat(state) == out.value.made_at
    assert set(a) == {"now", "because", "floor_soc", "reserve_soc", "items", "path", "prices", "sun",
                      "cost_expected", "cost_selfuse", "comfort_given_up", "calc_s"}
    assert set(a["items"][0]) == {"mode", "start", "end", "level_start", "level_end", "until", "reason"}
    assert set(a["path"]) == {"start", "step_min", "mid", "low", "high"}
    assert set(a["prices"][0]) >= {"start", "end", "import_p", "export_p", "slot_prob", "event", "free", "estimated"}
    assert a["cost_expected"] == 1.23 and a["cost_selfuse"] == 2.35 and a["comfort_given_up"] == 0.06
    assert a["floor_soc"] == 12.0 and a["reserve_soc"] == 12.0 and a["calc_s"] == 1.83
    assert all(round(x, 1) == x for x in a["path"]["mid"])


def test_the_sun_and_house_the_plan_used_are_published_in_half_hour_kw(made):
    eng, out = made
    a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_timeline"][1]
    sun = a["sun"]
    assert set(sun) == {"start", "step_min", "low", "mid", "high", "house"} and sun["step_min"] == 30
    assert len(sun["mid"]) == len(sun["low"]) == len(sun["high"]) == len(sun["house"]) == 97
    t0 = datetime.fromisoformat(sun["start"]).astimezone(timezone.utc)
    for i, (lo, mid, hi) in enumerate(zip(sun["low"], sun["mid"], sun["high"], strict=True)):
        t = t0 + timedelta(minutes=30 * i)
        assert lo <= mid <= hi
        if 9 <= t.hour < 15:
            assert mid == pytest.approx(1.0)                  # 0.5 kWh in a half hour is 1 kW
        elif t.hour < 7 or t.hour >= 17:
            assert mid == 0.0
    assert sun["house"][0] == pytest.approx(0.6)               # 0.3 kWh in a half hour


def test_sun_buckets_share_segments_that_do_not_line_up_with_the_half_hours():
    now = T0.replace(minute=10)
    segs = [segment(now, hours=1 / 3, solar=0.4, load=0.2),           # 00:10 to 00:30: all of it in the first bucket
            segment(now + timedelta(minutes=20), hours=1 / 3, solar=0.0, load=0.2)]    # 00:30 to 00:50
    sun = publish._sun(value_result(now, forecast(now, segs)), LOCAL)
    assert datetime.fromisoformat(sun["start"]).minute == 0
    assert sun["mid"] == [1.2, 0.0] and sun["house"] == [0.6, 0.6]       # kW over the time covered


def test_prices_merge_consecutive_equal_segments_and_keep_the_slot_dashed(made):
    eng, out = made
    prices = publish.entity_states(out, eng, eng.s, LOCAL)["v2_timeline"][1]["prices"]
    assert 4 <= len(prices) < 30
    slot = [p for p in prices if p["slot_prob"] is not None]
    assert len(slot) == 1 and slot[0]["slot_prob"] == 0.8 and slot[0]["slot_import_p"] == 6.99
    for a, b in zip(prices, prices[1:], strict=False):
        assert a["end"] == b["start"]


def test_the_value_curve_is_eleven_levels_for_each_hour(made):
    eng, out = made
    state, a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_value_curve"]
    assert set(a) == {"start", "step_min", "levels", "values", "unit"}
    assert a["levels"] == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100] and a["step_min"] == 60 and a["unit"] == "p/kWh"
    assert 40 <= len(a["values"]) <= 48 and all(len(r) == 11 for r in a["values"])
    first = out.value.lam[0]
    assert a["values"][0][0] == round(first[0], 2) and a["values"][0][10] == round(first[-1], 2)
    assert a["values"][0][5] == round(first[90], 2)
    assert size(a) < 6000


def test_the_triggers_sensor_has_the_last_thirty_events_and_the_days_counts(made):
    eng, out = made
    state, a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_triggers"]
    assert set(a) == {"recent", "today"} and len(a["recent"]) == 30
    assert set(a["recent"][0]) == {"at", "kind", "text", "effect"}
    assert set(a["today"]) >= {"causes", "revalues", "mode_changes", "flip_flops", "deadlines_missed", "backstop",
                               "longest_calc_s"}
    assert state == str(a["today"]["revalues"]) and int(state) >= 1
    assert a["recent"][0]["at"] >= a["recent"][-1]["at"]                 # newest first


def test_the_diag_sensor(made):
    eng, out = made
    state, a = publish.entity_states(out, eng, eng.s, LOCAL)["diag_v2"]
    assert state == "learning"                                            # fewer than 3 days known
    assert set(a) == {"weights", "solar_bias", "soc_offset", "filter_gap_max_today", "comfort"}
    assert set(a["weights"]) == {"solar", "load", "days", "start"}
    assert a["weights"]["solar"]["midday"] == [0.25, 0.5, 0.25] and a["weights"]["start"]["load"] == [0.25, 0.5, 0.25]
    assert set(a["solar_bias"]) == {"days", "by_hour"}
    assert set(a["comfort"][0]) == {"date", "hours_above", "hours_below", "given_up", "decisions_changed"}
    eng.learner.days = 5
    assert publish.entity_states(out, eng, eng.s, LOCAL)["diag_v2"][0] == "ok"


def test_the_settings_sensor_has_the_catalogue_and_fits():
    state, attrs = publish.settings_state(V2Settings())
    assert state == str(len(SETTINGS))
    assert set(attrs) == {"settings", "sections", "values"} and len(attrs["settings"]) == len(SETTINGS)
    assert size(attrs) < LIMIT and attrs["values"]["reserve_soc"] == 12
    assert "<<" not in json.dumps(attrs)


def test_before_the_first_value_result_the_sensors_are_unknown_and_nothing_breaks(monkeypatch):
    class Broken:
        def build(self, *a, **k):
            raise RuntimeError("no")
    monkeypatch.setattr(engine_mod, "forecast", Broken())
    eng = EngineV2(V2Settings())
    out = eng.step(inp(T0))
    states = publish.entity_states(out, eng, eng.s, None)
    assert states["v2_value"] == ("unknown", {}) and states["v2_timeline"] == ("unknown", {})
    assert states["v2_value_curve"] == ("unknown", {})
    assert states["v2_mode"][1]["values_at"] is None and states["v2_mode"][0] == SELF_USE
    for _, attrs in states.values():
        json.dumps(attrs, allow_nan=False)


def test_not_sending_is_published_for_passive(monkeypatch):
    monkeypatch.setattr(engine_mod, "forecast", BigStubForecast())
    monkeypatch.setattr(engine_mod, "value", BigStubValue())
    from pe_core.engine_v2.types import Situation
    eng = EngineV2(V2Settings())
    out = eng.step(inp(T0, situation=Situation(active=False, mode_reason="Passive mode"), import_rate=0.07))
    a = publish.entity_states(out, eng, eng.s, LOCAL)["v2_mode"][1]
    assert a["sending"] is False and a["not_sending_reason"] == "Passive mode"


def test_no_nan_or_infinity_anywhere(made):
    eng, out = made
    for _, attrs in publish.entity_states(out, eng, eng.s, LOCAL).items():
        for v in _walk(attrs):
            assert not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _walk(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)
    else:
        yield x

