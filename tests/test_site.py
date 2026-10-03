"""The `site:` section: which plant this home has (schema, migration, adapters built by name, publishing, switching)."""
import json
import sys

import pytest
import yaml
from replay_harness import Clock
from test_demo_control import NOW, load_app, version  # noqa: F401  (NOW: the frozen clock the app starts on)

from pe_core.adapters import registry
from pe_core.adapters.kraken import supplier_of
from pe_core.adapters.null import NoCharger, NoEvents, NoForecast
from pe_core.adapters.options import site_options
from pe_core.adapters.solis import SolisInverter
from pe_core.config import ConfigError, Site, parse_config, site_choices

TODAYS = {"inverter": "solis", "inverter_firmware": None, "ev_charger": "zappi", "car": "none", "tariff": "auto",
          "forecast": "solcast", "events": "axle"}


def cfg_with(site):
    return parse_config({"site": site} if site is not None else {})


# --- schema ---------------------------------------------------------------------------------------------------

def test_no_site_means_todays_plant():
    assert parse_config({}).site == Site() and Site().as_dict() == TODAYS
    assert parse_config({"site": None}).site == Site()


def test_a_full_site_parses():
    site = cfg_with({**TODAYS, "inverter_firmware": " 420044 ", "ev_charger": "none", "tariff": "octopus",
                     "forecast": "none", "events": "none"}).site
    assert (site.inverter_firmware, site.ev_charger, site.tariff, site.forecast, site.events) == \
        ("420044", "none", "octopus", "none", "none")


def test_a_partial_site_fills_in_todays_choices():
    assert cfg_with({"tariff": "edf"}).site == Site(tariff="edf")


@pytest.mark.parametrize("site, fragment", [
    ("solis", "'site' must be a mapping"),
    ({"colour": "red"}, "unknown site key(s): colour"),
    ({"inverter": "nope"}, "site 'inverter' must be one of solis"),
    ({"inverter": None}, "site 'inverter' must be one of"),
    ({"ev_charger": "wallbox"}, "site 'ev_charger' must be one of zappi, none"),
    ({"car": "tesla"}, "site 'car' must be one of none"),
    ({"tariff": "agile"}, "site 'tariff' must be one of auto, "),
    ({"forecast": "met"}, "site 'forecast' must be one of solcast, none"),
    ({"events": "x"}, "site 'events' must be one of axle, none"),
    ({"inverter_firmware": 420044}, "'inverter_firmware' must be text in quotes"),
])
def test_bad_sites_are_refused_in_words(site, fragment):
    with pytest.raises(ConfigError) as err:
        cfg_with(site)
    assert fragment in str(err.value)


def test_blank_firmware_is_none():
    assert cfg_with({"inverter_firmware": "  "}).site.inverter_firmware is None


def test_choices_come_from_the_registry(monkeypatch):
    monkeypatch.setitem(registry._registry["ev"], "wallbox", lambda role_entity=None: None)
    assert "wallbox" in site_choices()["ev_charger"]
    assert cfg_with({"ev_charger": "wallbox"}).site.ev_charger == "wallbox"
    assert site_choices()["tariff"][0] == "auto" and site_choices()["inverter"] == ("solis",)


# --- site_options ---------------------------------------------------------------------------------------------

def test_site_options_lists_every_choice_compactly():
    opts = site_options()
    assert set(opts) == {"inverter", "ev_charger", "car", "tariff", "forecast", "events"}
    solis = opts["inverter"][0]
    assert solis["id"] == "solis" and solis["status"] == "verified" and solis["verified_firmware"] == ["420044"]
    assert solis["firmware_variants"] == ["420044"] and "Solis" in solis["name"]
    assert [o["id"] for o in opts["tariff"]] == ["auto", "edf", "octopus"]
    assert {o["id"]: o["status"] for o in opts["tariff"]}["octopus"] == "community"
    assert [o["id"] for o in opts["ev_charger"]] == ["zappi", "none"]
    for rows in opts.values():
        assert all(set(r) >= {"id", "name", "status", "firmware_variants"} for r in rows)
    assert len(json.dumps(opts, separators=(",", ":"))) < 2000


# --- the app --------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _own_names():
    yield
    from pe_core.names import set_current
    set_current(None)                                   # the app sets the module's names map; put his words back


@pytest.fixture
def make(tmp_path, monkeypatch):
    folder = tmp_path / "powerengine"
    folder.mkdir()

    def build(text=None, **args):
        powerengine = load_app(monkeypatch)
        if text is not None:
            (folder / "config.yaml").write_text(text)
        app = powerengine.PowerEngine()
        app.args = {"settings_file": str(folder / "config.yaml"), **args}
        Clock.now, app.tz = NOW, None
        app.initialize()
        return app
    build.folder = folder
    build.config = folder / "config.yaml"
    yield build
    sys.modules.pop("powerengine", None)


def lines(app):
    return [x["msg"] for x in app._log_ring.lines]


def saved(make):
    return yaml.safe_load(make.config.read_text())


def backups(make):
    return sorted(p.name for p in make.folder.glob("config.yaml.bak-*"))


PLAIN = "schema_version: 1\noperation: {mode: passive}\ninputs: {}\n"


def test_migration_adds_todays_site_with_a_backup_and_one_info_line(make):
    app = make(PLAIN)
    assert saved(make)["site"] == {**TODAYS, "inverter_firmware": "420044"}
    assert app.cfg.site == Site(inverter_firmware="420044") and "site" in app.cfg.raw
    assert len(backups(make)) == 1
    added = [m for m in lines(app) if m.startswith("Site added to the configuration: ")]
    assert len(added) == 1 and "inverter solis" in added[0] and "tariff auto" in added[0]
    assert "firmware 420044 (assumed: the variant PowerEngine has always used; not readable from the inverter)" \
        in added[0]


def test_migration_is_idempotent(make):
    make(PLAIN)
    first = make.config.read_text()
    app = make(None)                                   # restart on the migrated file
    assert make.config.read_text() == first and len(backups(make)) == 1
    assert not [m for m in lines(app) if m.startswith("Site added")]


def test_a_saved_site_is_left_alone(make):
    make(PLAIN + "site: {tariff: octopus}\n")
    assert saved(make)["site"] == {"tariff": "octopus"} and backups(make) == []


def test_migration_is_skipped_unconfigured(make):
    app = make(None)
    assert app.cfg is None and not make.config.exists()


def test_migration_is_skipped_in_demo_mode(make):
    app = make(PLAIN, demo="sunny")
    assert make.config.read_text() == PLAIN and backups(make) == []
    assert app._demo == "sunny" and not [m for m in lines(app) if m.startswith("Site added")]


def test_migration_survives_a_failed_save(make, monkeypatch):
    def broken(*a, **k):
        raise OSError("read-only file system")
    app = make(PLAIN)                                  # (migrates once, normally)
    make.config.write_text(PLAIN)
    monkeypatch.setattr(sys.modules["powerengine"], "save_config", broken)
    app.initialize()
    assert make.config.read_text() == PLAIN and app.cfg.site == Site()   # in memory: firmware unset, same variant
    assert any("Could not add the site to the configuration" in w for w in lines(app))
    assert app._inverter().name == "solis"


def test_the_names_and_adapters_follow_the_site(make):
    app = make(PLAIN + "site: {ev_charger: none, forecast: none, events: none, tariff: octopus}\n")
    assert isinstance(app._ev(), NoCharger) and isinstance(app._forecast(), NoForecast)
    assert isinstance(app._events(), NoEvents) and app._tariff().name == "octopus"
    names = app._names()
    assert (names["ev_charger"], names["forecast"], names["event"]) == ("car charger", "forecast", "grid-services")
    assert names["supplier"] == "Octopus" and names["inverter"] == "Solis"
    assert app._forecast().day_kwh([{"pv_estimate": 2}]) is None
    assert app._forecast().read(lambda *a: [1], ["x"]) == []
    assert app._events().read_event(lambda role: {"state": "on"}) == (False, None, None)
    assert app._ev().read(lambda role: {"state": "1"})["power_w"] is None


def test_the_readings_cope_with_every_part_left_out(make):
    from pe_core.readings import read
    app = make(PLAIN + "site: {ev_charger: none, forecast: none, events: none}\n")
    r = read(app.cfg, lambda eid: None, NOW, app._tariff(), app._events(), app._ev(), app._forecast())
    assert (r.ev_power, r.axle_active, r.forecast_today_kwh, r.ev_state()) == (None, False, None, "unplugged")


def test_todays_choices_build_todays_adapters(make):
    app = make(PLAIN + "site: {}\n")
    assert isinstance(app._inverter(), SolisInverter) and app._ev().name == "zappi"
    assert app._forecast().name == "solcast" and app._events().name == "axle"
    assert app._tariff().name == supplier_of(None)                 # "auto": told apart by the rate sensor
    app.cfg = parse_config({**app.cfg.raw, "site": {"tariff": "edf"}})
    assert app._tariff().name == "edf"


def test_firmware_reaches_the_definition_and_a_change_rebuilds(make):
    app = make(PLAIN + "site: {inverter_firmware: '420044'}\n")
    first = app._inverter()
    assert first.definition.firmware == "420044" and first.definition.variant == "420044"
    assert app._inverter() is first                               # cached while the site is unchanged
    app.cfg = parse_config({**app.cfg.raw, "site": {"inverter_firmware": "FB01"}})
    second = app._inverter()
    assert second is not first and second.definition.firmware == "FB01" and second.definition.variant is None
    app.cfg = parse_config({**app.cfg.raw, "site": {"ev_charger": "none"}})
    assert isinstance(app._ev(), NoCharger)


def test_the_version_sensor_carries_the_site_for_the_card(make):
    app = make(PLAIN + "site: {tariff: edf}\n")
    a = version(app)
    assert a["site"] == {**TODAYS, "tariff": "edf"} and a["firmware_detected"] is None and a["retest_required"] is False
    assert a["site_options"] == site_options() and a["names"]["supplier"] == "EDF"
    assert len(json.dumps(a, separators=(",", ":"))) < 9000       # the wizard data is about 3.5 KB of it


def test_a_firmware_entity_is_reported_when_the_definition_names_one(make, monkeypatch):
    app = make(PLAIN)
    app.states["sensor.solis_firmware_version"] = {"state": "FB01", "attributes": {}}
    inv = app._inverter()
    monkeypatch.setitem(inv.d, "firmware_entity", {"domain": "sensor", "tail": "firmware_version"})
    assert inv.firmware_detected() == "FB01"
    app.states["sensor.solis_firmware_version"]["state"] = "unavailable"
    assert inv.firmware_detected() is None


def save_event(app, config):
    app._on_save("pe_config_save", {"config": config}, {})
    return [kw for ev, kw in app.real_events if ev == "pe_config_result"][-1]


ACTIVE = "schema_version: 1\noperation: {mode: active}\ninputs: {}\nsite: {inverter_firmware: '420044'}\n"


def test_changing_the_firmware_switches_to_passive_and_asks_for_the_tests_again(make):
    app = make(ACTIVE)
    result = save_event(app, {**app.cfg.raw, "site": {"inverter_firmware": "FB01"}})
    assert result["ok"] and "Passive" in result["message"]
    assert saved(make)["operation"]["mode"] == "passive" and app.cfg.mode == "passive"
    assert saved(make)["site"]["inverter_firmware"] == "FB01" and app._retest_required()
    assert version(app)["retest_required"] is True and version(app)["site"]["inverter_firmware"] == "FB01"
    assert any("supervised tests need running again" in w for w in lines(app))


def test_a_firmware_that_resolves_to_the_same_variant_does_not_switch(make):
    app = make("schema_version: 1\noperation: {mode: active}\ninputs: {}\nsite: {}\n")    # no firmware: the default
    result = save_event(app, {**app.cfg.raw, "site": {"inverter_firmware": "420044"}})
    assert result["ok"] and "Passive" not in result["message"]
    assert saved(make)["operation"]["mode"] == "active" and not app._retest_required()
    save_event(app, {**app.cfg.raw, "site": {}})                 # and back to none: still the same variant
    assert saved(make)["operation"]["mode"] == "active" and not app._retest_required()


def test_changing_the_inverter_switches_to_passive(make, monkeypatch):
    monkeypatch.setitem(registry._registry["inverter"], "other",
                        lambda ha, role_entity, *a, firmware=None, **k: SolisInverter(ha, role_entity, *a,
                                                                                    firmware=firmware))
    app = make(ACTIVE)
    save_event(app, {**app.cfg.raw, "site": {"inverter": "other", "inverter_firmware": "420044"}})
    assert saved(make)["operation"]["mode"] == "passive" and app._retest_required()


def test_other_site_changes_only_rebuild_the_adapters(make):
    app = make(ACTIVE)
    result = save_event(app, {**app.cfg.raw, "site": {"inverter_firmware": "420044", "ev_charger": "none"}})
    assert result["ok"] and saved(make)["operation"]["mode"] == "active" and not app._retest_required()
    assert isinstance(app._ev(), NoCharger) and version(app)["site"]["ev_charger"] == "none"


def test_a_card_that_does_not_send_the_site_keeps_it(make):
    app = make(ACTIVE)
    raw = {k: v for k, v in app.cfg.raw.items() if k != "site"}
    assert save_event(app, raw)["ok"] and saved(make)["site"] == {"inverter_firmware": "420044"}
    assert saved(make)["operation"]["mode"] == "active"


def test_a_bad_site_is_refused_and_nothing_changes(make):
    app = make(ACTIVE)
    before = make.config.read_text()
    result = save_event(app, {**app.cfg.raw, "site": {"events": "nope"}})
    assert result["ok"] is False and "site 'events'" in result["message"] and make.config.read_text() == before


def test_the_retest_flag_is_kept_in_a_file_and_can_be_cleared(make):
    app = make(ACTIVE)
    app._set_retest(True)
    assert app._retest_required()
    app._set_retest(False)
    assert not app._retest_required()


# --- definitions: status and firmware --------------------------------------------------------------------------

def test_solis_is_verified_on_its_firmware():
    from pe_core.adapters.definition import load_definition
    d = load_definition("solis")
    assert d["status"] == "verified" and d["verified_firmware"] == ["420044"] and d.get("firmware_entity") is None


@pytest.mark.parametrize("extra, fragment", [
    ({"status": "great"}, "'status' must be one of verified, community, draft"),
    ({"verified_firmware": "420044"}, "verified_firmware"),
    ({"firmware_entity": {"domain": "sensor"}}, "'firmware_entity' needs a 'domain' and a 'tail'"),
])
def test_definition_status_and_firmware_keys_are_checked(extra, fragment):
    import copy

    from pe_core.adapters.definition import DefinitionError, _read, definition_path, parse_definition
    data = {**copy.deepcopy(_read(str(definition_path("solis")))), **extra}
    with pytest.raises(DefinitionError) as err:
        parse_definition(data)
    assert fragment in str(err.value)


def test_a_definition_without_a_status_is_a_draft(tmp_path):
    import copy

    from pe_core.adapters.definition import _read, definition_path, parse_definition
    data = copy.deepcopy(_read(str(definition_path("solis"))))
    del data["status"], data["verified_firmware"]
    assert parse_definition(data).get("status", "draft") == "draft"
