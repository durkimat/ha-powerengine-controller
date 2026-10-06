"""The HA packages: the main one needs no other controller; the optional one is the Predbat handover."""
from pathlib import Path

import yaml

HA = Path(__file__).resolve().parents[1] / "docs" / "ha"
PKG = HA / "powerengine_handover.yaml"
PREDBAT_PKG = HA / "powerengine_predbat_handover.yaml"


def _load(path=PREDBAT_PKG):
    return yaml.safe_load(path.read_text())


def _steps(script):
    out = []
    for s in _load()["script"][script]["sequence"]:
        if "action" in s:
            out.append((s["action"], s.get("target", {}).get("entity_id")))
        elif "event" in s:
            out.append(("event:" + s["event"], s["event_data"]["operation"]))
    return out


def test_main_package_names_no_predbat_entity_and_no_owner_automation():
    text = PKG.read_text().lower()
    for bad in ("predbat", "battery_controller", "input_select", "charge_house_battery", "house_battery_start",
                "house_battery_stop", "legacy"):
        assert bad not in text, bad
    assert "automation.turn_off" not in text


def test_main_package_holds_what_powerengine_itself_needs():
    d = _load(PKG)
    assert set(d) == {"script", "automation"}
    assert set(d["script"]) == {"powerengine_update"}
    assert [a["id"] for a in d["automation"]] == ["powerengine_watchdog", "powerengine_restart_after_update",
                                                  "powerengine_restart_if_stopped"]


def test_watchdog_depends_on_powerengine_not_on_a_selector():
    wd = next(a for a in _load(PKG)["automation"] if a["id"] == "powerengine_watchdog")
    cond = str(wd["conditions"])
    assert "sensor.pe_state_operation_mode" in cond and "'active'" in cond and "input_select" not in cond


def test_two_controllers_predbat_first():
    assert _load()["input_select"]["battery_controller"]["options"] == ["Predbat", "PowerEngine"]


def test_to_predbat_makes_powerengine_passive_before_releasing_predbat():
    steps = _steps("battery_handover_to_predbat")
    passive = steps.index(("event:pe_set_control", "passive"))
    unpause = steps.index(("switch.turn_off", "switch.pe_ctl_pause"))
    release = steps.index(("switch.turn_off", "switch.predbat_set_read_only"))
    assert passive == 0 and passive < unpause < release
    assert not any(a.startswith("automation.") for a, _ in steps)


def test_to_powerengine_locks_predbat_then_goes_active():
    steps = _steps("battery_handover_to_powerengine")
    lock = steps.index(("switch.turn_on", "switch.predbat_set_read_only"))
    resume = steps.index(("switch.turn_off", "switch.pe_ctl_pause"))
    active = steps.index(("event:pe_set_control", "active"))
    assert steps[0] == ("switch.turn_on", "switch.predbat_set_read_only") and lock < resume < active
    assert not any(a.startswith("automation.") for a, _ in steps)


def test_optional_package_names_no_owner_automation():
    text = PREDBAT_PKG.read_text().lower()
    for bad in ("charge_house_battery", "house_battery_start", "house_battery_stop", "legacy"):
        assert bad not in text, bad


def test_selector_uses_own_scripts_only():
    text = PREDBAT_PKG.read_text()
    assert "predbat_handover_to_" not in text
    assert set(_load()["script"]) == {"battery_handover_to_powerengine", "battery_handover_to_predbat"}


def test_the_packages_do_not_define_the_same_thing_twice():
    a, b = _load(PKG), _load(PREDBAT_PKG)
    for domain in set(a) & set(b):
        if isinstance(a[domain], dict):
            assert not set(a[domain]) & set(b[domain])
        else:
            assert not {x["id"] for x in a[domain]} & {x["id"] for x in b[domain]}


def test_everything_is_named_powerengine():
    for path in (PKG, PREDBAT_PKG):
        d = _load(path)
        names = [a["alias"] for a in d["automation"]] + [x["alias"] for x in d["script"].values()]
        if "input_select" in d:
            names.append(d["input_select"]["battery_controller"]["name"])
        assert all(n.startswith("PowerEngine - ") for n in names), names


def test_ids_unchanged_and_automations_present():
    assert [a["id"] for a in _load()["automation"]] == ["battery_controller_selector", "powerengine_restart_predbat"]
    assert {"powerengine_restart_after_update", "powerengine_restart_if_stopped"} <= {
        a["id"] for a in _load(PKG)["automation"]}


def test_update_script_installs_then_restarts_and_the_auto_restart_stands_aside():
    pkg = _load(PKG)
    seq = pkg["script"]["powerengine_update"]["sequence"]
    text = str(seq)
    assert "update.install" in text and "hassio.addon_restart" in text and "a0d7b954_appdaemon" in text
    assert text.index("update.install") < text.index("hassio.addon_restart")
    auto = next(a for a in pkg["automation"] if a["id"] == "powerengine_restart_after_update")
    assert auto["conditions"][0]["entity_id"] == "script.powerengine_update"
