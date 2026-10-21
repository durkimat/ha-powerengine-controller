"""Engine 2.1 beta: engine v2 with a settings profile, a plan-following executor and a re-plan at the target
(docs/plans/engine-v2-recap-1.md). Engine v2's own behaviour is proved unchanged by the replay."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from pe_core.config import ConfigError, parse_config
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
    assert s.level_top_rate_p == 200.0 and s.slot_bot_factor == 30.0
    assert s.prefer_self_use is False and s.min_run_pct == 5


def test_what_you_saved_beats_the_profile_and_nothing_follows_v1():
    beta = profile_values("2.1 beta")
    s = parse_v2({"range_low_soc": 60, "executor": "lines", "reserve_soc": 15}, beta)
    assert s.range_low_soc == 60 and s.reserve_soc == 15            # "executor" is retired: ignored, not an error
    assert s.arbitrage is False  # engine v2's own default: v1's settings are not read
    assert s.level_top_rate_p == 200.0  # unsaved: the profile's


def test_engine_v2_without_the_profile_is_unchanged():
    s = parse_v2({})
    assert s == V2Settings()
    assert s.range_firmness == "gentle" and s.min_run_pct == 0 and s.slot_bot_factor == 1.0


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
    assert config("v2_1").engine_v2.level_top_rate_p == 200.0
    assert config("v2").engine_v2.level_top_rate_p == 50.0       # the default firmness, gentle
    assert config("v1").engine_v2.level_top_rate_p == 50.0
    saved = config("v2_1", {"engine_v2": {"reserve_soc": 20}}).engine_v2
    assert saved.reserve_soc == 20 and saved.level_top_rate_p == 200.0
    with pytest.raises(ConfigError, match="v1, v2"):
        config("v3")


def test_the_new_settings_are_checked():
    with pytest.raises(SettingsError, match="below its top"):
        parse_v2({"range_low_soc": 80, "range_high_soc": 70})
    with pytest.raises(SettingsError, match="must be one of"):
        parse_v2({"range_firmness": "hard"})
    assert parse_v2({"executor": "magic"}) == V2Settings()           # a retired setting: dropped, whatever its value
    assert all(k in SETTINGS for k in BUILTIN_PROFILES["2.1 beta"])  # the profile names only real settings


def test_the_catalogue_shows_the_profiles_defaults_and_the_engines_on_offer():
    _, plain = settings_state(V2Settings(), None)
    _, beta = settings_state(parse_v2({}, profile=profile_values("2.1 beta")), "2.1 beta")
    row = lambda attrs, key: next(r for r in attrs["settings"] if r["key"] == key)  # noqa: E731
    assert not any(r["key"] in ("executor", "price_band_p", "level_band_pct", "min_dwell_s", "reversal_levy_p")
                   for r in plain["settings"])                       # the retired settings are not on offer
    assert row(beta, "range_firmness")["default"] == "firm" and row(plain, "range_firmness")["default"] == "gentle"
    assert row(beta, "arbitrage")["default"] == row(plain, "arbitrage")["default"]  # untouched keys: the same
    assert beta["engines"] == ["v1", "v2"] and beta["values"]["range_firmness"] == "firm"
    assert any(sec["key"] == "range" for sec in beta["sections"])


def test_a_charge_or_sale_reaching_its_level_does_not_ask_for_a_re_plan():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    ev = (Event(now, "level", "The battery reached 90%: the charge ends"),)
    tr = Triggers(V2Settings())
    later = now + timedelta(seconds=11)  # past the batch window
    assert tr.due(now, ev, now) is None and tr.due(later, (), now) is None


T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _tl(*items):
    """items: (mode, start minute, end minute, level_start, level_end, remainder)."""
    from pe_core.engine_v2.types import TimelineItem
    out = [TimelineItem(mode, T0 + timedelta(minutes=a), T0 + timedelta(minutes=b), l0, l1, "", "", remainder=rem)
           for mode, a, b, l0, l1, rem in items]
    return SimpleNamespace(timeline=tuple(out))


def test_a_level_at_the_corridor_edge_does_not_ask_for_a_re_plan():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    tr = Triggers(V2Settings())
    later = now + timedelta(seconds=11)
    assert tr.due(now, (Event(now, "level_edge", "The battery reached 80%: the charge ends"),), now) is None
    assert tr.due(later, (), now) is None


def test_the_expected_range_of_a_run_is_from_where_it_starts_to_its_level():
    from pe_core.engine_v2.observe import Observer
    vr = _tl(("charge", 0, 20, 50, 80, False), ("hold", 20, 30, 80, 80, True),
             ("self_use", 30, 60, 80, 70, False))
    span = Observer._run_span
    assert span(vr, T0 + timedelta(minutes=5)) == (50, 80)      # on the run
    assert span(vr, T0 + timedelta(minutes=25)) == (50, 80)     # in the hold that fills out its segment
    assert span(vr, T0 + timedelta(minutes=40)) is None         # later: the clock's path again
