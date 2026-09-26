import pytest

from pe_core import __version__
from pe_core.config import ConfigError, load_config, parse_config


def test_version_is_semver():
    parts = __version__.split("-")[0].split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_missing_file_means_unconfigured(tmp_path):
    assert load_config([str(tmp_path / "nope.yaml")]) == (None, None)


def test_defaults_are_safe():
    cfg = parse_config({})
    assert cfg.mode == "passive"
    assert cfg.remove_entities is False
    assert cfg.solar_plants == ()


def test_valid_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "inputs:\n"
        "  battery_soc: {entity: sensor.solis_battery_soc}\n"
        "  export_rate: {value: 0.15, unit: GBP/kWh}\n"
        "solar_plants:\n"
        "  - id: main\n"
        "    name: Solis (roof)\n"
        "    power: {entity: sensor.solis_pv_total_power}\n"
        "    energy_today: {entity: sensor.solis_power_generation_today}\n"
        "    forecast: solcast_site\n"
        "  - id: garage\n"
        "    power: {entity: sensor.solax_power}\n"
        "    energy_today: {entity: sensor.solax_yield_today}\n"
        "operation:\n"
        "  mode: active\n"
    )
    cfg, used = load_config([str(p)])
    assert used == str(p)
    assert cfg.mode == "active"
    assert set(cfg.inputs) == {"battery_soc", "export_rate"}
    assert cfg.features["axle"] is True and cfg.features["arbitrage"] is False
    assert [pl.id for pl in cfg.solar_plants] == ["main", "garage"]
    assert cfg.solar_plants[1].forecast == "none" and cfg.solar_plants[1].enabled is True
    assert cfg.solar_plants[1].name == "garage"


PLANT = {"id": "p1", "power": {"entity": "sensor.a"}, "energy_today": {"entity": "sensor.b"}}


@pytest.mark.parametrize(
    "data",
    [
        [1, 2],
        {"operation": {"mode": "on"}},
        {"inputs": {"battery_soc": {}}},
        {"inputs": {"battery_soc": {"entity": "sensor.a", "value": 1}}},
        {"inputs": {"not_a_role": {"entity": "sensor.a"}}},
        {"inputs": {"battery_soc": {"entity": "sensor.a", "invert": True}}},     # not a signed input
        {"inputs": {"battery_soc": {"value": 50}}},                              # must be an entity
        {"inputs": {"battery_capacity": {"value": "lots"}}},
        {"inputs": {"battery_soc": {"entity": "switch.a"}}},                     # wrong domain
        {"inputs": {"battery_soc": {"entity": "Not An Entity"}}},
        {"inputs": {"smart_target_soc": {"entity": "number.edf_x_intelligent_bump_charge"}}},
        {"features": {"teleport": True}},
        {"features": {"axle": "yes"}},
        {"schema_version": 99},
        # the app's own AppDaemon definition must never be mistaken for settings
        {"powerengine": {"module": "powerengine", "class": "PowerEngine"}},
        {"solar_plants": {"id": "x"}},
        {"solar_plants": [{**PLANT, "id": "Bad Id"}]},
        {"solar_plants": [PLANT, PLANT]},
        {"solar_plants": [{**PLANT, "forecast": "magic"}]},
        {"solar_plants": [{**PLANT, "power": {}}]},
        {"solar_plants": [{**PLANT, "enabled": "yes"}]},
    ],
)
def test_invalid_config_rejected(data):
    with pytest.raises(ConfigError):
        parse_config(data)


def test_bad_yaml_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("inputs: [unclosed\n")
    with pytest.raises(ConfigError):
        load_config([str(p)])


def test_signed_input_can_be_inverted():
    cfg = parse_config({"inputs": {"grid_power": {"entity": "sensor.solis_meter_active_power", "invert": True}}})
    assert cfg.inputs["grid_power"]["invert"] is True


def test_required_roles_follow_features():
    from pe_core.config import required_roles
    with_axle = required_roles(parse_config({}))
    without = required_roles(parse_config({"features": {"axle": False, "free_power_days": False}}))
    assert "axle_event_active" in with_axle and "axle_event_active" not in without
    assert "battery_soc" in without and "battery_soh" not in with_axle


def test_every_setting_is_in_exactly_one_config_page_section():
    from pe_core.config import SAFETY, SETTING_SECTIONS, settings_catalogue
    keys = [k for _, _, ks in SETTING_SECTIONS for k in ks]
    assert sorted(keys) == sorted(SAFETY) and len(keys) == len(set(keys))
    cat = settings_catalogue()
    assert sorted(k for s in cat["sections"] for k in s["keys"]) == sorted(s["key"] for s in cat["safety"])


def test_use_measured_capacity_flag():
    from pe_core.config import use_measured
    assert use_measured(parse_config({"inputs": {"battery_capacity": {"value": 18}}}), "battery_capacity")
    cfg = parse_config({"inputs": {"battery_capacity": {"value": 18, "use_measured": False}}})
    assert not use_measured(cfg, "battery_capacity")


def test_use_measured_only_on_measurable_inputs():
    import pytest

    from pe_core.config import ConfigError
    with pytest.raises(ConfigError, match="no measured figure"):
        parse_config({"inputs": {"battery_max_charge_power": {"value": 4800, "use_measured": True}}})
    with pytest.raises(ConfigError, match="true or false"):
        parse_config({"inputs": {"battery_capacity": {"value": 18, "use_measured": "yes"}}})


def test_round_trip_efficiency_input():
    from pe_core.config import use_measured
    from pe_core.planner import params_from
    assert params_from(parse_config({})).efficiency == 0.95
    cfg = parse_config({"inputs": {"battery_round_trip": {"value": 81, "use_measured": False}}})
    assert params_from(cfg).efficiency == 0.9 and not use_measured(cfg, "battery_round_trip")
    with pytest.raises(ConfigError, match="50 to 100"):
        parse_config({"inputs": {"battery_round_trip": {"value": 0.9}}})
