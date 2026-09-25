from fixtures import AXLE_19_20, BST, CONFIG, NOW, STATES, get_state

from pe_core.modes import ModeDecision
from pe_core.readings import read
from pe_core.status import MAX_STATE_LEN, entity_states, pence, summary

PASSIVE = ModeDecision("passive", "passive", "Passive")


def test_passive_summary_reads_naturally():
    text = summary(read(CONFIG, get_state(), NOW), PASSIVE, BST)
    assert text.startswith("PASSIVE. Battery")
    assert "Battery 71%, discharging 1.0 kW" in text
    assert "exporting 0.2 kW" in text
    assert "Import 30.28p, 6.99p from 00:00; export 15p" in text
    assert "Next smart slot 21:00" in text


def test_unconfigured_summary_gives_reason():
    m = ModeDecision("unconfigured", "unconfigured", "2 required input(s) not ready: battery_soc, grid_power")
    assert summary(None, m) == "UNCONFIGURED. 2 required input(s) not ready: battery_soc, grid_power"


def test_events_in_summary():
    st = {**STATES, **AXLE_19_20}
    assert "Axle event at 19:00–20:00" in summary(read(CONFIG, get_state(st), NOW), PASSIVE, BST)


def test_entity_states_cover_every_state_entity():
    from pe_core.entities import STATE_ENTITIES
    out = entity_states(read(CONFIG, get_state(), NOW), PASSIVE, BST)
    assert {e.key for e in STATE_ENTITIES} - {"state_decision", "state_activity"} == set(out)
    assert out["state_import_rate"][1]["pence"] == 30.28
    assert out["state_ev"][0] == "Plugged in"
    assert out["state_smart_charge"][0] == "Next slot 21:00"
    assert all(len(str(state)) <= MAX_STATE_LEN for state, _ in out.values())


def test_pence_formatting():
    assert pence(0.15) == "15p" and pence(0.069930) == "6.99p"


def test_decision_leads_the_summary():
    from pe_core.config import parse_config
    from pe_core.decide import decide
    r = read(CONFIG, get_state(), NOW)
    d = decide(r, parse_config({}))
    text = summary(r, PASSIVE, BST, d)
    assert text.startswith("PASSIVE. Would self-use: nothing better to do at 30.28p")
    out = entity_states(r, PASSIVE, BST, d, since="17:50")
    assert out["state_decision"][0] == "self_use" and out["state_decision"][1]["since"] == "17:50"
