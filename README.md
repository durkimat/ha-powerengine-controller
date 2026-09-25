# PowerEngine

A Home Assistant energy controller for a solar + battery + EV home, running as an
[AppDaemon](https://appdaemon.readthedocs.io/) app. It decides when to charge,
hold, discharge or export the battery using tariff prices, solar forecasts, EV
smart-charge slots and VPP events, and explains every decision in plain English.

> **Status: early development (0.0.x).** Current builds are scaffolding only and
> do not control any device.

## Install (HACS)

1. HACS → ⋮ → *Custom repositories* → add this repo with category **AppDaemon**
   (enable AppDaemon apps in the HACS integration options first).
2. Install **PowerEngine**. The app ships its own app definition, so no
   `apps.yaml` edit is needed.
3. Install the companion [config card](https://github.com/durkimat/ha-powerengine-card).

Settings live in `/homeassistant/powerengine/config.yaml`, outside the app
folder, so updates never overwrite them. A fresh install runs in **dry run**.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

Decision logic lives in `apps/powerengine/pe_core/` and has no Home Assistant
dependency; `powerengine.py` is a thin AppDaemon adapter.
