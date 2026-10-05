"""Engine v2, layer 2 (forecast.build): segments, smart slots as two outcomes, events, car, sun and house spreads."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pe_core import decide as v1d
from pe_core.adapters.base import ForecastPoint
from pe_core.engine_v2 import forecast as F
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import CHARGE, BatteryFacts, Situation, StepInput
from pe_core.forecast import LoadProfile
from pe_core.override import Override
from pe_core.readings import Readings, Window

UTC = timezone.utc
BST = timezone(timedelta(hours=1))
DAY = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
CHEAP, STD = 0.0699, 0.3028


def at(h, m=0, day=0):
    return DAY + timedelta(days=day, hours=h, minutes=m)


def rate_windows(days=1, cheap=(), std=STD):
    """Half-hour rates for `days` days from DAY; the half-hour indexes from DAY in `cheap` are at 6.99p."""
    out = []
    for i in range(48 * days):
        s = DAY + timedelta(minutes=30 * i)
        out.append(Window(s, s + timedelta(minutes=30), CHEAP if i in cheap else std))
    return out


def make(now, days=2, cheap=(), overnight=(), sun=None, profile=True, **kw):
    r = Readings(now=now, battery_soc=50, import_rate=STD, export_rate=0.15, rates=rate_windows(days, cheap), **kw)
    prof = LoadProfile({(wk, h): 600.0 for wk in (False, True) for h in range(48)}, 10) if profile else None
    pts = sun if sun is not None else []
    return StepInput(now=now, readings=r, facts=BatteryFacts(), situation=Situation(active=True), tz=UTC,
                     solar_points=pts, load_profile=prof, overnight=set(overnight))


def sun_points(kwh=0.5, band=True, days=2):
    out = []
    for i in range(48 * days):
        s = DAY + timedelta(minutes=30 * i)
        k = kwh if 20 <= (i % 48) <= 30 else 0.0
        out.append(ForecastPoint(s, k, 0.5 * k if band else None, 1.5 * k if band else None))
    return out


ST = V2Settings()


def test_segments_cut_at_real_dispatch_minutes():
    now = at(21, 0)
    inp = make(now)
    inp.readings.rates = rate_windows(2, cheap={46, 47, 48, 49})
    inp.readings.dispatches = [Window(at(23, 12), at(0, 47, day=1), 0.0)]
    fc = F.build(inp, ST)
    starts = {s.start for s in fc.segments}
    ends = {s.end for s in fc.segments}
    assert at(23, 12) in starts and at(0, 47, day=1) in ends
    assert at(23, 12) in ends and at(0, 47, day=1) in starts      # cut on both sides
    inside = [s for s in fc.segments if s.slot_prob is not None]
    assert inside[0].start == at(23, 12) and inside[-1].end == at(0, 47, day=1)
    before = next(s for s in fc.segments if s.start == at(23, 0))
    assert before.end == at(23, 12) and before.slot_prob is None and before.import_p == pytest.approx(30.28)
    assert all(s.hours <= 0.5 + 1e-9 for s in fc.segments)


def test_slot_is_two_outcomes_not_a_blend():
    now = at(21, 0)
    inp = make(now)
    inp.readings.rates = rate_windows(2, cheap={46, 47, 48, 49})
    inp.readings.dispatches = [Window(at(23, 0), at(1, 0, day=1), 0.0)]
    inp.slot_certainty = lambda start, seen: 0.7
    fc = F.build(inp, ST)
    seg = next(s for s in fc.segments if s.start == at(23, 30))
    assert seg.slot_prob == pytest.approx(0.7)
    assert seg.slot_import_p == pytest.approx(6.99)       # the slot's price
    assert seg.import_p == pytest.approx(30.28)           # the price without it: not an average
    assert seg.overnight is False


def test_running_slot_is_certain_and_no_certainty_means_certain():
    now = at(23, 20)
    inp = make(now)
    inp.readings.rates = rate_windows(2, cheap={46, 47, 48, 49})
    inp.readings.dispatches = [Window(at(23, 0), at(1, 0, day=1), 0.0)]
    inp.slot_certainty = lambda start, seen: 0.2
    fc = F.build(inp, ST)
    assert all(s.slot_prob == 1.0 for s in fc.segments if s.slot_prob is not None and s.start < at(1, 0, day=1))
    inp.readings.now = now
    inp2 = make(at(21, 0))
    inp2.readings.rates = rate_windows(2, cheap={46, 47, 48, 49})
    inp2.readings.dispatches = [Window(at(23, 0), at(1, 0, day=1), 0.0)]
    fc2 = F.build(inp2, ST)
    assert next(s for s in fc2.segments if s.start == at(23, 30)).slot_prob == 1.0


def test_slot_inside_overnight_window_is_not_a_slot():
    now = at(21, 0)
    inp = make(now, overnight=range(46, 48))
    inp.readings.rates = rate_windows(2, cheap={46, 47})
    inp.readings.dispatches = [Window(at(23, 0), at(0, 0, day=1), 0.0)]
    fc = F.build(inp, ST)
    seg = next(s for s in fc.segments if s.start == at(23, 0))
    assert seg.slot_prob is None and seg.slot_import_p is None and seg.overnight
    assert seg.import_p == pytest.approx(6.99)


def test_slot_ignored_when_it_is_not_for_the_whole_house():
    inp = make(at(21, 0))
    inp.readings.rates = rate_windows(2, cheap={46, 47})
    inp.readings.dispatches = [Window(at(23, 0), at(0, 0, day=1), 0.0)]
    inp.slots_whole_house = False
    seg = next(s for s in F.build(inp, ST).segments if s.start == at(23, 0))
    assert seg.slot_prob is None and seg.import_p == pytest.approx(30.28)


def test_prices_beyond_the_published_ones_are_estimated():
    now = at(20, 0)
    inp = make(now, days=1)                  # prices end at midnight
    fc = F.build(inp, ST)
    assert fc.segments[-1].end - now >= timedelta(hours=36)
    first_est = next(s for s in fc.segments if s.price_estimated)
    assert first_est.start == at(0, 0, day=1)
    assert not any(s.price_estimated for s in fc.segments if s.start < at(0, 0, day=1))
    assert first_est.import_p == pytest.approx(30.28)
    assert any("estimated" in n for n in fc.notes)


def test_horizon_is_between_36_and_48_hours():
    fc = F.build(make(at(8, 0), days=3), ST)
    span = fc.segments[-1].end - fc.segments[0].start
    assert timedelta(hours=47) < span <= timedelta(hours=48, minutes=30)
    fc = F.build(make(at(8, 0), days=1), ST)
    assert fc.segments[-1].end - at(8, 0) >= timedelta(hours=36)


def test_first_segment_starts_now_and_is_pro_rata():
    now = at(12, 10)
    inp = make(now, sun=sun_points(1.0))
    inp.readings.rates = rate_windows(2)
    fc = F.build(inp, ST)
    a = fc.segments[0]
    assert a.start == now and a.end == at(12, 30)
    assert a.load_kwh.mid == pytest.approx(0.3 * 20 / 30)          # 600 W for half an hour = 0.3 kWh
    assert a.solar_kwh.mid == pytest.approx(1.0 * 20 / 30)


def test_events_and_free_power():
    inp = make(at(10, 0))
    inp.readings.axle_start, inp.readings.axle_end = at(17, 0), at(17, 30)
    inp.readings.free_start, inp.readings.free_end = at(13, 0), at(14, 0)
    fc = F.build(inp, ST)
    ev = [s for s in fc.segments if s.event]
    assert [(s.start, s.end) for s in ev] == [(at(17, 0), at(17, 30))]
    assert ev[0].event_p == pytest.approx(100 + 15)                  # on top of the export rate
    fr = [s for s in fc.segments if s.free]
    assert fr[0].start == at(13, 0) and fr[-1].end == at(14, 0) and all(s.import_p == 0.0 for s in fr)
    off = F.build(inp, replace(ST, events=False, free_power=False))
    assert not any(s.event or s.free for s in off.segments)
    no_top = F.build(inp, replace(ST, event_plus_export=False))
    assert next(s for s in no_top.segments if s.event).event_p == pytest.approx(100)


def test_event_cuts_at_its_real_minutes():
    inp = make(at(10, 0))
    inp.readings.axle_start, inp.readings.axle_end = at(17, 10), at(17, 50)
    fc = F.build(inp, ST)
    ev = [s for s in fc.segments if s.event]
    assert ev[0].start == at(17, 10) and ev[-1].end == at(17, 50)
    assert next(s for s in fc.segments if s.start == at(17, 0)).event is False


def test_car_only_in_the_running_half_hour():
    now = at(19, 40)
    inp = make(now)
    inp.readings.ev_plug, inp.readings.ev_power = "Charging", 7000.0
    fc = F.build(inp, ST)
    cars = [s for s in fc.segments if s.car_kw]
    assert cars and all(s.end <= at(20, 0) for s in cars)
    assert cars[0].car_kw == pytest.approx(7.0)
    inp.readings.ev_plug = "Waiting for EV"
    assert not any(s.car_kw for s in F.build(inp, ST).segments)


def test_sun_spread_bands_and_bias():
    inp = make(at(8, 0), sun=sun_points(1.0))
    seg = next(s for s in F.build(inp, ST).segments if s.start == at(11, 0))
    assert (seg.solar_kwh.low, seg.solar_kwh.mid, seg.solar_kwh.high) == pytest.approx((0.5, 1.0, 1.5))
    # a forecast with no bands: 0.7x and 1.2x of the middle
    inp = make(at(8, 0), sun=sun_points(1.0, band=False))
    seg = next(s for s in F.build(inp, ST).segments if s.start == at(11, 0))
    assert (seg.solar_kwh.low, seg.solar_kwh.high) == pytest.approx((0.7, 1.2))
    # learned bias for that local hour scales all three; clamped to 0.5 to 1.5
    learned = {"solar_bias": {"by_hour": {"11": 0.8, "12": 5.0}}}
    inp = make(at(8, 0), sun=sun_points(1.0))
    segs = F.build(inp, ST, learned).segments
    assert next(s for s in segs if s.start == at(11, 0)).solar_kwh.mid == pytest.approx(0.8)
    assert next(s for s in segs if s.start == at(12, 0)).solar_kwh.mid == pytest.approx(1.5)
    off = F.build(inp, replace(ST, learn_solar_bias=False), learned).segments
    assert next(s for s in off if s.start == at(11, 0)).solar_kwh.mid == pytest.approx(1.0)


def test_weights_start_from_settings_and_follow_what_is_learned():
    inp = make(at(8, 0), sun=sun_points(1.0))
    seg = next(s for s in F.build(inp, ST).segments if s.start == at(11, 0))
    assert seg.solar_kwh.w == pytest.approx((0.25, 0.5, 0.25)) and seg.load_kwh.w == pytest.approx((0.25, 0.5, 0.25))
    learned = {"weights": {"solar": {"midday": [0.6, 0.3, 0.1], "morning": [1, 1, 2]}, "load": [0.2, 0.6, 0.2]}}
    segs = F.build(inp, ST, learned).segments
    mid = next(s for s in segs if s.start == at(11, 0))
    assert mid.solar_kwh.w == pytest.approx((0.6, 0.3, 0.1)) and mid.load_kwh.w == pytest.approx((0.2, 0.6, 0.2))
    am = next(s for s in segs if s.start == at(9, 0))
    assert am.solar_kwh.w == pytest.approx((0.25, 0.25, 0.5))
    pm = next(s for s in segs if s.start == at(16, 0))
    assert pm.solar_kwh.w == pytest.approx((0.25, 0.5, 0.25))          # no learned afternoon weights: the start
    custom = F.build(inp, replace(ST, solar_low_pct=50, solar_mid_pct=50, solar_high_pct=0)).segments[3]
    assert custom.solar_kwh.w == pytest.approx((0.5, 0.5, 0.0))


def test_house_spread_default_and_learned():
    inp = make(at(8, 0))
    seg = next(s for s in F.build(inp, ST).segments if s.start == at(11, 0))
    assert (seg.load_kwh.low, seg.load_kwh.mid, seg.load_kwh.high) == pytest.approx((0.24, 0.3, 0.39))
    learned = {"load_spread": {str(22): (-0.05, 0.2)}}                 # half-hour 22 is 11:00
    seg = next(s for s in F.build(inp, ST, learned).segments if s.start == at(11, 0))
    assert (seg.load_kwh.low, seg.load_kwh.high) == pytest.approx((0.25, 0.5))


def test_no_history_notes_and_flat_house():
    fc = F.build(make(at(8, 0), profile=False), ST)
    assert any("house load" in n for n in fc.notes) and any("solar" in n for n in fc.notes)
    assert fc.segments[1].load_kwh.mid == pytest.approx(0.25)           # 500 W default


def test_override_marks_segments_except_events():
    inp = make(at(10, 0))
    inp.readings.axle_start, inp.readings.axle_end = at(11, 0), at(11, 30)
    inp.situation = Situation(active=True, override=Override(v1d.GRID_CHARGE, at(12, 0), at(10, 0)))
    segs = F.build(inp, ST).segments
    assert all(s.manual == CHARGE for s in segs if s.end <= at(12, 0) and not s.event)
    assert next(s for s in segs if s.event).manual is None
    assert all(s.manual is None for s in segs if s.start >= at(12, 0))
    inp.situation = Situation(active=True, override=Override(v1d.HOLD, None, at(10, 0)))
    assert all(s.manual == "hold" for s in F.build(inp, ST).segments if not s.event)


def test_max_segment_splits_long_runs_and_charge_factor():
    inp = make(at(10, 0))
    inp.facts = replace(inp.facts, charge_factor=0.6)
    fc = F.build(inp, replace(ST, max_segment_min=10))
    assert all(s.hours <= 10 / 60 + 1e-9 for s in fc.segments)
    assert all(s.charge_factor == 0.6 for s in fc.segments)
    assert fc.segments[0].end == at(10, 10) or fc.segments[0].hours <= 10 / 60 + 1e-9


def test_gbp_to_pence_and_empty_prices():
    fc = F.build(make(at(10, 0)), ST)
    assert fc.segments[0].export_p == pytest.approx(15.0) and fc.segments[0].import_p == pytest.approx(30.28)
    inp = make(at(10, 0))
    inp.readings.rates, inp.readings.import_rate = [], None
    seg = F.build(inp, ST).segments[0]
    assert seg.import_p == F.FALLBACK_IMPORT_P
