"""The HA handover package: two controllers only, and the order of each handover is the safe one."""
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parents[1] / "docs" / "ha" / "powerengine_handover.yaml"


def _load():
    return yaml.safe_load(PKG.read_text())


def _steps(script):
    out = []
    for s in _load()["script"][script]["sequence"]:
        if "action" in s:
            out.append((s["action"], s.get("target", {}).get("entity_id")))
        elif "event" in s:
            out.append(("event:" + s["event"], s["event_data"]["operation"]))
    return out


def test_two_controllers_predbat_first():
    assert _load()["input_select"]["battery_controller"]["options"] == ["Predbat", "PowerEngine"]


def test_to_predbat_makes_powerengine_passive_before_releasing_predbat():
    steps = _steps("battery_handover_to_predbat")
    passive = steps.index(("event:pe_set_control", "passive"))
    unpause = steps.index(("switch.turn_off", "switch.pe_ctl_pause"))
    release = steps.index(("switch.turn_off", "switch.predbat_set_read_only"))
    assert passive == 0 and passive < unpause < release
    assert any(a == "automation.turn_off" for a, _ in steps)
    assert not any(a == "automation.turn_on" for a, _ in steps)


def test_to_powerengine_locks_predbat_then_goes_active():
    steps = _steps("battery_handover_to_powerengine")
    lock = steps.index(("switch.turn_on", "switch.predbat_set_read_only"))
    resume = steps.index(("switch.turn_off", "switch.pe_ctl_pause"))
    active = steps.index(("event:pe_set_control", "active"))
    assert steps[0][0] == "automation.turn_off" and lock < resume < active


def test_selector_uses_own_scripts_only():
    text = PKG.read_text()
    assert "predbat_handover_to_" not in text
    assert "Legacy automations" not in text
    assert set(_load()["script"]) == {"battery_handover_to_powerengine", "battery_handover_to_predbat"}
