"""The demo on a PowerEngine that is not set up: it starts by itself, and the dashboard's buttons change or end it."""

import os

from test_setup import running, start, wait_for


def version(hass):
    return hass.states.get("sensor.pe_diag_version")


def demo_title(hass):
    return (version(hass).attributes.get("demo") or {}).get("title")


async def ask(hass, data, condition, what, tries=8):
    """Press the card's button (the engine may not be listening for the first second or two) until it has happened."""
    for _ in range(tries):
        hass.bus.async_fire("pe_demo", data)
        try:
            await wait_for(hass, condition, timeout=10, what=what)
            return
        except AssertionError:
            continue
    raise AssertionError(f"timed out waiting for {what}")


def pe_states(hass):
    return [s for s in hass.states.async_all() if s.entity_id.split(".", 1)[1].startswith("pe_")]


async def settled(hass):
    await wait_for(hass, lambda: version(hass).state not in ("unavailable", "unknown"), what="the engine's entities")


async def test_a_new_install_shows_the_demo_and_the_buttons_change_it_and_leave_it(hass, tmp_path):
    await start(hass, tmp_path)
    await running(hass)
    await wait_for(hass, lambda: demo_title(hass) == "Sunny day", timeout=60, what="the demo to start by itself")
    soc = "sensor.pe_state_battery_soc"
    await wait_for(hass, lambda: hass.states.get(soc) and hass.states.get(soc).state not in ("unknown", "unavailable"),
                   what="the simulated battery")
    assert 0 < float(hass.states.get(soc).state) <= 100
    assert version(hass).attributes["setup"] == "unconfigured"
    assert [d["key"] for d in version(hass).attributes["demo"]["days"]] == ["sunny", "dull", "axle", "car"]
    # its entities are the integration's, and none is unavailable now the engine has started
    assert not [s.entity_id for s in pe_states(hass) if s.state == "unavailable"]
    assert not os.path.exists(tmp_path / "config.yaml")

    await ask(hass, {"action": "day", "day": "dull"}, lambda: demo_title(hass) == "Dull day", "the day to change")

    await ask(hass, {"action": "exit"}, lambda: version(hass).attributes.get("setup") == "unconfigured"
              and not version(hass).attributes.get("demo"), "the demo to end")
    assert version(hass).attributes["setup"] == "unconfigured" and (tmp_path / "demo_off").exists()

    await ask(hass, {"action": "start", "day": "car"}, lambda: demo_title(hass) == "Car charging day",
              "the demo to start again")
    assert not (tmp_path / "demo_off").exists()


async def test_a_left_demo_does_not_come_back_by_itself(hass, tmp_path):
    (tmp_path / "demo_off").write_text("")
    await start(hass, tmp_path)
    await running(hass)
    await settled(hass)
    await wait_for(hass, lambda: version(hass).attributes.get("setup") == "unconfigured", what="the setup state")
    assert not version(hass).attributes.get("demo")
