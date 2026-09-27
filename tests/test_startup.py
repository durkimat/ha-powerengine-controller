"""Start-up smoke test: PowerEngine.initialize() runs against a do-nothing AppDaemon (0.9.12 failed to start)."""
import shutil
import sys
import types

import pytest

AD_METHODS = ("listen_state", "listen_event", "run_every", "run_in", "run_daily", "run_at", "run_minutely",
              "run_hourly", "run_once", "call_service", "fire_event", "set_state", "cancel_timer",
              "cancel_listen_state", "cancel_listen_event", "get_app", "get_plugin_api", "get_ad_api",
              "mqtt_publish", "mqtt_subscribe", "get_timezone", "get_entity", "entity_exists")


class _Stub:
    """The AppDaemon calls PowerEngine makes, each returning a harmless value."""

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

    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = Hass
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
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
