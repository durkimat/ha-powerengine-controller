"""One names map: what the supplier and devices are called, taken from the adapters (step 6).

The rule: identical text for this user (EDF, Zappi, Solcast, Solis, Axle), his own words for another supplier."""
import json
import pathlib
import re

import pytest

from pe_core import names as nm
from pe_core.adapters.axle import AxleEvents
from pe_core.adapters.kraken import KrakenTariff
from pe_core.adapters.myenergi import ZappiCharger
from pe_core.adapters.solcast import SolcastForecast
from pe_core.adapters.solis import SolisInverter
from pe_core.config import settings_catalogue
from pe_core.dashboard import SOURCE, energy_flow_card, render
from pe_core.roles import ROLE_BY_KEY, catalogue

GOLDEN = pathlib.Path(__file__).parent / "golden" / "dashboard_edf_zappi_solcast_solis_axle.lovelace"
HIS = {"supplier": "EDF", "tariff": "EDF tariff", "dispatch": "EDF smart slot", "dispatch_short": "EDF slot",
       "smart_charge": "EDF smart charge", "ev_charger": "Zappi", "forecast": "Solcast", "inverter": "Solis",
       "event": "Axle"}


@pytest.fixture(autouse=True)
def _reset_names():
    yield
    nm.set_current(None)


def octopus_names():
    return nm.build_names(KrakenTariff("octopus"), ZappiCharger(), SolcastForecast(), None, AxleEvents())


def his_card():
    from test_dashboard import _plant
    return energy_flow_card([_plant("main", "Main")], 18000, 12, True, "solis")


# --- the map ------------------------------------------------------------------------------

def test_default_map_is_his_words():
    assert nm.default_names() == HIS == nm.current()


def test_map_is_built_from_the_adapters():
    built = nm.build_names(KrakenTariff("edf"), ZappiCharger(), SolcastForecast(), SolisInverter(None, dict, 51.2),
                           AxleEvents())
    assert built == HIS


def test_octopus_supplier_changes_only_the_supplier_terms():
    o = octopus_names()
    assert o["supplier"] == "Octopus" and o["tariff"] == "Octopus tariff"
    assert o["dispatch"] == "Octopus intelligent dispatch"
    assert o["ev_charger"] == "Zappi" and o["forecast"] == "Solcast"


def test_no_adapters_gives_neutral_words():
    n = nm.build_names()
    assert n == nm.neutral_names()
    assert n["supplier"] == "your supplier" and n["dispatch"] == "smart-charge slot"
    assert not any(w in " ".join(n.values()) for w in ("EDF", "Zappi", "Solcast", "Solis", "Axle", "Octopus"))


def test_fill_and_unknown_placeholder():
    assert nm.fill("Ask <<supplier>> about the <<ev_charger>>", HIS) == "Ask EDF about the Zappi"
    assert nm.fill("plain text, a << b >> and {{ jinja }}", HIS) == "plain text, a << b >> and {{ jinja }}"
    with pytest.raises(ValueError):
        nm.fill("hello <<nonsense>>", HIS)


def test_fill_falls_back_to_neutral_when_a_term_is_missing():
    assert nm.fill("<<supplier>>", {}) == "your supplier"


def test_set_current_changes_what_N_reads():
    nm.set_current(octopus_names())
    assert nm.N("supplier") == "Octopus"
    nm.set_current(None)
    assert nm.N("supplier") == "EDF"


# --- Python texts -------------------------------------------------------------------------

def test_smart_request_log_line():
    from pe_core.smartcharge import ask_message
    a = {"from": "07:00", "to": "07:30", "why": "no slot soon"}
    assert ask_message("Asking", a, HIS) == "Asking EDF for smart-charge slots: ready-by 07:00 → 07:30 (no slot soon)"
    assert "EDF" not in ask_message("Asking", a, octopus_names())
    assert "Octopus for smart-charge" in ask_message("Would ask", a, octopus_names())
    assert "your supplier" in ask_message("Asking", a, nm.neutral_names())


def test_event_decision_reasons_read_the_same_and_follow_the_names():
    from datetime import datetime, timezone

    from pe_core.config import parse_config
    from pe_core.decide import decide
    from pe_core.readings import Readings
    cfg = parse_config({"inputs": {"battery_capacity": {"value": 18}, "battery_max_discharge_power": {"value": 4800}}})
    r = Readings(now=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc), battery_soc=60, battery_power=500,
                 import_rate=0.3, ev_power=0, ev_plug="EV Disconnected", axle_active=True)
    assert decide(r, cfg).reason == "Axle event in progress (paid £1 per kWh exported)"
    nm.set_current(nm.build_names(KrakenTariff("octopus"), None, None, None, None))
    assert decide(r, cfg).reason.startswith("grid-services event in progress")


def test_waterfall_labels():
    from test_costs import _day

    from pe_core.costs import waterfall
    days = [_day("2026-09-21", none=10, solar=8, tariff=7, self_use_adj=5, actual_adj=4, carry=0.2,
                 events_metered=1.0, axle_income=0.6, paid=4.6)]
    labels = [s["label"] for s in waterfall(days, "yesterday")["steps"]]
    assert "EDF tariff" in labels and "Axle & free power" in labels and labels[-1] == "You paid (after Axle payments)"
    nm.set_current(octopus_names())
    labels = [s["label"] for s in waterfall(days, "yesterday")["steps"]]
    assert "Octopus tariff" in labels and not any("EDF" in x for x in labels)


def test_role_and_setting_help_read_the_same():
    text = json.dumps(catalogue(), ensure_ascii=False) + json.dumps(settings_catalogue(), ensure_ascii=False)
    assert "<<" not in text
    assert "Car charge target sent to EDF." in text and "Axle event active" in text
    assert "7.4 kW for a 32 A Zappi" in text and "Extra charge kept above what an Axle event needs." in text


def test_role_and_setting_help_follow_the_names():
    nm.set_current(octopus_names())
    text = json.dumps(catalogue(), ensure_ascii=False) + json.dumps(settings_catalogue(), ensure_ascii=False)
    assert "EDF" not in text and "Car charge target sent to Octopus." in text
    assert "Octopus's smart-charging state" in text
    assert ROLE_BY_KEY["smart_target_soc"].description.count("<<supplier>>") == 1   # the table keeps the placeholder


# --- the dashboard ------------------------------------------------------------------------

def test_dashboard_with_his_names_is_byte_identical_to_the_frozen_original():
    """GOLDEN is what the dashboard was, rendered with his energy-flow card, before names were placeholders."""
    text = open(SOURCE, encoding="utf-8").read()
    assert render(text, his_card(), HIS) == GOLDEN.read_text(encoding="utf-8")


def test_dashboard_with_octopus_names_has_no_edf_in_user_text():
    text = render(open(SOURCE, encoding="utf-8").read(), his_card(), octopus_names())
    for i, line in enumerate(text.split("\n"), 1):
        if "EDF" in line:
            # only YAML comments and the supplier-comparison sentence may still name EDF
            assert line.lstrip().startswith("#") or "EDF tariffs keep your export rate" in line, (i, line)
    assert "Octopus smart slot" not in text and "EDF slot" not in text and "Octopus dispatch" in text
    assert "<<" not in text


def test_every_placeholder_in_the_dashboard_is_a_known_term():
    text = open(SOURCE, encoding="utf-8").read()
    assert nm.placeholders(text) <= set(nm.TERMS)
    assert re.search(r"<<[a-z_]+>>", text)


def test_unknown_placeholder_fails_instead_of_shipping():
    with pytest.raises(ValueError):
        render("name: <<no_such_term>>\n", None, HIS)


# --- the app ------------------------------------------------------------------------------

def test_app_publishes_the_names_map_on_the_version_sensor():
    import sys
    import types
    from unittest import mock
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")

    class Hass:
        def __getattr__(self, name):
            raise AttributeError(name)

        def log(self, *a, **k):
            pass
    hassapi.Hass = Hass
    mods = {n: types.ModuleType(n) for n in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass")}
    mods["appdaemon.plugins.hass.hassapi"] = hassapi
    with mock.patch.dict(sys.modules, mods):
        sys.modules.pop("powerengine", None)
        import powerengine
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg = None
        sent = {}
        e._publisher_obj = types.SimpleNamespace(
            publish=lambda key, state, attrs=None: sent.__setitem__(f"powerengine/{key}/attributes", attrs))
        e._publish_names()
        assert json.loads(sent["powerengine/diag_version/attributes"])["names"] == nm.neutral_names()
        e.cfg = object()
        e._tariff, e._ev, e._forecast = (lambda: KrakenTariff("edf")), ZappiCharger, SolcastForecast
        e._inverter, e._events = (lambda: SolisInverter(None, dict, 51.2)), AxleEvents
        assert e._names() == HIS and nm.N("event") == "Axle"
        sys.modules.pop("powerengine", None)
