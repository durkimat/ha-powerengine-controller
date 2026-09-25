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
    assert cfg.dry_run is True
    assert cfg.remove_entities is False


def test_valid_inputs(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "inputs:\n"
        "  battery_soc: {entity: sensor.solis_battery_soc}\n"
        "  export_rate: {value: 0.15, unit: GBP/kWh}\n"
        "operation:\n"
        "  dry_run: false\n"
    )
    cfg, used = load_config([str(p)])
    assert used == str(p)
    assert cfg.dry_run is False
    assert set(cfg.inputs) == {"battery_soc", "export_rate"}


@pytest.mark.parametrize(
    "data",
    [
        [1, 2],
        {"operation": {"dry_run": "yes"}},
        {"inputs": {"x": {}}},
        {"inputs": {"x": {"entity": "sensor.a", "value": 1}}},
        {"schema_version": 99},
        # the app's own AppDaemon definition must never be mistaken for settings
        {"powerengine": {"module": "powerengine", "class": "PowerEngine"}},
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
