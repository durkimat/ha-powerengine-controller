"""Engine v2 settings profiles: built in ("defaults", "2.1 beta") and saved by name, loaded by the config page."""

import json

import pytest

from pe_core.config import ConfigError, parse_config
from pe_core.engine_v2.settings import (
    BUILTIN_PROFILES,
    MAX_USER_PROFILES,
    PROFILE_KEYS,
    SETTINGS,
    SITE_KEYS,
    SettingsError,
    catalogue,
    parse_profiles,
    parse_v2,
    profile_values,
    profiles_catalogue,
)


def cfg(extra=None, engine="v2"):
    raw = {"operation": {"mode": "passive"}, "system": {"engine": engine, "control_method": "ram_remote"}}
    raw.update(extra or {})
    return parse_config(raw)


def test_a_built_in_profile_holds_tuning_only():
    assert BUILTIN_PROFILES["defaults"] == {}
    assert set(BUILTIN_PROFILES["2.1 beta"]) <= set(PROFILE_KEYS)
    assert not set(PROFILE_KEYS) & SITE_KEYS and set(PROFILE_KEYS) | SITE_KEYS == set(SETTINGS)


def test_the_profile_in_use_gives_values_unless_you_changed_them():
    c = cfg({"v2_profile": "2.1 beta", "engine_v2": {"top_up_cost_p": 4}})
    assert c.v2_profile == "2.1 beta" and c.engine_v2.executor == "plan" and c.engine_v2.top_up_cost_p == 4
    assert cfg().v2_profile == "defaults" and cfg().engine_v2.executor == "lines"


def test_a_profile_beats_a_v1_seed_but_your_changes_beat_the_profile():
    saved = {"mine": {"arbitrage": 1}}
    with pytest.raises(ConfigError, match="can't hold"):
        cfg({"v2_profiles": saved})                     # a house setting is not a profile's
    mine = {"mine": {"price_band_p": 1.5, "top_up_cost_p": 2}}
    c = cfg({"v2_profile": "mine", "v2_profiles": mine, "engine_v2": {"price_band_p": 0.2}})
    assert c.engine_v2.price_band_p == 0.2 and c.engine_v2.top_up_cost_p == 2


def test_an_old_v2_1_config_is_engine_v2_with_the_beta_profile():
    c = cfg(engine="v2_1")
    assert c.system["engine"] == "v2" and c.v2_profile == "2.1 beta" and c.engine_v2.executor == "plan"
    assert c.engine_v2 == cfg({"v2_profile": "2.1 beta"}).engine_v2          # the same settings as the profile
    c = cfg({"v2_profile": "defaults"}, engine="v2_1")
    assert c.v2_profile == "defaults" and c.engine_v2.executor == "lines"     # a saved choice wins over the alias


def test_unknown_or_bad_profiles_are_refused():
    with pytest.raises(ConfigError, match="unknown engine v2 profile"):
        cfg({"v2_profile": "nope"})
    for name in ("", " x", "defaults", "2.1 BETA", "x" * 31):
        with pytest.raises(SettingsError):
            parse_profiles({name: {}})
    with pytest.raises(SettingsError, match="between"):
        parse_profiles({"a": {"price_band_p": 99}})
    with pytest.raises(SettingsError, match="at most"):
        parse_profiles({f"p{i}": {} for i in range(MAX_USER_PROFILES + 1)})
    assert parse_profiles({"a": {"reversal_cost_p": 3}}) == {"a": {}}         # a retired setting is dropped
    with pytest.raises(SettingsError):
        profile_values("missing", {})


def test_the_catalogue_lists_the_profiles_and_defaults_follow_the_one_in_use():
    saved = {"mine": {"price_band_p": 1.5}}
    cat, profiles = catalogue(parse_v2({}), "mine", saved), profiles_catalogue("mine", saved)
    assert next(r for r in cat["settings"] if r["key"] == "price_band_p")["default"] == 1.5
    assert profiles["profile"] == "mine" and profiles["saved"] == ["mine"]
    assert [b["name"] for b in profiles["builtin"]] == ["defaults", "2.1 beta", "2.2 beta"]
    assert "reserve_soc" in profiles["site_keys"]
    assert len(json.dumps(profiles_catalogue("x", {f"p{i}": {} for i in range(10)}))) < 4000


def test_the_2_2_beta_profile_is_2_1_beta_plus_the_new_settings_and_they_are_neutral_elsewhere():
    from pe_core.engine_v2.settings import BETA22_PROFILE, BETA_PROFILE, BUILTIN_PROFILES, V2Settings
    new = {"hold_only_car_reserve", "free_arbitrage", "reversal_levy_p", "charge_max_import_p"}
    p22, p21 = BUILTIN_PROFILES[BETA22_PROFILE], BUILTIN_PROFILES[BETA_PROFILE]
    assert {k: v for k, v in p22.items() if k not in new} == p21
    assert new <= set(p22) and p22["reversal_levy_p"] == 4.0
    assert p22["charge_max_import_p"] == 15.0
    d = V2Settings()
    assert (d.hold_only_car_reserve, d.free_arbitrage, d.reversal_levy_p,
            d.charge_max_import_p) == (False, False, 0.0, 0.0)
