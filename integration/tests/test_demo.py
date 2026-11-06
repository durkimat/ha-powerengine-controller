"""The demo, started from the dashboard on a PowerEngine that is not set up, run by the real engine."""

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


async def test_the_demo_starts_runs_on_the_integrations_entities_and_exits(hass, tmp_path):
    await start(hass, tmp_path)
    await running(hass)
    assert version(hass).attributes["setup"] == "unconfigured" and not version(hass).attributes.get("demo")
    assert [d["key"] for d in version(hass).attributes["demo_days"]] == ["sunny", "dull", "axle", "car"]

    await ask(hass, {"action": "start", "day": "sunny"}, lambda: demo_title(hass) == "Sunny day", "the demo to start")
    soc = "sensor.pe_state_battery_soc"
    await wait_for(hass, lambda: hass.states.get(soc) and hass.states.get(soc).state not in ("unknown", "unavailable"),
                   what="the simulated battery")
    assert 0 < float(hass.states.get(soc).state) <= 100
    # its entities are the integration's (one registry entry each), and none went unavailable while the engine restarted
    assert not [s.entity_id for s in pe_states(hass) if s.state == "unavailable"]
    assert os.path.isfile(tmp_path / "demo.json") and not os.path.exists(tmp_path / "config.yaml")

    await ask(hass, {"action": "day", "day": "dull"}, lambda: demo_title(hass) == "Dull day", "the day to change")

    await ask(hass, {"action": "exit"}, lambda: version(hass).state not in ("unavailable", "unknown")
              and not version(hass).attributes.get("demo"), "the demo to end")
    assert version(hass).attributes["setup"] == "unconfigured" and not os.path.exists(tmp_path / "demo.json")
