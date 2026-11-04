"""The Host seam (pe_core/host.py): the app reaches AppDaemon only through `self.host`."""

import ast
import pathlib

from pe_core import host
from pe_core.compare.harness import _fake_appdaemon

APP = pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine" / "powerengine.py"

# AppDaemon-specific code that still reaches AppDaemon itself (its internals, plugins, the demo's swap of the
# instance's methods). It moves into the AppDaemon host when the engine becomes its own process; nothing is added here.
ALLOWED_DIRECT = {
    "__setattr__", "_track", "_untrack", "_install_tracking", "_timer_gone", "_wipe", "_demo_setup", "log",
    "_rest_states_poster", "_lossless_set_state", "_quiet_set_state", "_plugin_configured", "_mqtt_api",
}
# Names the app inherits from AppDaemon's Hass that the host replaces. (set_state is the publishers' door and is
# listed above with the code that wraps it.)
HOST_NAMES = set(host.METHODS) | {"args", "set_state"}


def test_the_protocol_the_appdaemon_host_and_the_fake_appdaemon_have_the_same_methods():
    protocol = {n for n, v in vars(host.Host).items()
                if not n.startswith("_") and (callable(v) or isinstance(v, property))}
    assert protocol == set(host.METHODS) | {"args"}
    for name in host.METHODS:
        assert callable(getattr(host.AppDaemonHost, name)), name
        assert callable(getattr(_fake_appdaemon(), name)), f"the fake AppDaemon has no {name}"


def test_the_appdaemon_host_is_a_host():
    assert isinstance(host.AppDaemonHost(_fake_appdaemon()()), host.Host)


def test_the_appdaemon_host_calls_the_apps_method_as_it_is_at_that_moment():
    app = _fake_appdaemon()()
    h = host.AppDaemonHost(app)
    app.set_fake("sensor.a", "1")
    assert h.get_state("sensor.a") == "1"
    app.get_state = lambda *a, **k: "swapped"          # what the demo does
    assert h.get_state("sensor.a") == "swapped"
    app.args = {"settings_file": "x"}
    assert h.args == {"settings_file": "x"}
    assert h.run_in(lambda kw: None, 5)                # a handle comes back through the host
    assert h.get_timezone() == "Europe/London"


def test_the_app_uses_appdaemons_methods_only_through_the_host():
    """No `self.get_state(...)`, `self.run_in(...)` and the like in PowerEngine, outside the few AppDaemon-specific
    methods listed in ALLOWED_DIRECT."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PowerEngine")
    leaks = []
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name in ALLOWED_DIRECT:
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"
                    and node.attr in HOST_NAMES and node.attr not in {fn.name for fn in cls.body
                                                                       if isinstance(fn, ast.FunctionDef)}):
                leaks.append(f"{fn.name}: self.{node.attr} (line {node.lineno})")
    assert not leaks, "use self.host.<name> instead:\n" + "\n".join(leaks)


def test_the_directly_allowed_methods_exist():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PowerEngine")
    defined = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert ALLOWED_DIRECT <= defined, ALLOWED_DIRECT - defined
