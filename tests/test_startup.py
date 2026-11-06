"""Start-up smoke test: PowerEngine.initialize() runs against a do-nothing host (0.9.12 failed to start)."""
import shutil
import sys

import pytest

AD_METHODS = ("on_state", "on_event", "call_every", "call_later", "call_daily", "run_at", "run_minutely",
              "run_hourly", "run_once", "call_service", "fire_event", "set_state", "cancel_call",
              "off_state", "off_event", "get_app", "get_plugin_api", "get_ad_api",
              "mqtt_publish", "mqtt_subscribe", "get_timezone", "get_entity", "entity_exists")


class _Stub:
    """The host calls PowerEngine makes, each returning a harmless value."""

    def __getattr__(self, name):
        if name in AD_METHODS:
            return lambda *a, **k: None
        raise AttributeError(name)

    def get_state(self, *a, **k):
        return None


@pytest.fixture
def engine(monkeypatch, tmp_path):
    class Hass(_Stub):
        def log(self, *a, **k):
            pass

    sys.modules.pop("powerengine", None)
    import powerengine
    powerengine.PowerEngine = type("PowerEngine", (powerengine.PowerEngine, Hass), {})
    e = powerengine.PowerEngine()
    folder = tmp_path / "powerengine"
    folder.mkdir()
    return e, folder, powerengine


def test_initialize_runs_unconfigured(engine):
    e, folder, powerengine = engine
    e.args = {"settings_file": str(folder / "config.yaml")}          # no file: unconfigured
    e.initialize()
    assert e.cfg is None and hasattr(e, "damper")


def test_initialize_runs_with_a_config(engine):
    import pathlib
    e, folder, powerengine = engine
    src = pathlib.Path(__file__).parent / "fixtures_config.yaml"
    if src.exists():
        shutil.copy(src, folder / "config.yaml")
    else:
        (folder / "config.yaml").write_text("schema_version: 1\noperation: {mode: passive}\ninputs: {}\n")
    e.args = {"settings_file": str(folder / "config.yaml")}
    e.initialize()
    assert e.cfg is not None and e.damper.hold_until is not None


def test_cost_sensors_are_published_without_anything_set_up_first(engine, monkeypatch):
    """The cost sensors must not wait for a publisher handle (the integration's engine has none of its own)."""
    e, folder, powerengine = engine
    (folder / "config.yaml").write_text("schema_version: 1\noperation: {mode: passive}\ninputs: {}\n"
                                        "system: {}\n")
    e.args = {"settings_file": str(folder / "config.yaml")}
    e.initialize()
    e.costbook = object()
    e._months = []
    monkeypatch.setattr(e, "_scenario_params", lambda: None)
    monkeypatch.setattr(powerengine, "cost_entity_states", lambda *a, **k: {"cost_today": ("1.5", {})})
    seen = []
    monkeypatch.setattr(e, "_publish_if_changed", lambda key, state, attrs: seen.append((key, state)))
    e._publish_costs()
    assert seen == [("cost_today", "1.5")]
