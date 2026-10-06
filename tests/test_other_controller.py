"""The other_controller setting: none / predbat / other, derived when not chosen (0.9.108)."""
import sys
import types

import pytest

from pe_core.config import ConfigError, other_controller, parse_config, required_roles, settings_catalogue
from pe_core.modes import CHOOSE_CONTROLLER, effective_mode, guard_problems, guard_status

PREDBAT = {"guard_read_only": {"entity": "switch.predbat_set_read_only"}}
OTHER = {"guard_off_1": {"entity": "automation.other_battery_control"}}


def _cfg(inputs=None, choice=None, mode="active"):
    data = {"operation": {"mode": mode}, "inputs": inputs or {}}
    if choice is not None:
        data["system"] = {"other_controller": choice}
    return parse_config(data)


def test_setting_is_a_system_choice_in_the_control_section():
    entry = next(e for e in settings_catalogue()["system"] if e["key"] == "other_controller")
    assert entry["section"] == "control" and entry["default"] == ""
    assert [o[0] for o in entry["options"]] == ["none", "predbat", "other"]
    assert entry["help"] and "<<" not in entry["help"]


def test_parse_accepts_the_choices_and_rejects_others():
    for value in ("none", "predbat", "other"):
        assert _cfg(choice=value).system["other_controller"] == value
    with pytest.raises(ConfigError):
        _cfg(choice="bogus")


def test_derived_when_not_chosen():
    assert other_controller(_cfg(PREDBAT)) == "predbat"
    assert other_controller(_cfg({**PREDBAT, **OTHER})) == "predbat"
    assert other_controller(_cfg(OTHER)) == "other"
    assert other_controller(_cfg({"guard_read_only": {"entity": "switch.some_controller_read_only"}})) == "other"
    assert other_controller(_cfg()) == "unset"
    assert other_controller(None) == "unset"


def test_a_choice_wins_over_the_guards():
    assert other_controller(_cfg(PREDBAT, "none")) == "none"
    assert other_controller(_cfg(PREDBAT, "other")) == "other"
    assert other_controller(_cfg({}, "predbat")) == "predbat"


def test_derivation_writes_nothing_to_the_saved_config():
    cfg = _cfg(PREDBAT)
    other_controller(cfg)
    assert "system" not in cfg.raw or "other_controller" not in cfg.raw.get("system", {})


def test_none_needs_no_guards_and_active_is_allowed():
    cfg = _cfg(choice="none")
    assert guard_status(cfg, lambda e: None) == ([], [])
    m = effective_mode(cfg, guards=guard_problems(cfg, lambda e: None))
    assert (m.configured, m.effective) == ("active", "active")


def test_none_ignores_a_mapped_guard_in_the_wrong_state():
    cfg = _cfg({**PREDBAT, **OTHER}, "none")
    states = {"switch.predbat_set_read_only": "off", "automation.other_battery_control": "on"}
    assert guard_status(cfg, states.get) == ([], [])


@pytest.mark.parametrize("choice", ["predbat", "other", None])
def test_predbat_and_other_keep_todays_guard_behaviour(choice):
    cfg = _cfg({**PREDBAT, **OTHER}, choice)
    states = {"switch.predbat_set_read_only": "off", "automation.other_battery_control": "on"}
    problems, absent = guard_status(cfg, states.get)
    assert len(problems) == 2 and absent == []
    m = effective_mode(cfg, guards=problems)
    assert m.effective == "passive" and "another controller may be in charge" in m.reason
    safe = {"switch.predbat_set_read_only": "on", "automation.other_battery_control": "off"}
    assert guard_status(cfg, safe.get) == ([], [])
    assert guard_status(cfg, lambda e: None)[1] == ["switch.predbat_set_read_only", "automation.other_battery_control"]


def test_unset_with_no_guards_keeps_refusing_active_with_the_new_reason():
    cfg = _cfg()
    probs = guard_problems(cfg, lambda e: None)
    assert probs == [CHOOSE_CONTROLLER]
    m = effective_mode(cfg, guards=probs)
    assert m.effective == "passive"
    assert m.reason == ("Active refused: Choose whether another battery controller is installed "
                        "(Config page, Inverter control).")
    assert "redbat" not in m.reason


def test_guard_roles_are_never_required():
    for choice in ("none", "predbat", "other", None):
        assert not [k for k in required_roles(_cfg(choice=choice)) if k.startswith("guard_")]


# --- the app ---------------------------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.states, a.logs, a.notes, a.cleared = {}, [], [], []
    a.get_state = lambda eid, attribute=None: a.states.get(eid)
    a.log = lambda msg, level="INFO": a.logs.append(msg)
    a._notify = lambda event, msg: a.notes.append(msg)
    a._clear_notice = lambda key: a.cleared.append(key)
    return a


def test_with_none_a_missing_guard_is_silent(app):
    app.cfg = _cfg({**PREDBAT, **OTHER}, "none")
    problems, absent = guard_status(app.cfg, app._guard_state)
    app._note_absent_guards(absent)
    assert problems == [] and absent == [] and not app.logs and not app.notes


def test_a_missing_guard_notice_names_no_product(app):
    app.cfg = _cfg(PREDBAT, "predbat")
    app._note_absent_guards(["switch.predbat_set_read_only"])
    assert app.notes and all("Predbat" not in n[2] and "legacy" not in n[2].lower() for n in app.notes)
    assert all("Predbat" not in m for m in app.logs)


def test_publishes_the_derived_value_on_the_version_sensor(app):
    published = []
    app._publish_state = lambda key, state, attrs=None: published.append((key, attrs))
    app._names = lambda: {}
    app._real_config_exists = lambda: True
    app._demo = False
    app._site_attributes = lambda: {}
    for cfg, want in ((_cfg(PREDBAT), "predbat"), (_cfg(OTHER), "other"), (_cfg(), "unset"),
                      (_cfg(PREDBAT, "none"), "none")):
        app.cfg = cfg
        app._publish_names()
        assert published[-1][0] == "diag_version" and published[-1][1]["other_controller"] == want


def test_controller_switch_event_needs_no_input_select(app, tmp_path):
    reloads = []
    app.cfg = _cfg(choice="none", mode="passive")
    app._save_path = lambda: str(tmp_path / "config.yaml")
    app._reload = lambda: reloads.append(1)
    app._logbook = lambda msg: None
    app._on_set_control("pe_set_control", {"operation": "active"}, {})      # no input_select anywhere
    assert reloads == [1] and not app.notes
    assert all("redbat" not in m for m in app.logs)
    import inspect
    assert "input_select" not in inspect.getsource(sys.modules["powerengine"])
