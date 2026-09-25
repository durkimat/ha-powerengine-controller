# PowerEngine

A Home Assistant energy controller for a solar + battery + EV home, running as an
[AppDaemon](https://appdaemon.readthedocs.io/) app. It decides when to charge,
hold, discharge or export the battery using tariff prices, solar forecasts, EV
smart-charge slots and VPP events, and explains every decision in plain English.

> **Status: beta (0.1.x), Passive only.** PowerEngine monitors and explains your
> system and has its own dashboard. It does not control any device yet.

## Install

Follow the **[installation guide](docs/INSTALL.md)**. It covers the MQTT broker,
AppDaemon setup, HACS, the config page, updating, rollback, uninstalling and
troubleshooting, with a check at each step.

A fresh install runs in **Passive** mode: it monitors, plans and simulates, but
never controls anything. Your settings live in
`/homeassistant/powerengine/config.yaml`, outside the app folder, so updates
never overwrite them.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

Decision logic lives in `apps/powerengine/pe_core/` and has no Home Assistant
dependency; `powerengine.py` is a thin AppDaemon adapter.
