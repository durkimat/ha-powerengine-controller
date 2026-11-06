"""What PowerEngine knows about other integrations' entities is data, not code (adapters/integration_data.py)."""
import json
import pathlib
import re

import pytest
import yaml

from pe_core.adapters import integration_data as data
from pe_core.adapters.detect import DETECT
from pe_core.roles import ROLE_BY_KEY, ROLES, catalogue, roles_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/golden/suggestions_and_detection.json").read_text())


def test_the_suggestions_and_the_detection_table_are_exactly_what_they_were_when_they_were_code():
    """Moving them into data files changed nothing: the same patterns, in the same order, for every role."""
    now = {r.key: {"suggest": list(r.suggest), "suggest_not": list(r.suggest_not)} for r in ROLES
           if r.suggest or r.suggest_not}
    assert now == GOLDEN["roles"]
    assert json.loads(json.dumps(DETECT)) == GOLDEN["detect"]


def test_every_integration_file_is_valid_and_names_known_roles():
    files = sorted((ROOT / "engine/pe_core/adapters/integrations").glob("*.yaml"))
    assert {f.stem for f in files} == {"axle", "edf", "octopus", "solcast", "zappi"}
    for f in files:
        d = data.parse(yaml.safe_load(f.read_text()), f.name)
        assert d["name"] == f.stem
        assert set(d["roles"]) <= set(ROLE_BY_KEY), f.name
    assert set(data.role_suggestions()) <= set(ROLE_BY_KEY)


def test_a_file_that_cannot_be_used_is_refused_naming_the_problem():
    good = yaml.safe_load((ROOT / "engine/pe_core/adapters/integrations/axle.yaml").read_text())

    def bad(**change):
        return data.parse({**good, **change}, "x.yaml")
    for change, text in (({"part": "toaster"}, "part must be one of"), ({"name": ""}, "name is required"),
                         ({"extra": 1}, "unknown key"), ({"roles": []}, "roles must be a mapping"),
                         ({"detect": {"integration": {"name": "A"}}}, "detect needs exactly"),
                         ({"roles": {"r": {"suggest": ["(unclosed"]}}}, "not a regular expression"),
                         ({"roles": {"r": {"suggest": "x"}}}, "list of strings"),
                         ({"roles": {"r": {"hint": []}}}, "roles.r is")):
        with pytest.raises(data.IntegrationDataError, match=text):
            bad(**change)


def test_an_integration_file_suggesting_for_an_unknown_role_is_refused(monkeypatch):
    unknown = {"no_such_role": {"suggest": (), "suggest_not": ()}}
    monkeypatch.setattr("pe_core.roles.integration_suggestions", lambda: unknown)
    with pytest.raises(ValueError, match="no_such_role"):
        roles_for()


def test_the_python_names_no_integration_and_no_entity():
    """The code has no entity-id patterns and no brand of integration: they are in the data files."""
    domains = "sensor|number|select|button|switch|binary_sensor|event"
    entity = re.compile(rf"({domains})(\\\.|\.)(solis|edf|myenergi|solcast|axle|octopus)")
    brand = re.compile(r"edf_energy|myenergi|solcast_pv|axle_vpp|octopus_energy|solax_modbus", re.I)
    for path in (ROOT / "engine/pe_core/roles.py", ROOT / "engine/pe_core/adapters/detect.py",
                 ROOT / "engine/pe_core/adapters/integration_data.py", ROOT / "card/ha-powerengine-card.js"):
        text = path.read_text()
        assert not entity.search(text), (path.name, entity.search(text).group(0))
        assert not brand.search(text), (path.name, brand.search(text).group(0))


def test_the_catalogue_carries_the_first_plants_suggestions_from_the_inverters_definition():
    cat = catalogue()
    assert cat["plant_suggest"]["power"] and all(re.compile(p) for p in cat["plant_suggest"]["energy_today"])
