"""Engine 2.1 beta in the closed loop (the real forecast, value and executor layers against a simulated battery): the
6 Oct evening with a smart slot and a grid event, as tests/test_engine_v2_evening.py runs it for engine v2."""

import evening_world as w

from pe_core.engine_v2.settings import parse_v2, profile_values


def test_engine_2_1_beta_runs_the_evening_without_turning_back_on_itself():
    # With selling allowed. Without it the profile alternates self-use and one-to-five-minute charges around 93% all
    # night (28 changes): the same on the code before the intent settings, which this test did not see, as it passed
    # the profile as the string "2.1" (reported, not fixed here).
    run = w.simulate(parse_v2({"arbitrage": True}, profile=profile_values("2.1 beta")))
    changes = run["changes"]
    modes = [m for _, m, _ in changes]
    assert "event" in modes and modes.index("event") > 0  # it sold into the grid event
    assert w.reversals(changes) == []  # no charge turned round into a sale or back
    assert len(changes) <= 16  # a few changes, not a flurry (12 when written)
    assert run["end_level"] > 60  # and it refilled for the morning
    assert len(run["values"]) >= 2  # it re-planned as things changed
