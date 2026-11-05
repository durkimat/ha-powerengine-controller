"""The HA package PowerEngine installs: the one-button update and the watchdog. It names no other controller."""
from pathlib import Path

import yaml

HA = Path(__file__).resolve().parents[1] / "docs" / "ha"
PKG = HA / "powerengine_handover.yaml"


def _load(path=PKG):
    return yaml.safe_load(path.read_text())


def test_package_names_no_other_controller_and_no_owner_automation():
    text = PKG.read_text().lower()
    for bad in ("predbat", "battery_controller", "input_select", "charge_house_battery", "house_battery_start",
                "house_battery_stop", "legacy"):
        assert bad not in text, bad
    assert "automation.turn_off" not in text


def test_package_holds_what_powerengine_itself_needs():
    d = _load()
    assert set(d) == {"script", "automation"}
    assert set(d["script"]) == {"powerengine_update"}
    assert [a["id"] for a in d["automation"]] == ["powerengine_watchdog"]


def test_watchdog_depends_on_powerengine_not_on_a_selector():
    wd = next(a for a in _load()["automation"] if a["id"] == "powerengine_watchdog")
    cond = str(wd["conditions"])
    assert "sensor.pe_state_operation_mode" in cond and "'active'" in cond and "input_select" not in cond


def test_everything_is_named_powerengine():
    d = _load()
    names = [a["alias"] for a in d["automation"]] + [x["alias"] for x in d["script"].values()]
    assert all(n.startswith("PowerEngine - ") for n in names), names


def test_update_script_installs_then_restarts_the_engine():
    text = str(_load()["script"]["powerengine_update"]["sequence"])
    assert "update.install" in text and "powerengine.restart_engine" in text
    assert text.index("update.install") < text.index("powerengine.restart_engine")
    assert "hassio" not in text and "appdaemon" not in text.lower()
