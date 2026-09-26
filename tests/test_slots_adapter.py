"""The app's three-slot control path, with the stub AppDaemon from test_pause_guards."""
from datetime import datetime, timezone

from test_pause_guards import GUARDED, app  # noqa: F401  (fixture)
from test_schedule import plan_of

from pe_core.config import parse_config
from pe_core.decide import EXPORT, GRID_CHARGE, SELF_USE, Decision
from pe_core.modes import effective_mode
from pe_core.planner import Plan

UTC = timezone.utc


def _with_slots(a):
    for e in list(a.states):
        if e.startswith(("number.timed_", "button.timed_update")) and "current" not in e:
            a.states[e + "_2"] = 0
            a.states[e + "_3"] = 0
    a.mode = effective_mode(parse_config(GUARDED), build_supports_active=True)
    a._publish_if_changed = lambda key, state, attrs: a.published.append((key, state, attrs))
    a._within_write_limit = lambda n: True
    a._test_running = lambda: False
    return a


def test_slots_programme_the_night_and_then_stay_quiet(app):  # noqa: F811
    a = _with_slots(app)
    now = datetime(2026, 9, 24, 22, 0, tzinfo=UTC)
    acts = [EXPORT] * 2 + [GRID_CHARGE] * 6 + [SELF_USE] * 8
    a.plan = Plan(slots=plan_of(acts, now), made_at=now)
    r = type("R", (), {"now": now})()
    a._params = lambda readings=None: __import__("pe_core.planner", fromlist=["Params"]).Params()
    a._control(r, Decision(EXPORT, "plan", "sell"))
    first = [c for c in a.calls]
    assert any(s == "button/press" for s, _ in first)
    assert a.published[-1][2]["strategy"] == "slots"
    a.calls.clear()
    a._ctl["last_write"] = None
    a._control(r, Decision(EXPORT, "plan", "sell"))
    assert not a.calls                                            # nothing changed: no writes


def test_pause_closes_all_three_slots(app):  # noqa: F811
    a = _with_slots(app)
    a.states["number.timed_charge_start_hour_3"] = 23
    a._release()
    assert a.states["number.timed_charge_start_hour_3"] == 0
    assert ("button/press", {"entity_id": "button.timed_update_button_3"}) in a.calls


def test_preview_lists_all_six_windows(app):  # noqa: F811
    a = _with_slots(app)
    a.mode = effective_mode(parse_config({}))                      # Passive: preview only
    now = datetime(2026, 9, 24, 22, 0, tzinfo=UTC)
    a.plan = Plan(slots=plan_of([EXPORT] * 2 + [GRID_CHARGE] * 4, now), made_at=now)
    a._params = lambda readings=None: __import__("pe_core.planner", fromlist=["Params"]).Params()
    a._control(type("R", (), {"now": now})(), Decision(EXPORT, "plan", "sell"))
    rows = a.published[-1][2]["rows"]
    assert len([r for r in rows if r["slot"] in (1, 2, 3)]) == 6 and not a.calls
