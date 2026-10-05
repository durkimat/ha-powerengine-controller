"""Engine v2: learning (docs/plans/engine-v2.md section 5.1 and 4)."""

import json
import random
from datetime import datetime, timedelta, timezone

from test_engine_v2_observe import readings, segment

from pe_core.engine_v2.learning import MODES, W_MAX, W_MIN, Learner, clamp_weights
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import Forecast, Observation

D0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def day_forecast(day, solar_mid=1.0, bias=None):
    """A day of half-hours: the raw sun (8 to 16) scaled by the learned bias, as forecast.build does."""
    segs = []
    for i in range(48):
        t = day + timedelta(minutes=30 * i)
        sun = solar_mid * (bias or {}).get(str(t.hour), 1.0) if 8 <= t.hour < 16 else 0.0
        segs.append(segment(t, solar=sun, load=0.3))
    return Forecast(made_at=day, segments=tuple(segs))


def obs_at(t, level=50.0):
    return Observation(now=t, level_reported=level, level_filtered=level, net_load_kw=0.0, car_charging=False,
                       data_ok=True)


def feed_day(ln, day, solar_factor=1.0, house_factor=1.0, hours=(0, 24), step=120, solar_mid=1.0):
    """The actual sun is `solar_factor` times the raw forecast (`solar_mid` kWh a half-hour)."""
    fc = day_forecast(day, solar_mid, ln.solar_bias())
    changed = []
    t = day + timedelta(hours=hours[0])
    end = day + timedelta(hours=hours[1])
    while t < end:
        sun_w = solar_mid * 2000.0 * solar_factor if 8 <= t.hour < 16 else 0.0
        r = readings(t, solar_power=sun_w, house_power=600.0 * house_factor)
        changed.append(ln.observe(t, obs_at(t), r, fc, timezone.utc))
        t += timedelta(seconds=step)
    return changed


def test_the_starting_weights_are_the_settings_until_something_is_seen():
    ln = Learner(V2Settings(solar_low_pct=20, solar_mid_pct=60, solar_high_pct=20))
    assert [round(x, 3) for x in ln.solar_weights("midday")] == [0.2, 0.6, 0.2]
    assert ln.diag()["weights"]["start"]["solar"] == [0.2, 0.6, 0.2]
    assert ln.diag()["weights"]["days"] == 0


def test_one_dull_day_moves_the_weights_only_a_little_because_of_the_prior():
    ln = Learner(V2Settings())
    feed_day(ln, D0, solar_factor=0.7)                  # the low figure all day
    w = ln.solar_weights("midday")
    assert 0.25 < w[0] < 0.36 and abs(sum(w) - 1) < 1e-9
    assert ln.days == 1


def test_a_fortnight_of_dull_days_makes_the_low_weight_heavy_but_never_certain():
    ln = Learner(V2Settings(learn_solar_bias=False))          # the bias would learn the centre and leave the spread
    for d in range(14):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.7, hours=(7, 17))
    for part in ("morning", "midday", "afternoon"):
        w = ln.solar_weights(part)
        assert w[0] > 0.5 and all(W_MIN - 1e-9 <= x <= W_MAX + 1e-9 for x in w) and abs(sum(w) - 1) < 1e-9
    for d in range(14, 40):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.7, hours=(9, 11))
    assert max(ln.solar_weights("midday")) <= W_MAX + 1e-9 and min(ln.solar_weights("midday")) >= W_MIN - 1e-9


def test_recent_days_count_more_than_old_ones():
    ln = Learner(V2Settings(scenario_half_life_days=5, scenario_prior_days=1, learn_solar_bias=False))
    for d in range(10):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.7, hours=(8, 16))
    for d in range(10, 30):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=1.2, hours=(8, 16))
    w = ln.solar_weights("midday")
    assert w[2] > w[0] + 0.3


def test_the_load_weights_learn_from_all_day():
    ln = Learner(V2Settings(scenario_prior_days=1))
    for d in range(5):
        feed_day(ln, D0 + timedelta(days=d), house_factor=1.3)
    w = ln.load_weights()
    assert w[2] > 0.5


def test_nothing_is_learned_when_the_switch_is_off():
    ln = Learner(V2Settings(learn_scenario_weights=False, learn_solar_bias=False, learn_soc_offset=False))
    for d in range(3):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.7, house_factor=1.3, hours=(8, 16))
    assert ln.solar_weights("midday") == clamp_weights([0.25, 0.5, 0.25])
    assert ln.load_weights() == clamp_weights([0.25, 0.5, 0.25])
    assert ln.solar_bias() == {} and ln.soc_offset() == {m: 0.0 for m in MODES}


def test_a_dropout_longer_than_two_minutes_throws_the_half_hour_away():
    ln = Learner(V2Settings())
    fc = day_forecast(D0)
    t = D0 + timedelta(hours=10)
    for i in range(0, 25):                              # 12 minutes of data
        t = D0 + timedelta(hours=10, seconds=30 * i)
        ln.observe(t, obs_at(t), readings(t, solar_power=2000.0, house_power=600.0), fc, timezone.utc)
    t = t + timedelta(minutes=10)                       # a gap
    for i in range(0, 25):
        ln.observe(t + timedelta(seconds=30 * i), obs_at(t), readings(t, solar_power=2000.0, house_power=600.0),
                   fc, timezone.utc)
    assert ln.solar == {} and ln.days == 0


# ---- the solar bias ----------------------------------------------------------------------------
def test_solar_bias_is_actual_over_forecast_per_hour():
    ln = Learner(V2Settings())
    for d in range(3):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.8, hours=(8, 16))
    bias = ln.solar_bias()
    assert set(bias) == {str(h) for h in range(8, 16)}
    assert all(abs(v - 0.8) < 0.03 for v in bias.values())


def test_solar_bias_is_kept_within_half_to_one_and_a_half():
    ln = Learner(V2Settings())
    feed_day(ln, D0, solar_factor=0.1, hours=(9, 11))
    feed_day(ln, D0 + timedelta(days=1), solar_factor=3.0, hours=(12, 14))
    bias = ln.solar_bias()
    assert bias["9"] == 0.5 and bias["12"] == 1.5


def test_the_bias_does_not_run_away_once_the_forecast_carries_it():
    ln = Learner(V2Settings())
    for d in range(12):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.8, hours=(8, 16))
    assert all(abs(v - 0.8) < 0.03 for v in ln.solar_bias().values())


def test_only_the_last_fourteen_days_count_for_the_bias():
    ln = Learner(V2Settings())
    for d in range(30):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.6 if d < 10 else 1.0, hours=(10, 11), step=60)
    assert len(ln.by_date) <= 14
    assert abs(ln.solar_bias()["10"] - 1.0) < 0.02


# ---- load spread -------------------------------------------------------------------------------
def test_load_spread_is_the_20th_and_80th_percentile_of_the_residual():
    ln = Learner(V2Settings())
    factors = [0.6, 0.8, 1.0, 1.2, 1.4, 1.6]
    for d, f in enumerate(factors):
        feed_day(ln, D0 + timedelta(days=d), house_factor=f, hours=(10, 11), step=60)
    spread = ln.load_spread()
    assert "20" in spread
    lo, hi = spread["20"]
    assert lo < 0 < hi and abs(lo) < 0.15 and hi < 0.2           # the residual is 0.3 kWh x (factor - 1) at most
    # a view with both key styles for the forecast layer
    view = ln.learned()["load_spread"]
    assert view["20"] == view[20]


def test_load_spread_needs_a_few_samples():
    ln = Learner(V2Settings())
    for d in range(3):
        feed_day(ln, D0 + timedelta(days=d), house_factor=1.3, hours=(10, 11), step=60)
    assert ln.load_spread() == {}


# ---- the level offset --------------------------------------------------------------------------
def offset_cycle(ln, t0, charging_level=93.0, holding_level=94.0, charge_w=-4000.0):
    t = t0
    for _ in range(6):                                          # charging, reading a point low
        ln.observe(t, obs_at(t, charging_level), readings(t, battery_power=charge_w), None, timezone.utc)
        t += timedelta(seconds=30)
    for _ in range(14):                                         # then holding, reading the true level
        ln.observe(t, obs_at(t, holding_level), readings(t, battery_power=0.0), None, timezone.utc)
        t += timedelta(seconds=30)
    return t


def test_the_offset_while_charging_is_learned_from_the_jump_at_the_change():
    ln = Learner(V2Settings())
    t = D0
    for _ in range(20):
        t = offset_cycle(ln, t)
    assert -1.0 <= ln.offsets["charging"] < -0.8
    assert ln.offsets["holding"] == 0.0 and ln.offsets["discharging"] == 0.0
    assert ln.soc_offset()["charging"] == round(ln.offsets["charging"], 2)


def test_a_jump_with_much_energy_flowing_or_too_big_is_not_learned():
    ln = Learner(V2Settings())
    t = D0
    for _ in range(6):                                          # holding, then charging hard
        ln.observe(t, obs_at(t, 50.0), readings(t, battery_power=0.0), None, timezone.utc)
        t += timedelta(seconds=30)
    for _ in range(14):
        ln.observe(t, obs_at(t, 52.0), readings(t, battery_power=-4800.0), None, timezone.utc)
        t += timedelta(seconds=30)
    assert ln.offsets["charging"] == 0.0
    ln2 = Learner(V2Settings())
    offset_cycle(ln2, D0, charging_level=85.0, holding_level=94.0)
    assert ln2.offsets["charging"] == 0.0


# ---- has anything moved enough -----------------------------------------------------------------
def test_observe_says_changed_only_when_the_forecast_inputs_moved_enough():
    ln = Learner(V2Settings(scenario_prior_days=1))
    per_day = []
    for d in range(12):
        per_day.append(sum(feed_day(ln, D0 + timedelta(days=d), solar_factor=0.7, hours=(8, 16))))
    assert per_day[0] >= 1 and sum(per_day[-3:]) <= 2, per_day      # the first day moves it; settled, it is quiet
    again = feed_day(ln, D0 + timedelta(days=12), solar_factor=0.7, hours=(0, 4))     # night: nothing to learn
    assert not any(again)


def test_changes_are_reported_at_most_once_an_hour():
    ln = Learner(V2Settings(scenario_prior_days=0.1))
    flags = feed_day(ln, D0, solar_factor=0.7, hours=(8, 16))
    assert 1 <= sum(flags) <= 8


def test_a_quiet_start_is_not_a_change():
    ln = Learner(V2Settings())
    assert not any(feed_day(ln, D0, hours=(8, 10)))


# ---- weights bound and the published view ------------------------------------------------------
def test_clamp_weights_always_sums_to_one_within_the_bounds():
    rnd = random.Random(7)
    for _ in range(500):
        w = clamp_weights([rnd.random() ** 3 * 50 for _ in range(3)])
        assert abs(sum(w) - 1) < 1e-9 and all(W_MIN - 1e-9 <= x <= W_MAX + 1e-9 for x in w), w
    assert clamp_weights([0, 0, 0]) == [1 / 3] * 3 or abs(sum(clamp_weights([0, 0, 0])) - 1) < 1e-9


def test_the_diag_and_learned_views_have_the_card_and_forecast_shapes():
    ln = Learner(V2Settings())
    feed_day(ln, D0, solar_factor=0.9, hours=(8, 12))
    d = ln.diag()
    assert set(d) == {"weights", "solar_bias", "soc_offset"}
    assert set(d["weights"]) == {"solar", "load", "days", "start"} and set(d["weights"]["solar"]) == {
        "morning", "midday", "afternoon"}
    assert set(d["soc_offset"]) == {"charging", "holding", "discharging"}
    assert set(d["solar_bias"]) == {"days", "by_hour"}
    view = ln.learned()
    assert view["weights"]["solar"]["midday"] == view["solar_weights"]["midday"]
    assert len(view["load_weights"]) == 3


# ---- comfort -----------------------------------------------------------------------------------
def test_comfort_hours_above_and_below_the_band_and_the_cash_given_up():
    ln = Learner(V2Settings(comfort_low_soc=20, comfort_high_soc=90))
    t = D0
    for i in range(361):                                        # one hour at 95%
        ln.note_comfort(t + timedelta(seconds=10 * i), 95.0, 6.0, timezone.utc)
    t = t + timedelta(seconds=3610)
    for i in range(181):                                        # half an hour at 10%
        ln.note_comfort(t + timedelta(seconds=10 * i), 10.0, None, timezone.utc)
    row = ln.comfort_rows()[-1]
    assert row["date"] == "2026-09-01" and abs(row["hours_above"] - 1.0) < 0.05 and abs(row["hours_below"] - 0.5) < 0.05
    assert row["given_up"] == 0.06 and row["decisions_changed"] == 0


def test_comfort_keeps_a_fortnight_of_days():
    ln = Learner(V2Settings())
    for d in range(20):
        ln.note_comfort(D0 + timedelta(days=d), 50.0, None, timezone.utc)
    assert len(ln.comfort) == 14 and len(ln.comfort_rows()) == 7


# ---- state -------------------------------------------------------------------------------------
def test_state_is_json_safe_and_a_restored_learner_has_the_same_weights_and_does_not_flag_a_change():
    ln = Learner(V2Settings(scenario_prior_days=1))
    for d in range(4):
        feed_day(ln, D0 + timedelta(days=d), solar_factor=0.7, house_factor=1.2, hours=(8, 16))
    offset_cycle(ln, D0 + timedelta(days=4))
    st = json.loads(json.dumps(ln.state()))
    ln2 = Learner(V2Settings(scenario_prior_days=1), st)
    assert ln2.solar_weights("midday") == ln.solar_weights("midday")
    assert ln2.load_weights() == ln.load_weights() and ln2.solar_bias() == ln.solar_bias()
    assert ln2.offsets == ln.offsets and ln2.days == ln.days
    assert not any(feed_day(ln2, D0 + timedelta(days=5), hours=(0, 2)))
