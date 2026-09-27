"""PowerEngine Modbus probe: read (and, for a few remote-control registers, write) Solis registers through the
existing SolaX Modbus connection, without adding polled entities.

Added to check why RAM remote control caps at about 2 kW: the remote-control block has "battery charge limit
power" (43130) and "battery discharge limit power" (43131) next to the force powers (43136 charge, 43129
discharge), and a remote-control timeout (43282). SolaX Modbus doesn't expose them for this model.

Services (Developer tools -> Actions, tick "Return response"):
  pe_modbus_probe.read   address, count (1-20), register_type (holding|input)  -> {"values": [...]}
  pe_modbus_probe.write  address (43129/43130/43131/43136/43282 only), value (raw)
Every call is logged and shown as a notification.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

DOMAIN = "pe_modbus_probe"
_LOGGER = logging.getLogger(__name__)
WRITABLE = {43129, 43130, 43131, 43136, 43282}

READ_SCHEMA = vol.Schema({
    vol.Required("address"): vol.All(vol.Coerce(int), vol.Range(min=30000, max=49999)),
    vol.Optional("count", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
    vol.Optional("register_type", default="holding"): vol.In(["holding", "input"]),
    vol.Optional("hub"): str,
})
WRITE_SCHEMA = vol.Schema({
    vol.Required("address"): vol.All(vol.Coerce(int), vol.In(sorted(WRITABLE))),
    vol.Required("value"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
    vol.Optional("hub"): str,
})


def _hub(hass: HomeAssistant, name: str | None):
    hubs = {k: v["hub"] for k, v in (hass.data.get("solax_modbus") or {}).items()
            if isinstance(v, dict) and "hub" in v}
    if not hubs:
        raise HomeAssistantError("No SolaX Modbus hub found")
    if name:
        if name not in hubs:
            raise HomeAssistantError(f"No SolaX Modbus hub named '{name}' (found: {', '.join(hubs)})")
        return name, hubs[name]
    if len(hubs) > 1:
        raise HomeAssistantError(f"Several SolaX Modbus hubs ({', '.join(hubs)}): say which with 'hub'")
    return next(iter(hubs.items()))


async def _notify(hass: HomeAssistant, message: str) -> None:
    await hass.services.async_call("persistent_notification", "create",
                                   {"title": "PowerEngine Modbus probe", "message": message,
                                    "notification_id": "pe_modbus_probe"})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def read(call: ServiceCall) -> ServiceResponse:
        name, hub = _hub(hass, call.data.get("hub"))
        addr, count, kind = call.data["address"], call.data["count"], call.data["register_type"]
        unit = getattr(hub, "_modbus_addr", 1)
        fn = hub.async_read_holding_registers if kind == "holding" else hub.async_read_input_registers
        resp = await fn(unit=unit, address=addr, count=count)
        if resp is None or getattr(resp, "isError", lambda: True)():
            raise HomeAssistantError(f"Read of {kind} {addr} x{count} failed: {resp}")
        values = list(resp.registers)
        rows = {str(addr + i): v for i, v in enumerate(values)}
        _LOGGER.warning("Probe read %s %s x%s via %s: %s", kind, addr, count, name, rows)
        await _notify(hass, f"Read {kind} {addr}-{addr + count - 1} via {name}:\n"
                            + "\n".join(f"- {k}: {v}" for k, v in rows.items()))
        return {"hub": name, "type": kind, "values": rows}

    async def write(call: ServiceCall) -> ServiceResponse:
        name, hub = _hub(hass, call.data.get("hub"))
        addr, value = call.data["address"], call.data["value"]
        unit = getattr(hub, "_modbus_addr", 1)
        await hub.async_write_register(unit=unit, address=addr, payload=value)
        _LOGGER.warning("Probe wrote %s = %s via %s", addr, value, name)
        await _notify(hass, f"Wrote {addr} = {value} via {name}. Read it back to confirm.")
        return {"hub": name, "address": addr, "value": value}

    hass.services.async_register(DOMAIN, "read", read, schema=READ_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "write", write, schema=WRITE_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
    return True
