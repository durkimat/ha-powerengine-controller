# PowerEngine as a Home Assistant integration

PowerEngine runs as a Home Assistant **integration**. The integration is thin and
rarely changes; the engine that plans and controls is a separate process that it starts and watches. So:

- no AppDaemon, no `apps.yaml` and no MQTT broker are needed (PowerEngine's entities are real Home Assistant entities
  with the same ids, `sensor.pe_...`, as before);
- updating PowerEngine in HACS restarts **only the engine**, within a minute, and not Home Assistant. Home Assistant
  needs restarting only when a Repair says so (the integration and engine have changed how they talk);
- the engine keeps its settings and data in `/config/powerengine`, as the app does, and nothing in them changes.

It works wherever Home Assistant runs its own Python (OS, Supervised, Container, Core). Tested against Home Assistant
2026.10. [INSTALL.md](INSTALL.md) is the full guide: install, the dashboard, configuration and first-day checks. The rest of
this page covers moving from the old AppDaemon app (no longer released), and the shadow run.

## Moving from the AppDaemon app

The engine will not start while the app is running (two would both control the inverter), nor while the app's MQTT
entities are still in the entity registry (the new ones would be `sensor.pe_..._2`). `powerengine.migrate_from_mqtt`
replaces them with native entities under the same ids, so history, dashboards and automations carry on. Choose a quiet
time (no grid event, no cheap-rate charge or sale running or about to start) and allow an hour.

1. **Back up** Home Assistant, `/config/powerengine`, the app's yaml and `/config/.storage/core.entity_registry`. Note
   whether the **Pause** switch is on.
2. **Put the app in Passive** (Config page). It hands the inverter back to Self-Use, so nothing after this can leave it
   in a forced state.
3. In HACS **remove the PowerEngine AppDaemon app** (keep a copy of its folder if you like), then add the repository
   again as an **Integration** (INSTALL.md, Step 1) and download it. Restart Home Assistant. Restart the AppDaemon add-on, so the
   app is really gone.
4. Add the integration, mode **Live**. It waits, and a Repair tells you why.
5. Run **`powerengine.migrate_from_mqtt`** (Developer tools, Actions). It saves a snapshot of the old entities in
   `/config/powerengine/migration/`, clears their MQTT messages, removes them, and starts the engine, in Passive. If it
   says a switch had no state to carry over, set it by hand (Pause was off unless you had turned it on).
6. **Check** for an hour: the entities, the history graphs across the gap, the card and Config page, the plan.
7. **Go Active** from the Config page, and watch the first real inverter command.

Your settings and data are untouched, so the way back is open: remove the integration, put the AppDaemon app back from
HACS (or its saved folder), and it republishes its entities under the same ids.

## Updating

HACS shows the update as for any integration. Download it: the engine restarts by itself within a minute (the
PowerEngine device's Engine sensor says when), and a Repair says if Home Assistant needs a restart too. The Restart
engine button and `powerengine.restart_engine` do it at once. Read the release notes' **Behaviour changes** first.

## Shadow run

A second engine, in mode **Shadow**, can run beside a live PowerEngine on its own copy of the data, with its own
entities (`sensor.pe_shadow_...`) and nothing sent to the inverter. It is how a change to the engine or the
integration is compared with the live one first. See [plans/shadow-run.md](plans/shadow-run.md).
