"""Discovery and retirement of the per-plant solar power sensors (issue #160)."""
import sys
import types

import pytest


@pytest.fixture
def engine(monkeypatch):
    sys.modules.pop("powerengine", None)
    import powerengine
    e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    return e, powerengine


class _FakeHA:
    """What the engine writes to Home Assistant: entity id -> the last state written."""

    def __init__(self):
        self.written = {}

    def set_state(self, entity_id, state=None, attributes=None, **kw):
        self.written[entity_id] = state


def _publishing(e):
    ha = _FakeHA()
    from pe_core.adapters.publish import DirectPublisher
    e._publisher_obj = DirectPublisher(ha.set_state)
    return ha


def _cfg(plants):
    from pe_core.config import SolarPlant
    return types.SimpleNamespace(solar_plants=tuple(
        SolarPlant(id=pid, name=name, power={}, energy_today={}, enabled=enabled)
        for pid, name, enabled in plants
    ))


def test_sync_solar_entities_discovers_enabled_plants(engine):
    e, powerengine = engine
    e.cfg = _cfg([("main", "Main", True), ("shed", "Shed", True), ("off", "Off", False)])
    ha = _publishing(e)
    e.get_state = lambda *a, **k: {}
    e._sync_solar_entities()
    assert "sensor.pe_state_solar_main_power" in ha.written and "sensor.pe_state_solar_shed_power" in ha.written
    assert "sensor.pe_state_solar_off_power" not in ha.written


def test_sync_solar_entities_retires_removed_or_disabled_plants(engine):
    e, powerengine = engine
    ha = _publishing(e)
    # HA already knows about a "roof" plant sensor from a previous run, and the total (never retired).
    e.get_state = lambda *a, **k: {
        "sensor.pe_state_solar_roof_power": "123",
        "sensor.pe_state_solar_power": "456",
        "sensor.pe_state_battery_power": "1",
    }
    e.cfg = _cfg([("main", "Main", True)])
    e._sync_solar_entities()
    assert "sensor.pe_state_solar_main_power" in ha.written
    assert ha.written["sensor.pe_state_solar_roof_power"] == "unavailable"
    assert "sensor.pe_state_solar_power" not in ha.written                              # the total is never touched


def test_sync_solar_entities_with_nothing_to_publish_to_is_a_noop(engine):
    e, powerengine = engine
    e.cfg = _cfg([("main", "Main", True)])
    e._sync_solar_entities()   # no way to write states: must not raise


def test_retired_entities_are_removed_only_where_home_assistant_still_has_them(engine, tmp_path):
    """The Costs custom-range selects (0.9.72-0.9.74) no longer exist: every start clears them."""
    from pe_core.entities import RETIRED_ENTITIES
    assert {e.entity_id for e in RETIRED_ENTITIES} == {"select.pe_ui_cost_from", "select.pe_ui_cost_to",
                                                   "select.pe_ui_history_plan", "select.pe_ui_history_day"}
    e, powerengine = engine
    e.cfg = None
    from pe_core.adapters.publish import DirectPublisher
    calls = []
    e._publisher_obj = DirectPublisher(lambda eid, **kw: calls.append((eid, kw.get("state"))))
    e.get_state = lambda eid=None, **k: "5 days ago" if eid == "select.pe_ui_cost_from" else None
    e._retire_old_entities()                   # only the one HA still has (no stray entity on a fresh install)
    assert calls == [("select.pe_ui_cost_from", "unavailable")]


def test_ui_defaults_ignore_keys_an_older_release_saved(engine, tmp_path):
    import json
    e, powerengine = engine
    e.cfg = None
    presets = []
    e._publisher_obj = types.SimpleNamespace(retains=True, preset=lambda key, value: presets.append(key))
    e._save_path = lambda: str(tmp_path / "config.yaml")
    (tmp_path / "ui.json").write_text(json.dumps({"right_align": True, "history": True, "cost_range": True}))
    e._ui_defaults()
    assert presets == []
    assert json.loads((tmp_path / "ui.json").read_text())["cost_range"] is True     # left alone, nothing reads it
