"""Engine v2's settings are its own (0.9.168): nothing is read from v1, and a config saved before is migrated once."""
import yaml
from test_site import PLAIN, lines, make, saved  # noqa: F401  (make: the fixture that starts the app on a folder)

from pe_core.config import parse_config
from pe_core.engine_v2.settings import V2Settings, parse_v2, profile_values, split_block

V1 = {"features": {"arbitrage": True, "axle": False, "axle_plus_export": True, "free_power_days": False},
      "safety": {"min_reserve_soc": 15}}


def test_v1_settings_no_longer_move_engine_v2():
    cfg = parse_config({"features": {"arbitrage": True, "axle": False}, "safety": {"min_reserve_soc": 20}})
    assert cfg.engine_v2 == V2Settings()


def test_the_split_writes_what_v2_was_following_when_it_differs():
    block = split_block({"wear_p": 1.0}, V1["features"], V1["safety"])
    assert block == {"wear_p": 1.0, "arbitrage": True, "events": False, "free_power": False, "reserve_soc": 15}


def test_the_split_leaves_a_saved_value_a_profile_value_and_equal_values_alone():
    block = split_block({"arbitrage": False}, V1["features"], V1["safety"], {"events": True})
    assert block == {"arbitrage": False, "free_power": False, "reserve_soc": 15}      # events: the profile's
    same = split_block({}, {"arbitrage": False, "axle": True}, {"min_reserve_soc": 12})
    assert same == {}


def test_after_the_split_engine_v2_uses_exactly_what_it_followed_before():
    before = {"arbitrage": True, "events": False, "event_plus_export": True, "free_power": False, "reserve_soc": 15}
    after = parse_v2(split_block({}, V1["features"], V1["safety"], profile_values("defaults")))
    assert all(getattr(after, k) == v for k, v in before.items())


def test_an_old_config_is_migrated_once_with_a_backup(make):  # noqa: F811
    text = PLAIN + "features: {arbitrage: true, axle: false}\nsafety: {min_reserve_soc: 15}\n"
    app = make(text)
    data = saved(make)
    assert data["v2_split"] is True and data["engine_v2"] == {"arbitrage": True, "events": False, "reserve_soc": 15}
    assert (app.cfg.engine_v2.arbitrage, app.cfg.engine_v2.events, app.cfg.engine_v2.reserve_soc) == (True, False, 15)
    assert any(m.startswith("Engine v2 settings are now its own") for m in lines(app))
    # a restart does nothing more; then v1's setting can change without moving v2
    again = make(None)
    assert not any(m.startswith("Engine v2 settings are now") for m in lines(again))
    data["features"]["arbitrage"] = False
    make.config.write_text(yaml.safe_dump(data))
    assert make(None).cfg.engine_v2.arbitrage is True


def test_a_config_already_marked_is_left_alone(make):  # noqa: F811
    app = make(PLAIN + "v2_split: true\nfeatures: {arbitrage: true}\n")
    assert "engine_v2" not in saved(make) and app.cfg.engine_v2.arbitrage is False
