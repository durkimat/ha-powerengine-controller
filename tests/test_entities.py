import pytest

from pe_core.entities import ENTITIES, EntityDef, validate_definitions


def test_definitions_follow_rules():
    validate_definitions()


def test_entity_ids_use_prefix():
    for ent in ENTITIES:
        assert ent.entity_id.split(".")[1].startswith("pe_")


def test_an_entitys_unique_id_and_id_follow_its_key():
    ent = ENTITIES[0]
    assert ent.unique_id == "powerengine_diag_version" and ent.entity_id == "sensor.pe_diag_version"


def test_no_definition_names_an_mqtt_topic():
    for ent in ENTITIES:
        assert not any(k.endswith("_topic") for k in ent.options), ent.key


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


def test_ui_switch_is_a_plain_switch():
    ent = next(e for e in ENTITIES if e.key == "ui_right_align")
    assert ent.component == "switch" and ent.options["entity_category"] == "config"
