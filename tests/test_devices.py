"""Read-only devices (docs/plans/multiple-devices.md, M1): config, readings, sensors, no change for one inverter."""
import pytest

from pe_core.adapters.definition import device_capabilities
from pe_core.config import ConfigError, parse_config
from pe_core.entities import device_entity, device_ref_from_entity
from pe_core.readings import read
from pe_core.status import entity_states

DEVICE = {"id": "garage", "adapter": "solis", "name": "Garage",
          "inputs": {"battery_soc": {"entity": "sensor.g_soc"},
                     "battery_power": {"entity": "sensor.g_bp", "invert": True},
                     "solar_power": {"entity": "sensor.g_pv"}}}


def cfg_with(devices):
    return parse_config({"devices": devices})


def test_no_devices_by_default():
    assert parse_config({}).devices == ()
    assert parse_config(None).devices == ()


def test_solis_capabilities():
    assert device_capabilities("solis") == {"solar": True, "battery": True, "drive": True}


def test_parses_a_read_only_device():
    (dev,) = cfg_with([DEVICE]).devices
    assert (dev.id, dev.adapter, dev.control, dev.name) == ("garage", "solis", "read_only", "Garage")
    assert dev.inputs["battery_power"]["invert"] is True


@pytest.mark.parametrize("bad, text", [
    ({**DEVICE, "id": "main"}, "main inverter"),
    ({**DEVICE, "id": "Bad Id"}, "'id' must be"),
    ({**DEVICE, "adapter": "nope"}, "adapter must be one of"),
    ({**DEVICE, "control": "controlled"}, "control must be read_only"),
    ({**DEVICE, "extra": 1}, "unknown key"),
    ({**DEVICE, "inputs": {"grid_power": {"entity": "sensor.x"}}}, "unknown input"),
    ({**DEVICE, "inputs": {"solar_power": {"entity": "sensor.x", "invert": True}}}, "can't be inverted"),
    ({**DEVICE, "inputs": {"battery_soc": {"entity": "not an entity"}}}, "not a valid entity id"),
])
def test_rejects_bad_devices(bad, text):
    with pytest.raises(ConfigError, match=text):
        cfg_with([bad])


def test_duplicate_ids_rejected():
    with pytest.raises(ConfigError, match="used twice"):
        cfg_with([DEVICE, DEVICE])


def test_readings_for_a_device_and_solar_total():
    cfg = cfg_with([DEVICE])
    watts = {"unit_of_measurement": "W"}
    states = {"sensor.g_soc": {"state": "55"}, "sensor.g_bp": {"state": "800", "attributes": watts},
              "sensor.g_pv": {"state": "1200", "attributes": watts}}
    r = read(cfg, lambda e: states.get(e, {"state": "unavailable"}))
    assert r.devices["garage"]["soc"] == 55
    assert r.devices["garage"]["battery_power"] == -800            # inverted
    assert r.devices["garage"]["solar_power"] == 1200
    assert r.solar_power == 1200                                    # counted in total solar


def test_only_mapped_inputs_are_read():
    dev = {"id": "shed", "adapter": "solis", "inputs": {"battery_soc": {"entity": "sensor.s_soc"}}}
    r = read(cfg_with([dev]), lambda e: {"state": "40"})
    assert r.devices == {"shed": {"soc": 40}}


def test_device_entity_ids_round_trip():
    for fname, suffix in (("soc", "soc"), ("battery_power", "battery_power"), ("solar_power", "solar_power")):
        ent = device_entity("garage_2", "Garage", fname)
        assert ent.entity_id == f"sensor.pe_state_dev_garage_2_{suffix}"
        assert device_ref_from_entity(ent.entity_id) == ("garage_2", fname)
    assert device_ref_from_entity("sensor.pe_state_solar_power") is None
    assert device_ref_from_entity("sensor.pe_state_dev_soc") is None


def test_device_sensors_are_published():
    cfg = cfg_with([DEVICE])
    states = {"sensor.g_soc": {"state": "55"}, "sensor.g_bp": {"state": "800"}, "sensor.g_pv": {"state": "1200"}}
    r = read(cfg, lambda e: states.get(e, {"state": "unavailable"}))
    out = entity_states(r, _mode())
    assert out["state_dev_garage_soc"] == (55, {})
    assert out["state_dev_garage_battery_power"][0] == -800
    assert out["state_dev_garage_solar_power"][0] == 1200


def test_no_device_sensors_without_devices():
    out = entity_states(read(parse_config({}), lambda e: {"state": "1"}), _mode())
    assert not [k for k in out if k.startswith("state_dev_")]


def _mode():
    from pe_core.modes import ModeDecision
    return ModeDecision(configured="passive", effective="passive", reason="test")
