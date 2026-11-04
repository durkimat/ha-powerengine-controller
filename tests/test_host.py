"""The Host seam (pe_core/host.py): the engine reaches AppDaemon only through `self.host`, and the engine class does
not inherit from AppDaemon."""

import ast
import pathlib

from pe_core import host
from pe_core.compare.harness import _fake_appdaemon
from pe_core.host_appdaemon import AppDaemonHost

APP = pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine" / "powerengine.py"

# Everything the engine used to take from AppDaemon's Hass, by name. None of it may be used on `self` in the engine.
HASS_NAMES = set(host.METHODS) | {"args", "set_state", "get_plugin_api", "AD", "namespace", "timer_running",
                                  "entity_exists", "get_now", "listen_log", "run_at", "run_once", "run_hourly",
                                  "run_minutely", "cancel_listen_log", "get_app", "set_namespace", "get_ad_api"}


def _engine_class():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")


def test_the_protocol_the_appdaemon_host_and_the_fake_appdaemon_have_the_same_methods():
    protocol = {n for n, v in vars(host.Host).items()
                if not n.startswith("_") and (callable(v) or isinstance(v, property))}
    assert protocol == set(host.METHODS) | set(host.EXTRAS) | {"args"}
    for name in (*host.METHODS, *host.EXTRAS, "args"):
        assert hasattr(AppDaemonHost, name), name
    for name in host.METHODS:
        assert callable(getattr(_fake_appdaemon(), name)), f"the fake AppDaemon has no {name}"


def test_the_appdaemon_host_is_a_host():
    assert isinstance(AppDaemonHost(_fake_appdaemon()()), host.Host)


def test_the_appdaemon_host_calls_the_apps_method_as_it_is_at_that_moment():
    app = _fake_appdaemon()()
    h = AppDaemonHost(app)
    app.set_fake("sensor.a", "1")
    assert h.get_state("sensor.a") == "1"
    app.get_state = lambda *a, **k: "swapped"          # what a test does
    assert h.get_state("sensor.a") == "swapped"
    app.args = {"settings_file": "x"}
    assert h.args == {"settings_file": "x"}
    assert h.run_in(lambda kw: None, 5)                # a handle comes back through the host
    assert h.get_timezone() == "Europe/London"


def test_an_override_replaces_a_call_until_it_is_cleared():
    app = _fake_appdaemon()()
    h = AppDaemonHost(app)
    app.set_fake("sensor.a", "1")
    h.override(get_state=lambda *a, **k: "gate")
    assert h.get_state("sensor.a") == "gate" and h.method("get_state")() == "gate"
    h.clear_overrides()
    assert h.get_state("sensor.a") == "1"


def test_the_engine_does_not_inherit_from_appdaemon():
    cls = _engine_class()
    assert cls.bases == []
    names = {n.id for n in ast.walk(cls) if isinstance(n, ast.Name)}
    assert "hass" not in names, "the engine must not use AppDaemon's module"
    assert _only_setattr(cls)


def _only_setattr(cls):
    """super() is allowed only in __setattr__ (it records attributes), which calls object's."""
    ok = {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__setattr__"}
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef) and fn.name not in ok:
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "super":
                    return False
    return True


def test_the_engine_uses_appdaemons_methods_only_through_the_host():
    """No `self.get_state(...)`, `self.run_in(...)`, `self.AD` and the like in the engine."""
    cls = _engine_class()
    own = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    leaks = []
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"
                    and node.attr in HASS_NAMES and node.attr not in own and not isinstance(node.ctx, ast.Store)):
                leaks.append(f"{fn.name}: self.{node.attr} (line {node.lineno})")
    assert not leaks, "use self.host.<name> instead:\n" + "\n".join(leaks)


def test_the_appdaemon_app_is_the_engine_with_appdaemons_base():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    shell = [n for top in tree.body if isinstance(top, ast.If) for n in top.body
             if isinstance(n, ast.ClassDef) and n.name == "PowerEngine"]
    assert len(shell) == 1
    assert [ast.unparse(b) for b in shell[0].bases] == ["Engine", "hass.Hass"]


def test_the_engine_hands_itself_to_nothing_that_could_call_appdaemon_through_it():
    """Adapters were once given the app and called `ha.get_state()` on it, which the demo's replacement of the app's
    methods reached; now they get the host. `self` may go only to getattr/setattr/hasattr and the host itself."""
    allowed = {"getattr", "setattr", "hasattr", "AppDaemonHost", "isinstance"}
    leaks = []
    for node in ast.walk(_engine_class()):
        if isinstance(node, ast.Call):
            args = list(node.args) + [k.value for k in node.keywords]
            if any(isinstance(a, ast.Name) and a.id == "self" for a in args):
                callee = ast.unparse(node.func)
                if callee not in allowed:
                    leaks.append(f"line {node.lineno}: {callee}(self, ...)")
            if (isinstance(node.func, ast.Name) and node.func.id in ("getattr", "setattr", "hasattr") and node.args
                    and isinstance(node.args[0], ast.Name) and node.args[0].id == "self" and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value in HASS_NAMES):
                leaks.append(f"line {node.lineno}: {node.func.id}(self, {node.args[1].value!r})")
    assert not leaks, "\n".join(leaks)


def test_a_slow_call_to_home_assistant_is_logged_with_what_it_was(monkeypatch):
    import time

    from pe_core import host_appdaemon
    app = _fake_appdaemon()()
    logged = []
    app._hass_log = lambda msg, *a, **k: logged.append(msg)
    app.call_service = lambda service, **kw: time.sleep(0.03)
    h = AppDaemonHost(app)
    h.call_service("number/set_value", entity_id="number.x", value=1)
    assert logged == []                                              # quick calls say nothing
    monkeypatch.setattr(host_appdaemon, "SLOW_CALL_S", 0.01)
    h.call_service("number/set_value", entity_id="number.x", value=1)
    assert len(logged) == 1 and "call_service number/set_value number.x took 0." in logged[0]


def test_a_slow_engine_tick_says_where_the_time_went():
    import importlib
    import sys
    sys.path.insert(0, str(APP.parent))
    powerengine = importlib.import_module("powerengine")
    engine = object.__new__(powerengine.Engine)
    logged = []
    engine.__dict__["log"] = lambda msg, **k: logged.append(msg)
    started = powerengine.monotonic() - 12.0
    engine._note_slow_tick(started, {"read": 0.2, "step": 0.9, "publish": 1.4, "hand over": 11.8})
    assert logged == ["Slow engine tick: 12.0 s (read 0.2 s, step 0.7 s, publish 0.5 s, hand over 10.4 s, rest 0.2 s)"]
    logged.clear()
    engine._note_slow_tick(powerengine.monotonic() - 1.0, {"read": 0.2})
    assert logged == []
