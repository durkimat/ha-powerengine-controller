from pe_core.config import parse_config
from pe_core.modes import effective_mode


def test_unconfigured_without_file():
    m = effective_mode(None)
    assert (m.configured, m.effective) == ("unconfigured", "unconfigured")


def test_config_error_is_unconfigured():
    assert effective_mode(None, "bad yaml").effective == "unconfigured"


def test_passive_by_default():
    assert effective_mode(parse_config({})).effective == "passive"


def test_active_refused_by_passive_only_build():
    m = effective_mode(parse_config({"operation": {"mode": "active"}}), build_supports_active=False)
    assert m.configured == "active" and m.effective == "passive"
    assert "only supports Passive" in m.reason


def test_active_allowed_when_build_supports_it():
    m = effective_mode(parse_config({"operation": {"mode": "active"}}), build_supports_active=True)
    assert m.effective == "active"


def test_active_needs_safe_guards_even_in_this_build():
    m = effective_mode(parse_config({"operation": {"mode": "active"}}), guards=["no handover guards are mapped"])
    assert m.effective == "passive" and "Active refused" in m.reason


def test_missing_required_inputs_keep_it_unconfigured():
    m = effective_mode(parse_config({}), missing_required=["battery_soc", "grid_power"])
    assert m.effective == "unconfigured" and "2 required" in m.reason
