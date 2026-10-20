"""Engine v2's settings that say what you want (0.9.157): the migration of saved blocks, the derived tuning, the
profiles, the moved smart-slot odds and the catalogue."""

import pytest

from pe_core.config import parse_config
from pe_core.engine_v2 import settings as S
from pe_core.engine_v2 import value as V
from pe_core.engine_v2.settings import (
    BETA22_PROFILE,
    BETA_PROFILE,
    FIRMNESS_CURVES,
    SECTIONS,
    SETTINGS,
    SLOT_BOT_FACTOR,
    SettingsError,
    V2Settings,
    catalogue,
    parse_profiles,
    parse_v2,
    profile_values,
)

# a 2.2 beta block as an earlier release saved it
OLD_22 = {
    "level_top_start_soc": 90, "level_top_rate_p": 200.0, "level_top_doubling_pts": 3.0,
    "level_bot_start_soc": 70, "level_bot_rate_p": 10.0, "level_bot_doubling_pts": 5.0,
    "slot_bot_factor": 30.0, "slot_bot_start_soc": 80,
    "hold_only_car_reserve": True, "prefer_self_use": False,
    "wear_house_p": 1.0, "wear_sale_p": 3.0,
    "learn_scenario_weights": False, "learn_solar_bias": False,
    "comfort_cost_p": 0.3, "top_up_cost_p": 5.0, "solar_low_pct": 25,
}


# ---- saved blocks from before ----------------------------------------------------------------------------------
def test_an_old_22_beta_block_gives_the_new_values():
    s = parse_v2(OLD_22)
    assert (s.range_low_soc, s.range_high_soc) == (70, 90)
    assert s.range_firmness == "firm"                       # bottom rate 10 is firm's
    assert s.slot_floor_soc == 80
    assert s.hold_use == "car and reserve"
    assert s.wear_p == 3.0                                   # the larger of the two
    assert s.learn is False
    # what the engine reads is the same as before
    assert s.level_top_rate_p == 200.0 and s.level_bot_rate_p == 10.0 and s.slot_bot_factor == SLOT_BOT_FACTOR


def test_old_only_keys_are_ignored_without_error_and_leave_the_defaults():
    old_only = {k: OLD_22[k] for k in ("comfort_cost_p", "top_up_cost_p", "solar_low_pct")}
    old_only.update({"comfort_low_soc": 30, "load_high_pct": 30, "executor": "lines"})
    assert parse_v2(old_only) == V2Settings()
    assert parse_profiles({"mine": old_only}) == {"mine": {}}


def test_an_old_bottom_rate_of_zero_means_no_range_and_other_rates_the_nearest_firmness():
    assert parse_v2({"level_bot_rate_p": 0}).range_firmness == "off"
    assert parse_v2({"level_bot_rate_p": 2}).range_firmness == "gentle"
    assert parse_v2({"level_bot_rate_p": 60}).range_firmness == "strict"


def test_old_hold_flags_and_wear_translate():
    assert parse_v2({"prefer_self_use": True}).hold_use == "cheap times"
    assert parse_v2({"prefer_self_use": False}).hold_use == "any time"
    assert parse_v2({"hold_only_car_reserve": False}).hold_use == "cheap times"
    assert parse_v2({"wear_house_p": 2.0}).wear_p == 2.0


def test_an_explicit_new_key_wins_over_its_old_equivalent():
    s = parse_v2({**OLD_22, "range_low_soc": 60, "range_high_soc": 95, "range_firmness": "gentle",
                  "slot_floor_soc": 50, "hold_use": "cheap times", "wear_p": 0.5, "learn": True})
    assert (s.range_low_soc, s.range_high_soc, s.range_firmness) == (60, 95, "gentle")
    assert s.slot_floor_soc == 50 and s.hold_use == "cheap times" and s.wear_p == 0.5 and s.learn is True


def test_saved_profiles_are_migrated_too():
    got = parse_profiles({"mine": {"level_top_start_soc": 85, "hold_only_car_reserve": True}})
    assert got == {"mine": {"range_high_soc": 85, "hold_use": "car and reserve"}}


# ---- the derived tuning ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("firmness", S.FIRMNESS)
def test_firmness_sets_the_level_curves(firmness):
    s = V2Settings(range_firmness=firmness)
    top_rate, top_d, bot_rate, bot_d = FIRMNESS_CURVES[firmness]
    assert (s.level_top_rate_p, s.level_top_doubling_pts) == (top_rate, top_d)
    assert (s.level_bot_rate_p, s.level_bot_doubling_pts) == (bot_rate, bot_d)
    assert (s.level_top_start_soc, s.level_bot_start_soc) == (s.range_high_soc, s.range_low_soc)
    assert (top_rate == 0) is (firmness == "off")


def test_firmness_grows_from_off_to_strict():
    tops = [FIRMNESS_CURVES[f][0] for f in S.FIRMNESS]
    bots = [FIRMNESS_CURVES[f][2] for f in S.FIRMNESS]
    assert tops == sorted(tops) and bots == sorted(bots)


def test_hold_use_sets_the_two_old_flags():
    car = V2Settings(hold_use="car and reserve")
    cheap = V2Settings(hold_use="cheap times")
    anytime = V2Settings(hold_use="any time")
    assert (car.hold_only_car_reserve, car.prefer_self_use) == (True, False)
    assert (cheap.hold_only_car_reserve, cheap.prefer_self_use) == (False, True)
    assert (anytime.hold_only_car_reserve, anytime.prefer_self_use) == (False, False)


def test_slot_floor_sets_the_slot_factor_and_start():
    off, on = V2Settings(), V2Settings(slot_floor_soc=80)
    assert (off.slot_bot_factor, off.slot_bot_start_soc) == (1.0, 0)
    assert (on.slot_bot_factor, on.slot_bot_start_soc) == (SLOT_BOT_FACTOR, 80)


def test_wear_and_learning_are_one_value_each():
    s = V2Settings(wear_p=2.5, learn=False)
    assert s.wear_house_p == s.wear_sale_p == 2.5
    assert s.learn_scenario_weights is False and s.learn_solar_bias is False
    assert V2Settings().learn_scenario_weights and V2Settings().learn_solar_bias


@pytest.mark.parametrize("caution", S.CAUTION)
def test_the_starting_weights_no_longer_depend_on_the_caution(caution):
    s = V2Settings(forecast_caution=caution)
    assert s.solar_weights == pytest.approx((0.25, 0.5, 0.25)) and s.load_weights == pytest.approx((0.25, 0.5, 0.25))


def test_the_caution_says_whose_needs_a_sale_keeps_for():
    assert V.KEEP_CASE["optimistic"] is None and V.KEEP_CASE["balanced"] == "mid" and V.KEEP_CASE["cautious"] == "high"
    assert V2Settings().forecast_caution == "balanced"


def test_the_old_names_are_properties_not_arguments():
    with pytest.raises(TypeError):
        V2Settings(level_top_rate_p=1.0)
    with pytest.raises(TypeError):
        V2Settings(prefer_self_use=False)
    assert not {"level_top_rate_p", "prefer_self_use", "wear_house_p"} & set(V2Settings().as_dict())


# ---- profiles --------------------------------------------------------------------------------------------------
def test_the_2_1_beta_profile_values():
    s = parse_v2({}, profile=profile_values(BETA_PROFILE))
    assert (s.range_low_soc, s.range_high_soc, s.range_firmness) == (70, 90, "firm")
    assert s.min_run_pct == 5 and s.slot_floor_soc == 80 and s.hold_use == "any time"
    assert s.level_top_rate_p == 200.0 and s.level_bot_rate_p == 10.0
    assert V.leg_band(s) == (70, 90, 5)


def test_the_2_2_beta_profile_values():
    s = parse_v2({}, profile=profile_values(BETA22_PROFILE))
    assert (s.range_low_soc, s.range_high_soc, s.range_firmness) == (70, 90, "gentle")
    assert s.min_run_pct == 5 and s.slot_floor_soc == 80
    assert s.hold_use == "car and reserve" and s.free_arbitrage is True and s.charge_max_import_p == 15.0


def test_the_default_profile_is_empty_and_gentle():
    assert profile_values("defaults") == {}
    s = parse_v2({})
    assert s.range_firmness == "gentle" and (s.range_low_soc, s.range_high_soc) == (20, 90)
    assert s.min_run_pct == 0 and s.slot_floor_soc == 0 and s.slot_odds_pct == 60 and s.hold_use == "cheap times"


# ---- smart-slot odds moved from v1's safety block --------------------------------------------------------------
def config(extra):
    raw = {"operation": {"mode": "passive"}, "system": {"engine": "v2", "control_method": "ram_remote"}}
    raw.update(extra)
    return parse_config(raw)


def test_a_saved_safety_slot_certainty_seeds_the_v2_odds_without_error():
    cfg = config({"safety": {"smart_slot_certainty_pct": 90}})
    assert cfg.engine_v2.slot_odds_pct == 90
    assert "smart_slot_certainty_pct" not in cfg.safety


def test_an_explicit_v2_odds_setting_wins_over_the_saved_safety_one():
    cfg = config({"safety": {"smart_slot_certainty_pct": 90}, "engine_v2": {"slot_odds_pct": 70}})
    assert cfg.engine_v2.slot_odds_pct == 70


def test_no_saved_odds_is_sixty():
    assert config({}).engine_v2.slot_odds_pct == 60


# ---- the catalogue and the checks ------------------------------------------------------------------------------
def test_the_catalogue_sections_and_every_setting_in_exactly_one():
    cat = catalogue()
    assert [s["key"] for s in cat["sections"]] == ["allowed", "range", "trading", "forecast", "advanced"]
    listed = [k for s in cat["sections"] for k in s["keys"]]
    assert sorted(listed) == sorted(SETTINGS) and len(listed) == len(set(listed))
    assert [k for _, _, keys in SECTIONS for k in keys] == listed
    rows = {r["key"]: r for r in cat["settings"]}
    assert rows["range_firmness"]["options"] == list(S.FIRMNESS) and rows["hold_use"]["options"] == list(S.HOLD_USE)
    assert rows["forecast_caution"]["options"] == list(S.CAUTION)


def test_a_range_whose_bottom_is_not_below_its_top_is_refused():
    with pytest.raises(SettingsError):
        parse_v2({"range_low_soc": 90, "range_high_soc": 90})
    with pytest.raises(SettingsError):
        parse_v2({"range_low_soc": 95, "range_high_soc": 80})
    assert parse_v2({"range_low_soc": 89, "range_high_soc": 90}).range_low_soc == 89


def test_a_choice_must_be_one_of_its_options():
    for key, bad in (("range_firmness", "hard"), ("hold_use", "never"), ("forecast_caution", "bold")):
        with pytest.raises(SettingsError):
            parse_v2({key: bad})


def test_the_shortest_run_only_acts_when_set():
    assert V.leg_band(V2Settings()) is None
    assert V.leg_band(V2Settings(min_run_pct=15)) == (20, 90, 15)
    assert V.leg_need((20, 90, 15), "charge", 80) == 10 and V.leg_need((20, 90, 15), "export", 50) == 15
