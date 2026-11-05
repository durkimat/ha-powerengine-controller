# Cut-over: from the AppDaemon app to the integration

For a system that has run the shadow ([shadow-run.md](shadow-run.md)) and compared well. The aim: the engine runs as the
integration's process, on the real data folder, publishing the same entity ids, with no gap in history and with the
inverter never left under two controllers. Plan and reasons: [native-integration.md](native-integration.md).

**Choose** a quiet daytime window: no grid event, no cheap-rate charge or sale running or about to start, the car not
charging on a plan. Allow an hour. Nothing in the data files changes format, so the way back is open the whole time.

## What the integration does for you

- It will not start the engine while the app's heartbeat (`sensor.pe_diag_heartbeat`) is under five minutes old.
- It will not start the engine while the app's MQTT entities are in the registry (it would make `sensor.pe_..._2`
  duplicates). A Repair says so. The `powerengine.migrate_from_mqtt` service takes the app's entities away:
  - writes `<folder>/migration/mqtt-entities-<time>.json` (every old entity's state and what you had set on it: name,
    icon, area, labels, hidden);
  - clears the app's retained MQTT messages (discovery, state, attributes, the two switches' command topics), so the
    old entities do not return at the next start;
  - removes the old registry entries and any state left behind, and a shadow run's `sensor.pe_shadow_...` entries;
  - then lets the engine start. The new entities take the same ids; the pause and right-align switches start in the
    state they had; what you had set on an entity is put back on its new one. History carries on by entity id.
  It refuses while the app is still beating. Running it again finds nothing.

## Steps

1. **Back up.** A Home Assistant backup; `tar czf` of `/config/powerengine`; a copy of the app's yaml
   (`/config/appdaemon/apps/ha-powerengine-controller/powerengine.yaml`) and of `/config/.storage/core.entity_registry`
   and `core.restore_state`. Note the Pause and Right-align switches' states: the migration can only carry them over
   while the app's entities still have a state, and by then the app is stopped (it says so when it could not).
2. **Put the live app in Passive** (Config page, or `operation: mode: passive` in config.yaml). The app hands the
   inverter back to Self-Use and sends nothing, so nothing after this can leave the inverter in a forced state. Check
   `sensor.pe_state_control_method` / the inverter's mode.
3. **Install the integration's current files** on Home Assistant (copy `custom_components/powerengine/`, or HACS once it
   is released as an integration) and restart Home Assistant once. If it already runs as a shadow it comes back as one.
4. **Make the entry live**: Settings, Devices & services, PowerEngine, Configure: mode Live. It waits ("Waiting",
   with a Repair that the app is running).
5. **Stop the AppDaemon app**: move `powerengine.yaml` aside, then **restart the AppDaemon add-on** (it did not unload a
   running app when the file moved, on 5 Nov 2026), or disable the app in HACS. The old heartbeat goes `unavailable`
   when the add-on stops; the migration refuses while it is still updating, so otherwise wait five minutes.
6. **Run `powerengine.migrate_from_mqtt`** (Developer tools, Actions; a call from the REST API returns 500 whatever
   happens, as the service answers with data). It returns what it did, including anything to set by hand. The engine starts within
   seconds, in **Passive** (the config says so), on `/config/powerengine`.
7. **Check, still Passive** (for an hour, through at least one planning cycle):
   - the entity ids and counts match the snapshot (`sensor.pe_*`, 86 entities); no `_2` duplicates; history graphs
     continue across the gap;
   - the card and the Config page load and save; the Costs tab and cost book carry on; notifications work;
   - `sensor.pe_state_decision`, the plan and the inverter-command preview are what you would expect.
8. **Go Active** (Config page). Watch the first real command against what the old app would have done: the log line
   "RAM remote control: ..." and the inverter's own state. Keep watching through a charge and a sale.
9. **For a few days**, look each morning at the engine sensor, the Repairs list and the log.
10. **Only then change how it is installed**: remove the AppDaemon entry in HACS (this deletes the app's files), add the
    repository again as an integration, and download. The integration's files are the same, so nothing restarts the
    engine unless the version differs.

## Rolling back

Disable (or delete) the PowerEngine entry: the engine stops and its entities go. Put `powerengine.yaml` back: the app
starts, publishes its MQTT entities again under the same ids and carries on from the same data folder. If the app was
removed in HACS, download it again from HACS first. Put the app in Active from the Config page when it is running.
