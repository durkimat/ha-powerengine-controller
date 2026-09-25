"""Guard against service-call arguments that AppDaemon reserves or rewrites (both broke logbook writes in 0.3.x)."""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine" / "powerengine.py"
RESERVED = {"domain", "service", "namespace", "entity_id"}


def test_logbook_calls_use_only_safe_arguments():
    tree = ast.parse(SRC.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "call_service"
             and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "logbook/log"]
    assert calls, "expected at least one logbook call"
    for c in calls:
        assert not {k.arg for k in c.keywords} & RESERVED
