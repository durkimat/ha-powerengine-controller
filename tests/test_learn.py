"""Learned limits and the cold-battery caution (0.8.0)."""
from datetime import datetime, timedelta, timezone

from pe_core import learn as L
from pe_core.energy import HalfHour
from pe_core.forecast import Slot
from pe_core.planner import Params, PlanSlot, charge_limit_kw, step

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
COLD = L.ColdSettings()


def half(i=0, **kw):
    h = {"start": (T0 + timedelta(minutes=30 * i)).isoformat(), "seconds": 1800, "soc_start": 40, "soc_end": 50,
         "battery_in": 0.0, "battery_out": 0.0, "house": 0.3, "solar": 0.0, "grid_export": 0.0, "car": 0.0}
    h.update(kw)
    return h


def charge(kw, tb=None, soc=(40, 50), i=0):
    return half(i, cmd="grid_charge", cmd_kw=4.8, battery_in=kw / 2, soc_start=soc[0], soc_end=soc[1], tb_c=tb)


def learn(halves):
    return L.learn(halves, 4.8, 4.8, 12, 6.0, COLD)


def test_nothing_learned_from_nothing():
    lr = learn([])
    assert lr.max_charge_kw is None and lr.taper == () and lr.cold_threshold_c is None


def test_charge_rate_from_full_rate_asks():
    lr = learn([charge(4.2, tb=15, i=i) for i in range(8)])
    assert lr.max_charge_kw == 4.2 and lr.charge_samples == 8


def test_charge_rate_ignores_part_asks_and_cold():
    hs = [charge(4.2, tb=15, i=i) for i in range(6)]
    hs += [dict(charge(2.0, tb=15, i=10 + i), cmd_kw=2.0) for i in range(6)]      # asked for 2 kW
    hs += [charge(2.4, tb=0, i=20 + i) for i in range(6)]                           # cold
    assert learn(hs).max_charge_kw == 4.2


def test_charge_rate_fallback_needs_many_half_hours():
    hs = [half(i, battery_in=2.3) for i in range(19)]
    assert learn(hs).max_charge_kw is None
    assert learn(hs + [half(30, battery_in=2.4)]).max_charge_kw == 4.8


def test_taper_near_full():
    hs = [charge(4.8, tb=15, i=i) for i in range(6)]
    hs += [charge(3.6, tb=15, soc=(91, 97), i=10 + i) for i in range(3)]
    hs += [charge(1.2, tb=15, soc=(96, 99), i=20 + i) for i in range(3)]
    lr = learn(hs)
    assert lr.taper == ((90.0, 0.75), (95.0, 0.25))


def test_reserve_where_discharge_stops():
    hs = [half(i, soc_start=15, soc_end=15, house=0.6) for i in range(4)]
    assert learn(hs).reserve_soc == 15
    held = [dict(h, cmd="hold") for h in hs]
    assert learn(held).reserve_soc is None


def test_export_limit_only_when_below_the_battery_rate():
    sells = [half(i, cmd="export", cmd_kw=4.8, battery_out=2.4, grid_export=1.8, soc_end=60) for i in range(6)]
    lr = learn(sells)
    assert lr.export_kw == 3.6
    free = [dict(h, grid_export=2.3) for h in sells]              # exports what the battery gives: no grid limit
    assert learn(free).export_kw is None


def test_car_rate():
    hs = [half(i, car=3.6) for i in range(4)]
    assert learn(hs).car_kw == 7.2


def test_cold_slow_raises_threshold_and_sets_factor():
    hs = [charge(4.8, tb=15, i=i) for i in range(6)]
    hs += [charge(2.2, tb=t, i=10 + i) for i, t in enumerate((2.0, 4.5, 5.2))]
    lr = learn(hs)
    assert lr.cold_threshold_c == 5.7 and lr.cold_factor == 0.46


def test_cold_fast_at_three_drops_threshold():
    hs = [charge(4.8, tb=t, i=i) for i, t in enumerate((3.0, 3.4, 3.8, 15, 15, 15))]
    lr = learn(hs)
    assert lr.cold_threshold_c == 3.0 and lr.cold_factor is None


def test_battery_temperature_lags_outside():
    outside = {T0 + timedelta(hours=h): (10.0 if h < 24 else 0.0) for h in range(72)}
    tb = L.battery_temps(outside, 24)
    assert tb[T0 + timedelta(hours=23)] == 10.0
    after_12h = tb[T0 + timedelta(hours=36)]
    assert 3.5 < after_12h < 7.0                       # well on its way down, not there yet
    assert tb[T0 + timedelta(hours=71)] < 2.0


def test_caution_hysteresis_keeps_it_on_through_a_mild_spell():
    tb = {T0 + timedelta(hours=h): t for h, t in enumerate((6, 3.5, 5.5, 6.5, 7.5))}
    c = L.caution_by_hour(tb, 4.0, 3.0)
    assert [c[k] for k in sorted(c)] == [False, True, True, True, False]


def test_slot_factors_and_summary():
    tb = {T0 + timedelta(hours=h): 2.0 for h in range(3)}
    caution = L.caution_by_hour(tb, 4.0, 3.0)
    starts = [T0 + timedelta(minutes=30 * i) for i in range(8)]
    f = L.slot_factors(starts, caution, 0.5)
    assert f == [0.5] * 6 + [1.0, 1.0]
    s = L.cold_summary(starts, f, tb, T0, 4.0, 0.5, False)
    assert s["active_now"] and len(s["periods"]) == 1 and s["battery_c_now"] == 2.0


def test_planner_charges_slower_when_cold_and_near_full():
    p = Params(taper=((95.0, 0.25),))
    warm = Slot(T0, 0.07, 0.15)
    cold = Slot(T0, 0.07, 0.15, charge_factor=0.5)
    assert charge_limit_kw(warm, p, 50) == 4.8
    assert charge_limit_kw(cold, p, 50) == 2.4
    assert charge_limit_kw(warm, p, 96) == 1.2
    a, b = PlanSlot(warm, "grid_charge", ""), PlanSlot(cold, "grid_charge", "")
    assert step(a, 50, p) > step(b, 50, p)


def test_half_hour_notes_the_command():
    h = HalfHour(T0)
    h.note("grid_charge", 4.8)
    h.note("grid_charge", 3.0)
    assert (h.cmd, h.cmd_kw) == ("grid_charge", 3.0)
    h.note("hold", None)
    assert h.cmd == "mixed" and h.as_dict()["cmd"] == "mixed"
    p = HalfHour(T0)
    p.note(None, None)
    p.note("grid_charge", 4.8)
    assert p.cmd == "mixed"


def _app(monkeypatch, cfg=None):
    import sys
    import types

    from pe_core.config import parse_config
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config(cfg or {})
    a.tz = timezone.utc
    a.measured = None
    return a


def test_plan_uses_learned_figures_control_uses_configured(monkeypatch):
    a = _app(monkeypatch)
    a.learned = L.Learned(max_charge_kw=4.3, reserve_soc=15.0, taper=((95.0, 0.3),), car_kw=7.0, export_kw=3.6)
    p = a._params()
    assert (p.max_charge_kw, p.min_reserve_soc, p.taper, p.ev_charger_kw, p.export_limit_kw) == \
        (4.3, 15.0, ((95.0, 0.3),), 7.0, 3.6)
    assert a._control_params().max_charge_kw == 4.8


def test_learned_figures_can_be_turned_off(monkeypatch):
    a = _app(monkeypatch, {"features": {"learn_taper": False, "learn_reserve": False},
                           "inputs": {"battery_max_charge_power": {"value": 4800, "use_measured": False}}})
    a.learned = L.Learned(max_charge_kw=4.3, reserve_soc=15.0, taper=((95.0, 0.3),))
    p = a._params()
    assert (p.max_charge_kw, p.min_reserve_soc, p.taper) == (4.8, 12, ())


def test_implausible_learned_rate_is_ignored(monkeypatch):
    a = _app(monkeypatch)
    a.learned = L.Learned(max_charge_kw=1.0, reserve_soc=40.0)
    p = a._params()
    assert p.max_charge_kw == 4.8 and p.min_reserve_soc == 12


def test_cold_applies_to_slots_and_uses_learned_threshold(monkeypatch):
    a = _app(monkeypatch)
    a.learned = L.Learned(cold_threshold_c=3.0)
    tb = {T0 + timedelta(hours=h): 3.5 for h in range(4)}
    a._tb = tb
    a._caution = L.caution_by_hour(tb, *a._cold_in_use()[:1], 3.0)
    slots = [Slot(T0 + timedelta(minutes=30 * i), 0.07, 0.15) for i in range(4)]
    out, summary = a._apply_cold(slots, T0)
    assert all(s.charge_factor == 1.0 for s in out) and summary["threshold_c"] == 3.0   # 3.5 is fine now
    a.learned = L.Learned()
    a._caution = L.caution_by_hour(tb, a._cold_in_use()[0], 3.0)
    out, summary = a._apply_cold(slots, T0)
    assert all(s.charge_factor == 0.5 for s in out) and summary["active_now"]


def test_learn_publishes_a_table(monkeypatch):
    import types
    a = _app(monkeypatch)
    hs = [charge(4.2, tb=15, i=i) for i in range(8)]
    a.costbook = types.SimpleNamespace(halves=lambda today: hs)
    a._today = lambda: T0.date()
    pub = {}
    a._publish_state = lambda key, state, attrs=None: pub.__setitem__(key, (state, attrs))
    a.log = lambda *args, **kw: None
    a._learn()
    state, attrs = pub["diag_learned"]
    assert state == "1 learned"
    row = attrs["rows"][0]
    assert row["learned"] == "4.2 kW" and row["in_use"] == "4.20 kW" and row["samples"] == 8


def test_weather_recent_and_series(tmp_path):
    from pe_core.weather import Weather
    w = Weather(str(tmp_path / "w.json"), 51.75, -0.34)
    data = {"hourly": {"time": ["2026-12-01T00:00", "2026-12-01T01:00"], "temperature_2m": [1.5, None]}}
    assert w.refresh_recent(get=lambda url: data) == 1
    s = w.series(T0, T0 + timedelta(hours=2))
    assert s == {T0: 1.5}


def test_each_learned_figure_has_its_own_switch(monkeypatch):
    a = _app(monkeypatch, {"features": {"learn_car": False, "use_learned": True}})     # legacy key ignored
    a.learned = L.Learned(reserve_soc=15.0, car_kw=7.0)
    p = a._params()
    assert p.min_reserve_soc == 15.0 and p.ev_charger_kw == 7.4


def test_battery_location_sets_the_lag(monkeypatch):
    assert _app(monkeypatch)._cold_settings().lag_h == 24
    assert _app(monkeypatch, {"system": {"battery_location": "outside"}})._cold_settings().lag_h == 6
    a = _app(monkeypatch, {"system": {"battery_location": "custom"}, "safety": {"battery_temp_lag_h": 40}})
    assert a._cold_settings().lag_h == 40


def test_bad_battery_location_is_rejected():
    import pytest

    from pe_core.config import ConfigError, parse_config, settings_catalogue
    with pytest.raises(ConfigError, match="one of"):
        parse_config({"system": {"battery_location": "shed"}})
    sysset = {x["key"]: x for x in settings_catalogue()["system"]}
    assert sysset["battery_location"]["section"] == "cold" and len(sysset["battery_location"]["options"]) == 4
    assert "options" not in sysset["house_load_includes_ev"]


def test_measured_battery_temperature_anchors_the_estimate():
    outside = {T0 + timedelta(hours=h): 0.0 for h in range(48)}
    tb = L.battery_temps(outside, 24, anchor=(T0 + timedelta(hours=24), 10.0))
    assert tb[T0 + timedelta(hours=24)] == 10.0
    assert 5 < tb[T0 + timedelta(hours=36)] < 10


def test_own_outside_sensor_wins(tmp_path):
    from pe_core.weather import Weather
    w = Weather(str(tmp_path / "w.json"), 51.75, -0.34)
    w.refresh_recent(get=lambda url: {"hourly": {"time": ["2026-12-01T00:00"], "temperature_2m": [5.0]}})
    w.set_local(T0, 1.0)
    assert w.series(T0, T0) == {T0: 1.0}
    w.save()
    assert Weather(str(tmp_path / "w.json"), 51.75, -0.34).series(T0, T0) == {T0: 1.0}
