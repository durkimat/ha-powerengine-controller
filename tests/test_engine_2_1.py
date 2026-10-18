"""Engine 2.1 beta: engine v2 with a settings profile, a plan-following executor and a re-plan at the target
(docs/plans/engine-v2-recap-1.md). Engine v2's own behaviour is proved unchanged by the replay."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from pe_core.config import ConfigError, parse_config
from pe_core.engine_v2.execute import Executor
from pe_core.engine_v2.publish import settings_state
from pe_core.engine_v2.settings import (
    BUILTIN_PROFILES,
    ENGINES,
    SETTINGS,
    SettingsError,
    V2Settings,
    parse_v2,
    profile_of,
    profile_values,
)
from pe_core.engine_v2.triggers import Triggers
from pe_core.engine_v2.types import Event


def test_the_profile_fills_what_the_config_does_not_set():
    s = parse_v2({}, profile=profile_values("2.1 beta"))
    for key, value in BUILTIN_PROFILES["2.1 beta"].items():
        assert getattr(s, key) == value
    assert s.executor == "plan" and s.level_top_rate_p == 200.0 and s.slot_bot_factor == 30.0
    assert s.prefer_self_use is False and s.top_up_cost_p == 0.0


def test_what_you_saved_beats_the_profile_and_other_keys_still_seed_from_v1():
    beta = profile_values("2.1 beta")
    s = parse_v2({"top_up_cost_p": 4, "executor": "lines", "reserve_soc": 15}, {"arbitrage": True}, {}, beta)
    assert s.top_up_cost_p == 4 and s.executor == "lines" and s.reserve_soc == 15
    assert s.arbitrage is True  # seeded from v1's feature, as for engine v2
    assert s.level_top_rate_p == 200.0  # unsaved: the profile's


def test_engine_v2_without_the_profile_is_unchanged():
    s = parse_v2({})
    assert s == V2Settings() and s.executor == "lines"
    assert s.level_top_rate_p == 0 and s.level_bot_rate_p == 0 and s.slot_bot_factor == 1.0


def test_the_profile_is_chosen_by_the_engine_setting():
    assert (
        profile_of("v2_1") == "2.1 beta"
        and profile_of("v2") is None
        and profile_of("v1") is None
        and profile_of(None) is None
    )
    assert ENGINES == ("v1", "v2")


def config(engine, extra=None):
    raw = {
        "operation": {"mode": "passive"},
        "system": {"engine": engine, "control_method": "ram_remote"},
        "safety": {"min_reserve_soc": 12},
    }
    raw.update(extra or {})
    return parse_config(raw)


def test_the_system_setting_accepts_v2_1_and_applies_the_profile():
    assert config("v2_1").engine_v2.executor == "plan"
    assert config("v2").engine_v2.executor == "lines"
    assert config("v1").engine_v2.executor == "lines"
    saved = config("v2_1", {"engine_v2": {"top_up_cost_p": 4}}).engine_v2
    assert saved.top_up_cost_p == 4 and saved.executor == "plan"
    with pytest.raises(ConfigError, match="v1, v2"):
        config("v3")


def test_the_new_settings_are_checked():
    with pytest.raises(SettingsError, match="above your reserve"):
        parse_v2({"level_bot_rate_p": 5, "level_bot_start_soc": 10, "reserve_soc": 15})
    with pytest.raises(SettingsError, match="below 100%"):
        parse_v2({"level_top_rate_p": 5, "level_top_start_soc": 100})
    with pytest.raises(SettingsError, match="one of"):
        parse_v2({"executor": "magic"})
    assert parse_v2({"executor": "plan"}).executor == "plan"
    assert all(k in SETTINGS for k in BUILTIN_PROFILES["2.1 beta"])  # the profile names only real settings


def test_the_catalogue_shows_the_profiles_defaults_and_the_engines_on_offer():
    _, plain = settings_state(V2Settings(), None)
    _, beta = settings_state(parse_v2({}, profile=profile_values("2.1 beta")), "2.1 beta")
    row = lambda attrs, key: next(r for r in attrs["settings"] if r["key"] == key)  # noqa: E731
    assert row(plain, "executor")["default"] == "lines" and row(beta, "executor")["default"] == "plan"
    assert row(beta, "level_top_rate_p")["default"] == 200.0 and row(plain, "level_top_rate_p")["default"] == 0.0
    assert row(beta, "arbitrage")["default"] == row(plain, "arbitrage")["default"]  # untouched keys: the same
    assert beta["engines"] == ["v1", "v2"] and beta["values"]["executor"] == "plan"
    assert any(sec["key"] == "levels" for sec in beta["sections"])


def test_a_charge_or_sale_reaching_its_level_does_not_ask_for_a_re_plan():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    ev = (Event(now, "level", "The battery reached 90%: the charge ends"),)
    tr = Triggers(V2Settings())
    later = now + timedelta(seconds=11)  # past the batch window
    assert tr.due(now, ev, now) is None and tr.due(later, (), now) is None


def _plan_with(mode):
    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    item = SimpleNamespace(mode=mode, start=t0, end=t0.replace(hour=13))
    return SimpleNamespace(timeline=(item,)), t0.replace(minute=30)


T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _tl(*items, same_prices=True):
    """items: (mode, start minute, end minute, level_start, level_end, lo, hi, remainder)."""
    from pe_core.engine_v2.types import Segment, Spread, TimelineItem
    out = []
    for mode, a, b, l0, l1, lo, hi, rem in items:
        out.append(TimelineItem(mode, T0 + timedelta(minutes=a), T0 + timedelta(minutes=b), l0, l1, "", "",
                                level_lo=lo, level_hi=hi, remainder=rem))
    sp = Spread(0, 0, 0)
    segs = tuple(
        Segment(T0 + timedelta(minutes=m), T0 + timedelta(minutes=m + 30), 7.0 if (same_prices or m < 30) else 30.0,
                15.0, sp, sp)
        for m in range(0, 120, 30))
    return SimpleNamespace(timeline=tuple(out), forecast=SimpleNamespace(segments=segs))


ALLOW = SimpleNamespace(allowed=frozenset({"charge", "export", "hold", "self_use"}))


def test_the_plan_executor_takes_the_plans_mode_when_the_limits_allow_it():
    vr = _tl(("export", 0, 60, 80, 60, 60, 60, False))
    now = T0 + timedelta(minutes=30)
    ex = Executor(V2Settings(executor="plan"))
    assert ex._plan_candidate(ALLOW, 70.0, vr, now) == "export"
    # a limit takes the plan's mode away: nothing is said, and the caller falls back to its lines
    assert ex._plan_candidate(SimpleNamespace(allowed=frozenset({"hold", "self_use"})), 70.0, vr, now) is None


def test_a_charge_runs_to_the_corridor_edge_not_the_clock():
    vr = _tl(("charge", 0, 20, 50, 80, 78, 82, False), ("hold", 20, 30, 80, 80, 80, 80, True),
             ("export", 30, 60, 80, 60, 60, 60, False))
    ex = Executor(V2Settings(executor="plan"))
    ex.mode = "charge"
    assert ex._plan_candidate(ALLOW, 70.0, vr, T0 + timedelta(minutes=10)) == "charge"
    assert ex._plan_candidate(ALLOW, 77.0, vr, T0 + timedelta(minutes=10)) == "charge"        # short of the edge (78)
    assert ex._edge == 78
    # at the edge the next action starts at once (the hold only fills out the segment), not at minute 30
    assert ex._plan_candidate(ALLOW, 78.0, vr, T0 + timedelta(minutes=10)) == "export"
    # slower than planned: the item's time is over but the edge is not reached, the charge goes on
    assert ex._plan_candidate(ALLOW, 75.0, vr, T0 + timedelta(minutes=25)) == "charge"


def test_the_next_action_waits_for_the_clock_when_a_price_changes_before_it():
    vr = _tl(("charge", 0, 20, 50, 80, 80, 80, False), ("hold", 20, 30, 80, 80, 80, 80, True),
             ("export", 30, 60, 80, 60, 60, 60, False), same_prices=False)
    ex = Executor(V2Settings(executor="plan"))
    ex.mode = "charge"
    assert ex._plan_candidate(ALLOW, 80.0, vr, T0 + timedelta(minutes=10)) == "hold"
    assert ex._plan_candidate(ALLOW, 80.0, vr, T0 + timedelta(minutes=31)) == "export"


def test_a_sale_stops_at_the_upper_edge_of_its_corridor():
    vr = _tl(("export", 0, 20, 90, 70, 68, 72, False), ("hold", 20, 30, 70, 70, 70, 70, True),
             ("self_use", 30, 60, 70, 65, 65, 65, False))
    ex = Executor(V2Settings(executor="plan"))
    ex.mode = "export"
    assert ex._plan_candidate(ALLOW, 73.0, vr, T0 + timedelta(minutes=5)) == "export"
    assert ex._plan_candidate(ALLOW, 72.0, vr, T0 + timedelta(minutes=5)) == "self_use"


def test_the_lines_executor_does_not_look_at_the_plans_mode():
    vr, now = _plan_with("export")
    ln = SimpleNamespace(
        value_p=20.0,
        store_sun_line_p=15.8,
        use_line_p=6.3,
        charge_now=False,
        charge_target_soc=None,
        sell_floor_soc=None,
        buy_line_p=7.0,
        sell_line_p=14.2,
    )
    lim = SimpleNamespace(
        allowed=frozenset({"export", "hold", "self_use", "charge"}), floor_soc=12.0, ceiling_soc=100.0
    )
    assert Executor(V2Settings(executor="lines"))._candidate(lim, ln, 60.0, vr, now) != "export"


def test_a_level_at_the_corridor_edge_does_not_ask_for_a_re_plan():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    tr = Triggers(V2Settings())
    later = now + timedelta(seconds=11)
    assert tr.due(now, (Event(now, "level_edge", "The battery reached 80%: the charge ends"),), now) is None
    assert tr.due(later, (), now) is None


def test_a_re_plan_inside_the_corridor_leaves_the_runs_edge_alone():
    ex = Executor(V2Settings(executor="plan"))
    ex.mode = "charge"
    first = _tl(("charge", 0, 20, 50, 80, 78, 82, False), ("hold", 20, 30, 80, 80, 80, 80, True))
    assert ex._plan_candidate(ALLOW, 70.0, first, T0 + timedelta(minutes=5)) == "charge" and ex._edge == 78
    inside = _tl(("charge", 0, 20, 50, 80, 74, 82, False), ("hold", 20, 30, 80, 80, 80, 80, True))
    assert ex._plan_candidate(ALLOW, 70.0, inside, T0 + timedelta(minutes=6)) == "charge"
    assert ex._edge == 78                                       # not 74: the new plan still contains the old edge
    outside = _tl(("charge", 0, 20, 50, 90, 88, 92, False), ("hold", 20, 30, 90, 90, 90, 90, True))
    assert ex._plan_candidate(ALLOW, 70.0, outside, T0 + timedelta(minutes=7)) == "charge"
    assert ex._edge == 88                                       # a plan that moved the target takes over


def test_a_run_that_ended_at_its_edge_is_not_started_again_by_a_wobble():
    vr = _tl(("export", 0, 60, 90, 60, 68, 72, False))
    ex = Executor(V2Settings(executor="plan"))
    ex.mode = "export"
    assert ex._plan_candidate(ALLOW, 75.0, vr, T0 + timedelta(minutes=5)) == "export"
    ex._reached = ex._edge_key                                  # what the level event records at the edge
    ex.mode = "hold"
    assert ex._plan_candidate(ALLOW, 75.0, vr, T0 + timedelta(minutes=6)) != "export"   # the level wobbled up


def test_the_expected_range_of_a_run_is_its_whole_corridor():
    from pe_core.engine_v2.observe import Observer
    vr = _tl(("charge", 0, 20, 50, 80, 78, 82, False), ("hold", 20, 30, 80, 80, 80, 80, True),
             ("self_use", 30, 60, 80, 70, 70, 70, False))
    span = Observer._run_span
    assert span(vr, T0 + timedelta(minutes=5)) == (50, 82)      # on the run
    assert span(vr, T0 + timedelta(minutes=25)) == (50, 82)     # in the hold that fills out its segment
    assert span(vr, T0 + timedelta(minutes=40)) is None         # later: the clock's path again
