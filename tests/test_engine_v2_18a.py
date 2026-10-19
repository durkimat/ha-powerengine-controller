"""Engine v2, change after the first live evening (docs/plans/engine-v2.md section 18a): the cost of reversing, a leg
running to its step's end, and "learned" revaluing only when it matters. The closed-loop check of the evening is in
test_engine_v2_evening.py."""

import pytest

from pe_core.engine_v2 import triggers, value
from pe_core.engine_v2.settings import SECTIONS, SETTINGS, catalogue, parse_v2
from pe_core.engine_v2.types import CHARGE, EXPORT
from pe_core.engine_v2.value import CHARGE_K, DISCHARGE_K, HOLD_K, NONE_K, switch_cost


# ---- 1. the settings in the catalogue ------------------------------------------------------------
def test_the_settings_exist_in_the_catalogue_and_their_sections():
    cat = catalogue()
    rows = {r["key"]: r for r in cat["settings"]}
    assert "reversal_cost_p" not in rows and "top_up_cost_p" not in rows and rows["switch_cost_p"]["default"] == 2.0
    sections = {s["key"]: s["keys"] for s in cat["sections"]}
    assert "switch_cost_p" in sections["trading"]
    assert cat["values"]["switch_cost_p"] == 2.0
    listed = [k for _, _, keys in SECTIONS for k in keys]
    assert sorted(listed) == sorted(SETTINGS) and len(listed) == len(set(listed))     # every setting in one section


# ---- 2. the cost of changing mode -----------------------------------------------------------------------------------
def test_every_change_costs_the_same_and_staying_costs_nothing():
    for a in (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K):
        for b in (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K):
            assert switch_cost(a, b, 2.0) == (0.0 if a == b else 2.0)
    assert switch_cost(CHARGE_K, DISCHARGE_K, 0.0) == 0.0


def test_a_detour_through_hold_never_costs_less_than_the_direct_change():
    kinds = (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K)
    for a in kinds:
        for b in kinds:
            for c in kinds:
                assert switch_cost(a, c, 1.5) <= switch_cost(a, b, 1.5) + switch_cost(b, c, 1.5)


def test_a_saved_reversal_setting_from_an_earlier_release_is_ignored_not_an_error():
    s = parse_v2({"reversal_cost_p": 0.6, "top_up_cost_p": 4})
    assert s.switch_cost_p == 2.0 and not hasattr(s, "top_up_cost_p")


def test_the_programme_adds_the_change_cost_to_a_turn_from_a_charge_to_a_sale():
    cands = [(10.0, CHARGE, 1.0), (9.0, EXPORT, 1.0)]
    assert value._pick(cands, CHARGE_K, 2.0) == pytest.approx(10.0)                # 9 + 2 loses to staying at 10
    assert value._pick(cands, CHARGE_K, 0.5) == pytest.approx(9.5)                 # a small cost lets it turn



# ---- 4. "learned" revalues only when it matters -----------------------------------------------------
SIG = ((18.0, 0.95, 4.8, 4.8, (), (), 12.0, 6.0, 7.4), 30.0)


def _sig(cap=18.0, eta=0.95, days=30.0, taper=()):
    return ((cap, eta, 4.8, 4.8, taper, (), 12.0, 6.0, 7.4), days)


def test_a_rounding_change_in_a_learned_fact_is_not_a_change():
    assert not triggers.learned_moved(SIG, SIG)
    assert not triggers.learned_moved(SIG, _sig(eta=0.9505))             # 0.05%
    assert not triggers.learned_moved(SIG, _sig(cap=18.1))               # 0.56%
    assert not triggers.learned_moved(SIG, _sig(days=30.4))              # under a day


def test_a_real_change_is_one():
    assert triggers.learned_moved(SIG, _sig(eta=0.96))                   # 1.05%
    assert triggers.learned_moved(SIG, _sig(cap=17.5))
    assert triggers.learned_moved(SIG, _sig(days=31.0))
    assert triggers.learned_moved(SIG, _sig(days=None))                  # the profile went away
    assert triggers.learned_moved(SIG, _sig(taper=((95, 0.5),)))         # a taper appeared
    assert triggers.learned_moved(_sig(taper=((95, 0.5),)), _sig(taper=((95, 0.3),)))


def test_the_first_look_is_not_a_change():
    assert not triggers.learned_moved(None, SIG)


def test_a_slow_drift_adds_up_against_the_last_reported_figure():
    ref, moved = SIG, 0
    for i in range(1, 40):                                             # 0.1% a step; the reference moves on a change
        new = _sig(cap=18.0 * (1 - 0.001 * i))
        if triggers.learned_moved(ref, new):
            moved += 1
            ref = new
    assert moved == 3                                                   # about every 1%
