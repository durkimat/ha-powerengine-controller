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
    assert s.executor == "plan" and s.replan_on_level and s.level_top_rate_p == 200.0 and s.slot_bot_factor == 300.0
    assert s.late_events is False and s.prefer_self_use is False and s.top_up_cost_p == 0.0


def test_what_you_saved_beats_the_profile_and_other_keys_still_seed_from_v1():
    beta = profile_values("2.1 beta")
    s = parse_v2({"top_up_cost_p": 4, "executor": "lines", "reserve_soc": 15}, {"arbitrage": True}, {}, beta)
    assert s.top_up_cost_p == 4 and s.executor == "lines" and s.reserve_soc == 15
    assert s.arbitrage is True  # seeded from v1's feature, as for engine v2
    assert s.level_top_rate_p == 200.0  # unsaved: the profile's


def test_engine_v2_without_the_profile_is_unchanged():
    s = parse_v2({})
    assert s == V2Settings() and s.executor == "lines" and not s.replan_on_level
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


def test_a_charge_or_sale_reaching_its_level_asks_for_a_re_plan_only_when_set_to():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    ev = (Event(now, "level", "The battery reached 90%: the charge ends"),)
    off, on = Triggers(V2Settings()), Triggers(V2Settings(replan_on_level=True))
    later = now + timedelta(seconds=11)  # past the batch window
    assert off.due(now, ev, now) is None and off.due(later, (), now) is None
    assert on.due(now, ev, now) is None  # batched with anything else that arrives
    assert on.due(later, (), now) == "Level"  # asked for, and named by the kind of event


def _plan_with(mode):
    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    item = SimpleNamespace(mode=mode, start=t0, end=t0.replace(hour=13))
    return SimpleNamespace(timeline=(item,)), t0.replace(minute=30)


def test_the_plan_executor_takes_the_plans_mode_when_the_limits_allow_it():
    vr, now = _plan_with("export")
    allowed = SimpleNamespace(allowed=frozenset({"export", "hold", "self_use", "charge"}))
    assert Executor(V2Settings(executor="plan"))._candidate(allowed, None, 60.0, vr, now) == "export"
    not_allowed = SimpleNamespace(allowed=frozenset({"hold", "self_use"}))
    # a limit takes the plan's mode away: the executor falls back to its lines
    ln = SimpleNamespace(
        value_p=10.0,
        store_sun_line_p=15.8,
        use_line_p=6.3,
        charge_now=False,
        charge_target_soc=None,
        sell_floor_soc=None,
        buy_line_p=7.0,
        sell_line_p=14.2,
    )
    got = Executor(V2Settings(executor="plan"))._candidate(not_allowed, ln, 60.0, vr, now)
    assert got in ("hold", "self_use")


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
