# PowerEngine: installation guide

A complete, from-scratch setup, in order: four steps, about 30 minutes. Each step ends with a **Check** so
you know it worked before moving on.

> Keep this guide current: any release that adds or changes a setup step
> updates this file in the same pull request.

**Version this guide matches:** 0.9.213 (beta; Passive by default, Active available)

---

## Before you start

You need:

| Requirement | Why | Notes |
| --- | --- | --- |
| Home Assistant (OS, Supervised, Container or Core) | PowerEngine runs as an integration with its own engine process | Tested against 2026.10 |
| [HACS](https://hacs.xyz/) | Installs and updates PowerEngine | |
| Admin access to HA | Adding the integration, dashboards | |
| A terminal on the HA machine (optional) | Looking at files and logs | The **Advanced SSH & Web Terminal** add-on or **Studio Code Server** |

Tested with: Solis hybrid inverter via the SolaX Modbus integration, Fox ESS
batteries, Solcast, myenergi Zappi, EDF (edf_energy) tariff, Axle VPP.

> **Inverter wear: read this first.** PowerEngine re-plans every few minutes, so it changes what the inverter does
> far more often than a fixed daily schedule. On inverters driven through **EEPROM-backed settings** (Solis timed
> charge/discharge windows, and similar on other brands), every change is a write to memory with a limited life.
> Even with the daily write limit, dampening and paired writes, we still saw 60–110 writes a day on timed windows.
> - **Use a RAM (temporary) control method if your inverter has one.** On Solis that's *RAM remote control*
>   (Config → Inverter control → Control method), tested with the supervised tests first. It writes nothing to
>   EEPROM, and the inverter falls back to Self-Use by itself if PowerEngine stops.
> - **EEPROM-only inverters may not suit a dynamic plan.** If yours has no RAM method, keep arbitrage off, raise
>   the window change cost, set a low daily write limit, and watch *Inverter writes today* on the Health tab for
>   the first weeks. If the writes stay high, a fixed schedule may be kinder to the inverter.
> - **Firmware matters.** The same model can offer different control methods on different firmware (on Solis, RAM
>   remote control works on 420044; Remote Dispatch needs FB00 or later). Run the supervised tests on your own
>   firmware before going Active.

---

## Step 1: install PowerEngine

PowerEngine is a Home Assistant integration. The integration is thin and rarely changes; the engine that plans and
controls is a separate process it starts and watches. No AppDaemon, no MQTT broker and no `apps.yaml` are needed, and
PowerEngine's entities are ordinary Home Assistant entities (`sensor.pe_...`).

1. **HACS → ⋮ → Custom repositories**, add both:
   - `https://github.com/durkimat/ha-powerengine-controller`, category **Integration**
   - `https://github.com/durkimat/ha-powerengine-card`, category **Dashboard**
2. **Download both, at the latest version of each.** The card is only released when it changes, so its version can be
   behind the integration's. While PowerEngine is in beta, turn on pre-releases for each repo in HACS, then pick the
   latest version.
3. **Restart Home Assistant**, then reload the browser page (so the card's resource loads).
4. **Settings → Devices & services → Add integration → PowerEngine**, mode **Live**. The engine starts; with no
   `config.yaml` yet it waits in Passive ("No config.yaml yet; set PowerEngine up on the config page").

**Check the device:** *Settings → Devices & services → PowerEngine*:

| Entity | Expected |
| --- | --- |
| Engine | running, on the version you installed |
| Version | matches what you installed |
| Heartbeat | a time that moves on every minute |
| Config OK | Off (until configured) |
| Operation mode / Configured mode | unconfigured |
| Input mapping | unconfigured |
| Input catalogue / Settings catalogue | the number of inputs and settings PowerEngine knows about |

**Check the log** (*Settings → System → Logs*, search for `powerengine`). Before any configuration it says:

```
PowerEngine 0.9.213 starting
No config.yaml found (...); running unconfigured.
Inputs: unconfigured; mode unconfigured (...)
```

If the engine does not start, a Repair on the integration says why. The engine's own log lines are in the
**Diagnostics** log sensor (`sensor.pe_diag_log`) and Home Assistant's log (search for `powerengine`).

---

## Step 2: the PowerEngine dashboard

PowerEngine ships its own dashboard and keeps it up to date: on every start it
writes `/homeassistant/powerengine/dashboard.yaml`. You register it with HA once.

1. **Install two dashboard cards from HACS** (search each, then Download):
   - **Sunsynk Power Flow Card** (by slipx06): the energy flow picture (Monitoring tab)
   - **ApexCharts Card**: the plan chart (Plan tab) and the daily cost chart (Costs tab)

   Reload the browser afterwards.
2. **Register the dashboard** in `configuration.yaml`. If your
   `configuration.yaml` has **no** `lovelace:` section yet, paste this into the
   HA terminal (it appends to the end of the file):

   ```bash
   cat >> /homeassistant/configuration.yaml <<'EOF'

   # PowerEngine dashboard (managed by the PowerEngine app)
   lovelace:
     dashboards:
       powerengine-dash:
         mode: yaml
         title: Power Engine
         icon: mdi:lightning-bolt
         show_in_sidebar: true
         filename: powerengine/dashboard.yaml
   EOF
   ```

   If you **already** have a `lovelace:` section, edit the file instead
   (`nano /homeassistant/configuration.yaml`) and add just the
   `powerengine-dash:` block under its `dashboards:`.

   If you keep a local git copy of your config, pull it afterwards so your next
   push doesn't overwrite this change.

3. **Check and restart Home Assistant** (only needed the first time):

   ```bash
   ha core check && ha core restart
   ```

**Check:** *Power Engine* appears in the sidebar with five tabs: **Monitoring** (it says *UNCONFIGURED*
until Step 3), **Plan**, **Costs**, **Health** and **Config** (the configuration card, with suggested entities
pre-filled).

Anyone can open the Config tab, but only admins can save; other users see it
read-only.

- The dashboard is managed: edits to `dashboard.yaml` are overwritten on the
  next update. To customise, copy the cards into a dashboard of your own.
- The Energy flow card (Monitoring tab) is generated from your configured solar plant(s): one panel per
  enabled plant (up to five), or a single total if you have none configured or just one. It regenerates
  whenever you save the Config tab, so manual edits inside its generated block in `dashboard.yaml` are
  replaced the same way the rest of the file is.

---

## Step 3: configure PowerEngine

**Prefer to be guided?** The **Your system** card on the Config tab lists what PowerEngine uses and has a **Change your system** button. Its panel
finds your devices in Home Assistant, fills the inputs from them, checks the signs and puts the result in a draft; **Apply to System** then saves it (see
[WIZARD.md](WIZARD.md)). It needs the card from the release that introduced it. Check its result against the notes below, then carry on from
step 3. The rest of this step describes the full page, which is also how you change things later.

Open the PowerEngine dashboard's **Config** tab (you must be an admin to save). The page has one section per
topic, and each section holds everything for it: its on/off switches, its inputs (required first, then optional),
its settings and what it learns.

- **Search** at the top finds any input or setting by name, description or entity ID. The chips filter to *Needs
  attention*, *Required* or *Optional*.
- **Colours:** a green strip is a required input that's working; red is one that's missing or failing; amber is
  needed only for something not in use yet (going live, or a feature that's off); grey is optional. The line
  under the search box says how many required inputs need attention; tap it to jump to the first.
- Section headers show counts (e.g. *8 required ✓ · 3 optional*) and open by themselves when something needs
  attention. Switched-off topics are greyed out.

Work through it top to bottom:

1. **Operation:** leave on **Passive** (monitor and simulate only). Going live is done later with the Battery
   controller panel.
2. **Battery, Grid and house, Solar, Tariff and planning:** the required inputs. Unmapped inputs show
   **Suggested: <entity>** (click to use it), and a section with several has **Use all N suggested entities**.
   For every input:
   - Check the **Now:** value looks right.
   - Signed inputs (battery power, grid power): read the **reads as** text. If it says *charging* when the
     battery is discharging (or *importing* when you're exporting), tick **Invert**.
   - **Battery power must have a sign.** Watch it while the battery charges: if it never goes negative, the
     sensor is unsigned (Solis via SolaX Modbus is). Then map **Battery charging power** and **Battery
     discharging power** (Solis: `sensor.solis_battery_input_energy` / `sensor.solis_battery_output_energy`,
     which despite their names are power in W). The single **Battery power** input then shows *Not used*.
   - *Grid and house*: tick **House load includes the car charger** if the inverter's house load includes
     the car, and set the **Main supply fuse** (default 60 A).
   - *Tariff and planning*: *Automatic cheap threshold* and *Top up when cheap* are on by default.
3. **Car and EDF smart charge:** the charger inputs, **Car charger power** (7.4 kW for a 32 A Zappi) and
   *Smart-charge optimisation* (only records what it would ask EDF for until live).
4. **Selling:** **Export limit** (your DNO-approved kW) and, if you want it, *Energy arbitrage* (check your
   export tariff allows exporting energy bought from the grid; while Passive it only plans and simulates) with
   **Battery wear cost** and the arbitrage band.
5. **Axle events, Free-power sessions, Cold battery:** switch on what you use; their inputs become required.
6. **Inverter control (needed to go live):** the Solis timed-window hours/minutes, currents, apply button and
   storage mode (amber until you go live, then required), the **Inverter clock** and **Sync inverter clock**,
   **Other battery controller** (choose *No other battery controller* or *Another controller*) and, if you have one,
   the **Handover guards** (read only: **Other controller
   read-only** is a switch that must be on, **Other control off (1)/(2)** are automations or switches that must be
   off). Map them now so PowerEngine can count the writes your current setup makes (Health tab, EEPROM wear) and show
   what it would set. With *No other battery controller* the guards are not needed, not shown and not checked.
   If you have not chosen, PowerEngine goes by the guards you have mapped, and with none mapped it stays Passive
   and says "Choose whether another battery controller is installed".
7. **Notifications:** shown in Home Assistant's notification area (the bell) by default, and each clears itself
   when the problem is over. Or pick your phone's notify service (usually `notify.mobile_app_<phone name>`), or
   Off. Tick what you want to hear about.
8. Fix anything shown in red.
9. **Solar plants** (in *Solar*): the main plant (on the hybrid inverter) is pre-filled. Use **+ Add solar
   plant** for extra arrays.
10. **Save.** PowerEngine checks everything, writes `/homeassistant/powerengine/config.yaml` and keeps the
   previous version as `config.yaml.bak-<date>`.

**Check:** the banner says *Saved*, inputs show *PowerEngine check: OK*, and *Operation mode* becomes
**passive**. If it stays *unconfigured*, the reason (on the entity, and at the top of the card after a refresh)
lists the inputs still needing attention.

---

## Step 4: first-day checks

| Tab | What to expect |
| --- | --- |
| **Monitoring** | A status line saying what PowerEngine *would* do and why ("PASSIVE. Would self-use: …"), the energy flow, and an activity list that fills as decisions change. |
| **Plan** | A headline, a 36-hour chart (planned battery %, prices, solar and house-load forecasts, grid charging) and the actions table. The house-load forecast is learned from the last 14 days of history within a few minutes. |
| **Plan history** | Pick a day and a plan to compare with what happened. Fills in from the first day: each hour's first plan is kept for 60 days. |
| **Simulator** | Empty until the first overnight run (01:30). Then a ranking of current Octopus and EDF tariffs on your recorded days. Needs internet access from Home Assistant to api.octopus.energy, api.edfgb-kraken.energy and (for the heat pump) open-meteo.com. Open the tab as an admin once to import a year of history from HA's statistics; heat-pump and equipment settings are on the same tab. |
| **Costs** | About 14 days filled from HA history within a few minutes of starting (the log shows `Cost backfill: …` lines), then counted live every half-hour. The *Unexplained* column should be small (tens of pence a day); if it's pounds, check the sensors (see Troubleshooting). |
| **Health** | Findings (if any), battery efficiency and losses, plan vs what happened (from the second day), smart-charge slots, requests to EDF, inverter writes. |

After **14 full days**, the battery's round-trip efficiency and usable capacity are measured and replace the
configured figures (Health tab says *measured*). Untick **Use measured** next to *Usable battery capacity* or *Battery round-trip
efficiency* on the Config tab to keep the configured figure instead.

### What PowerEngine keeps

All in `/homeassistant/powerengine/` (outside the app folder, so updates never touch it):

| File | What |
| --- | --- |
| `config.yaml` (+ `.bak-<date>`) | Your configuration and backups |
| `dashboard.yaml` | The managed dashboard (rewritten on update) |
| `costs/` | Half-hour energy and cost records (about 20 KB a day, 400 days), plan snapshots (hourly ones for 60 days), smart-charge slot and request history |
| `load_history.json` | PowerEngine's own house-load record |
| `inverter_writes.json`, `notifications.json`, `ui.json` | Write counts, sent notifications, dashboard defaults |
| `simulator/` | Tariff list, cached tariff rates and per-day Simulator results (a few MB after a year) |

---

### Learned figures and cold-battery caution

PowerEngine learns from the half-hours it records (Health tab, *Learned from use*, updated each night):

| Figure | How it's learned | Used when |
| --- | --- | --- |
| Charge / discharge rate | Median power reached when it asked for the full rate (below 90%, battery not cold) | *Use measured* ticked on *Max charge/discharge power* (default) |
| Charge taper | Share of that rate reached from 90% and from 95% | *Learn: charge slow-down near full* feature |
| Reserve | Charge level where the battery stops supplying the house | *Learn: where discharging stops* (only ever raises the reserve) |
| Export limit | Selling tops out below the battery's own rate | *Learn: export ceiling* |
| Car charge rate | Typical kW of a half-hour the car charged throughout | *Learn: car charge rate* |
| Cold threshold and rate | Charges that slowed, or didn't, at a given battery temperature | *Learn cold behaviour* |

All the *Learn* switches are on by default; each sits under *Learning* in its topic (Battery, Car, Selling,
Cold battery) and can be switched off on its own.

The rates only learn from half-hours where PowerEngine was in control and asked for the full rate, so they build up
once it's live. The inverter is always asked for the configured rate; learned figures only shape the plan.

**Cold-battery caution** (feature on by default; settings under *Cold battery*): the battery's temperature is
estimated from the outside temperature, following it over a time set by **Battery location** (garage or outbuilding
24 h, outside 6 h, inside 72 h, or *Custom* with *Battery warm-up time*). The outside temperature comes from
Open-Meteo's forecast for your home's location (the last 3 days and the next 3, fetched hourly). Two optional inputs
in the *Cold battery* section improve it: **Outside temperature** (your own sensor, used for the hours it has seen
instead of the forecast) and **Battery temperature** (the battery's own sensor: the estimate ahead starts from it,
and the learning uses it). Leave both unmapped for forecast only.
Below *Cold caution
below* (4 °C) the plan expects charging at *Cold charge rate* (50%), and stays cautious until the battery is
*Cold caution release* (3 °C) warmer, so a single milder afternoon doesn't end it. With learning on, a charge that
slows at 5 °C raises the threshold to about 5.5 °C, and normal charging seen at 3 °C lowers it to 3 °C. The Plan
tab lists the cautious periods; the Health tab shows the estimated battery temperature.

**Battery limits (optional, RAM remote control):** a cold or nearly full battery may take less than the power asked
for. Two optional inputs in the battery section let PowerEngine keep every remote-control command within what the
battery allows: **BMS charge limit** and **BMS discharge limit** (Solis: `sensor.solis_bms_battery_charge_limit` and
`sensor.solis_bms_battery_discharge_limit`, in amps, normally 100 A). The command is limited to amps times 52 V, the
"following" check judges the battery against that lower figure, and a limit of 0 becomes a hold (charge) or
Self-Use (discharge). Unmapped or unavailable sensors change nothing, except that with no charge limit the
cold-battery caution's charge rate caps the command while the battery is cold. The diagnostics export lists the limits
and the recent command, expected and actual battery power. If a charge or discharge still isn't followed after 3
minutes, PowerEngine re-sends it lower (down to 3000 W) and tells you once.

## Active mode

In Active mode PowerEngine writes the Solis timed-slot settings (storage mode stays Self-Use). With all three
charge and three discharge windows available (SolaX Modbus's `_2`/`_3` entities, found automatically from the first
window's), the plan's next charge periods (grid charge or hold) and sell periods within 24 hours are set in one go
and only rewritten when they change, so a repeating night costs next to no writes; hold and charge share the charge
current (0 A to hold). With only one window it falls back to a single window at most 35 minutes ahead. And, if *Smart-charge optimisation* is on, sets EDF's ready-by time to ask for
slots. It never uses Backup or Off-Grid mode and never writes bump/boost entities.

### One-off setup (before going live)

1. **Map on the Config tab** and save everything in the *Inverter control* section:
   - the timed-window entities (found automatically; check none show a problem);
   - *Inverter clock* = `sensor.solis_rtc`, *Sync inverter clock* = `button.solis_sync_rtc`;
   - if something else also controls the battery, the handover guards (see "Safety" below). With *No other battery
     controller* none are needed.
2. Check the **Daily write limit** (same section, default 150).
3. **The package** (one-off, then PowerEngine looks after it). Make sure `configuration.yaml` has

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

   and that the `packages` folder exists next to it (`/config/packages/`), then restart Home Assistant once if you
   had to add either. After that PowerEngine writes and updates its own file in that folder when it starts (so after
   every update): `powerengine_handover.yaml` (the update button and the watchdog). It starts with a line saying
   PowerEngine manages it; edits are replaced, and the old file is kept beside it as `<name>.bak-<date>` (the newest
   three are kept). A file in that folder that PowerEngine did not write is never touched. It never creates the
   `packages` folder. The copy in `docs/ha/` is the same file, for reading.
   When the file changes, PowerEngine notifies you and the Config page offers **Load PowerEngine's Home Assistant
   changes**: press it and Home Assistant starts using the change (no restart). If you keep a git copy of your Home
   Assistant configuration, pull before you push, or the push puts the old file back.

### Testing (paused)

Testing needs PowerEngine to be the controller but **paused**: nothing drives the battery and the inverter is on
Self-Use.

1. Pause with the *Pause control* toggle at the top right of the Monitoring tab.
2. Run the supervised tests (below). They're refused unless PowerEngine is paused (or Passive) and the guards are safe.
3. When done, resume (turn *Pause control* off).

While paused, remember nobody is optimising the battery: don't leave it paused overnight unless you mean to.

### Going live for the first time

1. Do the one-off setup above.
2. Pause and run the supervised tests: *Hold*, *Grid charge* (3000 W), *Force discharge* (3000 W), *Self-Use*, each for
   2–3 minutes; each should end **passed** and the battery power should follow.
3. Resume, and set *Operation* to **Active** on the Config tab.
4. Check within a few minutes: *Operation mode* reads *active*; Health tab *Inverter control preview* shows the windows
   in place; the inverter's timed windows (including the `_2`/`_3` ones) match.
5. First night: the battery reaches the planned level by 06:00.

### Safety

- **Handover guards** (when *Other battery controller* is not *No other battery controller*): if a guard becomes
  unsafe while PowerEngine is in control (the other controller taken out of read-only by hand, say), PowerEngine stops
  writing at once and notifies you; it writes nothing more, since something else has taken over. A guard entity that
  doesn't exist at all (the other controller not connected to Home Assistant) counts as safe: PowerEngine carries on
  and notifies you once.
- **Pause:** returns the inverter to Self-Use once, then no changes until you resume.
- **Inputs failing:** if a required input stops working (e.g. while HA or the inverter integration restarts),
  PowerEngine stops making changes and leaves the inverter's programmed windows running. If the inputs are still
  missing after 10 minutes, it returns the inverter to Self-Use and notifies you. With RAM remote control the wait is
  90 seconds (a blip of a sensor no longer switches remote control Off); the inverter's own failsafe returns it to
  Self-Use within about 5 minutes if PowerEngine stops refreshing. Either way it takes control again by itself within
  30 seconds of the inputs recovering.
- **Stopped app:** windows set ahead keep running as planned. The watchdog automation in the handover package closes
  every window (Self-Use) if PowerEngine's heartbeat stops for 15 minutes while it's the battery controller.
- **Read-back:** every write is read back after 6 seconds and retried once; if it still doesn't match, control
  stops until the engine restarts (the Restart engine button) and you're notified.
- **Daily write limit:** control pauses if PowerEngine's own writes reach it in a day. Resuming allows the limit
  again. Only real inverter writes count: update-button presses (each sends a whole block of window times),
  currents, the storage mode and remote-control settings. Window start/end times are staged in HA until the button
  sends them, so they're shown but not counted. Today's writes: *Writes today* on the Monitoring tab, details at the
  top of the Health tab.
- **Inverter clock:** checked every 10 minutes. While live it's synced weekly, and within 10 minutes if it's a
  minute or more out (including when the clocks change, since the inverter doesn't adjust for daylight saving).

### Control method: timed windows or RAM remote control

Config → Inverter control → **Control method**.

**Use RAM remote control if your inverter supports it** (see *Inverter wear* at the top): run the RC tests on the
Tests tab first, then switch. Timed windows remain the default only because they work on every Solis firmware.

- **Timed windows** (default): PowerEngine programs the inverter's three charge and three discharge windows. They
  are stored in the inverter's EEPROM, so each change wears it (Health tab, *Inverter writes today*).
- **RAM remote control:** PowerEngine sends Force charge / Force discharge / Off with a power through SolaX Modbus's
  *Battery control override* entities. These are temporary settings: no EEPROM writes. The command is re-sent every
  **RAM refresh** (default 1 minute); if PowerEngine stops sending, the inverter returns to Self-Use by itself
  (about 5 minutes on firmware 420044; check yours with the *RC failsafe* test on the Tests tab first).

Switching over (either way) needs no other steps: on the next cycle PowerEngine closes the timed windows once (to
RAM) or switches remote control Off (to timed windows). The Monitoring tab's **Inverter control** tile shows the
method in use, the current command and whether the inverter is following it; you're notified if it stops
following for 3 minutes, and PowerEngine falls back to the timed windows if the remote-control entities disappear.

### Supervised inverter tests

**Tests** tab (right of Config), admins only. **These write to the inverter** when you start one. Turn on *Pause
control* first (it's at the top of the tab), pick a test, read its instructions (what it does, what to watch for
on the inverter screen, what PowerEngine checks), tick the box and start. *Stop and revert* ends it early.
Result and a timeline: `sensor.pe_diag_test_write`, shown on the card.

**Timed windows** (the method PowerEngine uses today; EEPROM-backed): Hold, Grid charge, Force discharge or
Self-Use for 1 to 10 minutes. It writes the settings, reads them back after 10 seconds, records battery power
and SoC each minute, then returns the inverter to Self-Use and reads that back. The window it sets ends two minutes
after the test, so a restart can't leave it running.

**RAM remote control** (Solis register 43135 with the power in 43136/43129, via SolaX Modbus's *Battery control
override* entities): checks whether the inverter can be driven without writing the timed windows at all. Each closes
the timed windows first and switches remote control Off at the end, samples battery and grid power every 30 s and
gives a verdict:
- *RC force charge* / *RC force discharge*: **worked** if the battery charged/discharged at 60% or more of the power
  asked.
- *RC hold*: force charge at 0 W; **worked** if the battery stayed within 300 W of zero (run it with some house
  load).
- *RC failsafe*: force charges for 2 minutes, then reloads SolaX Modbus so the command stops being re-sent
  (without writing Off) and watches for the inverter to drop it by itself. **reverted** (with the time) is what a
  safe fallback needs; allow up to 35 minutes, since the inverter's RC timeout is reported as 5 to 30 minutes.

---

## Updating

HACS shows the update as for any integration. **Download** it (or press **Update** on the Configuration page, which
installs the latest integration and card from HACS and waits for the new version): the engine restarts by itself
within a minute, the PowerEngine device's Engine sensor says when, and you get a *PowerEngine updated* notification.
A Repair says if Home Assistant needs a restart too, which only happens when the integration and the engine have
changed how they talk. The Restart engine button and `powerengine.restart_engine` do it at once. The dashboard updates
with the card; refresh the browser after updating. Read the release notes' **Behaviour changes** first: they list
anything that changes what PowerEngine does.

Update the card too, when HACS offers it. The card is only released when it changes, so its version can be behind the
integration's; that's fine. Each warns only if the other is older than the minimum it needs.

Your settings (`/config/powerengine/config.yaml`) live outside the integration's folder, so updates never touch them.

## Rolling back

HACS → PowerEngine → ⋮ → **Redownload** → pick the previous version. The engine restarts on it within a minute. Do
the same for the card if you need to.

## Uninstalling

1. Settings → Devices & services → PowerEngine → **Delete**. Its entities go with it.
2. HACS: remove both repos.
3. Optional: remove the `powerengine-dash:` entry from `configuration.yaml`, and delete `/config/powerengine/`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| PowerEngine entities show *Unavailable* | The engine is stopped or restarting | Check the Engine sensor and the integration's Repairs; the Restart engine button restarts it |
| Card says *app not detected* | The engine is not running | Step 1; check the Engine sensor and Repairs |
| Card warns that the app (or the card) is too old | One of them is older than the other's minimum version | Update the one named in the warning to its latest version, then reload the page |
| `Unknown command: ha` | Commands run on your own computer | Use HA's terminal add-on |
| `Config problem: ...` in the log | `config.yaml` has an error | The message names the problem; fix or restore the backup |
| Dashboard missing from the sidebar | `lovelace:` entry not added, or HA not restarted | Step 2 |
| Energy flow or plan chart says *Custom element doesn't exist* | Sunsynk Power Flow Card or ApexCharts Card not installed | Step 2.1, then reload the browser |
| Plan says *house load learned from 0 days* | No history for the house-load input yet (new install, or recorder excludes it) | Wait a day; check the recorder keeps the house-load entity |
| PowerEngine missing from HACS (repos disappeared) but still running | HACS lost its record of the custom repositories | Re-add both under *HACS → ⋮ → Custom repositories* (PowerEngine as **Integration**) and download the latest version; settings and dashboard are unaffected |
| Dashboard values are *unknown* | Inputs not configured, or mode *unconfigured* | Step 3 |
| Save says *could not send* | You're not an admin | Log in as an admin user |
| Save says *no reply from PowerEngine* | The engine is not running, or can't write its folder | Check the Engine sensor and Home Assistant's log for the reason |
| Mode stays *unconfigured* after saving | Required inputs missing or failing checks | See the reason on *Operation mode*; fix the inputs flagged on the card |
| Costs tab empty after a few minutes | The backfill couldn't read history (recorder excludes an input, or a sensor was renamed) | Check Home Assistant's log for `Cost backfill` lines and warnings |
| Health: *Battery power never shows charging* | Unsigned battery power sensor | Step 3.4: map the charging/discharging pair |
| Costs: *Unexplained* is pounds a day | A sensor is wrong or missing (house load, grid, battery, solar) | Compare the *Energy by day* table with the inverter's daily counters |
| Health: many inverter writes a day | Your current controller (another battery controller, if you run one) writes often | See the EEPROM section on the Health tab |
