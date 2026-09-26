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
    return out


def test_two_controllers_predbat_first():
    assert _load()["input_select"]["battery_controller"]["options"] == ["Predbat", "PowerEngine"]


def test_to_predbat_pauses_before_releasing_predbat():
    steps = _steps("battery_handover_to_predbat")
    pause = steps.index(("switch.turn_on", "switch.pe_ctl_pause"))
    release = steps.index(("switch.turn_off", "switch.predbat_set_read_only"))
    assert pause < release
    assert any(a == "automation.turn_off" for a, _ in steps)
    assert not any(a == "automation.turn_on" for a, _ in steps)


def test_to_powerengine_locks_predbat_before_resuming():
    steps = _steps("battery_handover_to_powerengine")
    lock = steps.index(("switch.turn_on", "switch.predbat_set_read_only"))
    resume = steps.index(("switch.turn_off", "switch.pe_ctl_pause"))
    assert steps[0][0] == "automation.turn_off" and lock < resume


def test_selector_uses_own_scripts_only():
    text = PKG.read_text()
    assert "predbat_handover_to_" not in text
    assert "Legacy automations" not in text
