# Shadow run: the engine under the integration, beside the live PowerEngine

Goal: show, over a day or two, that the engine running under the Home Assistant integration plans as the live
AppDaemon app does, before anything is switched over (docs/plans/native-integration.md, migration step 2). It changes
nothing in the house.

## What a shadow run is

The integration is added in **Shadow** mode. Then:

- **Its data is its own.** On its first start it copies the live folder (`/config/powerengine`, less the cost-history
  backup archive and old config backups) to `/config/powerengine_shadow`, with `publisher` set to `auto`. What it
  learns and records from then on stays there. `powerengine.refresh_shadow` copies afresh.
- **It reads the house, publishes its own entities.** It reads the same inverter, tariff, forecast and car sensors as
  the live engine. Its own entities are `sensor.pe_shadow_<key>` (the live engine's `sensor.pe_<key>`), with their own
  unique ids and a device called "PowerEngine (shadow)". It never sees the live engine's entities.
- **It controls nothing, and cannot.** The engine holds itself to Passive (`engine_main --shadow`; Passive plans and
  decides exactly as Active does, and sends nothing), and the integration refuses every service call, event and state
  write outside its own entities that the engine asks for, whatever it does, and writes each refusal down in
  `/config/powerengine_shadow/shadow_ledger.jsonl` (the engine sensor shows the count). It writes nothing outside its
  folder: no handover packages, no dashboard, and not the live folder.
- **It does not run the heavy overnight jobs** (tariff simulator, low-write study, engine comparison): the live engine
  does them.

## What it shows, and what it does not

It exercises everything about the new way of running the engine except the first write to the inverter: the engine
process and its connection, timers and callbacks (the 30 s cycle, the engine's tick, the daily jobs), reading states and
history, the engine's own entities, restarts and reconnections. If its decisions, plan, target and cost figures track
the live engine's, the host is right. The comparison covers the planning decision, engine v2's mode, target and power,
the command it would send, the plan headline and the battery reading it saw.

What it cannot show: that a write reaches the inverter (that is checked at the cut-over, in Passive first, with a first
real command watched), the nightly jobs, and MQTT removal.

Expect small differences. The two engines tick a few seconds apart, so a decision changes at slightly different moments
(differences under three minutes are not reported). The shadow's learned data starts as a copy of the live engine's and
drifts, so a difference that starts late in the run and grows is drift; one from the start is worth looking at.

## Doing it

1. A Home Assistant backup first (it is a new integration and a restart).
2. Copy `custom_components/powerengine/` from this repository to `/config/custom_components/powerengine/` on Home
   Assistant (for example `tools/build_integration.py --sync`, then scp or rsync). Restart Home Assistant once.
3. Settings, Devices & services, Add integration, PowerEngine: Mode **Shadow**, folder left empty.
4. Check: `sensor.powerengine_shadow_engine` reads `running`; `sensor.pe_shadow_diag_version` has the version; the
   Repairs page has no PowerEngine issue; the AppDaemon app and the live entities carry on as before.
5. Leave it for a day or two. To compare, on the Home Assistant host (the Supervisor's token is in its environment):
   `ssh root@<ha> 'python3 - --hours 24' < tools/shadow_compare.py` (add `--min-minutes` to change what counts as a
   difference).
6. To finish: delete the PowerEngine integration entry (its shadow entities go with it), remove
   `/config/custom_components/powerengine` and `/config/powerengine_shadow`, restart Home Assistant.

## Load

A second Python process of about the size of the AppDaemon app, a second reading of the same history at start-up and
hourly (the recorder query for the load profile), and a second set of 84 entities in the recorder. Fine for a day or two.
