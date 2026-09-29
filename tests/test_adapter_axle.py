"""The Axle event adapter: reading the integration's entities into what Readings stores, and as neutral GridEvents."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fixtures import AXLE_19_20, BST, CONFIG, NOW, STATES, S, get_state

from pe_core.adapters import GRID_EVENT, GridEvent, GridEventAdapter, display
from pe_core.adapters import get as get_adapter
from pe_core.adapters import names as adapter_names
from pe_core.adapters.axle import AxleEvents
from pe_core.readings import read

ROLE_ENTITY = {"axle_event_active": "sensor.axle_active", "axle_event_start": "sensor.axle_start",
               "axle_event_end": "sensor.axle_end"}


def roles(states):
    return lambda role: states.get(ROLE_ENTITY.get(role, ""))


class FakeHA:
    def __init__(self, states):
        self.states = states

    def get_state(self, entity_id=None, attribute=None):
        return self.states.get(entity_id)

    def call_service(self, service, **data):
        pass


def test_none_when_the_integration_says_unknown():
    assert AxleEvents().read_event(roles(STATES)) == (False, None, None)
    assert AxleEvents().read_event(roles({})) == (False, None, None)


def test_scheduled_event():
    active, start, end = AxleEvents().read_event(roles({**STATES, **AXLE_19_20}))
    assert active is False
    assert start.astimezone(BST).hour == 19 and end - start == timedelta(hours=1)


def test_active_event():
    active, start, end = AxleEvents().read_event(roles({**STATES, **AXLE_19_20, "sensor.axle_active": S("on")}))
    assert active is True and start is not None and end is not None


def test_readings_agree_with_the_adapter():
    states = {**STATES, **AXLE_19_20, "sensor.axle_active": S("on")}
    r = read(CONFIG, get_state(states), NOW)
    assert (r.axle_active, r.axle_start, r.axle_end) == AxleEvents().read_event(roles(states))
    assert r.axle_state() == "active"
    assert read(CONFIG, get_state({**STATES, **AXLE_19_20}), NOW, events=AxleEvents()).axle_state() == "scheduled"


def test_grid_events_as_export_events():
    ha = FakeHA({**STATES, **AXLE_19_20})
    ev = AxleEvents(ROLE_ENTITY.get).grid_events(ha, NOW)
    assert len(ev) == 1 and isinstance(ev[0], GridEvent)
    assert ev[0].direction == "export" and ev[0].value_per_kwh is None
    assert ev[0].start.astimezone(BST).hour == 19 and ev[0].end.astimezone(BST).hour == 20


def test_no_grid_events_when_none_scheduled_or_finished():
    t = AxleEvents(ROLE_ENTITY.get)
    assert t.grid_events(FakeHA(STATES), NOW) == []
    assert t.grid_events(FakeHA({**STATES, **AXLE_19_20}), NOW + timedelta(hours=3)) == []
    only_start = {**STATES, "sensor.axle_start": S("2026-09-22T19:00:00+01:00")}
    assert t.grid_events(FakeHA(only_start), NOW) == []


def test_grid_events_need_a_role_map():
    with pytest.raises(RuntimeError):
        AxleEvents().grid_events(FakeHA(STATES), NOW)


def test_protocol_registry_and_names():
    a = get_adapter("event", "axle")(ROLE_ENTITY.get)
    assert isinstance(a, AxleEvents) and isinstance(a, GridEventAdapter) and a.name == "axle"
    assert "axle" in adapter_names("event")
    assert display(GRID_EVENT, a.display_names()) == "Axle event"
