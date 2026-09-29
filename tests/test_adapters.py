"""Tests for the adapter layer's shapes, vocabulary and registry (no concrete adapters yet)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from pe_core.adapters import (
    ControlMethod,
    Dispatch,
    EVAdapter,
    EVState,
    ForecastAdapter,
    ForecastPoint,
    GridEvent,
    HomeAssistant,
    InverterAdapter,
    InverterCapabilities,
    TariffAdapter,
    display,
    get,
    names,
    register,
)
from pe_core.adapters import registry as registry_module

# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


def test_control_method_is_frozen():
    m = ControlMethod(name="ram_remote", storage="ram", failsafe_min=5.0, max_power_w=5000)
    assert m.name == "ram_remote"
    with pytest.raises(FrozenInstanceError):
        m.name = "other"  # type: ignore[misc]


def test_inverter_capabilities_method_lookup():
    ram = ControlMethod(name="ram_remote", storage="ram", failsafe_min=5.0, max_power_w=5000)
    timed = ControlMethod(name="timed_windows", storage="eeprom", failsafe_min=None, max_power_w=None)
    caps = InverterCapabilities(
        methods=(ram, timed),
        actions=frozenset({"grid_charge", "hold", "self_use"}),
        max_charge_w=6000,
        max_discharge_w=6000,
        never_touch=("Backup", "Off-Grid"),
    )
    assert caps.method("ram_remote") is ram
    assert caps.method("timed_windows") is timed
    assert caps.method("nonexistent") is None
    with pytest.raises(FrozenInstanceError):
        caps.max_charge_w = 1  # type: ignore[misc]


def test_dispatch_and_grid_event_defaults():
    start = datetime(2026, 9, 28, 1, 0)
    end = datetime(2026, 9, 28, 4, 0)
    d = Dispatch(start=start, end=end, kwh=6.0)
    assert d.for_car is True

    e = GridEvent(start=start, end=end, direction="export", value_per_kwh=0.30)
    assert e.direction == "export"


def test_ev_state_and_forecast_point():
    s = EVState(plugged=True, charging=False, complete=False)
    assert s.power_w is None

    f = ForecastPoint(start=datetime(2026, 9, 28, 12, 0), kwh=1.5)
    assert f.low_kwh is None and f.high_kwh is None


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------


def test_display_uses_adapter_name_then_default_then_term():
    assert display("dispatch", {"dispatch": "EDF smart slot"}) == "EDF smart slot"
    assert display("dispatch") == "smart-charge slot"
    assert display("dispatch", {}) == "smart-charge slot"
    assert display("not_a_real_term") == "not_a_real_term"
    assert display("not_a_real_term", {"not_a_real_term": "custom"}) == "custom"


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test gets a fresh registry so tests can't see each other's registrations."""
    saved = {kind: dict(entries) for kind, entries in registry_module._registry.items()}
    yield
    registry_module._registry.clear()
    registry_module._registry.update(saved)


def test_register_and_get_round_trip():
    def factory():
        return "a-solis-adapter"

    register("inverter", "solis", factory)
    assert get("inverter", "solis") is factory
    assert names("inverter") == ["solis"]


def test_get_unknown_name_raises_helpful_key_error():
    register("tariff", "edf", lambda: None)
    with pytest.raises(KeyError, match="edf"):
        get("tariff", "nonexistent")


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError):
        register("boiler", "x", lambda: None)
    with pytest.raises(ValueError):
        get("boiler", "x")
    with pytest.raises(ValueError):
        names("boiler")


def test_names_empty_when_nothing_registered():
    assert names("forecast") == []


# ---------------------------------------------------------------------------
# protocol conformance (structural typing via isinstance, since these are runtime_checkable)
# ---------------------------------------------------------------------------


class DummyHA:
    def get_state(self, entity_id, attribute=None):
        return None

    def call_service(self, service, **data):
        return None


class DummyInverter:
    name = "dummy"

    def capabilities(self):
        return InverterCapabilities(methods=(), actions=frozenset(), max_charge_w=0, max_discharge_w=0)

    def writes_for(self, decision, now, context):
        return []

    def release(self):
        return []

    def verify(self, writes, ha):
        return []

    def write_storage(self, write):
        return "ram"


class DummyTariff:
    name = "dummy"

    def display_names(self):
        return {}

    def import_rates(self, ha, now):
        return []

    def export_rate(self, ha, now):
        return None

    def dispatches(self, ha, now):
        return []

    def grid_events(self, ha, now):
        return []


class DummyEV:
    name = "dummy"

    def display_names(self):
        return {}

    def state(self, ha):
        return EVState(plugged=False, charging=False, complete=False)


class DummyForecast:
    name = "dummy"

    def half_hourly(self, ha, day):
        return []


def test_dummy_classes_satisfy_protocols():
    assert isinstance(DummyHA(), HomeAssistant)
    assert isinstance(DummyInverter(), InverterAdapter)
    assert isinstance(DummyTariff(), TariffAdapter)
    assert isinstance(DummyEV(), EVAdapter)
    assert isinstance(DummyForecast(), ForecastAdapter)


def test_incomplete_class_does_not_satisfy_protocol():
    class NotAnInverter:
        name = "nope"

    assert not isinstance(NotAnInverter(), InverterAdapter)


def test_forecast_point_used_with_date_type():
    # sanity: date is importable and usable alongside ForecastPoint/ForecastAdapter signatures
    assert isinstance(date(2026, 9, 28), date)
