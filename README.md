# PowerEngine

A Home Assistant energy controller for a solar + battery + EV home, running as an
[AppDaemon](https://appdaemon.readthedocs.io/) app. It decides when to charge,
hold, discharge or export the battery using tariff prices, solar forecasts, EV
smart-charge slots and VPP events, and explains every decision in plain English.

> **Status: early development (0.0.x).** Current builds are scaffolding only and
> do not control any device.

## Install (HACS)

0. **AppDaemon add-on (one-off):** HACS installs AppDaemon apps into
   `/homeassistant/appdaemon/apps/`, but recent AppDaemon add-on versions read
   apps from their own folder (`/addon_configs/a0d7b954_appdaemon/apps/`).
   Move any existing apps into `/homeassistant/appdaemon/apps/`, then add this
   under `appdaemon:` in `/addon_configs/a0d7b954_appdaemon/appdaemon.yaml` and
   restart the add-on:

   ```yaml
   appdaemon:
     app_dir: /homeassistant/appdaemon/apps
   ```
   **MQTT (one-off):** PowerEngine creates its entities through MQTT discovery,
   so AppDaemon needs its MQTT plugin. Create a non-admin HA user for it (the
   Mosquitto add-on accepts HA logins), put the login in
   `/addon_configs/a0d7b954_appdaemon/secrets.yaml` as `powerengine_mqtt_user` /
   `powerengine_mqtt_password`, and add this under `appdaemon:` → `plugins:`
   (next to `HASS:`), then restart the add-on:

   ```yaml
       MQTT:
         type: mqtt
         namespace: mqtt
         client_host: core-mosquitto
         client_port: 1883
         client_user: !secret powerengine_mqtt_user
         client_password: !secret powerengine_mqtt_password
         client_id: appdaemon-powerengine
         client_topics: NONE   # publish only; don't subscribe to every topic
   ```
1. HACS → ⋮ → *Custom repositories* → add this repo with category **AppDaemon**
   (enable AppDaemon apps in the HACS integration options first).
2. Install **PowerEngine**. The app ships its own app definition, so no
   `apps.yaml` edit is needed.
3. Install the companion [config card](https://github.com/durkimat/ha-powerengine-card).

Settings live in `/homeassistant/powerengine/config.yaml`, outside the app
folder, so updates never overwrite them. A fresh install runs in **Passive** mode: it monitors, plans and simulates,
but never controls anything. Active mode is switched on from the config page,
and only once the build supports it.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

Decision logic lives in `apps/powerengine/pe_core/` and has no Home Assistant
dependency; `powerengine.py` is a thin AppDaemon adapter.
