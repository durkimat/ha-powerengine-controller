# PowerEngine as a Home Assistant integration

The integration in [`custom_components/powerengine/`](../custom_components/powerengine/) (at the repository root, where HACS looks) starts the PowerEngine engine as its own process, keeps it running,
and gives it a way to read and act on Home Assistant. Updating PowerEngine then restarts only the engine, not Home
Assistant. The plan and the phases are in
[docs/plans/native-integration.md](../docs/plans/native-integration.md).

| File | What it does |
|---|---|
| `__init__.py` | Sets the entry up: the server for the engine, the supervisor, the once-a-minute check, the `restart_engine` service. |
| `supervisor.py` | Starts `engine/engine_main.py` as a process of Home Assistant's Python, forwards its log, restarts it after a wait, restarts it when its files change, and does not restart it for a protocol mismatch (exit 10). |
| `bridge.py` | `HassHome`: answers the engine's requests (states, history, templates, service calls, events) from Home Assistant, and sends it the changes and events it asked for. |
| `native.py` | The engine's own entities as real entities: registry, unique ids `powerengine_<key>`, ids `sensor.pe_<key>`, one device. The engine describes them, so a new one needs no integration update. |
| `sensor.py`, `binary_sensor.py`, `switch.py`, `button.py` | The platforms (the engine status sensor and "Restart engine" button are the integration's own). |
| `config_flow.py`, `diagnostics.py`, `strings.json` | Setup (one entry, the settings folder), download diagnostics, Repairs text. |

`engine/` (the whole app, `engine/`), `protocol.py` and `host_server.py` (from `engine/pe_core/`) and
the manifest's `version` are **generated** by `tools/build_integration.py` and committed, because HACS installs a
repository's `custom_components` folder as it stands. The release tools run `--sync` after they bump the version, and CI
(`tests/test_integration_sync.py`) fails a change that leaves them out of date: after changing the app, run

```bash
python tools/build_integration.py --sync
```

## Test

```bash
python -m venv hav && hav/bin/pip install -r integration/requirements.txt
cd integration && ../hav/bin/python -m pytest        # starts the real engine as a process, in a real Home Assistant
```

The tests run against Home Assistant (`pytest-homeassistant-custom-component`) with the real engine as the process. The
engine's own tests (`pytest` at the repository root) cover the protocol, the socket host and the engine process without
Home Assistant.

## Safety

The integration starts one engine and watches it. If the engine goes quiet for 15 minutes while it is in control of the
inverter, the integration's watchdog hands the inverter back to Self-Use (`custom_components/powerengine/watchdog.py`).

In **Shadow** mode it runs beside the live one on a copy of its data, under separate entities, and refuses every call
and event the engine asks for; see [docs/plans/shadow-run.md](../docs/plans/shadow-run.md). `tools/shadow_compare.py`
compares the two from Home Assistant's history.
