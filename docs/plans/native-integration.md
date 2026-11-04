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
   - **2b:** a scheduler for `run_in`/`run_every`/`run_daily` on a thread pool; a socket `Host` with the state copy,
     service calls and reconnection; a protocol module shared by both sides, with contract tests. Built and tested, but
     nothing in a running system uses it until the integration exists. Also move `Engine` out of `powerengine.py`
     into the engine package (the tests patch names in the `powerengine` module, so that move comes with a test
     update).
3. **The integration.** Manifest and a minimal config flow; the supervisor; the bridge; entity platforms built from
   `entities.py` (the set depends on the config, so entities are added and removed as it changes); diagnostics; Repairs.
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

- Entity ids must not change: pre-create registry entries with a fixed `suggested_object_id`. Test on a copy first.
- Timer behaviour (worker threads, `run_every` start strings such as `"now+240"`) must match AppDaemon's; the damping,
  early-target and event-run logic depends on it.
- With MQTT publishing, Mosquitto goes away only if nothing else uses it.
- The handover packages (`ha_packages/`) can become integration services later; leave them alone at first.
- The card moves to websocket commands later, not in the first cut-over.
