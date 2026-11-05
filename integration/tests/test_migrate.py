"""Taking over from the AppDaemon app: its MQTT entities are replaced by native ones with the same ids."""

import json
from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service
from tests.test_setup import engine, running, start, wait_for

from custom_components.powerengine import const
from custom_components.powerengine.const import DOMAIN
from custom_components.powerengine.migrate import SNAPSHOT_DIR, old_entries

OLD = (("sensor", "diag_version"), ("sensor", "state_status"), ("switch", "ctl_pause"), ("switch", "ui_right_align"))


def old_app(hass, pause="on", area=None):
    """The AppDaemon app's entities as MQTT discovery left them: registry entries and states."""
    registry = er.async_get(hass)
    for domain, key in OLD:
        entry = registry.async_get_or_create(domain, "mqtt", f"powerengine_{key}", suggested_object_id=f"pe_{key}")
        value = {"ctl_pause": pause, "ui_right_align": "off"}.get(key, "old")
        hass.states.async_set(entry.entity_id, value)
    if area:
        registry.async_update_entity("switch.pe_ctl_pause", area_id=area, name="Pause PowerEngine", icon="mdi:pause")


def stale_heartbeat(hass):
    old = (dt_util.utcnow() - timedelta(minutes=10)).timestamp()
    hass.states.async_set(const.HEARTBEAT_ENTITY, "2026-11-04T11:50:00+00:00", timestamp=old)


async def test_the_engine_waits_while_the_old_entities_exist(hass, tmp_path):
    old_app(hass)
    entry = await start(hass, tmp_path)
    assert engine(hass).state == "waiting"
    assert ir.async_get(hass).async_get_issue(DOMAIN, const.ISSUE_OLD_ENTITIES)
    assert not ir.async_get(hass).async_get_issue(DOMAIN, const.ISSUE_RUNNING_ELSEWHERE)
    assert entry.runtime_data.supervisor._process is None
    await hass.config_entries.async_unload(entry.entry_id)
    assert not ir.async_get(hass).issues


async def test_the_migration_replaces_the_old_entities_with_native_ones_under_the_same_ids(hass, tmp_path):
    area = ar.async_get(hass).async_create("Plant room")
    old_app(hass, pause="on", area=area.id)
    published = async_mock_service(hass, "mqtt", "publish")
    entry = await start(hass, tmp_path)
    assert engine(hass).state == "waiting"

    result = await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True, return_response=True)
    assert result["entities"] == 4 and result["removed"] == 4 and result["shadow_removed"] == 0
    topics = {call.data["topic"]: call.data for call in published}
    assert topics["homeassistant/sensor/powerengine/diag_version/config"]["payload"] == ""
    assert topics["homeassistant/sensor/powerengine/diag_version/config"]["retain"] is True
    assert "powerengine/ctl_pause/set" in topics and "powerengine/ctl_pause/state" in topics
    assert not old_entries(hass)

    snapshot = json.loads(next((tmp_path / SNAPSHOT_DIR).glob("mqtt-entities-*.json")).read_text())
    by_id = {row["entity_id"]: row for row in snapshot["entities"]}
    assert by_id["switch.pe_ctl_pause"]["state"] == "on" and by_id["switch.pe_ctl_pause"]["area_id"] == area.id

    await running(hass)
    registry = er.async_get(hass)
    assert registry.async_get("sensor.pe_diag_version").platform == DOMAIN           # not sensor.pe_diag_version_2
    assert registry.async_get("sensor.pe_diag_version").unique_id == "powerengine_diag_version"
    assert not [e for domain, key in OLD for e in registry.entities if e == f"{domain}.pe_{key}_2"]
    assert not ir.async_get(hass).async_get_issue(DOMAIN, const.ISSUE_OLD_ENTITIES)
    pause = registry.async_get("switch.pe_ctl_pause")
    assert pause.platform == DOMAIN and pause.area_id == area.id               # what the owner set, carried over
    assert pause.name == "Pause PowerEngine" and pause.icon == "mdi:pause"
    await wait_for(hass, lambda: hass.states.get("switch.pe_ctl_pause").state == "on", what="the pause switch's state")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_migration_refuses_while_the_app_is_still_beating(hass, tmp_path):
    old_app(hass)
    async_mock_service(hass, "mqtt", "publish")
    hass.states.async_set(const.HEARTBEAT_ENTITY, "2026-11-04T12:00:00+00:00")
    entry = await start(hass, tmp_path)
    with pytest.raises(ServiceValidationError, match="still running"):
        await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True)
    assert old_entries(hass)
    stale_heartbeat(hass)
    result = await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True, return_response=True)
    assert result["removed"] == 4
    await hass.config_entries.async_unload(entry.entry_id)


async def test_without_mqtt_the_old_entities_still_go_and_it_says_the_messages_were_left(hass, tmp_path):
    old_app(hass)
    entry = await start(hass, tmp_path)
    result = await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True, return_response=True)
    assert result["removed"] == 4 and result["cleared_topics"] == 0
    assert result["notes"] and "retained messages" in result["notes"][0]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_running_it_again_finds_nothing(hass, tmp_path):
    entry = await start(hass, tmp_path)
    result = await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True, return_response=True)
    assert result["entities"] == 0 and result["snapshot"] is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_shadow_runs_leftovers_and_device_go_when_the_entry_becomes_live(hass, tmp_path):
    entry = await start(hass, tmp_path)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    device = devices.async_get_or_create(config_entry_id=entry.entry_id,
                                         identifiers={(DOMAIN, f"{entry.entry_id}_shadow")})
    for key in ("diag_version", "state_status"):
        registry.async_get_or_create("sensor", DOMAIN, f"powerengine_shadow_{key}", config_entry=entry,
                                     device_id=device.id, suggested_object_id=f"pe_shadow_{key}")
    result = await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True, return_response=True)
    assert result["shadow_removed"] == 2
    assert registry.async_get("sensor.pe_shadow_diag_version") is None
    assert devices.async_get(device.id) is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_it_says_when_a_switch_had_no_state_to_carry_over(hass, tmp_path):
    old_app(hass)
    hass.states.async_set("switch.pe_ctl_pause", "unavailable")           # the app was stopped before the migration
    entry = await start(hass, tmp_path)
    result = await hass.services.async_call(DOMAIN, const.SERVICE_MIGRATE, {}, blocking=True, return_response=True)
    assert any("switch.pe_ctl_pause" in note for note in result["notes"])
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_shadow_run_has_no_migration(hass, tmp_path):
    entry = await start(hass, tmp_path, mode=const.MODE_SHADOW)
    assert not hass.services.has_service(DOMAIN, const.SERVICE_MIGRATE)
    await hass.config_entries.async_unload(entry.entry_id)
