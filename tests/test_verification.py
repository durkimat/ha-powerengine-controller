import pytest

from pe_core import verification
from pe_core.adapters.definition import load_definition
from pe_core.config import parse_config
from pe_core.modes import effective_mode
from pe_core.verification import active_refusal


def test_the_owners_inverter_is_allowed_with_default_or_listed_firmware():
    assert active_refusal("solis", None) is None
    assert active_refusal("solis", "420044") is None


def test_unlisted_firmware_is_refused_with_the_verified_list():
    why = active_refusal("solis", "FB0001")
    assert why and "FB0001" in why and "420044" in why


@pytest.mark.parametrize("status", ["community", "draft"])
def test_unverified_status_is_refused(monkeypatch, status):
    real = load_definition("solis")
    fake = type(real)({**real.data, "status": status}, real.source, None, real.variant)
    monkeypatch.setattr(verification, "load_definition", lambda name, fw=None: fake)
    why = active_refusal("solis", None)
    assert why and status in why and "not yet verified" in why


def test_a_definition_that_cannot_load_is_refused():
    assert "could not be loaded" in active_refusal("no_such_inverter", None)


def test_effective_mode_refuses_active_but_leaves_passive_alone():
    active = parse_config({"operation": {"mode": "active"}})
    m = effective_mode(active, unverified="the inverter setup is draft")
    assert (m.configured, m.effective) == ("active", "passive")
    assert m.reason.startswith("Active refused: the inverter setup is draft")
    assert effective_mode(parse_config({}), unverified="x").reason.startswith("Passive:")
    assert effective_mode(active, unverified=None).effective == "active"
