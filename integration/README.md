# PowerEngine as a Home Assistant integration

The integration in `custom_components/powerengine/` starts the PowerEngine engine as its own process, keeps it running,
and gives it a way to read and act on Home Assistant. Updating PowerEngine then restarts only the engine, not Home
Assistant. The plan, the phases and how a running system migrates are in
[docs/plans/native-integration.md](../docs/plans/native-integration.md).

| File | What it does |
|---|---|
| `__init__.py` | Sets the entry up: the server for the engine, the supervisor, the once-a-minute check, the `restart_engine` service. |
| `supervisor.py` | Starts `engine/engine_main.py` as a process of Home Assistant's Python, forwards its log, restarts it after a wait, restarts it when its files change, and does not restart it for a protocol mismatch (exit 10). |
| `bridge.py` | `HassHome`: answers the engine's requests (states, history, templates, service calls, events) from Home Assistant, and sends it the changes and events it asked for. |
| `native.py` | The engine's own entities as real entities: registry, unique ids `powerengine_<key>`, ids `sensor.pe_<key>`, one device. The engine describes them, so a new one needs no integration update. |
| `sensor.py`, `binary_sensor.py`, `switch.py`, `button.py` | The platforms (the engine status sensor and "Restart engine" button are the integration's own). |
| `config_flow.py`, `diagnostics.py`, `strings.json` | Setup (one entry, the settings folder), download diagnostics, Repairs text. |

`protocol.py` and `host_server.py` are **not** in this folder: they are copied from `apps/powerengine/pe_core/` by the
build, so both ends are written against the same files. `engine/` is the whole app (`apps/powerengine/`), copied in by
the build too.

## Build and test

```bash
python tools/build_integration.py            # integration/build/custom_components/powerengine/ (what HACS would install)
python -m venv hav && hav/bin/pip install -r integration/requirements.txt
cd integration && ../hav/bin/python -m pytest        # the tests build it first, and start the real engine as a process
```

The tests run on the built folder, against a real Home Assistant (`pytest-homeassistant-custom-component`), with the
real engine as the process. The engine's own tests (`pytest` at the repository root) cover the protocol, the socket host
and the engine process without Home Assistant.

## Safety

The integration will not start the engine while another PowerEngine is running (the AppDaemon app's heartbeat sensor
updated in the last five minutes): two would both control the inverter. It raises a Repair and starts the engine within
a minute of the other stopping. It does not yet run in shadow mode; that, and the migration, are phase 5.
