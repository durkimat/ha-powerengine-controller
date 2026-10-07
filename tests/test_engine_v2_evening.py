"""Engine v2 closed loop, built like the first live evening (6 Oct 2026; docs/plans/engine-v2.md 18a, "Check").

The car's smart slot runs 19:12 to 04:00 at 6.66p, export pays 15p, a grid event runs 19:30 to 20:30, the standard rate
is 28.84p and the morning is dear. Buying and selling both pay for hours. The real forecast, value and executor layers
run with a simulated battery (tests/evening_world.py). Before the change the executor turned a charge into a sale and
back every one to three minutes near the top, and the plan cycled between 85 and 100%."""

import evening_world as w
import pytest

from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import CHARGE

NEW = V2Settings(arbitrage=True, prefer_self_use=False, late_events=False)     # the 18a regime (no Hold limit, no late event)
# The same evening on the code before 18a (commit dc56430, default settings, 30 s steps), recorded: 22 mode changes, 13
# turns of a charge or sale that had run under 25 minutes, 7 A-B-A flips in 10 minutes, the battery at 100% long before
# the last charge, and a cash result of -450.0p on the simulated meter.
BEFORE = {"cash_p": -450.0, "changes": 22, "short_turns": 13, "flips": 7}


@pytest.fixture(scope="module")
def run():
    return w.simulate(NEW)


def _flip_flops(changes, within_s=600):
    """A to B to A inside ten minutes (the executor's own flip-flop count)."""
    return [c for p, q, c in zip(changes, changes[1:], changes[2:], strict=False)
            if p[1] == c[1] and (c[0] - p[0]).total_seconds() < within_s]


def test_no_leg_is_turned_round_before_25_minutes_and_nothing_flip_flops(run):
    assert w.reversals(run["changes"]) == []                         # was 13
    assert _flip_flops(run["changes"]) == []                         # was 7
    assert len(run["changes"]) <= BEFORE["changes"] / 2 and len(run["changes"]) <= 10


def test_with_the_defaults_the_same_evening_is_still_steady_and_full_at_04_00():
    on = w.simulate(V2Settings(arbitrage=True))             # prefer Self-use and prepare for a late event, both on
    assert w.reversals(on["changes"]) == [] and _flip_flops(on["changes"]) == []
    assert len(on["changes"]) <= 12
    at_four = next(lvl for t, _, lvl in on["log"] if t >= w.at(4, 0, 1))
    assert at_four >= 97.0, at_four
    assert on["cash_p"] - BEFORE["cash_p"] <= 15.0, on["cash_p"]


def test_every_turn_within_25_minutes_of_a_change_follows_the_hold_that_ends_a_long_leg(run):
    # read literally, "no reversal within 25 minutes of the previous mode change" meets the minimum-time hold that ends
    # a leg of an hour or more: the leg itself ran 25 minutes or more (`reversals` above) and nothing else is that close
    for t, _from, _to, gap in w.since_last_change(run["changes"]):
        before = next(c for c in reversed(run["changes"]) if c[0] < t)
        assert gap >= 25.0 or before[1] == "hold", (t, gap, before)


def test_the_level_stays_at_the_top_until_the_last_charge_before_04_00(run):
    last = max(t for t, mode, _ in run["changes"] if mode == CHARGE and t < w.at(4, 0, 1))
    before = [lvl for t, _, lvl in run["log"] if t < last]
    assert max(before) <= 91.0, max(before)                          # was 100%


def test_the_battery_is_full_when_the_cheap_slot_ends(run):
    at_four = next(lvl for t, _, lvl in run["log"] if t >= w.at(4, 0, 1))
    assert at_four >= 97.0, at_four


def test_the_night_costs_no_more_than_a_few_pence_extra(run):
    # real cash from the simulated meter (the plan's own expected cost is not comparable with the old one: it now
    # includes the top-up and the comfort cost, which are not money)
    assert run["cash_p"] - BEFORE["cash_p"] <= 10.0, run["cash_p"]
    assert run["end_level"] >= 99.0


def test_the_plan_never_cycles_above_the_top_except_for_the_last_fill(run):
    for vr in run["values"]:
        items = [it for it in vr.timeline if it.mode == CHARGE]
        above = [it for it in items if it.level_end > 90.5]
        assert len(above) <= 1, [(it.start, it.level_end) for it in above]


# the variants with most room to cycle (no grid event to empty the battery first) and the emptiest start; the main run
# above is 66% with the event (each night-long run takes about 20 s, and CI's release check waits 12 minutes)
@pytest.mark.parametrize(("soc0", "event"), [(66.0, False), (85.0, False), (40.0, True)])
def test_every_variant_keeps_under_the_top_until_the_last_fill_and_ends_full(soc0, event):
    # at the default top-up price (5p) a cycle above the top earns less than it costs (about 7p of spread between
    # 6.66p in and 15p out after losses), so even the variant that topped a cycle at 96% at 2p stays under the top
    r = w.simulate(NEW, soc0=soc0, event=event)
    last = max(t for t, mode, _ in r["changes"] if mode == CHARGE and t < w.at(4, 0, 1))
    assert max(lvl for t, _, lvl in r["log"] if t < last) <= 92.0      # a soft top: up to a 30 s step past it (was 96%)
    assert next(lvl for t, _, lvl in r["log"] if t >= w.at(4, 0, 1)) >= 97.0
    assert w.reversals(r["changes"]) == []
