"""The rate and smart-slot lists are kept through a short outage of their sensors (pe_core/hold.py)."""

from datetime import timedelta

from fixtures import CONFIG, NOW, STATES, S, get_state

from pe_core.hold import HOLD_S, Hold
from pe_core.readings import read


def _entity(role):
    return CONFIG.inputs[role]["entity"]


def _down(states, role):
    return {**states, _entity(role): S("unavailable")}


def _slots(hold, states, at):
    return read(CONFIG, get_state(states), at, hold=hold)


def test_a_dropout_keeps_the_last_slots_and_rates():
    hold = Hold()
    good = _slots(hold, STATES, NOW)
    assert good.dispatches and good.rates
    for role in ("smart_dispatches", "import_rates_today"):
        r = _slots(hold, _down(STATES, role), NOW + timedelta(seconds=90))
        assert r.dispatches == good.dispatches and r.rates == good.rates, role


def test_without_a_hold_the_lists_read_empty():
    r = read(CONFIG, get_state(_down(STATES, "smart_dispatches")), NOW)
    assert r.dispatches == []


def test_no_slots_from_an_available_sensor_is_believed_at_once():
    hold = Hold()
    _slots(hold, STATES, NOW)
    withdrawn = {**STATES, _entity("smart_dispatches"): S("on", planned_dispatches=[], completed_dispatches=[])}
    assert _slots(hold, withdrawn, NOW + timedelta(seconds=30)).dispatches == []


def test_a_long_outage_is_not_held_for_ever():
    lines = []
    hold = Hold(lambda msg, **k: lines.append((msg, k.get("level"))))
    _slots(hold, STATES, NOW)
    down = _down(STATES, "smart_dispatches")
    start = NOW + timedelta(seconds=10)                                   # the sensor goes
    assert _slots(hold, down, start).dispatches
    assert _slots(hold, down, start + timedelta(seconds=HOLD_S - 1)).dispatches
    assert _slots(hold, down, start + timedelta(seconds=HOLD_S + 1)).dispatches == []
    assert [level for _, level in lines].count("WARNING") == 1
    _slots(hold, down, start + timedelta(seconds=HOLD_S + 60))
    assert [level for _, level in lines].count("WARNING") == 1            # said once


def test_nothing_to_keep_before_a_good_reading():
    assert _slots(Hold(), _down(STATES, "smart_dispatches"), NOW).dispatches == []


def test_it_says_when_it_starts_and_stops_keeping():
    lines = []
    hold = Hold(lambda msg, **k: lines.append(msg))
    _slots(hold, STATES, NOW)
    _slots(hold, _down(STATES, "smart_dispatches"), NOW + timedelta(seconds=10))
    _slots(hold, _down(STATES, "smart_dispatches"), NOW + timedelta(seconds=40))
    _slots(hold, STATES, NOW + timedelta(seconds=100))
    assert len(lines) == 2 and "keeping the last known" in lines[0] and "back after 90 s" in lines[1]
