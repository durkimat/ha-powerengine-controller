# Plan: PowerEngine as a Home Assistant integration with its own engine process

**Goal.** Install PowerEngine from HACS in one step, with no AppDaemon, no `apps.yaml` and (with direct publishing) no
MQTT, and still update the engine often without restarting Home Assistant.

**Shape.**

```
custom_components/powerengine/        thin and stable: a Home Assistant restart is needed only when it changes
  config flow, supervisor, bridge, entity platforms, services, diagnostics, repairs
  engine/                             changes often: the integration restarts just this process
```

- The integration starts the engine as a subprocess of Home Assistant's own Python (so it works on Container and Core
  installs too), restarts it if it dies, and sends its log to Home Assistant's.
- They talk over a local connection with a shared secret and a versioned protocol. The engine holds no Home Assistant
  token: the integration sends it the states it asked for and the events it needs, and makes the service calls it asks
  for.
- The engine keeps a local copy of the states it subscribed to, so `get_state` stays synchronous and instant, as the
  code expects.
- A HACS update replaces the files. The integration sees the engine's `VERSION` change (or the owner presses "Update
  engine") and restarts only the engine. A protocol change is the one thing that needs a Home Assistant restart, and a
  Repair says so.
- The card keeps using the same events (`pe_config_save`, `pe_set_control`, ...). The integration forwards them, so the
  card needs no change for the first cut-over.

## Phases

1. **A host seam in the app (done in 0.9.201).** Every call to AppDaemon goes through `self.host`
   (`pe_core/host.py`); `tests/test_host.py` fails if a new direct call appears. No change in behaviour.
2. **Engine process.**
   - **2a (done in 0.9.202):** the AppDaemon-specific code that was left (the demo's timer and listener tracking, the
     REST and MQTT publishing set-up, the plugin lookup) is in `pe_core/host_appdaemon.py`, and the `Engine` class in
     `powerengine.py` no longer inherits from AppDaemon's `Hass`; the AppDaemon app is `Engine` plus `Hass`.
   - **2b (done in 0.9.203):** everything the engine needs to run without AppDaemon, built and tested, but nothing in a
     running system uses it until the integration exists:
     - `pe_core/host_scheduler.py`: `run_in`, `run_every`, `run_daily` and `cancel_timer` with AppDaemon's meanings
       (start strings `now`, `now+N`, `HH:MM:SS`; wall-clock days across clock changes; a late repeat does not burst),
       and the one thread every callback runs on, so callbacks never overlap, as in AppDaemon (the engine relies on it).
     - `pe_core/protocol.py`: the versioned messages, the handshake (shared secret, protocol version) and the framing.
     - `pe_core/host_socket.py` (`SocketHost`, the engine's side): a local copy of every entity read, so `get_state`
       stays synchronous; state and event listeners fed by the integration; service calls, events, history and
       templates as requests; carrying on from the copy through a lost connection and reconnecting.
     - `pe_core/host_server.py` (`HostServer`, the integration's side, written against a small `Home` interface so
       Phase 3 only has to implement `Home` over Home Assistant).
     - `apps/powerengine/engine_main.py`: the engine process (`PE_SECRET=... python engine_main.py --address ...`), with
       exit statuses the supervisor can act on (10: protocol mismatch, so a Home Assistant restart is needed).
     - Tests: the same questions put to the AppDaemon host and the socket host, the handshake, reconnection, the timers,
       and the real engine started as a real process against a fake Home Assistant.
     - **Not done, on purpose:** moving `Engine` out of `powerengine.py`. The AppDaemon import in that file is already
       guarded, so the engine runs from it in its own process today, and a move would only change which file holds
       it and rewrite about 50 test patches. Revisit when Phase 3 decides how the engine is packaged inside the
       integration.
3. **The integration (built on the `integration` branch; not released, because HACS needs a place to install it from, see
   below).** `integration/custom_components/powerengine/` (`integration/README.md` has the file map):
   - a minimal config flow (one entry; the settings folder, defaulting to the app's `powerengine` folder);
   - the supervisor: starts the engine as a process of Home Assistant's own Python, forwards its log, restarts it after
     5 s, 10 s, 30 s, 1 min and 5 min if it stops, raises a Repair if it keeps stopping, restarts it when an update
     replaced its files (checked every minute; the "Restart engine" button and `powerengine.restart_engine` do it at
     once), and does not restart it for a protocol mismatch (exit 10; a Repair says to restart Home Assistant);
   - the bridge (`HassHome`): states (the engine keeps a copy), history from the recorder, templates, service calls
     (which wait for the device, as a slow inverter needs), events with who caused them;
   - native entities from the engine's catalogue (`native.py`): in the registry with the same unique ids MQTT gave
     them (`powerengine_<key>`), the same entity ids (`sensor.pe_...`), on one device; the engine describes them, so a
     new entity needs no integration update. The pause switch holds what it is given and the engine hears a change as
     any other state change. Values Home Assistant would reject (a bad time, an enum value not in its options)
     become `unknown` with one warning;
   - diagnostics (status, versions, the engine's last log lines, no secret) and Repairs;
   - a guard: it will not start the engine while the AppDaemon app is running (its heartbeat sensor updated in the
     last five minutes), because two would both control the inverter.
   Tested against Home Assistant 2026.10.4 (`pytest-homeassistant-custom-component`), with the real engine as the
   process.
4. **Tests.** Entity creation and the bridge with `pytest-homeassistant-custom-component`; the engine against a fake
   host; a shadow run compared with the live app (`tools/engine_compare.py`).
5. **Cut-over tooling and migration** (below).

## Migrating a running system

Rules: no change to any data file format in the migration releases, and the engine never writes to the inverter while
it is in shadow mode.

1. Back up: a Home Assistant backup, the cost history and a copy of `/config/powerengine`.
2. **Shadow run, for days.** The integration runs alongside the AppDaemon app on a *copy* of the data folder, with all
   writes blocked and its entities under a temporary prefix. Compare its plans with the live app's and fix every
   difference.
3. **Cut-over** in a quiet daytime window (not during a grid event or a cheap-rate charge): stop and disable the app
   (keep a backup of `apps.yaml`); clear the old entities (with MQTT publishing, empty retained discovery messages and
   the registry entries; with direct publishing there is nothing registered to remove); create the native entities
   with the **same entity ids**, so history, dashboards and automations carry on; sync the data folder one last time;
   start the engine on the real folder in Passive, check, then Active.
4. **Verify**: entity ids, history continuity, the card and Config page, one full planning cycle, the cost book,
   notifications, and the first real inverter command against what the old app would have done.
5. **Rollback** stays ready: disable the integration entry and re-enable the AppDaemon app. Nothing needs converting
   because the file formats are unchanged.
6. Keep the AppDaemon path for a few weeks, then remove it from the docs and the releases. For other owners, the
   config flow detects `/config/powerengine/config.yaml` and offers to import it, telling them to stop the AppDaemon
   app first.

## Risks and open decisions

- **Where HACS installs the integration from (decide before releasing it).** A HACS repository is one category, and
  `ha-powerengine-controller` is an AppDaemon one. Options: a new repository (`ha-powerengine`, integration category)
  whose release workflow runs `tools/build_integration.py` and commits `custom_components/powerengine/`; or turn this
  repository into the integration repository and keep the AppDaemon app as a legacy install. The card (a third HACS
  entry) could later be registered by the integration itself, so there is one thing to install.
- The "Entity publishing" setting offers auto, MQTT and direct; under the integration `auto` means native. Nothing in
  the Config page names native yet.
- `expire_after` (the heartbeat sensor goes unavailable after 5 minutes without a beat under MQTT) is not applied to
  native entities yet.

- Entity ids must not change: pre-create registry entries with a fixed `suggested_object_id`. Test on a copy first.
- Timer behaviour (worker threads, `run_every` start strings such as `"now+240"`) must match AppDaemon's; the damping,
  early-target and event-run logic depends on it.
- With MQTT publishing, Mosquitto goes away only if nothing else uses it.
- The handover packages (`ha_packages/`) can become integration services later; leave them alone at first.
- The card moves to websocket commands later, not in the first cut-over.
