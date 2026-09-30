"""Commands from HA in direct publishing mode (demo plan B2)."""
import sys
import types
from unittest import mock

import pytest

from pe_core.adapters.publish import DirectPublisher, MqttPublisher
from pe_core.commands import entity_ids, from_pe_command, from_service


class FakeHA:
    def __init__(self):
        self.states = {}
        self.calls = []

    def set_state(self, entity_id, state=None, attributes=None):
        self.calls.append((entity_id, state))
        self.states[entity_id] = state


@pytest.fixture
def app():
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")

    class Hass:
        def __getattr__(self, name):
            raise AttributeError(name)

        def log(self, *a, **k):
            pass
    hassapi.Hass = Hass
    mods = {n: types.ModuleType(n) for n in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass")}
    mods["appdaemon.plugins.hass.hassapi"] = hassapi
    with mock.patch.dict(sys.modules, mods):
        sys.modules.pop("powerengine", None)
        import powerengine
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        ha = FakeHA()
        e.cfg, e.mqtt, e.set_state = None, None, ha.set_state
        e.get_state = lambda eid=None, **k: ha.states.get(eid)
        e._publisher_obj = DirectPublisher(ha.set_state)
        e.ha = ha
        yield e
        sys.modules.pop("powerengine", None)


def call(app, domain, service, **data):
    app._on_call_service("call_service", {"domain": domain, "service": service, "service_data": data}, {})


def test_entity_id_shapes():
    assert entity_ids("switch.a") == ["switch.a"]
    assert entity_ids(["switch.a", "switch.b"]) == ["switch.a", "switch.b"]
    assert entity_ids("switch.a, switch.b") == ["switch.a", "switch.b"]
    assert entity_ids(None) == [] and entity_ids({"x": 1}) == [] and entity_ids(5) == []


def test_pause_switch_toggle_reaches_the_command_path_and_republishes(app):
    call(app, "switch", "turn_on", entity_id="switch.pe_ctl_pause")
    assert app.ha.states["switch.pe_ctl_pause"] == "on"           # the state its listeners (guards, pause) react to
    call(app, "switch", "toggle", entity_id=["switch.pe_ctl_pause"])
    assert app.ha.states["switch.pe_ctl_pause"] == "off"
    call(app, "switch", "toggle", entity_id="switch.pe_ctl_pause")
    call(app, "switch", "turn_off", entity_id="switch.pe_ctl_pause")
    assert app.ha.states["switch.pe_ctl_pause"] == "off"


def test_command_path_is_the_one_place(app):
    seen = []
    app._on_command = lambda key, value: seen.append((key, value))
    call(app, "switch", "turn_on", entity_id="switch.pe_ctl_pause")
    app._on_pe_command("pe_command", {"entity_id": "switch.pe_ui_right_align", "value": "OFF"}, {})
    assert seen == [("ctl_pause", "ON"), ("ui_right_align", "OFF")]


def test_select_option_is_validated(app):
    from pe_core.entities import ENTITIES
    options = next(e for e in ENTITIES if e.key == "ui_history_day").options["options"]
    call(app, "select", "select_option", entity_id="select.pe_ui_history_day", option=options[1])
    assert app.ha.states["select.pe_ui_history_day"] == options[1]
    call(app, "select", "select_option", entity_id="select.pe_ui_history_day", option="not an option")
    assert app.ha.states["select.pe_ui_history_day"] == options[1]
    call(app, "select", "select_next", entity_id="select.pe_ui_history_day")
    assert app.ha.states["select.pe_ui_history_day"] == options[2]
    call(app, "select", "select_previous", entity_id="select.pe_ui_history_day")
    assert app.ha.states["select.pe_ui_history_day"] == options[1]


def test_pe_command_event(app):
    app._on_pe_command("pe_command", {"entity_id": "switch.pe_ctl_pause", "value": "ON"}, {})
    assert app.ha.states["switch.pe_ctl_pause"] == "on"
    app._on_pe_command("pe_command", {"entity_id": "switch.pe_ctl_pause", "value": "toggle"}, {})
    assert app.ha.states["switch.pe_ctl_pause"] == "off"
    n = len(app.ha.calls)
    app._on_pe_command("pe_command", {"entity_id": "switch.pe_ctl_pause", "value": "banana"}, {})
    app._on_pe_command("pe_command", {"value": "ON"}, {})
    app._on_pe_command("pe_command", None, {})
    assert len(app.ha.calls) == n


def test_other_entities_and_services_are_ignored(app):
    call(app, "switch", "turn_on", entity_id="switch.kitchen_light")
    call(app, "switch", "turn_on", entity_id=["switch.kitchen_light", "light.pe_ctl_pause"])
    call(app, "switch", "turn_on")
    call(app, "light", "turn_on", entity_id="switch.pe_ctl_pause")            # wrong domain for the entity
    call(app, "switch", "reload", entity_id="switch.pe_ctl_pause")
    call(app, "sensor", "turn_on", entity_id="sensor.pe_diag_version")          # sensors take no commands
    app._on_call_service("call_service", None, {})
    assert app.ha.calls == []


def test_a_mixed_call_only_touches_ours(app):
    call(app, "switch", "turn_on", entity_id=["switch.kitchen_light", "switch.pe_ui_right_align"])
    assert app.ha.calls == [("switch.pe_ui_right_align", "on")]


def test_number_values_are_clamped():
    from pe_core.entities import EntityDef
    n = EntityDef("number", "x", "X", {"min": 1, "max": 10})
    known = {n.entity_id: n}
    got = lambda v: from_service("number", "set_value", {"entity_id": n.entity_id, "value": v}, lambda e: None, known)  # noqa: E731
    assert got(50) == [(n, "10")] and got(-3) == [(n, "1")] and got("4.5") == [(n, "4.5")] and got("abc") == []
    assert from_pe_command({"entity_id": n.entity_id, "value": 99}, lambda e: None, known) == [(n, "10")]


def test_listeners_only_in_direct_mode(app):
    # what initialize() registers: the call_service and pe_command listeners exist only when the publisher isn't MQTT
    events = []
    app.listen_event = lambda cb, name, **k: events.append(name)
    for pub, expected in ((DirectPublisher(app.ha.set_state), ["call_service", "pe_command"]),
                          (MqttPublisher(types.SimpleNamespace(mqtt_publish=lambda *a, **k: None)), [])):
        events.clear()
        app._publisher_obj = pub
        app._listen_for_commands()
        assert events == expected


def test_costs_range_selects_take_day_options_only(app):
    from pe_core.history import RANGE_OPTIONS
    call(app, "select", "select_option", entity_id="select.pe_ui_cost_from", option="5 days ago")
    call(app, "select", "select_option", entity_id="select.pe_ui_cost_to", option="Yesterday")
    assert app.ha.states["select.pe_ui_cost_from"] == "5 days ago"
    assert app.ha.states["select.pe_ui_cost_to"] == "Yesterday"
    call(app, "select", "select_option", entity_id="select.pe_ui_cost_from", option="Today")     # not a whole day
    call(app, "select", "select_option", entity_id="select.pe_ui_cost_from", option="31 days ago")
    assert app.ha.states["select.pe_ui_cost_from"] == "5 days ago"
    app._on_pe_command("pe_command", {"entity_id": "select.pe_ui_cost_to", "value": RANGE_OPTIONS[3]}, {})
    assert app.ha.states["select.pe_ui_cost_to"] == RANGE_OPTIONS[3]


def test_publish_costs_hands_the_two_selects_to_the_waterfall(app):
    import powerengine
    seen = {}

    def fake_states(book, today, months, sp, custom):
        seen["custom"] = custom
        return {"cost_waterfall": (1.0, {"periods": {}})}
    app.costbook, app.mqtt, app._published, app._months = object(), object(), {}, []
    app._today = lambda: None
    app._publish_state = lambda key, state, attrs: seen.setdefault("published", key)
    app.ha.states.update({"select.pe_ui_cost_from": "7 days ago", "select.pe_ui_cost_to": "Yesterday"})
    with mock.patch.object(powerengine, "cost_entity_states", fake_states):
        app._on_cost_range("select.pe_ui_cost_from", "state", "3 days ago", "7 days ago", {})
    assert seen == {"custom": ("7 days ago", "Yesterday"), "published": "cost_waterfall"}
