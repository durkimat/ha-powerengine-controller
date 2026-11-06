"""A shadow run: the engine beside the live PowerEngine, on its own copy of the data, with its own entities, able to
change nothing. These try to make it act on Home Assistant, and check that what it reads is the live house but what it
writes and publishes is its own."""

import json

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from test_setup import engine, running, wait_for

from custom_components.powerengine import const, shadow
from custom_components.powerengine.const import DOMAIN

SHADOW_VERSION = "sensor.pe_shadow_diag_version"


def live_folder(hass):
    import pathlib
    folder = pathlib.Path(hass.config.path("powerengine"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text("system:\n  control_method: ram_remote\noperation:\n  mode: active\n")
    (folder / "load_history.json").write_text('{"days": 15}')
    (folder / "config.yaml.bak-20261030").write_text("old")
    (folder / "costs-backup.tgz").write_text("big")
    (folder / "costs").mkdir(exist_ok=True)
    (folder / "costs" / "2026-11-04.json").write_text("{}")
    return folder


async def start_shadow(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_MODE: const.MODE_SHADOW, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_the_engines_own_entities_are_the_shadows_and_nobody_elses_are():
    assert shadow.to_ha("sensor.pe_state_status") == "sensor.pe_shadow_state_status"
    assert shadow.to_ha("switch.pe_ctl_pause") == "switch.pe_shadow_ctl_pause"
    assert shadow.to_ha("sensor.pe_state_dev_car_power") == "sensor.pe_shadow_state_dev_car_power"
    for other in ("sensor.solis_inverter_battery_soc", "sensor.pe_other_sensor", "number.pe_state_x", "zone.home"):
        assert shadow.to_ha(other) == other
    assert shadow.from_ha("sensor.pe_shadow_state_status") == "sensor.pe_state_status"
    assert shadow.from_ha("sensor.pe_state_status") is None                 # the live engine's: not the shadow's to see
    assert shadow.from_ha("sensor.pe_other_sensor") == "sensor.pe_other_sensor"
    assert shadow.from_ha("sensor.solis_x") == "sensor.solis_x"


def test_seeding_copies_the_live_folder_once_without_the_bulky_or_stale_files(tmp_path):
    live, copy = tmp_path / "live", tmp_path / "copy"
    live.mkdir()
    (live / "config.yaml").write_text("system:\n  control_method: ram_remote\n  other: 1\n")
    (live / "data.json").write_text("{}")
    (live / "old.tgz").write_text("x")
    (live / "config.yaml.bak-1").write_text("x")
    (live / "diagnostics").mkdir()
    (live / "diagnostics" / "a.json").write_text("x")
    (live / "costs").mkdir()
    (live / "costs" / "d.json").write_text("{}")
    assert shadow.seed(live, copy) == 3
    assert sorted(p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file()) == [
        "config.yaml", "costs/d.json", "data.json"]
    assert (copy / "config.yaml").read_text() == "system:\n  control_method: ram_remote\n  other: 1\n"
    (live / "data.json").write_text('{"newer": true}')
    assert shadow.seed(live, copy) == 0 and (copy / "data.json").read_text() == "{}"      # not over its own
    assert shadow.seed(tmp_path / "none", tmp_path / "copy2") == 0                          # nothing to copy


async def test_a_shadow_run_publishes_its_own_entities_and_leaves_the_live_ones_alone(hass, caplog):
    live = live_folder(hass)
    hass.states.async_set("sensor.pe_state_status", "Active", {"live": True})             # the live engine's
    hass.states.async_set("switch.pe_ctl_pause", "off")
    entry = await start_shadow(hass)
    await wait_for(hass, lambda: engine(hass) and engine(hass).state == "running")
    await wait_for(hass, lambda: hass.states.get("sensor.pe_shadow_diag_version") is not None
                   and hass.states.get("sensor.pe_shadow_diag_version").state not in ("unknown", "unavailable"))
    assert hass.states.get("sensor.pe_state_status").state == "Active"                   # untouched
    assert hass.states.get("sensor.pe_state_status").attributes == {"live": True}
    registry = er.async_get(hass)
    mine = [e for e in registry.entities.values() if e.platform == DOMAIN and "pe_shadow_" in e.entity_id]
    assert len(mine) == 84
    assert registry.async_get("sensor.pe_shadow_diag_version").unique_id == "powerengine_shadow_diag_version"
    assert registry.async_get("sensor.pe_diag_version") is None
    device = dr.async_get(hass).async_get(registry.async_get("sensor.pe_shadow_diag_version").device_id)
    assert device.name == "PowerEngine (shadow)"
    name = hass.states.get("sensor.pe_shadow_diag_version").attributes["friendly_name"]
    assert name == "PowerEngine (shadow) Version"
    # it works on its own copy, held to passive, and the copy has the live settings
    copy = hass.config.path("powerengine_shadow")
    assert json.loads(open(f"{copy}/load_history.json").read()) == {"days": 15}
    assert not __import__("os").path.exists(f"{copy}/costs-backup.tgz")
    assert not __import__("os").path.exists(f"{copy}/config.yaml.bak-20261030")
    assert "control_method: ram_remote" in open(f"{copy}/config.yaml").read()           # the live settings, copied
    assert open(f"{live}/config.yaml").read() == "system:\n  control_method: ram_remote\noperation:\n  mode: active\n"
    assert hass.states.get("sensor.pe_shadow_state_operation_mode") is not None
    assert engine(hass).attributes["mode"] == "shadow"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_what_a_shadow_engine_asks_to_call_or_fire_is_refused_and_written_down(hass, tmp_path):
    from pe_core.host_socket import SocketHost
    live_folder(hass)
    entry = await start_shadow(hass)
    seen, events = [], async_capture_events(hass, "pe_config_result")

    async def set_value(call):
        seen.append(call.data)
    hass.services.async_register("number", "set_value", set_value)
    runtime = entry.runtime_data
    runtime.supervisor._stopping = True                       # no engine of its own: a client stands in for it
    await runtime.supervisor.stop()
    client = SocketHost(runtime.server.address, runtime.server._secret, retry=(0.05,))
    await hass.async_add_executor_job(client.start, 10)
    ask = lambda fn, *a, **k: hass.async_add_executor_job(lambda: fn(*a, **k))        # noqa: E731
    try:
        await ask(client.call_service, "number/set_value", entity_id="number.solis_charge", value=5000)
        await ask(client.call_service, "notify/mobile_app_phone", message="hi")
        await ask(client.fire_event, "pe_config_result", ok=True, message="Saved.")
        await ask(client.set_state, "sensor.somebody_elses", state="x", attributes={})
        await hass.async_block_till_done()
        assert seen == [] and events == [] and hass.states.get("sensor.somebody_elses") is None
        summary = runtime.home.ledger.summary()
        assert summary["refused"] == 4
        assert set(summary["by_kind"]) == {"service number/set_value", "service notify/mobile_app_phone",
                                           "event pe_config_result", "state sensor.somebody_elses"}
        rows = [json.loads(line) for line in open(runtime.home.ledger.path)]
        assert rows[0]["data"] == {"entity_id": "number.solis_charge", "value": 5000}
        await hass.async_block_till_done()
        assert engine(hass).attributes["shadow"]["refused"] == 4
    finally:
        await hass.async_add_executor_job(client.stop)
        await hass.config_entries.async_unload(entry.entry_id)


async def test_a_shadow_engine_reads_the_live_house_but_its_own_entities_from_the_shadow(hass):
    from pe_core.host_socket import SocketHost
    live_folder(hass)
    hass.states.async_set("sensor.solis_inverter_battery_soc", "84")                       # the house: shared
    hass.states.async_set("sensor.pe_state_status", "LIVE")                                # the live engine's
    entry = await start_shadow(hass)
    runtime = entry.runtime_data
    await runtime.supervisor.stop()
    client = SocketHost(runtime.server.address, runtime.server._secret, retry=(0.05,))
    await hass.async_add_executor_job(client.start, 10)
    ask = lambda fn, *a, **k: hass.async_add_executor_job(lambda: fn(*a, **k))        # noqa: E731
    try:
        await ask(client.native_entities.define, "sensor", "state_status", "Status", {})
        await ask(client.set_state, "sensor.pe_state_status", state="SHADOW", attributes={})
        await hass.async_block_till_done()
        assert await ask(client.get_state, "sensor.solis_inverter_battery_soc") == "84"
        assert await ask(client.get_state, "sensor.pe_state_status") == "SHADOW"            # its own, not "LIVE"
        assert hass.states.get("sensor.pe_state_status").state == "LIVE"
        assert hass.states.get("sensor.pe_shadow_state_status").state == "SHADOW"
        everything = await ask(client.get_state)
        assert "sensor.pe_state_status" in everything and everything["sensor.pe_state_status"]["state"] == "SHADOW"
        assert everything["sensor.solis_inverter_battery_soc"]["state"] == "84"
        assert not any(k.startswith("sensor.pe_shadow_") for k in everything)               # it never sees the prefix
        heard = []
        await ask(client.on_state, lambda *a: heard.append(a), "sensor.pe_state_status")
        await hass.async_block_till_done()
        hass.states.async_set("sensor.pe_state_status", "LIVE AGAIN")             # the live one moves: not heard
        await ask(client.set_state, "sensor.pe_state_status", state="SHADOW 2", attributes={})
        await hass.async_block_till_done()
        await wait_for(hass, lambda: heard, timeout=10)
        assert [h[3] for h in heard] == ["SHADOW 2"]
    finally:
        await hass.async_add_executor_job(client.stop)
        await hass.config_entries.async_unload(entry.entry_id)


async def test_the_shadow_runs_beside_the_live_powerengine_and_refuses_the_live_folder(hass):
    live_folder(hass)
    entry = await start_shadow(hass)
    await running(hass, SHADOW_VERSION)
    await hass.config_entries.async_unload(entry.entry_id)
    bad = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_MODE: const.MODE_SHADOW,
                                                           const.CONF_SETTINGS_DIR: hass.config.path("powerengine")})
    bad.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(bad.entry_id)
    assert bad.state.name == "SETUP_ERROR"


async def test_refreshing_the_shadow_copies_the_live_folder_afresh(hass):
    live = live_folder(hass)
    entry = await start_shadow(hass)
    await running(hass, SHADOW_VERSION)
    (live / "load_history.json").write_text('{"days": 16}')
    copy = hass.config.path("powerengine_shadow")
    assert json.loads(open(f"{copy}/load_history.json").read()) == {"days": 15}
    await hass.services.async_call(DOMAIN, const.SERVICE_REFRESH_SHADOW, {}, blocking=True)
    await wait_for(hass, lambda: json.loads(open(f"{copy}/load_history.json").read()) == {"days": 16})
    await running(hass, SHADOW_VERSION)
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture(autouse=True)
def _clean_folders(hass):
    import shutil
    yield
    for name in ("powerengine", "powerengine_shadow"):
        shutil.rmtree(hass.config.path(name), ignore_errors=True)
