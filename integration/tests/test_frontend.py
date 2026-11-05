"""The dashboard card is served by the integration and added to the frontend's pages (frontend.py)."""

import re

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerengine import const, frontend
from custom_components.powerengine.const import DOMAIN
from custom_components.powerengine.supervisor import EngineSupervisor


async def setup_without_engine(hass, tmp_path, monkeypatch, **options):
    """The integration set up, with the supervisor not starting an engine (these tests are about the card)."""
    monkeypatch.setattr(EngineSupervisor, "start", lambda self: None)
    hass.data[DATA_EXTRA_MODULE_URL] = set()          # what Home Assistant's frontend keeps (it is not running here)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path), **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_the_card_is_served_and_added_to_the_pages_with_a_fingerprint(hass, hass_client, tmp_path, monkeypatch):
    entry = await setup_without_engine(hass, tmp_path, monkeypatch)
    (url,) = hass.data[DATA_EXTRA_MODULE_URL]
    assert re.fullmatch(re.escape(frontend.CARD_URL) + r"\?v=[0-9a-f]{12}", url), url
    client = await hass_client()
    response = await client.get(url)
    body = await response.text()
    assert response.status == 200 and "customElements.define" in body and "powerengine-update-card" in body
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_replaced_card_gets_a_new_address_and_unloading_removes_it(hass, tmp_path, monkeypatch):
    card = tmp_path / "card.js"
    card.write_text("// one")
    monkeypatch.setattr(frontend, "CARD_FILE", card)
    entry = await setup_without_engine(hass, tmp_path, monkeypatch)
    (first,) = hass.data[DATA_EXTRA_MODULE_URL]
    await frontend.async_refresh(hass)
    assert hass.data[DATA_EXTRA_MODULE_URL] == {first}                      # unchanged: the same address
    card.write_text("// two")
    await frontend.async_refresh(hass)                                       # what the once-a-minute check does
    (second,) = hass.data[DATA_EXTRA_MODULE_URL]
    assert second != first and second.startswith(frontend.CARD_URL)
    await hass.config_entries.async_unload(entry.entry_id)
    assert hass.data[DATA_EXTRA_MODULE_URL] == set()


async def test_a_shadow_run_does_not_add_the_card(hass, tmp_path, monkeypatch):
    entry = await setup_without_engine(hass, tmp_path, monkeypatch, **{const.CONF_MODE: const.MODE_SHADOW})
    assert hass.data[DATA_EXTRA_MODULE_URL] == set()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_without_the_frontend_nothing_is_added_and_nothing_breaks(hass, tmp_path, monkeypatch):
    monkeypatch.setattr(EngineSupervisor, "start", lambda self: None)
    hass.data.pop(DATA_EXTRA_MODULE_URL, None)                               # no frontend (a test, or Core without one)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
