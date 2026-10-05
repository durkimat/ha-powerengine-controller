"""Engine v2, layer 1: the filtered level and the events (docs/plans/engine-v2.md section 4)."""

import json
from datetime import datetime, timedelta, timezone

from pe_core.engine_v2.observe import Observer
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import BatteryFacts, Forecast, Segment, Situation, Spread, StepInput, ValueResult
from pe_core.parsing import Window
from pe_core.readings import Readings

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SETTINGS = V2Settings()


# ---- helpers shared by the engine v2 test files ------------------------------------------------
def windows(start, n=96, rate=0.30):
    return [Window(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 1)), rate) for i in range(n)]


def readings(now, **kw):
    base = dict(battery_soc=50.0, battery_power=0.0, grid_power=0.0, house_power=500.0, solar_power=0.0,
                import_rate=0.30, export_rate=0.15, rates=windows(T0 - timedelta(hours=12)),
                ev_plug="Connected", ev_power=0.0)
    base.update(kw)
    return Readings(now=now, **base)


def inp(now, facts=None, situation=None, **kw):
    sit_kw = {k: kw.pop(k) for k in ("active", "override", "house_load_includes_ev") if k in kw}
    flags = {k: kw.pop(k) for k in ("settings_changed", "learned_changed", "solar_points") if k in kw}
    sit = situation or Situation(active=sit_kw.pop("active", True), **sit_kw)
    return StepInput(now=now, readings=readings(now, **kw), facts=facts or BatteryFacts(), situation=sit,
                     tz=timezone.utc, **flags)


def segment(start, hours=0.5, import_p=30.0, export_p=15.0, solar=0.0, load=0.25, **kw):
    return Segment(start=start, end=start + timedelta(hours=hours), import_p=import_p, export_p=export_p,
                   solar_kwh=Spread(solar * 0.7, solar, solar * 1.2), load_kwh=Spread(load * 0.8, load, load * 1.3),
                   **kw)


def forecast(now, segs):
    return Forecast(made_at=now, segments=tuple(segs))


def value_result(now, fc, path=None, timeline=(), lam=(), **kw):
    base = dict(made_at=now, because="test", forecast=fc, step_kwh=0.5, lam=lam, timeline=tuple(timeline),
                path=path or {"start": now.isoformat(), "step_min": 15, "mid": [], "low": [], "high": []},
                cost_expected_p=100.0, cost_selfuse_p=150.0, comfort_given_up_p=6.0, calc_s=1.0)
    base.update(kw)
    return ValueResult(**base)


def run(obs, start, seconds, step=10, **kw):
    """Update the observer every `step` seconds; returns the list of (time, Observation)."""
    out = []
    for i in range(0, seconds + 1, step):
        t = start + timedelta(seconds=i)
        k = {key: (v(i) if callable(v) else v) for key, v in kw.items()}
        out.append((t, obs.update(inp(t, **k), None, None)))
    return out


def kinds(results):
    return [e.kind for _, o in results for e in o.events]


# ---- the filtered level ------------------------------------------------------------------------
def test_first_tick_resets_to_the_reading_and_says_start():
    o = Observer(SETTINGS)
    ob = o.update(inp(T0, battery_soc=63.0), None, None)
    assert ob.level_filtered == 63.0 and ob.level_reported == 63.0
    assert [e.kind for e in ob.events] == ["start"]
    assert ob.events[0].revalue and ob.events[0].urgent


def test_filter_does_not_dip_when_the_reading_drops_a_point_while_charging():          # B3
    o = Observer(SETTINGS)
    readings_seq = [94, 94, 94, 93, 93, 94, 94, 93, 94]
    levels = []
    for i, soc in enumerate(readings_seq):
        t = T0 + timedelta(seconds=10 * i)
        levels.append(o.update(inp(t, battery_soc=float(soc), battery_power=-3000.0), None, None).level_filtered)
    assert max(levels) - min(levels) < 0.3 and min(levels) >= 93.9, levels     # a point's wobble moves it by hundredths
    assert all(b >= a - 0.05 for a, b in zip(levels, levels[1:], strict=False)), levels


def test_filter_counts_the_energy_when_the_reading_is_missing():
    o = Observer(SETTINGS)
    o.update(inp(T0, battery_soc=50.0), None, None)
    # 4.8 kW into the battery for 60 s with no level reading: 0.08 kWh x 0.95 of 18 kWh
    for i in range(1, 7):
        ob = o.update(inp(T0 + timedelta(seconds=10 * i), battery_soc=None, battery_power=-4800.0), None, None)
    assert abs(ob.level_filtered - (50 + 4.8 * 60 / 3600 * 0.95 / 18 * 100)) < 0.01


def test_filter_discharge_counts_against_the_efficiency():
    o = Observer(SETTINGS)
    o.update(inp(T0, battery_soc=50.0), None, None)
    for i in range(1, 7):
        ob = o.update(inp(T0 + timedelta(seconds=10 * i), battery_soc=None, battery_power=4800.0), None, None)
    assert abs(ob.level_filtered - (50 - 4.8 * 60 / 3600 / 0.95 / 18 * 100)) < 0.01


def test_a_gap_of_three_points_held_ten_minutes_resets_to_the_reading():
    s = V2Settings(soc_filter_gain=0.001)          # follows the reading so slowly that only the reset can bring it back
    o = Observer(s)
    o.update(inp(T0, battery_soc=50.0), None, None)
    res = run(o, T0 + timedelta(seconds=10), 1200, battery_soc=60.0)
    assert res[10][1].level_filtered < 51                         # not reset yet, a few minutes in
    assert res[-1][1].level_filtered == 60.0                       # reset after the ten minutes
    assert o.gap_max_today >= 9.0


def test_the_filter_pulls_towards_the_reading_by_the_gain():
    o = Observer(V2Settings(soc_filter_gain=0.1))
    o.update(inp(T0, battery_soc=50.0), None, None)
    ob = o.update(inp(T0 + timedelta(seconds=10), battery_soc=52.0), None, None)
    assert abs(ob.level_filtered - 50.2) < 1e-6


def test_the_learned_offset_moves_the_target():
    o = Observer(V2Settings(soc_filter_gain=1.0))
    o.set_offsets({"charging": -1.0, "holding": 0.0, "discharging": 0.0})
    o.update(inp(T0, battery_soc=93.0), None, None)
    ob = o.update(inp(T0 + timedelta(seconds=10), battery_soc=93.0, battery_power=-4800.0), None, None)
    assert abs(ob.level_filtered - 94.0) < 0.1                    # reported minus offset: the true level is 94


# ---- the car (B6) ------------------------------------------------------------------------------
def test_a_car_blip_of_under_a_minute_gives_no_event():                                  # B6
    o = Observer(SETTINGS)
    o.update(inp(T0, ev_plug="Connected"), None, None)
    res = run(o, T0 + timedelta(seconds=10), 50, ev_plug="Charging", ev_power=7000.0)
    res += run(o, T0 + timedelta(seconds=70), 120, ev_plug="Connected", ev_power=0.0)
    assert "car_start" not in kinds(res) and "car_stop" not in kinds(res)
    assert not any(ob.car_charging for _, ob in res)


def test_a_five_minute_charge_gives_start_after_a_minute_and_stop_after_thirty_seconds():
    o = Observer(SETTINGS)
    o.update(inp(T0, ev_plug="Connected"), None, None)
    res = run(o, T0 + timedelta(seconds=10), 300, ev_plug="Charging", ev_power=7000.0)
    start = [t for t, ob in res if any(e.kind == "car_start" for e in ob.events)]
    assert len(start) == 1 and 55 <= (start[0] - T0).total_seconds() <= 80
    end = T0 + timedelta(seconds=310)
    res = run(o, end, 120, ev_plug="Connected", ev_power=0.0)
    stop = [t for t, ob in res if any(e.kind == "car_stop" for e in ob.events)]
    assert len(stop) == 1 and 25 <= (stop[0] - end).total_seconds() <= 50
    assert not res[-1][1].car_charging


def test_the_car_is_trusted_on_the_first_tick():
    o = Observer(SETTINGS)
    ob = o.update(inp(T0, ev_plug="Charging", ev_power=7000.0), None, None)
    assert ob.car_charging and "car_start" not in [e.kind for e in ob.events]


# ---- missing data (B11, B12) -------------------------------------------------------------------
def test_a_rate_unavailable_for_one_tick_gives_no_event():                               # B11
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    ob = o.update(inp(T0 + timedelta(seconds=10), import_rate=None, rates=[]), None, None)
    assert ob.data_ok and ob.events == ()
    ob = o.update(inp(T0 + timedelta(seconds=20)), None, None)
    assert ob.events == ()


def test_a_rate_missing_longer_than_the_grace_is_missing_data_once_then_back():             # B12
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    res = run(o, T0 + timedelta(seconds=10), 600, import_rate=None)
    assert kinds(res).count("data_missing") == 1
    assert res[3][1].data_ok and not res[-1][1].data_ok and res[-1][1].missing == ("import rate",)
    first_missing = next(t for t, ob in res if not ob.data_ok)
    assert 170 <= (first_missing - T0).total_seconds() <= 200
    back = o.update(inp(T0 + timedelta(seconds=620)), None, None)
    assert back.data_ok and [e.kind for e in back.events] == ["data_back"]


def test_missing_battery_level_counts_too():
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    res = run(o, T0 + timedelta(seconds=10), 300, battery_soc=None)
    assert "data_missing" in kinds(res) and not res[-1][1].data_ok


# ---- prices, slots, events ---------------------------------------------------------------------
def test_new_prices_make_one_prices_published_after_the_debounce():
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    new = windows(T0 - timedelta(hours=12), rate=0.25)
    res = run(o, T0 + timedelta(seconds=10), 60, rates=new, import_rate=0.25)
    assert kinds(res).count("prices_published") == 1
    pub = next(t for t, ob in res if any(e.kind == "prices_published" for e in ob.events))
    assert (pub - T0).total_seconds() >= 20


def test_a_window_dropping_off_the_end_of_the_list_is_not_new_prices():
    o = Observer(SETTINGS)
    rates = windows(T0 - timedelta(hours=1), n=20)
    o.update(inp(T0, rates=rates), None, None)
    later = T0 + timedelta(minutes=45)
    res = run(o, later, 120, rates=rates)
    assert "prices_published" not in kinds(res)


def test_smart_slot_added_changes_slots_and_the_boundaries_make_slot_events():
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    slot = [Window(T0 + timedelta(minutes=2), T0 + timedelta(minutes=4), 4.0)]
    res = run(o, T0 + timedelta(seconds=10), 300, dispatches=slot)
    k = kinds(res)
    assert k.count("slots_changed") == 1
    assert k.count("slot_start") == 1 and k.count("slot_end") == 1
    start = next(t for t, ob in res if any(e.kind == "slot_start" for e in ob.events))
    assert timedelta(minutes=2) <= start - T0 <= timedelta(minutes=2, seconds=10)


def test_a_price_boundary_gives_a_price_event():
    o = Observer(SETTINGS)
    rates = [Window(T0 - timedelta(hours=1), T0 + timedelta(minutes=1), 0.30),
             Window(T0 + timedelta(minutes=1), T0 + timedelta(hours=2), 0.07)]
    o.update(inp(T0, rates=rates), None, None)
    res = run(o, T0 + timedelta(seconds=10), 120, rates=rates,
              import_rate=lambda i: 0.30 if i < 60 else 0.07)
    assert kinds(res).count("price") == 1


def test_grid_event_start_and_end_are_events():
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    ev_start, ev_end = T0 + timedelta(minutes=1), T0 + timedelta(minutes=3)
    res = run(o, T0 + timedelta(seconds=10), 300, axle_start=ev_start, axle_end=ev_end,
              axle_active=lambda i: 60 <= i + 10 < 180)
    k = kinds(res)
    assert k.count("event_start") == 1 and k.count("event_end") == 1
    assert k.count("event_changed") == 1                          # the announcement itself


def test_free_power_start_and_end():
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    res = run(o, T0 + timedelta(seconds=10), 100, free_active=lambda i: i >= 30)
    assert kinds(res).count("free_start") == 1


# ---- sun and shortfall -------------------------------------------------------------------------
def test_net_load_crossing_zero_for_ten_seconds_gives_no_event():                        # B10 (observer part)
    o = Observer(SETTINGS)
    o.update(inp(T0, house_power=500.0, solar_power=0.0), None, None)
    res = run(o, T0 + timedelta(seconds=10), 100, house_power=500.0,
              solar_power=lambda i: 2000.0 if i in (40,) else 0.0)
    assert not [k for k in kinds(res) if k in ("short_to_sun", "sun_to_short")]


def test_sustained_spare_sun_gives_short_to_sun_and_back():
    o = Observer(SETTINGS)
    o.update(inp(T0, house_power=500.0, solar_power=0.0), None, None)
    res = run(o, T0 + timedelta(seconds=10), 120, house_power=500.0, solar_power=2000.0)
    assert kinds(res).count("short_to_sun") == 1
    res = run(o, T0 + timedelta(seconds=140), 120, house_power=500.0, solar_power=0.0)
    assert kinds(res).count("sun_to_short") == 1


def test_net_load_includes_the_car_and_takes_off_the_sun():
    o = Observer(SETTINGS)
    ob = o.update(inp(T0, house_power=500.0, solar_power=1000.0, ev_plug="Charging", ev_power=7000.0), None, None)
    assert abs(ob.net_load_kw - 6.5) < 1e-9


# ---- drift, band, forecast ---------------------------------------------------------------------
def test_drift_event_when_the_house_uses_more_than_forecast():
    fc = forecast(T0, [segment(T0 + timedelta(minutes=30 * i), load=0.25) for i in range(-1, 8)])
    o = Observer(SETTINGS)
    o.update(inp(T0, house_power=500.0), fc, None)
    got = []
    for i in range(1, 260):
        ob = o.update(inp(T0 + timedelta(seconds=10 * i), house_power=2000.0), fc, None)
        got += [(i, e.kind) for e in ob.events]
    drift = [i for i, k in got if k == "drift"]
    assert len(drift) == 1
    # 1.5 kW above forecast: 0.75 kWh after 30 minutes
    assert 170 <= drift[0] <= 190


def test_drift_cancels_when_it_goes_both_ways_and_resets_on_revalue():
    fc = forecast(T0, [segment(T0 + timedelta(minutes=30 * i), load=0.25) for i in range(-1, 8)])
    o = Observer(SETTINGS)
    o.update(inp(T0, house_power=500.0), fc, None)
    seen = []
    for i in range(1, 400):
        w = 1500.0 if (i // 30) % 2 == 0 else -500.0
        ob = o.update(inp(T0 + timedelta(seconds=10 * i), house_power=max(0.0, 500 + w)), fc, None)
        seen += [e.kind for e in ob.events]
    assert "drift" not in seen
    o.cusum = 5.0
    o.note_revalued(T0)
    assert o.cusum == 0.0 and not o.drift_fired


def test_band_exit_after_ten_minutes_outside_the_expected_range():
    path = {"start": T0.isoformat(), "step_min": 15, "mid": [50.0] * 12, "low": [48.0] * 12, "high": [52.0] * 12}
    vr = value_result(T0, forecast(T0, []), path=path)
    o = Observer(SETTINGS)
    o.update(inp(T0, battery_soc=50.0), None, vr)
    got = []
    for i in range(1, 100):
        ob = o.update(inp(T0 + timedelta(seconds=10 * i), battery_soc=60.0), None, vr)
        got += [(i, e.kind) for e in ob.events]
    hits = [i for i, k in got if k == "band_exit"]
    assert len(hits) == 1 and 55 <= hits[0] <= 70               # about 10 minutes of 10 s ticks


def test_a_battery_on_time_through_a_fast_charge_is_not_outside_the_band_between_the_paths_points():
    """The path has a point every 15 minutes and a charge moves about 7 points in one step: read at the step's start
    alone, a battery that is exactly on time was outside the band for most of every step (band_exit about every
    15 minutes). It is read between the points."""
    mid = [20.0 + 28.0 * i for i in range(3)]
    path = {"start": T0.isoformat(), "step_min": 15, "mid": mid, "low": mid, "high": mid}
    vr = value_result(T0, forecast(T0, []), path=path)
    o = Observer(SETTINGS)
    seen = []
    for sec in range(0, 25 * 60, 10):                       # 25 minutes, the level exactly on the line
        o.level = 20.0 + 28.0 * sec / 900
        o._band(T0.timestamp() + sec, vr, T0 + timedelta(seconds=sec), lambda kind, text: seen.append(kind))
    assert seen == [] and o.band_since is None


def test_inside_the_band_nothing_happens():
    path = {"start": T0.isoformat(), "step_min": 15, "mid": [50.0] * 12, "low": [48.0] * 12, "high": [52.0] * 12}
    vr = value_result(T0, forecast(T0, []), path=path)
    o = Observer(SETTINGS)
    o.update(inp(T0, battery_soc=50.0), None, vr)
    res = [o.update(inp(T0 + timedelta(seconds=10 * i), battery_soc=51.0), None, vr) for i in range(1, 100)]
    assert not any(e.kind == "band_exit" for ob in res for e in ob.events)


class P:
    def __init__(self, start, kwh):
        self.start, self.kwh = start, kwh


def test_forecast_update_when_the_rest_of_the_day_moves_by_more_than_the_setting():
    base = T0.replace(hour=10)
    pts = [P(base + timedelta(minutes=30 * i), 1.0) for i in range(0, 12)]
    o = Observer(SETTINGS)
    o.update(inp(T0, solar_points=pts), None, None)
    small = [P(p.start, 1.05) for p in pts]
    res = run(o, T0 + timedelta(seconds=10), 100, solar_points=small)
    assert "forecast_update" not in kinds(res)
    big = [P(p.start, 1.3) for p in pts]
    res = run(o, T0 + timedelta(seconds=120), 100, solar_points=big)
    assert kinds(res).count("forecast_update") == 1
    o.note_revalued(T0)
    res = run(o, T0 + timedelta(seconds=240), 100, solar_points=big)
    assert "forecast_update" not in kinds(res)               # the new forecast is the baseline now


# ---- bms, switches, override, settings ---------------------------------------------------------
def test_bms_limit_to_zero_or_a_half_kw_move_is_an_event():
    o = Observer(SETTINGS)
    o.update(inp(T0, facts=BatteryFacts(bms_charge_kw=4.8)), None, None)
    ob = o.update(inp(T0 + timedelta(seconds=10), facts=BatteryFacts(bms_charge_kw=4.5)), None, None)
    assert ob.events == ()
    ob = o.update(inp(T0 + timedelta(seconds=20), facts=BatteryFacts(bms_charge_kw=0.0)), None, None)
    assert [e.kind for e in ob.events] == ["bms"]
    ob = o.update(inp(T0 + timedelta(seconds=30), facts=BatteryFacts(bms_charge_kw=0.0)), None, None)
    assert ob.events == ()


def test_active_changing_is_a_mode_switch_and_urgent():
    o = Observer(SETTINGS)
    o.update(inp(T0, active=True), None, None)
    passive = Situation(active=False, mode_reason="Passive")
    ob = o.update(inp(T0 + timedelta(seconds=10), situation=passive), None, None)
    assert [e.kind for e in ob.events] == ["mode_switch"] and ob.events[0].urgent


def test_override_set_and_cleared_are_events():
    from pe_core.override import Override
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    ov = Override("hold", T0 + timedelta(hours=1), T0)
    ob = o.update(inp(T0 + timedelta(seconds=10), override=ov), None, None)
    assert [e.kind for e in ob.events] == ["override"]
    ob = o.update(inp(T0 + timedelta(seconds=20), override=ov), None, None)
    assert ob.events == ()
    ob = o.update(inp(T0 + timedelta(seconds=30)), None, None)
    assert [e.kind for e in ob.events] == ["override"]


def test_settings_and_learned_flags_become_events():
    o = Observer(SETTINGS)
    o.update(inp(T0), None, None)
    ob = o.update(inp(T0 + timedelta(seconds=10), settings_changed=True, learned_changed=True), None, None)
    assert sorted(e.kind for e in ob.events) == ["learned", "settings"]


# ---- state -------------------------------------------------------------------------------------
def test_state_is_json_safe_and_a_restored_observer_says_start_once_and_nothing_else():
    o = Observer(SETTINGS)
    o.update(inp(T0, dispatches=[Window(T0 + timedelta(hours=1), T0 + timedelta(hours=2), 4.0)]), None, None)
    run(o, T0 + timedelta(seconds=10), 60, dispatches=[Window(T0 + timedelta(hours=1), T0 + timedelta(hours=2), 4.0)])
    st = json.loads(json.dumps(o.state()))
    o2 = Observer(SETTINGS, st)
    disp = [Window(T0 + timedelta(hours=1), T0 + timedelta(hours=2), 4.0)]
    ob = o2.update(inp(T0 + timedelta(seconds=100), dispatches=disp), None, None)
    assert [e.kind for e in ob.events] == ["start"]
    res = run(o2, T0 + timedelta(seconds=110), 100, dispatches=disp)
    assert kinds(res) == []
    assert o2.level == 50.0
