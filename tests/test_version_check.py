"""Spotting a newly installed version so HA can restart AppDaemon (0.8.3)."""
import sys
import types

from pe_core import __version__
from pe_core.version import installed_version


def test_installed_version(tmp_path):
    f = tmp_path / "__init__.py"
    f.write_text('"""doc"""\n\n__version__ = "9.9.9"\n')
    assert installed_version(str(f)) == "9.9.9"
    assert installed_version(str(tmp_path / "missing.py")) is None


def test_the_real_file_matches_the_running_version():
    import pe_core
    assert installed_version(pe_core.__file__) == __version__


def test_update_event_fires_once(monkeypatch):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    fired = []
    a.fire_event = lambda ev, **kw: fired.append((ev, kw))
    a.log = lambda *args, **kw: None
    monkeypatch.setattr(powerengine, "installed_version", lambda path: __version__)
    a._check_update({})
    assert fired == []
    monkeypatch.setattr(powerengine, "installed_version", lambda path: "99.0.0")
    a._check_update({})
    a._check_update({})
    assert fired == [("pe_update_installed", {"running": __version__, "installed": "99.0.0"})]
