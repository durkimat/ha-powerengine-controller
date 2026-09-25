"""A small, anonymised system snapshot (shaped like a Solis + EDF + Zappi + Axle setup)."""

from datetime import datetime, timedelta, timezone

from pe_core.config import parse_config

BST = timezone(timedelta(hours=1))
NOW = datetime(2026, 9, 22, 17, 58, tzinfo=BST)


def _rates(day, cheap_until_slot=10, cheap=0.06993, peak=0.302831):
    start = datetime(2026, 9, day, 0, 0, tzinfo=BST)
    return [{"start": (start + timedelta(minutes=30 * i)).isoformat(),
             "end": (start + timedelta(minutes=30 * (i + 1))).isoformat(),
             "value_inc_vat": cheap if i < cheap_until_slot else peak} for i in range(48)]


def S(state, unit=None, **attrs):
    a = dict(attrs)
    if unit:
        a["unit_of_measurement"] = unit
    return {"state": state, "attributes": a, "last_updated": NOW.isoformat()}


STATES = {
    "sensor.bat_soc": S("71", "%"),
    "sensor.bat_power": S("1040", "W"),
    "sensor.meter_power": S("-240", "W"),
    "sensor.house_load": S("1238", "W"),
    "sensor.pv_power": S("96", "W"),
    "sensor.garage_pv": S("0.05", "kW"),
    "sensor.pv_today": S("14.9", "kWh"),
    "sensor.garage_today": S("0.5", "kWh"),
    "sensor.car_power": S("0", "W"),
    "sensor.rate_now": S("0.302831", "GBP/kWh"),
    "sensor.export_rate": S("0.15", "GBP/kWh"),
    "event.rates_today": S("2026-09-22T16:03:17", rates=_rates(22)),
    "event.rates_tomorrow": S("2026-09-22T16:03:17", rates=_rates(23)),
    "binary_sensor.dispatching": S("off", planned_dispatches=[
        {"start": "2026-09-22T21:00:00+01:00", "end": "2026-09-22T21:30:00+01:00", "charge_in_kwh": -0.7},
        {"start": "2026-09-22T21:30:00+01:00", "end": "2026-09-23T09:30:00+01:00", "charge_in_kwh": -84.0},
    ]),
    "sensor.plug": S("Waiting for EV"),
    "sensor.charger": S("Completed"),
    "sensor.axle_active": S("off"),
    "sensor.axle_start": S("unknown"),
    "sensor.axle_end": S("unknown"),
    "binary_sensor.free_now": S("off"),
    "sensor.free_start": S("unknown"),
    "sensor.free_end": S("unknown"),
    "sensor.fc_today": S("14.5291", "kWh", detailedForecast=[{"period_start": "x", "pv_estimate": 0.6}] * 48),
}

CONFIG = parse_config({
    "inputs": {
        "battery_soc": {"entity": "sensor.bat_soc"},
        "battery_power": {"entity": "sensor.bat_power"},
        "grid_power": {"entity": "sensor.meter_power"},
        "house_load_power": {"entity": "sensor.house_load"},
        "ev_charge_power": {"entity": "sensor.car_power"},
        "import_rate_now": {"entity": "sensor.rate_now"},
        "export_rate": {"entity": "sensor.export_rate"},
        "import_rates_today": {"entity": "event.rates_today"},
        "import_rates_tomorrow": {"entity": "event.rates_tomorrow"},
        "smart_dispatches": {"entity": "binary_sensor.dispatching"},
        "ev_plug_status": {"entity": "sensor.plug"},
        "ev_charger_status": {"entity": "sensor.charger"},
        "axle_event_active": {"entity": "sensor.axle_active"},
        "axle_event_start": {"entity": "sensor.axle_start"},
        "axle_event_end": {"entity": "sensor.axle_end"},
        "free_power_active": {"entity": "binary_sensor.free_now"},
        "free_power_next_start": {"entity": "sensor.free_start"},
        "free_power_next_end": {"entity": "sensor.free_end"},
        "solar_forecast_today": {"entity": "sensor.fc_today"},
    },
    "solar_plants": [
        {"id": "main", "power": {"entity": "sensor.pv_power"}, "energy_today": {"entity": "sensor.pv_today"}},
        {"id": "garage", "power": {"entity": "sensor.garage_pv"}, "energy_today": {"entity": "sensor.garage_today"}},
    ],
})


def get_state(states=None):
    states = STATES if states is None else states
    return lambda eid: states.get(eid)


AXLE_19_20 = {"sensor.axle_start": S("2026-09-22T19:00:00+01:00"), "sensor.axle_end": S("2026-09-22T20:00:00+01:00")}
