import pytest

from pe_core.entities import (
    AVAILABILITY_TOPIC,
    ENTITIES,
    EntityDef,
    discovery_payload,
    removal_messages,
    validate_definitions,
)


def test_definitions_follow_rules():
    validate_definitions()


def test_entity_ids_use_prefix():
    for ent in ENTITIES:
        assert ent.entity_id.split(".")[1].startswith("pe_")


def test_discovery_payload_shape():
    ent = ENTITIES[0]
    p = discovery_payload(ent, "9.9.9")
    assert p["unique_id"] == "powerengine_diag_version"
    assert p["default_entity_id"] == "sensor.pe_diag_version"
    assert p["device"]["identifiers"] == ["powerengine"] and p["device"]["sw_version"] == "9.9.9"
    assert p["availability_topic"] == AVAILABILITY_TOPIC
    assert ent.discovery_topic == "homeassistant/sensor/powerengine/diag_version/config"


def test_binary_sensor_payloads():
    ent = next(e for e in ENTITIES if e.component == "binary_sensor")
    p = discovery_payload(ent, "1")
    assert (p["payload_on"], p["payload_off"]) == ("ON", "OFF")


def test_removal_clears_everything():
    msgs = removal_messages()
    assert all(payload == "" for _, payload in msgs)
    topics = {t for t, _ in msgs}
    for ent in ENTITIES:
        assert ent.discovery_topic in topics and ent.state_topic in topics


@pytest.mark.parametrize(
    "bad",
    [
        (EntityDef("sensor", "version", "x"),),                                   # no group
        (EntityDef("sensor", "diag_a", "x"), EntityDef("sensor", "diag_a", "y")),  # duplicate
        (EntityDef("sensor", "cfg_x", "x", {"entity_category": "config"}),),       # HA rejects this
    ],
)
def test_validation_catches_mistakes(bad):
    with pytest.raises(ValueError):
        validate_definitions(bad)
