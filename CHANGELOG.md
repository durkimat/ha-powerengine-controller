# Changelog

Every release lists **Behaviour changes** (anything that changes what PowerEngine does to your system) first.

## 0.9.75 (beta)

### Behaviour changes
- **The savings chart's date-range section is gone.** The "Savings for a date range" section on the Costs tab (the From and To pickers, the summary line and the second chart) did not work the way you wanted, so it has been removed. The two settings behind it, "Costs range from" and "Costs range to", disappear from Home Assistant when the app starts.
- The savings chart itself is unchanged: the combined **PowerEngine** step (with the battery carry-over included), "No solar or battery" and "You paid" show exactly the same numbers as in 0.9.72, and so does the "Daily cost by scenario" chart. Yesterday, Last 7 days, This month and Last 30 days still work as before.
- The app asks for card 0.9.70 or newer again, as it did before 0.9.72.

### Other changes
- Nothing else changes in how the app controls your battery.

## 0.9.74 (beta)

### Behaviour changes
- The app now asks for card 0.9.74 or newer, and warns you if the PowerEngine card is older. The "Savings for a date range" chart on the dashboard needs the new card to work properly. Press Update on the Configuration page to get both.

### Other changes
- Nothing else changes in how the app controls your battery.

## 0.9.73 (beta)

### Behaviour changes
- **Remote control stays within what the battery will take.** If you map the new optional inputs *BMS charge limit* and *BMS discharge limit* (Solis: `sensor.solis_bms_battery_charge_limit`, `sensor.solis_bms_battery_discharge_limit`), PowerEngine lowers a charge or discharge command to the battery's own limit (amps times 52 V) when that is below the command. A limit of 0 turns a charge into a hold and a discharge into Self-Use. Nothing changes if the inputs are not mapped or read unavailable or nonsense.
- **Cold battery, no BMS sensor:** while the cold-battery caution says the battery is cold, the charge command is capped at the caution's charge rate (the rate the plan already assumed) instead of always asking for the full rate.
- **The "inverter not following" check** now compares the battery with the lower of the command and the BMS limit, so a charge the battery itself limits (cold, nearly full) is no longer reported as not following.
- **A command that doesn't take is stepped down.** If a charge or discharge above 3000 W is still not being followed after 3 minutes, and the battery is doing less than a fifth of it (idle, or going the other way, as when a refused setting leaves the previous one), PowerEngine re-sends it 1000 W lower (for example 5000, 4000, 3000 W) and sends one notification; each step is in the log. A battery that is doing some of it (limited by its own BMS or by taper) is not stepped down. These are remote-control commands, not EEPROM writes, so the write budget is untouched. The lower ceiling is dropped when the kind of command changes or after an hour. At 3000 W the usual "not following" report applies.

### Diagnostics
- The diagnostics export has a new `bms` section: the charge limit and discharge limit sensors now, and a short history (one row every 2 minutes and on each command change) of the command, the expected power, the actual battery power, SoC, the follow state and the limits, so the first cold spell can be read back.

### Setup
- Two optional inputs in the battery section (see docs/INSTALL.md, "Battery limits"). Existing set-ups keep working without them. The role catalogue grows by about 0.45 KB (still under the 16 KB limit).

### Not done
- Reading the force-power registers back (43136 / 43129) with a Modbus probe: the SolaX Modbus integration here gives no read-back entity for them, so it is left for a later release.

## 0.9.72 (beta)

### Behaviour changes
- **Costs tab, savings chart:** "Battery on self-use" and "Battery carry-over" were very small, so they are gone as separate bars. They are now part of **PowerEngine**, which shows everything the battery and PowerEngine did on top of solar and your tariff. The "Day-to-day cost" subtotal is gone too, because it would now equal the PowerEngine bar. "No solar or battery" and "You paid" are exactly the same as before.
- **Costs tab, "Daily cost by scenario":** it now has the same steps as the savings chart (No solar or battery, Solar, tariff, PowerEngine). The "+ battery on self-use" bars are gone, and each PowerEngine bar is that day's real cost including the carry-over, so the days add up to the savings chart. The headline sentences and the "What the scenarios mean" text say the same.
- **New: savings for any date range.** Under the savings chart there is a new "Savings for a date range" section with two pickers, **From** and **To** (for example "7 days ago" and "Yesterday"). Both days are included, up to 30 days back, and only complete days count. If From is after To the two are swapped, and if some days have no costs you are told how many days were used. Above the chart you see the first and last date and how many days it covers. It starts on the last 7 days.
- The "Savings waterfall" sensor's value is now what the battery and PowerEngine saved yesterday (before it was only PowerEngine's part on top of a plain self-use battery), so it is larger.

### Other changes
- Two new dashboard settings, `select.pe_ui_cost_from` and `select.pe_ui_cost_to`, hold the chosen range.
- The range is worked out from the days PowerEngine already keeps, so there are no extra calls to your supplier or any other outside service.

## 0.9.71 (beta)

### Behaviour changes
- None.

### Versions and tooling
- The app and the card no longer have to be at the same version. Each says the oldest version of the other it works
  with: the app needs card 0.9.70 or later, and the card needs app 0.9.69 or later. A warning shows only when one is
  older than that. The card is now released only when it changes, so its version may skip numbers.
- The app publishes `min_card_version` on `sensor.pe_diag_version`.
- Developer tools: `tools/release.sh`, which releases in one command, and `tools/diag_summary.py`, a one-screen summary
  of a diagnostics export.

## 0.9.70 (beta)

### Behaviour changes
- None for a set-up system. These are demo and first-install fixes from the second clean-install test.

### Demo and first install
- No more "Invalid callback handle" warnings when the demo starts, changes day or exits. Each daily timer was
  cancelled twice; the first cancel always worked, so no timers were left running.
- No "Unknown Plugin Configuration" warning at start-up when there is no MQTT plugin.
- The dashboard reloads itself once after starting, changing or leaving the demo, so the demo's own dashboard,
  including the solar panel, shows without a manual refresh.

## 0.9.69 (beta)

### Behaviour changes
- None for a set-up system using MQTT. Every fix below is on the demo and first-install path, found on the first
  clean-install test, on HA 2026.9.4 with AppDaemon add-on 0.19.2.

### Demo and first install
- **Timers are cancelled properly on newer AppDaemon.** A demo start, day switch or exit left the old repeating
  timers running alongside the new ones, because newer AppDaemon hands back a task instead of the timer's handle.
- **Values are written exactly (direct publishing).** Newer AppDaemon turned `true` into the text "true" and dropped
  zeros and `false` from attributes. That broke saving settings in the demo ("feature ... must be true or false") and
  shifted the plan chart's solar and forecast series by hours.
- **The demo keeps the recorded home's clock (Europe/London)** whatever time zone the HA install uses, so solar,
  prices and events appear at the recorded times. The sunny day's event appeared an hour late on an install set to
  Europe/Amsterdam.
- **A quiet start:** no "Entity ... not found" warnings when PowerEngine creates its own entities, no health-check
  crash before setup, and "no house-load input" and "no config.yaml" are information, not warnings.
- **Before setup,** Mode and Health read "Not set up yet" instead of a red "Blocked" and "Unknown". The welcome card
  names the demo days the same way as the banner.

## 0.9.68 (beta)

### Behaviour changes
- None. Late-notice Axle events already worked: with no cheap slot before an event, PowerEngine charges at the peak
  rate, but only as much as the event can sell at full power, because £1.15/kWh is worth far more than about 30p.

### Fixes
- The plan now says what a pre-event charge is for: "charge at 28.84p for the Axle event at 16:00: £1.15/kWh (£1
  Axle + 15p export)". It used to read "charge at 28.84p to sell at 15p from 02:00 tomorrow", which looked like a
  losing trade. New tests cover a late-notice event, and a battery that already holds enough.

## 0.9.67 (beta)

### Behaviour changes
- **Deep overnight selling is back.** PowerEngine learns the fixed overnight window from the half-hours at each day's
  lowest rate. On 30 Sep EDF priced the smart slots at its new 6.66p while that night still ran at the old 6.99p, so
  the night dropped out of that day's cheap set and the window came out empty. Overnight arbitrage then stayed inside
  the 75–90% band, as it does outside the window. A half-hour within 15% of the day's lowest rate now counts as cheap.
  The window, and deep overnight selling with a guaranteed refill, return at the next plan.

## 0.9.66 (beta)

### Behaviour changes
- None for your setup. On first start PowerEngine adds a `site:` section to your configuration describing the plant
  it has always controlled: inverter Solis on firmware 420044, car charger Zappi, tariff detected automatically,
  forecast Solcast and grid events Axle. A backup of the configuration is kept first. The firmware is recorded as
  assumed, because the inverter doesn't report it.

### Your system (making PowerEngine generic, step 8)
- The configuration now says which plant this home has, and PowerEngine builds each driver from it by name instead
  of assuming this one. The car charger, forecast and grid events can each be set to None.
- The Configuration page has a new **Your system** block at the top, with a dropdown for each part and each option's
  test status (verified, community or draft). If the inverter reports its firmware, the block shows whether it
  matches the one chosen.
- Changing the inverter, or to a firmware that works differently, switches PowerEngine to Passive. A banner then asks
  you to run the supervised tests on the Tests page before going Active again.
- `docs/SITE.md` describes the new `site:` keys.

## 0.9.65 (beta)

### Behaviour changes
- None. PowerEngine does exactly what it did before, with the same commands, texts and suggestions.

### Inverters as definition files (making PowerEngine generic, step 7)
- Everything PowerEngine knows about the Solis inverter now lives in a data file (`adapters/devices/solis.yml`):
  the remote-control entities and options, the timed windows, the clock, the entity suggestions on the Configuration
  page, and firmware variants. A small generic driver reads it.
- Supporting another inverter should mostly mean writing one of these files. See `docs/INVERTERS.md` for the format.
- The file ends `.yml` on purpose: AppDaemon treats any `.yaml` in the apps folder as app configuration.

## 0.9.64 (beta)

### Behaviour changes
- **No more flip-flopping at a charge target.** Once the battery reaches the plan's target for a half-hour it holds
  for the rest of that half-hour, unless the charge falls 2% or more below the target or the plan changes. Before,
  the inverter's whole-number charge reading (93% while charging, 94% while holding) made it switch between charge
  and hold every 30 seconds.
- **No forced charge at the peak rate when the plan only expects solar.** When the plan's charge in a non-cheap
  half-hour would come entirely from solar surplus, it now uses self-use instead of a forced charge. A forced charge
  draws its full power whatever the sun does, so on 29 Sep a cloudy spell meant a few pence of power were bought at
  30.28p to sell at 15p. Cheap, free-power and car smart-charge slots still force-charge as before.

### Fixes
- A car smart-charge slot that EDF has planned but not yet priced now reads "tariff still 30.28p" rather
  than naming the peak price as the slot's price.
- When a required input is briefly missing, the log and status now say "waiting for inputs" instead of
  "unconfigured".
- Smart-slot statistics: the planned energy per slot is now scaled to the slot's actual length, and the total counts
  only slots that ran. The total had included withdrawn and re-listed slots, giving impossible figures such as
  5,345 kWh over two weeks.

## 0.9.63 (beta)

### Behaviour changes
- None for your setup. The demo can only start on a PowerEngine that isn't set up yet, so it can never take over a
  real system.

### Demo mode (first working version)
- A PowerEngine with no configuration now opens on a welcome card with two choices: **Try the demo** or **Set up your
  system**.
- The demo runs on four recorded, scrubbed days from a real home: a sunny day, a dull day, a grid-services event day
  and a car-charging day. A simulated battery follows PowerEngine's own commands, so the plans, decisions, costs and
  charts behave as they would for real.
- **Nothing is controlled.** In demo mode every service call goes to the simulated home, never to Home Assistant, and
  the only things written are PowerEngine's own sensors. Its data goes in a separate demo folder.
- A banner on every page says it's a demo. It has buttons to switch day or exit. Settings can be changed in the demo
  and are reset on exit.
- It works without MQTT: PowerEngine can publish its entities directly, and their switches work too.
- Your dashboard gains a hidden demo card at the top of each page. It shows nothing on a set-up system.
- **Don't run a second PowerEngine in demo mode alongside your real one**: they would share entity names.

## 0.9.62 (beta)

### Behaviour changes
- None for your setup.

### Easier install (the first steps of the demo-mode plan)
- **New: PowerEngine setup card**, at the top of the Configuration page. It checks each piece PowerEngine needs:
  - HACS, and its AppDaemon option;
  - the AppDaemon add-on;
  - the PowerEngine app, and whether it's running;
  - the chart cards;
  - MQTT.

  Anything HACS can install gets a button (admins only). When everything is in place it shows one line: "All set". It can also be added to any dashboard from the card picker, which is how a new user will start.
- **New setting: Entity publishing** (Config page, under Other): auto, MQTT or direct.
  - **MQTT**, as today, keeps entities across restarts and lets their switches be changed from Home Assistant.
  - **Direct** needs no MQTT broker, for the coming demo mode. Entities are rebuilt at each start and switches are read-only for now.
  - **Auto** (the default) uses MQTT when it's there, so nothing changes for you.

## 0.9.61 (beta)

### Behaviour changes
- None, and no visible changes for your setup: every screen and message reads word for word as before.

### Under the hood (making PowerEngine generic, step 6)
- Supplier and device names (EDF, EDF smart slot, Zappi, Solcast, Solis, Axle) now come from the adapters rather
  than being written into the texts. Someone on Octopus would see "intelligent dispatch" where you see "EDF smart
  slot".
- The dashboard is written with those names when PowerEngine starts or the configuration is saved. A test checks
  that your dashboard comes out byte-for-byte the same as before.
- No entity names, attributes, settings or history changed.

## 0.9.60 (beta)

### Behaviour changes
- None unless you change the new settings: their defaults match what PowerEngine did before.

### Smart-charge requests, configurable (Config page, car and smart charge)
- **Requests per day** (4–10, default 6).
- **Time between requests** (10–120 minutes, default 20). Unsuccessful requests also back off: 30, 60, 120, then
  240 minutes. That usually limits things more than the daily cap.
- **Skip if a slot is due within** (1–8 hours, default 3).
- **Smart slots cover the whole house** (default on). Tick if your supplier charges the whole house the slot rate
  even when the car isn't charging (EDF does). Off: slots are planned at your normal rate for the house and battery,
  and PowerEngine doesn't ask for extra slots.
- **Don't ask when the car is full** (default off). This skips requests while the charger says the charge is
  complete, or the car drew nothing in the last slot. The success rate on the Config page shows whether requests for
  a full car work.

## 0.9.59 (beta)

### Behaviour changes
- None. This release reorganises how PowerEngine reads the solar forecast, so other forecast services can be
  supported later.

### Under the hood (making PowerEngine generic, step 5)
- The Solcast-specific part (the half-hourly forecast list and how its estimates become kWh) now sits in a forecast
  "adapter". The planner works from neutral half-hourly points.
- One robustness gain: a malformed entry in the forecast list is now skipped rather than stopping the plan.
- The recorded-night replay passed unchanged.

## 0.9.58 (beta)

### Behaviour changes
- None. This release reorganises how PowerEngine reads your car charger, so other chargers can be supported later.

### Under the hood (making PowerEngine generic, step 4)
- The Zappi-specific part now sits in a car-charger "adapter": turning the plug status ("Charging", "EV
  Disconnected" and so on) into charging, plugged in or unplugged, and spotting "charge complete".
- Spotting a full car from the smart-slot history and the check meter were already charger-neutral, so they stay
  as they are.
- The recorded-night replay passed unchanged.

## 0.9.57 (beta)

### Behaviour changes
- None. This release reorganises how PowerEngine reads your tariff, so other suppliers can be supported later.
  What it does should be exactly the same.

### Under the hood (making PowerEngine generic, step 3)
- Everything specific to how EDF's tariff reaches PowerEngine now sits in a tariff "adapter" for the Octopus
  Energy integration, which serves EDF and Octopus alike. That covers:
  - the half-hourly rates, standing charge and off-peak flag;
  - smart slots;
  - free-electricity sessions;
  - how PowerEngine asks for smart slots (the ready-by time and charge target).

  Axle events come through their own small adapter.
- When and how often PowerEngine asks EDF for slots is unchanged, including the back-off and settle rules.
- The recorded-night replay passed unchanged.

## 0.9.56 (beta)

### Behaviour changes
- **Exports in cheap slots now run their full half-hour** (#168). The planner treated the current half-hour as a
  whole 30 minutes even when it re-planned part-way through. A sale that had to stay above the 75% selling floor
  looked impossible after a minute of discharging, so the next re-plan switched to charging.
  - What that caused: it sold about 1 kWh at a time instead of the 2.6 kWh planned, and switched export ↔ charge
    every hour (every 5 minutes just after a restart).
  - Now: the current half-hour is planned for the time it has left. A planned sale completes, then the battery
    refills, with fewer, longer actions. On the recorded test night, timed-window mode also made about 30% fewer
    inverter writes.
- **Charge labels show where the charge is heading:** "Grid-charge to 90%", not the next half-hour's step (the
  label said 47% on 29 Sep while charging to 90%). Control still works half-hour by half-hour.

## 0.9.55 (beta)

### Behaviour changes
- **Axle events export at full power.** Before, discharge during an event was capped at 4 kW whatever your battery
  could do. On 28 Sep the battery ran at 3.97 kW, with about 2.5 kW reaching the grid, and there was 71% charge
  left at the end. Events now run at your battery's maximum discharge (5.2 kW), within the RAM remote-control limit
  (5 kW). At £1.15/kWh that's about £1.15 more per hour of event.
- **Axle preparation is sized to match.** The plan and the pre-event reserve now allow for the higher power, so
  the battery is topped up a little more before a scheduled event.

### Fixes
- **Last special event:** the Costs page now shows the whole event (all its half-hours added up), not just its
  last half-hour. Costs are recalculated once after updating (cost method 7) to rebuild it.
- **Plan sensor size:** a choppy plan could push `sensor.pe_plan` over Home Assistant's 16 KB limit (18 KB on the
  morning of 28 Sep), and HA then stops recording its history. The Actions list now shows the soonest changes
  that fit, with "…and N more later" underneath. The chart still covers the whole plan.

## 0.9.54 (beta)

### Behaviour changes
- None to control. Cost reporting only: past days are re-valued once after updating (cost method 6).

### Costs page, clearer
- **Tap a column** in the savings waterfall to see its full name and exact value. The old hover tooltip didn't
  work on phones.
- **"Everyday cost" is now "Day-to-day cost"**, with a key under the chart explaining every column, left to right.
- **The headline** says what you paid first, then the day-to-day comparison with a self-use battery and with no
  solar or battery.
- **Special events table** split into columns:
  - Axle kWh;
  - what Axle paid you;
  - what that energy was worth otherwise;
  - Axle net;
  - free power;
  - day-to-day energy (metered);
  - the standing charge, on its own, so it no longer hides in the everyday figure.
- **Axle net is fairer:** the energy an event uses is now costed at the overnight rate the battery is refilled at
  (with its losses), and solar at the export rate. Before, grid-charged energy was costed at the peak rate, which
  made Axle look worth less than it is.

## 0.9.53 (beta)

### Behaviour changes
- None. Card only.

### Costs page
- The savings waterfall is now drawn as columns, left to right. It starts at "no solar or battery" on the left,
  cascades down through each saving, and ends with what you paid on the right. On a phone the labels and values
  shorten to fit; tap a bar for its exact value.

## 0.9.52 (beta)

### Behaviour changes
- None to control. Cost reporting and the Costs page only.

### Costs page (#162)
- **Whole-day scenarios.** Each day is now costed five ways, using the same measured house, car and solar:
  - no solar or battery;
  - solar only;
  - plus your EDF tariff;
  - plus a battery on plain self-use (simulated);
  - PowerEngine (what you actually paid).

  Unlike the old S0–S4 steps, these can be compared directly with what you paid.
- **Where the savings came from:** a waterfall chart for yesterday, 7 days, this month or 30 days. It starts from
  "no solar or battery" and steps down through solar, the tariff, a self-use battery and PowerEngine to your
  everyday cost. It then adds the battery carry-over and Axle (including Axle's £1/kWh payments) to reach what you
  paid.
- **Battery carry-over:** charging tonight for tomorrow no longer makes today look dear. The day's change in
  battery charge is valued at the overnight rate, with the metered figure shown alongside. Axle and free-power
  events are kept out of it.
- A headline for yesterday, the last 7 days and this month, and a plain table of what each scenario assumes.
- The old cost-layer and energy tables are under **Detail and checks**. They attribute one day's cost for checking
  and aren't alternatives to compare.
- The page title no longer shows "Costs" twice.
- Past days are recalculated once after updating (cost method 5).

## 0.9.51 (beta)

### Behaviour changes
- None to control. Dashboard only.

### Monitoring
- **The Energy flow diagram now follows your configuration** (#160). Before, it was fixed. Now:
  - Each enabled solar plant gets its own panel, with its name.
  - The battery size comes from the capacity PowerEngine uses, and the floor from your minimum reserve.
  - The car appears only when an EV charger is set up.
- It updates when you save the configuration, and once more after start-up, when the real battery capacity is
  known. Refresh the dashboard to see it.
- New sensors: one power sensor per solar plant, `sensor.pe_state_solar_<id>_power`. They're added and removed
  as plants are. `sensor.pe_state_solar_power` is still the total.

## 0.9.50 (beta)

### Behaviour changes
- None. This release reorganises the inverter code so PowerEngine can later support other inverters. What it
  does to your inverter should be exactly the same.

### Under the hood (making PowerEngine generic, step 2)
- Everything specific to the Solis inverter now sits in one place, a Solis "adapter". That covers timed windows,
  RAM remote control, supervised tests and clock sync. The rest of PowerEngine asks the adapter what to write,
  and keeps the safety rules itself: dampening, the daily write limit, read-back checks and the write journal.
- The recorded-night replay test now also covers pause and resume, a restart mid-night, a failed read-back, the
  daily write limit, supervised tests and clock sync. Every change above passed it unchanged.

### Worth checking after updating
- Control carries on as before (RAM remote control, writes today, the Next line). If anything looks different,
  send a diagnostics export in the morning.

## 0.9.49 (beta)

### Behaviour changes
- None to control.

### Health
- **Dismiss** on each Health finding (admins). A dismissed finding stays hidden. A new or changed one shows again,
  since each day's check has its own title. Dismissals are logged and listed under the findings. The red *Health
  needs a look* tile on Monitoring goes away with the finding.
- The battery-ledger check is fairer: it flags a correction over 3 kWh **or** 10% of the day's battery
  throughput, whichever is larger (a busy arbitrage day moves 30+ kWh).
- **PowerEngine log** card at the bottom of Health: warnings by default, or all recent lines, newest first. The log
  is now saved across restarts (`log.json` beside the config) and still goes into the diagnostics export.

### Updates
- **New version check every 5 minutes, straight from GitHub** (HACS only looks every few hours). A blue *New
  version available* tile appears on Monitoring and taps through to Configuration. There the update card says
  which version is released and shows **what's new**: the changelog of every release since yours, newest first,
  with older ones as links when there's a lot.

## 0.9.48 (beta)

### Behaviour changes
- None to control.

### Dashboard: Monitoring
- **Next:** under the current decision, the plan's next two changes, e.g. "sell 16:00–19:00, then charge 23:30–05:00
  tomorrow" (new `sensor.pe_plan_next`).
- **Saved today** tile: today's saving against no solar or battery, with any Axle or free-power event value
  included (new `sensor.pe_cost_saved_today`); tap for the Costs tab.
- **Health needs a look:** a red tile that only appears when the Health tab has findings; tap to open it. The
  heartbeat, config-OK and version tiles moved to the Health tab.
- **Mode** now reads from `sensor.pe_state_status`: Active (green), Paused (orange), Passive (blue), Blocked or
  **Stopped** (red). Stopped is new: control stopped because inverter writes couldn't be confirmed, which until now
  only showed in the log while the tile still said Active.

## 0.9.47 (beta)

### Behaviour changes
- None to control.

### Dashboard: Monitoring tidied
- **Mode** is coloured by state: green Active, orange Paused, blue Passive, red when blocked (not configured, inputs
  not ready, or Active asked for but refused by a guard).
- The status box and the Activity list are merged. The top line (bold) is what PowerEngine is doing now, with
  today's earlier decisions listed below it, so it grows through the day. The mode's reason shows above it when not
  Active. The activity log now keeps 48 entries (was 20), enough for a busy day.
- **Now** holds the battery charge gauge plus the battery, grid, solar and house gauges, above the car, smart-charge,
  Axle and free-power tiles. The separate Battery % and Decision tiles are gone (the gauge and the activity's top
  line say the same).
- The *Last 24 hours* graphs are removed; the Plan history tab covers them.

## 0.9.46 (beta)

### Behaviour changes
- None to control.

### Updating
- **The Update button now makes HACS check GitHub first.** HACS only looks for new releases every few hours, so the
  button found nothing new straight after a release. It's now a PowerEngine card: it asks HACS to refresh both
  PowerEngine repositories (admins only), then runs the update script. It shows progress, and offers *Reload page*
  once the new version is running.

## 0.9.45 (beta)

### Behaviour changes
- **A full car no longer holds the battery through a smart slot.** If the latest EDF smart slot (run for at least 15
  minutes in the last 12 hours) passed with the car plugged in but drawing nothing, the car is taken as full. Coming
  smart slots are then planned as ordinary cheap time: arbitrage and charging allowed, no car load. Before this, a
  slot that hadn't started yet was always assumed to feed the car, so on 28 Sep the plan held the battery through
  EDF's 09:00–15:30 slot (6.99p) after two slots where the car drew nothing. If the car does start charging, the
  car-charging rule holds the battery at once, and the next slot's evidence resets the assumption.

## 0.9.44 (beta)

### Behaviour changes
- None to control: this only counts.

### Diagnostics
- **Simulated timed-window writes while on RAM control.** The virtual timed-window inverter (the one dampening
  uses) now also runs while PowerEngine drives the inverter by RAM remote control. It follows the same plan and
  decisions, so it counts the EEPROM writes timed windows would have made.
  - The Monitoring tile is now **Writes today (real · simulated)**: real EEPROM writes, then the simulated count
    using your dampening settings.
  - Health → Inverter writes today explains it. The plan under RAM switches more freely (RAM switch cost 0.5p
    against 5p for windows), so the simulated figure is an upper estimate of what a timed-window install would see.
  - The dampening 7-day table keeps filling while on RAM control.

## 0.9.43 (beta)

### Behaviour changes
- None.

### Updating
- The version line on Configuration names the app and the card instead of HACS's entity ids. The update script's
  notification says which of them it installed (handover package; picked up at your next HA config sync).

## 0.9.42 (beta)

### Behaviour changes
- None to control.

### Updating
- **One-button update.** Configuration now shows the running version and the installed/available versions of the
  app and card, with an **Update** button. It runs a new script in the handover package,
  `script.powerengine_update`, which:
  1. asks HACS for the latest releases;
  2. installs whichever of the app and card have an update;
  3. restarts AppDaemon if the app changed, and waits for the new version to report in;
  4. leaves a notification saying what's running (refresh the browser for the new card).
  The dashboard updates itself when the new app starts. Needs the updated `docs/ha/powerengine_handover.yaml` in
  `/config/packages/`, and HACS's update entities for both repositories enabled.
- The automatic restart after an update stands aside while the button's script is running, so AppDaemon isn't
  restarted twice.

## 0.9.41 (beta)

### Behaviour changes
- None.

### Dashboard
- Page titles are left-aligned, the same size on every page.
- Configuration and Tests now use the same layout as the other pages (sections, up to three columns wide) instead
  of stretching across the whole screen.

## 0.9.40 (beta)

### Behaviour changes
- None.

### Dashboard
- Every page now starts with its title (Monitoring, Plan, Plan history, Costs, Health, Simulator, Configuration,
  Tests), because on a phone only the tab icons show.

## 0.9.39 (beta)

### Behaviour changes
- **Discharge slow-down near empty is learned** (part of *Learn: charge and discharge slow-down*). From full-rate
  sales, PowerEngine measures how much of the discharge rate is reached in half-hours running below 40%, 30% and 20%.
  Your battery runs at about 4.4 kW below 40% against 4.8–5.2 kW higher up. The plan then runs deep sales and
  evening self-use at that speed. The learned discharge rate is also taken from above 40% now, so the slow tail no
  longer drags it down.
- **Inverter conversion losses are learned** (new *Learn: inverter conversion losses*, on). From full-rate
  half-hours with no solar, it measures how much grid energy reaches the battery when charging and how much of the
  battery's output reaches the house and grid when selling. The plan uses the battery's own round trip × both
  conversions: the grid-to-grid efficiency that arbitrage actually gets. Until now the plan used only the battery's
  own round trip (95.6%, measured at the battery); from 26–27 Sep the grid-to-grid figure looks nearer 80%. So
  arbitrage is now only planned where it pays after all losses. Cost accounting keeps the battery's own round trip,
  since it values the battery's own flows.
- Both re-learn from recent half-hours, so they follow any change of inverter, battery or an added battery. Health
  → Learned from use shows the figures, the samples, and the grid-to-grid round trip in use.

## 0.9.38 (beta)

### Behaviour changes
- **Axle exports are valued at Axle's £1/kWh plus the export rate** (new option *Axle also earns the export rate*,
  on by default, under Axle events): EDF pays its normal 15p on the same export, so an Axle kWh is worth £1.15.
  - Planning counts the full £1.15 when deciding whether to top up before an event.
  - Plan and decision text reads "£1.15/kWh (£1 Axle + 15p export)" instead of "£1/kWh".
  - Event figures on the Costs page: *gross* is kWh × £1.15. *Net* now adds the 15p the battery energy earns; exported
    solar is still credited with Axle's £1 only, since it would have earned the 15p anyway.
  - Past days are re-valued once on start (cost method 4), so earlier Axle events are corrected too.

## 0.9.37 (beta)

### Behaviour changes
- **Check meter energy counters.** Two new optional inputs under Grid and house, *Check meter import today* and
  *Check meter export today* (suggested: the Zappi's `grid_import_today` / `grid_export_today`). With *Use the check
  meter* on, they're preferred over the inverter's counters for the simulator's history import, hour by hour, with
  the inverter's figures filling any hours the check meter doesn't have.
- **Past cost days are rebuilt with the check meter.** The daily cost record now reads the check meter from HA
  history too (it already did live since 0.9.36), and days recorded before are re-measured where history allows,
  so past import/export no longer carry the Solis meter's ~16% overstatement.

### Other
- The configuration catalogue no longer carries role groups (the card places roles by its own topics). That takes it
  from about 15.6 KB to 14.5 KB, well under Home Assistant's 16 KB attribute limit.

## 0.9.36 (beta)

### Behaviour changes
- **The check meter is used for grid power** (new option *Use the check meter*, on by default, under Grid and house).
  While the check meter (the Zappi's grid CT) is reporting, PowerEngine uses it for grid import/export. It corrects
  the inverter's house load by the same difference, because the inverter's house load is its own meter plus its AC
  flow. On 28 Sep the Solis meter read about 16% high both ways. That made the house load about 0.9 kW too high while
  charging and read 0 while selling. If the check meter goes quiet for 3 minutes, the inverter's meter is used again.
  The learned usage profile is corrected too: the 14 days of history are adjusted half-hour by half-hour from both
  meters' history, and replace PowerEngine's own uncorrected record where they overlap.
- **Car charger inputs no longer stop control when they drop out.** The myenergi readings come from a cloud service;
  when they briefly go unavailable, PowerEngine carries on with the car assumed not charging and logs it, instead
  of handing the inverter back to Self-Use (it did for 2.5 minutes at 03:01 on 28 Sep). If they're unmapped or
  missing altogether they still count as not ready, and a long outage still notifies after 15 minutes.
- **Overnight switch cost** (new setting under Selling, 3p): with deeper selling overnight, each switch between
  charging and selling inside the fixed overnight window counts at least this in the plan. At 3p it only settles
  near-ties. Two cycles overnight usually trade more energy than one (about 30p a night more on 28 Sep's prices),
  so a single deep cycle takes about 10p here, or a battery wear figure of about 4p/kWh.

## 0.9.35 (beta)

### Behaviour changes
- **Grid charging stops at the half-hour's target.** Once the battery reaches the target the plan set for this
  half-hour, PowerEngine holds (the grid covers the house) until the next half-hour, as the plan assumed. Before this
  it kept charging to the end of the half-hour: overnight on 28 Sep it charged to 82% against a 76% target. With timed
  windows this can cost one extra write (the charge current set to 0) when a target is reached part-way through.

### Fixes
- Attribute sizes are now measured as Home Assistant stores them (compact JSON). The earlier figure was about 6% high,
  so the configuration catalogue was reported at 16.6 KB when it's about 15.6 KB.

## 0.9.34 (beta)

### Behaviour changes
- None.

### Diagnostics
- Every sensor's published attributes are now measured. If one passes 15,000 bytes, PowerEngine logs a warning once.
  Home Assistant stops recording a sensor's history above 16,384 bytes. The diagnostics export lists the largest
  ones. As of today only the configuration catalogue is close (about 15.4 KB); the next largest is about 10.6 KB.

## 0.9.33 (beta)

### Behaviour changes
- None. Card-only fix (Check meter moved under Grid and house); version kept in step with the card.

## 0.9.32 (beta)

### Behaviour changes
- None. Nothing new is written to the inverter; this release only reads and reports.

### Diagnostics
- **Grid meter cross-check.** A new optional input, Configuration → Grid and house → **Check meter**, takes a second,
  independent grid reading (suggested: the Zappi's grid CT, `sensor.myenergi_…_power_grid`). PowerEngine compares it
  with the inverter's meter, split by what the battery is doing (charging, discharging, idle). It shows the result on
  `sensor.pe_diag_grid_check`: the live difference, averages per battery state over the last 3 days, and a one-line
  verdict. This is to find out why the house load reads about 1.6 kW high whenever the battery charges from the grid.
- The diagnostics export now includes 24 h of history for the raw grid, check-meter, battery, house-load and car
  readings behind PowerEngine's figures, plus the cross-check sensor.

## 0.9.31 (beta)

### Behaviour changes
- After a start (an update or restart), the half-hour that's running is no longer held to whatever the first,
  unsettled plans chose. Mid-half-hour stickiness (which stops near-ties flip-flopping the inverter) now waits until
  the next half-hour begins, instead of only the 5-minute warm-up. This fixes an unexplained "hold" in the running
  half-hour after an update, when selling or charging would have paid.

## 0.9.30 (beta)

### Behaviour changes
- None.

### Dashboard
- The battery and grid gauges have short names (the long ones were cut off on a phone), with a line under them
  saying in words which way each is flowing and how fast (for example "discharging 5.06 kW", "exporting
  3.99 kW"), and a key to the gauge colours.

## 0.9.29 (beta)

### Behaviour changes
- None.

### Fixes
- **Monitoring flow card: the battery's direction was the wrong way round** (dots leaving the battery while it
  charged). The Sunsynk card reads battery power with the opposite sign to PowerEngine's (+ discharging), so it's
  now inverted in the card.

## 0.9.28 (beta)

### Behaviour changes
- None by default.

### New
- **Deeper selling overnight** (Config → Selling, on by default): 0.9.27's behaviour, one deeper overnight sale
  instead of many shallow cycles. Untick it and the arbitrage band's bottom holds inside the overnight window too
  (a hard floor, as it is the rest of the day).

## 0.9.27 (beta)

### Behaviour changes
- **Overnight arbitrage is one deeper cycle, not many shallow ones.** Inside the fixed overnight window the refill is
  guaranteed, so selling below the band's bottom no longer counts the outside-band cost there (down to the
  reserve plus 10%, as before). The plan then sells once and refills once, instead of cycling 90↔75% several
  times: the same energy and the same money (on a typical night the earnings were identical), with far fewer
  switches. Outside the overnight window the band's bottom is still a hard floor, and the top of the band still
  applies overnight until the final top-up.

## 0.9.26 (beta)

### Behaviour changes
- **With arbitrage on, grid charging stays within the arbitrage band, except for the final top-up.** Charging from
  the grid now stops at the band's top (90%) all day and through the night, and cycles stay inside the band. Only
  the last half-hours of the fixed overnight window, just long enough to charge from the band's top to the
  grid-charge target (100%) plus one to spare, may go above it, so the battery still starts the morning full for
  the day (and winter) ahead. Free-power sessions still fill to 100%. Before, the top was only a 2p/kWh cost, so
  evening cycles often ran up to 100%. Solar can still fill the battery above the band in the day.
- The plan chart's legend no longer shows a meaningless value for *Action* (it's for the hover text).

## 0.9.25 (beta)

### Behaviour changes
- None.

### Dashboard (#114, from the Trial tab)
- **Monitoring:** the energy flow is now the **Sunsynk Power Flow Card**, including the car, with a row of needle
  gauges below it: battery (charging ← → discharging), grid (export ← → import), solar, house and battery %.
  Install *Sunsynk Power Flow Card* from HACS if you haven't (Power Flow Card Plus is no longer used).
- **Plan:** hovering or tapping a half-hour on the plan chart now also shows **Action**: what PowerEngine plans then,
  and why. Units are shown for every value.
- The Trial tab is gone.

## 0.9.24 (beta)

### Behaviour changes
- None.

### New
- **Trial tab, B (Sunsynk flow): the car.** Shown as a load within the total. The card's load circle now uses a new
  sensor, `sensor.pe_state_load_power` (house + car), with the car's own power as one of the loads in it.

## 0.9.23 (beta)

### Behaviour changes
- None.

### Fixes (Trial tab)
- **B** Sunsynk flow: cut back to the documented minimum configuration (the extra options gave a configuration
  error).
- **D** Last 30 minutes: now HA's own history graph (the ApexCharts version stayed on "loading" at a 30-minute
  span).

## 0.9.22 (beta)

### Behaviour changes
- None.

### New
- **Trial tab** (#114): side-by-side options for a clearer live picture and plan, to choose from:
  - **A** the current flow card, tuned (coloured values, new flow-rate model);
  - **B** the Sunsynk Power Flow Card (install *Sunsynk Power Flow Card* from HACS first);
  - **C** needle gauges for battery (charging ← → discharging), grid (export ← → import), solar, house and battery %;
  - **D** the last 30 minutes of house, battery, grid and solar power;
  - **E** the plan chart with hover detail: each half-hour's planned action and the reason.
  A line at the top shows PowerEngine's decision and the inverter command. The chosen views will move to the
  Monitoring and Plan tabs, and the Trial tab will go.

## 0.9.21 (beta)

### Behaviour changes
- **RAM remote control never asks for more than the inverter accepts.** Reading the inverter's registers (the new
  PowerEngine Modbus probe) showed it refuses a force power above 5000 W (register value 500): asked for 5200 W,
  it kept the previous 2000 W, which is why charging and selling ran at about 2 kW. A new setting, *RAM max power*
  (default 5000 W, Config → Inverter control), caps the commands, and with RAM remote control the plan uses it as
  the charge and discharge rate. Timed windows are unchanged.

## 0.9.20 (beta)

### Behaviour changes
- **A restart no longer defers the current half-hour's plan.** Right after a start, the first plan is made before
  the load profile (about 30 s later) and the weather have loaded, and it often picks Self-Use. The mid-slot rule
  (0.9.8) then kept that choice for the rest of the half-hour, so a planned charge or sale was skipped until the
  next half-hour. For the first 5 minutes after a start the mid-slot rule is now off, so the plan settles on its
  real choice at once.

## 0.9.19 (beta)

### Behaviour changes
- **RAM remote control: the power is written before and after the mode, and again 5 seconds later.** A force
  charge asked for 5200 W ran at about 1900 W, close to the 2000 W left over from the earlier test: the power
  written straight after the mode change hadn't taken. Now it's written, then the mode, then the power again, and
  once more after 5 seconds; each refresh sends the power either side of the mode too.
- The "not following" notification and log now include what the inverter's power and mode settings read, to tell
  a setting that didn't take from a limit elsewhere (battery, BMS or fuse).

## 0.9.18 (beta)

### Behaviour changes
- **Cheaper switches with RAM remote control.** With the Control method set to RAM remote control, the optimiser
  counts *RAM switch cost* (default 0.5p) per switch between Self-Use, charging and selling instead of the
  *Window change cost* (5p), since a switch is only a temporary setting there. The plan can then take short
  arbitrage cycles it used to pass up. Timed windows keep the 5p cost. Both are on the Config tab under Inverter
  control.

## 0.9.17 (beta)

### Behaviour changes
- **A full car no longer blocks arbitrage in later smart slots.** When the car is plugged in but the charger reports
  the charge complete (Zappi "Completed"), PowerEngine no longer assumes the car will draw power in coming EDF
  slots, so those slots are planned as cheap house slots (charge and sell), not "charge the battery with the car".
  Before, only the rest of a slot already running was freed. If the car does start charging, the car-charging rule
  takes over at once (the battery holds or charges; it never feeds the car).

## 0.9.16 (beta)

### Behaviour changes
- None until you choose it: the Control method defaults to Timed windows, as before.

### New
- **RAM remote control** (Config → Inverter control → *Control method*). Drives the inverter through SolaX
  Modbus's *Battery control override* entities (Solis registers 43135 with the power in 43136/43129) instead of the
  timed windows: grid charge → Force charge at the planned power, hold → Force charge at 0 W, sell or Axle →
  Force discharge at the planned power, Self-Use → Off. Temporary settings, so no EEPROM writes. A change is sent
  at once; a force command is re-sent every *RAM refresh* (default 1 minute) so it stays inside the inverter's
  timeout (about 5 minutes on firmware 420044, measured on the Tests tab). If PowerEngine, AppDaemon or HA
  stops, the inverter returns to Self-Use by itself within about 5 minutes.
  - When it takes over, the timed windows are closed once and then left alone. Pausing, Passive, leaving Active
    for any reason, or switching back to Timed windows turns remote control Off.
  - **Monitoring:** PowerEngine checks the battery follows each command (90 s to respond; charging at least half
    the asked power unless nearly full, discharging at least half unless near the reserve, holding not
    discharging). If it doesn't for 3 minutes you're notified. If the remote-control entities go missing, it falls
    back to the timed windows and tells you.
  - **Status:** a new *Inverter control* tile on the Monitoring tab (method, command, whether the inverter is
    following it); details in the Health tab's control preview. Remote-control changes are journalled but not
    counted as inverter writes; refreshes aren't journalled. The dampening rules and planned-writes forecast
    apply to the timed windows only.

## 0.9.15 (beta)

### Behaviour changes
- **Battery health (SOH) and the inverter's minimum SOC are no longer flagged as stale.** They're settings rather than
  readings and can go years unchanged, so only a missing or unavailable entity is reported now.

## 0.9.14 (beta)

### Behaviour changes
- None.

### New
- **Dampening is measured.** Alongside the real inverter, PowerEngine runs the same plan and decisions on three
  virtual inverters (no dampening, restart hold-off only, and both) and counts the writes each would make. The
  Health tab's *Inverter writes today* shows the last 7 days: writes with no dampening, and what the restart
  hold-off saved and burst damping saved (or would have saved while off). The same summary appears in the
  config page's Dampening tuning section.
- A start-up test now runs PowerEngine's whole start-up against a stand-in AppDaemon, so a fault like 0.9.12's
  is caught before release.

## 0.9.13 (beta)

### Behaviour changes
- **Fix: 0.9.12 didn't start.** The restart hold-off was set up before the config was loaded, which stopped the app
  starting (no entities, dashboard blank). No other changes.

## 0.9.12 (beta)

### Behaviour changes
- **Restart hold-off (on by default).** For 5 minutes after PowerEngine starts, or control resumes or goes Active,
  nothing is written to the inverter: the plan and its inputs settle first, and the inverter keeps running the
  windows already set. Changes driven by safety (an Axle event, free power, the car charging, the minimum reserve)
  never wait; pausing still returns the inverter to Self-Use at once.

### New
- **Dampening tuning** section on the config page: *Restart hold-off* (on) with its time, and *Burst damping* (off
  for now, to evaluate after a few steady days): the first change to a window slot, current or the mode goes straight
  through, and another change to the same thing within the burst window (10 min) waits until the plan has been
  steady for the settle time (5 min), so a burst of changes becomes one write. The Health tab shows how many changes
  were held back today; the control preview shows why writes are being held.

## 0.9.11 (beta)

### Behaviour changes
- **EDF slot requests wait 15 minutes after a restart.** No ready-by change is sent until PowerEngine has been running
  for 15 minutes and EDF's dispatch and ready-by entities have been available for 15 minutes. Straight after a
  restart of AppDaemon, HA or the EDF integration, an empty slot list may only mean it hasn't loaded yet, and a
  request would make EDF re-plan for nothing.
- **Request results count half-hours gained and lost.** A re-plan can also move or drop slots already planned, so a
  request now only counts as a success if it gained more slot time than it lost; the back-off follows the same rule.
  The Health tab shows "+gained / −lost half-hours".
- A ready-by entity coming back after a restart (unavailable → a time) is no longer logged as a request by your
  automations.

### New
- The diagnostics export includes the slot requests and the slot record.

## 0.9.10 (beta)

### Behaviour changes
- None.

### New
- **Planned writes on the plan chart:** purple bars along the bottom of the Plan tab's chart show how many inverter
  writes the plan implies at each coming half-hour, if it runs as it stands (real writes only: update-button
  presses, currents, mode). Below the Actions table: the total for the next 24 hours and today's count so far.
  It's an estimate: replans, the settle delay and mid-slot changes aren't modelled.

## 0.9.9 (beta)

### Behaviour changes
- **New windows share a slot's update where they can.** Each of the inverter's three update buttons sends that
  slot's charge *and* discharge times in one write. When a new charge or discharge window needs a free slot,
  PowerEngine now prefers a slot whose button is being pressed anyway for the other kind, so a new charge window
  and a new discharge window usually cost one press rather than two. (Already the case: one press per slot per
  change, never one per window; the currents are separate single writes shared by all three slots.)

## 0.9.8 (beta)

### Behaviour changes
- **No flip-flopping part-way through a half-hour.** A replan in the middle of a half-hour keeps the action the
  inverter is already doing unless changing it gains at least 15p. The plan's next half-hour still takes over at the
  boundary as normal. Near-ties had been switching between hold, charge, sell and Self-Use every few minutes, each
  switch costing a current write and window updates.
- **The daily write limit remembers a resume across restarts.** Resuming after the limit lets PowerEngine carry on
  for the rest of the day, but an AppDaemon restart (an update, for example) forgot that and tripped the limit again
  straight away: back to Self-Use and more writes each time. The resume point is now saved with the write counts.

## 0.9.7 (beta)

### Behaviour changes
- None.

### New
- **Diagnostics export** on the Health tab (admins): one JSON file with the settings, the inverter write log (48 h),
  the plan, PowerEngine's recent log lines, live entity states (PowerEngine's, the mapped inputs and the inverter
  controls) and 24 h of battery, grid and mode history. Download, Share or Copy it, then upload it when there's no
  shell to pull files. Account numbers, serials and similar attributes are removed. A copy of the app's part is kept
  in `/homeassistant/powerengine/diagnostics/` (last 5).

## 0.9.6 (beta)

### Behaviour changes
- **Write counts (and the daily write limit) now count only real inverter writes.** SolaX Modbus keeps the timed
  windows' start/end times in Home Assistant and only sends them when the update button is pressed (one block
  write), so setting those numbers doesn't write to the inverter. They were counted as writes, which roughly
  tripled the count (last night: 170 journal entries, 43 real writes) and tripped the daily limit early. Button
  presses, charge/discharge currents, the storage mode and the remote-control settings still count. The staged
  times are still journalled and shown separately. The same daily limit now allows about three times as many real
  changes; lower it on the Config tab if you want the old protection.

### New
- **Writes today** tile on the Monitoring tab (tap for details).
- **Inverter writes today** at the top of the Health tab: count, changes, daily limit and what's left, writes seen
  from all sources, a breakdown by reason and the latest 30 writes (time, setting, old → new, why). The EEPROM wear
  table moved up with it. New sensor `sensor.pe_diag_writes_today`.

## 0.9.5 (beta)

### Behaviour changes
- None to normal control. New supervised tests write to the inverter only when you start one.

### New
- **Tests tab** (right of Config) with the supervised tests moved there from Config, a *Pause control* switch, and
  clear instructions per test: what it does, what to watch for on the inverter screen, and what PowerEngine checks.
- **RAM remote-control tests** for the Solis *Battery control override* registers (43135, with power in
  43136/43129): force charge, force discharge, hold (0 W force charge) and a failsafe test that stops the command
  being re-sent (SolaX Modbus reload, no Off written) and times how long the inverter takes to drop it by itself.
  Each samples battery and grid power every 30 s, checks the timed-window settings didn't change, and gives a
  verdict. First step towards control without EEPROM writes (#101).
- Test timelines now include grid power.

## 0.9.4 (beta)

### Behaviour changes
- **With arbitrage on, sitting above the arbitrage band costs a little (battery wear).** Where two plans earn the
  same, the plan now sells or uses the top of the battery first and fills it last, instead of charging to 100%
  early and parking there for hours before selling. Overnight this removes the "stops just after midnight" idle at
  full; the battery still ends the cheap window full.

## 0.9.3 (beta)

### Behaviour changes
- **The arbitrage band's bottom is a hard floor outside the fixed overnight window.** Selling there never takes
  the battery below *Arbitrage band: bottom* (75%): the refill might rely on optional smart-charge slots that EDF
  can withdraw, and the band keeps enough in the battery if it does. Inside the overnight window, where the refill
  is guaranteed, selling may still go deeper when it pays (down to the reserve plus 10%). Before, the band was only
  a 2p/kWh cost everywhere, which a 15p sale easily outweighed, so the plan sold down to ~35% in the middle of a
  daytime smart slot.

## 0.9.2 (beta)

### Behaviour changes
- None. Plan and Plan history charts: charging is green and selling red; the battery level is a bold blue line
  (its plan or alternative a light blue one); the kWh axis is grey; charge and sell blocks are more see-through and
  solar export more solid, so solar export shows through.

## 0.9.1 (beta)

### Behaviour changes
- **The inverter's "update times" button is pressed a few seconds after the new window times, not with them.** The
  Solis button sends the window entities' current values to the inverter. Sent in the same burst, it could go
  before the new values had landed and send the previous ones, so the inverter ran one change behind the plan.
  Last night that looks to be why the battery sold from 00:30 while PowerEngine was holding it: the inverter still
  had an earlier discharge window. The button now waits until the new values read back (up to about 12 s), and
  the read-back check runs after that.

## 0.9.0 (beta)

### Behaviour changes
- **Far fewer inverter writes** (last night: 318 in 9 hours, which hit the daily limit). From the write journal:
  - **A running window's start is no longer moved forward every half-hour.** The plan's period always starts at the
    current half-hour, so a window running since 21:30 was rewritten to 22:00, 22:30… (2–3 writes each time, for
    every running window). A running window with the right end time is now left alone.
  - **Only periods starting within 4 hours are programmed.** Windows for tomorrow afternoon were written overnight
    and rewritten each time the plan changed its mind about them. They now wait until they're 4 hours away; a
    window the plan no longer wants is closed once it's that close.
  - **Changes to later windows wait for the plan to settle.** A change that doesn't touch anything running or due
    within 30 minutes is written only once the plan has wanted it for 10 minutes, so a plan that flips and flips
    back (03:40 and 03:45 last night: 35 writes) writes nothing.
  In a simulated night with the plan remade every half-hour, the windows are programmed once and then only real
  changes are written.
- **The arbitrage band applies overnight too.** Its outside-band cost now counts in the fixed overnight window as
  well; the requirement to be full by 06:00 still wins, so the last top-up happens at the end of the window.

## 0.8.15 (beta)

### Behaviour changes
- None. Dashboard: phones in landscape now get the phone charts too (the phone version is used below 600 px wide
  **or** below 500 px tall; a landscape phone is wide but short), and the phone charts have more room on the right
  so the last time label and the Now marker aren't clipped.

## 0.8.14 (beta)

### Behaviour changes
- **The rest of a running EDF dispatch counts at its real price.** Only dispatches that haven't started yet are
  weighed by how likely they are. The rest of one already running was also weighed (6.99p looked like ~9.7p), so
  the plan paused charging in it to wait for the guaranteed overnight rate. If EDF ends a running dispatch early,
  the plan is remade straight away, as before.

## 0.8.13 (beta)

### Behaviour changes
- **Smart slots inside the fixed overnight window aren't discounted.** The planner weighs an uncertain EDF slot's
  price by its likelihood against the price without it. Inside the overnight window that price is the same cheap
  rate, but it was weighed against the peak rate, so slots there looked dearer (~9.7p) than the unslotted end of the
  window (6.99p). The plan then held for hours and charged at the very end. They're now counted at the real price.
- **Charge early in the overnight window.** With the same price all night, the optimiser now prefers charging
  sooner rather than leaving it all to the last hours (a tiny cost per half-hour of delay, overnight only), so a
  slow or cold battery still finishes by 06:00.

## 0.8.12 (beta)

### Behaviour changes
- **A smart slot the car has finished with is just cheap time.** If the car is unplugged, or plugged in but not
  charging while its dispatch is already running (finished or stopped), the rest of that dispatch no longer holds
  the battery for the car: the plan may charge, hold or sell in it like any cheap period (e.g. sell at 15p and
  refill at 6.99p). Later dispatches still expect the car unless it's unplugged. The plan is remade as soon as the
  car starts or stops charging.
- **Overnight car slots charge straight to full.** Inside the fixed overnight window, charging alongside the car
  goes to the grid-charge target (100%) instead of stopping at the arbitrage band's top (90%) and topping up after
  the car slot ends. Daytime car slots still stop at 90%.

## 0.8.11 (beta)

### Behaviour changes
- **Only real tariff prices are shown.** The plan chart's price line, every reason, the Actions table, the Plan
  history and the EDF-slot notes now show a smart slot's own price (e.g. 6.99p). The certainty-weighted figure is
  used only inside the planner's sums and never displayed. The EDF-slot note now reads *slot at 6.99p: 88% likely
  to happen (the plan allows for it not happening)*.
- Charging the battery alongside the car in a smart slot is decided on the slot's own price, not the weighted one.

## 0.8.10 (beta)

### Behaviour changes
- **EDF slots that carry on count as delivered.** EDF re-lists a running dispatch from the current half-hour
  (18:10–04:00 becomes 18:30–04:00), which was recorded as *cut short* and dragged the slot certainty down (to
  about 53%, so the plan counted a 6.99p slot as ~17.9p). A slot that vanishes while running with a new one starting
  within 10 minutes is now a continuation, and past records like that are re-read the same way (your history
  goes from ~53% to ~88%).
- **Prices shown as on the tariff:** the Actions table and reasons show a smart slot's own price, with how likely it
  is (e.g. *6.99p, 88% likely*), instead of the certainty-weighted figure the plan uses internally.
- **Three windows retried:** if the inverter's `_2`/`_3` window entities weren't found (e.g. at start-up before the
  integration was ready), PowerEngine used the single rolling window until AppDaemon restarted. It now looks again
  every 10 minutes and logs when it falls back.

### New
- **Write journal:** every inverter write is recorded with its time, entity, old and new value and why
  (`/homeassistant/powerengine/write_journal.json`, last 3 days), to find where writes add up.

## 0.8.9 (beta)

### Behaviour changes
- **Notifications go to Home Assistant's notification area by default** (the bell; a persistent notification per
  problem, dismissed automatically when it's over). Before, nothing was sent until a phone notify service was
  chosen. *Send to* now offers the notification area, your phones, or Off; configs that never chose one get the
  notification area.
- Health tab *Inputs*: a handover guard AppDaemon has lost track of is checked with Home Assistant directly (as
  control already does since 0.8.6), so it no longer shows *missing* when it's there.

## 0.8.8 (beta)

### Behaviour changes
- **Riding through restarts:** when required inputs go missing while live (e.g. an HA restart, with the inverter
  integration reconnecting), PowerEngine now stops making changes but leaves the inverter's programmed windows
  running for 10 minutes, instead of immediately closing them and writing them again when the inputs return. If
  they're still missing after 10 minutes, it returns the inverter to Self-Use and notifies you, as before. Fewer
  inverter writes, and a charge in progress isn't interrupted.

## 0.8.7 (beta)

### Behaviour changes
- **The battery charges alongside the car at a cheap rate.** In a car smart-charge slot at a cheap price (with
  *Top up when cheap* on), the plan now grid-charges the battery too, up to the top-up level (the arbitrage band's
  top with arbitrage on, else the grid-charge target), instead of just holding. Live: if the car is charging at a
  cheap rate the plan didn't expect, the battery charges too. The fuse limit still applies (house and car first).
  At a peak rate the battery still holds.

## 0.8.6 (beta)

### Behaviour changes
- **Guards checked with Home Assistant when AppDaemon has lost track of them.** AppDaemon keeps its own copy of
  HA's states and can miss an entity that was re-created after it started (Predbat restarting does this), which
  showed as *switch.predbat_set_read_only is None* while the switch was really on. When AppDaemon has no state for a
  guard, PowerEngine now asks HA directly (a template render) and rechecks every 30 s. Only if HA also has no such
  entity does it count as absent (safe, as in 0.8.5); if HA can't be asked, the guard is *unverified* and never
  counts as safe. This closes a gap in 0.8.5, where a re-created Predbat switch AppDaemon couldn't see would have
  counted as absent even if it was off.

## 0.8.5 (beta)

### Behaviour changes
- **A handover guard that doesn't exist counts as safe.** If a guard entity is missing, unknown or unavailable
  (e.g. Predbat's read-only switch while Predbat isn't connected to Home Assistant), PowerEngine no longer refuses
  Active: something that isn't in Home Assistant can't be controlling the inverter. It logs it, notifies you once
  (*handover guard not available*) and lists it in the Operation mode's `guards_absent` attribute. A guard that
  exists and is in the wrong state still stops control, as before.
- **Handover package:** *Restart Predbat if it didn't connect* also runs if the switch goes missing for 10 minutes
  while HA is running (not only after an HA restart), at most once an hour.

## 0.8.4 (beta)

### Behaviour changes
- **Quicker recovery when inputs come back:** while required inputs are missing (e.g. the SolaX Modbus integration
  reconnecting after an HA restart), PowerEngine now rechecks them every 30 s instead of every 5 minutes, so it
  goes back to Active within half a minute of them returning.

## 0.8.3 (beta)

### Behaviour changes
- **Restart after an update:** when HACS installs a newer version, PowerEngine notices within a minute (the version
  on disk differs from the one running) and fires `pe_update_installed` once. The handover package's new
  *PowerEngine - Restart AppDaemon after an update* automation restarts AppDaemon (add-on `a0d7b954_appdaemon`)
  30 s later and notifies you to refresh the browser.
- **Handover package (HA side):** everything is now named *PowerEngine - ...* (entity IDs unchanged), plus two more
  automations: *Restart AppDaemon if PowerEngine stops* (heartbeat 7 minutes old; at most once an hour) and
  *Restart Predbat if it didn't connect* (5 minutes after HA starts, if Predbat's read-only switch is missing;
  add-on `6adb4f0d_predbat`).

## 0.8.2 (beta)

### Behaviour changes
- None. The Config page is reorganised by topic, with search, filters and colour for required and optional
  inputs (card 0.8.2). The inverter's export-limit entity is now labelled *Inverter export limit (entity)* so it
  isn't confused with the *Export limit* setting (the kW your DNO allows).

## 0.8.1 (beta)

### Behaviour changes
- None by default. The choices behind 0.8.0 are now settings:
  - **Learn features, one per figure:** *Learn: charge slow-down near full*, *where discharging stops*, *export
    ceiling*, *car charge rate* (all on). They replace 0.8.0's single *Use learned limits* (ignored if saved).
  - **Battery location** (*Cold battery* settings): garage or outbuilding (24 h, the default), outside (6 h),
    inside (72 h) or custom (*Battery warm-up time*).
  - **Temperature source:** new optional inputs *Outside temperature* (your own sensor wins over the forecast for
    the hours it has seen) and *Battery temperature* (the battery's own sensor: the estimate ahead starts from it
    and the cold learning uses it). Unmapped: Open-Meteo forecast only, as in 0.8.0.
- The role catalogue leaves out `required` when it is "yes" (the card fills it in), to stay under HA's 16 KB
  attribute limit.

## 0.8.0 (beta)

### Behaviour changes
- **Cold-battery caution** (new feature, on by default): the plan expects charging at 50% of the normal rate while
  the battery is estimated to be below 4 °C, so it starts overnight charging earlier. The battery temperature is
  estimated from Open-Meteo's outside temperature (last 3 days and next 3, fetched hourly for your home zone's
  location), lagging it by the *Battery warm-up time* (24 h). Caution lasts until the battery is 3 °C above the
  threshold. New *Cold battery* settings: threshold, rate, release margin, warm-up time.
- **Learned limits** used for planning once there's enough data (Health tab, *Learned from use*): charge and
  discharge rates (*Use measured* on *Max charge/discharge power*, ticked by default), charge taper near full,
  reserve where discharging stops (only raises it), export ceiling and car charge rate (*Use learned limits*
  feature, on by default), cold threshold and rate (*Learn cold behaviour*, on by default).
- The inverter is still asked for the configured charge and discharge rates; learned figures only shape the plan.
- Each recorded half-hour now also stores what PowerEngine asked the inverter for, the outside temperature and the
  estimated battery temperature (the data the learning uses).

### New
- Entities: `sensor.pe_diag_learned` (the table) and `sensor.pe_diag_battery_temperature`.
- Plan tab: a note listing cold-caution periods. Health tab: *Learned from use* table and battery temperature.

## 0.7.7 (beta)

### Behaviour changes
- None. Dashboard: the Simulator tab moves to just before Config.

## 0.7.6 (beta)

### Behaviour changes
- None. Dashboard only.

### Dashboard
- **Charts on phones** (screens under 600 px wide): each chart has a phone version with only its main axis
  (the others still scale the lines; tap the chart for exact values), no sideways axis titles, smaller labels, the
  legend on top and a shorter span: *Next 18 hours* instead of 36, 5 days of daily costs instead of 8, 14 days of
  losses instead of 30. Desktop and tablet views are unchanged apart from the names below.
- Shorter series names everywhere (Battery, Alt plan, Price, Solar, Load, Charge, Export, Solar export, …), and
  every series now carries its unit (%, p/kWh, kWh), shown in the tooltip.

## 0.7.5 (beta)

### Behaviour changes
- None. Fix: *Operation mode* (`sensor.pe_state_operation_mode`) now accepts **paused**. Before, HA rejected that
  value and kept showing the previous one (usually *passive*), so the Battery controller panel showed ✗ while
  PowerEngine was correctly paused for testing.

### Card
- Battery controller panel: an entity it can't read (e.g. Predbat's read-only switch while Predbat is restarting) now
  makes the status *not fully live*, with a note, instead of *live*.

## 0.7.4 (beta)

### Behaviour changes
- **The Battery controller switch now leaves the chosen controller fully live.** Choosing *PowerEngine* sets
  *Operation* to **Active** (saved in the config, with a backup) and un-pauses it; choosing *Predbat* sets it to
  **Passive** (which closes PowerEngine's windows) before Predbat leaves read-only. Done through a new event,
  `pe_set_control` (`operation: active|passive`), fired by the handover package's scripts. The PowerEngine handover
  waits for *active* and notifies **PowerEngine is live** or **did NOT go live** with the reason.
- Handover package: the old `battery_pause_powerengine` script is gone (nothing uses it).

### New
- Battery controller panel: a status line (live / paused for testing / not fully live), **Pause for testing** and
  **Resume (go live)** buttons while PowerEngine is selected. Pausing is no longer shown as a fault.
- INSTALL.md: *Active mode* rewritten around the switch: how it works, one-off setup, testing, first go-live.

## 0.7.3 (beta)

### Behaviour changes
- **Handover package (`docs/ha/powerengine_handover.yaml`), HA side:** `input_select.battery_controller` now offers
  only **Predbat / PowerEngine** (Predbat first, so it's the fallback if the saved choice was *Legacy automations*).
  Handing to Predbat is now its own script, `script.battery_handover_to_predbat`: pause PowerEngine, wait until it
  has left Active and closed its windows, keep the legacy automations off, then turn Predbat's read-only off. It no
  longer calls the Predbat package's `predbat_handover_to_predbat`. Handing to PowerEngine waits 10 s after Predbat
  goes read-only before resuming. Nothing in the app changed.

### New
- **Battery controller panel** at the top of the Config tab (card 0.7.3, `custom:powerengine-handover-card`):
  Predbat | PowerEngine buttons with a confirm step, *Switching…* while a handover runs, and a table of what Predbat
  read-only, the legacy automations, PowerEngine pause and PowerEngine mode should be against what they are, with
  *Re-apply* when they don't match.

## 0.7.2 (beta)

### Behaviour changes
- None. Health tab: the *Inverter control preview* now shows the three-window design: each charge and discharge
  window PowerEngine wants against what the inverter holds now, both currents and the storage mode (0.7.1 showed
  only "The preview appears once PowerEngine has made a decision").

## 0.7.1 (beta)

### Behaviour changes
- **All three inverter windows** (Active; previewed in Passive). The plan's next charge periods (grid charge or hold)
  and sell periods within 24 hours are programmed into the Solis's three charge and three discharge windows at once
  (the `_2`/`_3` entities are found from the first window's), split at midnight. A window is only rewritten when its
  period has passed and the slot is needed, or when the plan moves it by more than 30 minutes (periods starting
  within 2 hours are always set exactly). Hold and charge share the charge current, set for the window running or
  the next one due. A repeating night therefore costs next to no writes. Without the extra windows it falls back to
  the rolling single window.
- **Watchdog automation** added to `docs/ha/powerengine_handover.yaml`: closes every window (Self-Use) if
  PowerEngine's heartbeat stops for 15 minutes while it's the battery controller.
- **The fixed overnight window** (learned from your rates: 23:00-06:00 for EDF Go Electric) is marked in the plan.
  Inside it the arbitrage band doesn't apply, and with *top up when cheap* on the optimiser must end it at the
  grid-charge target (100%), so it can sell deep and refill once rather than shuffle between 75% and 90%.
- **Window change cost** (new setting under *Inverter control*, default 5p): counted by the optimiser each time the
  plan switches between self-use, charging and selling (a hold/charge change counts a fifth), so it only switches
  when that clearly pays. On 24 Sep: 17 switches down to 10, for 10p less.
- **Car charging follows the plan:** while the car charges, the battery charges if the plan says so (to the plan's
  level, not 100%), and otherwise holds; it never feeds the car.
- Health tab: inverter writes for the three-slot design are counted alongside the others (*would*), so you can see
  the rate on your own plans while in Passive.
- Simulator results are recomputed overnight with the new rules.

## 0.7.0 (beta)

### Behaviour changes
- **The optimiser now plans the battery** (#67; new feature *Optimised planning*, on by default). Every re-plan, the
  optimiser chooses each half-hour's action for the lowest cost over the plan (same forecasts, prices, battery
  physics, fuse and export limits, battery wear, the arbitrage band and its outside-band cost). The rule-based plan
  still runs alongside: its reasons are kept where both agree, and new plain-English reasons explain the rest
  ("charge at 6.99p to sell at 15p from 13:00", "sell at 15p: refilled at 6.99p from 17:00", "keep the charge
  for 16:00 (30.28p)"). Grid charging targets the level the optimiser plans for that half-hour.
- Safety rules the optimiser always keeps: the battery never feeds the car in a smart-charge slot (hold or charge
  only), a sale never takes the battery below the reserve plus 10%, Axle events force-discharge, free-power sessions
  fill the battery, and the live overrides (car charging, reserve, Axle not yet started) still apply.
- Plan tab: says which method planned, and how much more the rule-based plan would cost (thin green line).
  Turning *Optimised planning* off goes back to the rule-based planner.
- Simulator: the planner table now compares the rule-based planner with the optimiser.

## 0.6.5 (beta)

### Behaviour changes
- **The arbitrage band is a guide, not a limit.** Selling below the band's bottom (75%) is allowed when it still pays
  after a new *Arbitrage outside-band cost* (default 2p/kWh, on top of wear) and the forecast load is still covered:
  the battery must reach the next cheap period with the reserve plus 10% and no half-hour before it may import more
  than it would have. Grid-charging above the band's top (90%) works the same way in the optimiser; routine cheap
  top-ups still stop at the top, and a forecast shortfall can still charge higher. Setting the cost to 0 ignores the
  band; a high value keeps arbitrage inside it.
- Simulator results are recomputed overnight with the new rule.

## 0.6.4 (beta)

### Behaviour changes
- **Arbitrage band** (new settings under *Arbitrage*): *Arbitrage lowest charge* (default 75%) and *Arbitrage highest
  charge* (default 90%). Selling from the battery now stops at the lowest charge (before: the reserve plus 10%), so
  arbitrage only ever uses the top of the battery. With arbitrage on, routine cheap top-ups (overnight and in
  smart-charge slots) stop at the highest charge instead of the grid-charge target, keeping the battery out of the
  full zone while it cycles; a forecast shortfall can still charge it higher. Arbitrage runs in any cheap period,
  smart-charge slots included. Passive-only effect until Active is chosen.
- Simulator: the optimiser keeps to the same band; PowerEngine's planner run now uses the planner's own charge
  targets (before, every grid charge went to 100%). Cached results are recomputed overnight.

## 0.6.3 (beta)

### Behaviour changes
- None to your devices.
- **Fix: export income on days rebuilt from HA history.** Half-hours rebuilt before the export-rate sensor had any
  history were valued at 0p for export, which understated export income on the Costs tab (11-24 Sep for you) and
  in the Simulator. They now fall back to your current export rate. The cost history is re-valued once on start-up
  (cost method 3) and the Simulator recomputes its cached results in the next overnight run.
- With that fixed, the planner comparison on 11-25 Sep shows PowerEngine's planner level with the best achievable
  (not £3 a month behind, as 0.6.2 reported); topping up overnight and exporting the next day's surplus solar is
  not a loss when export pays more than the cheap import plus losses and wear.

## 0.6.2 (beta)

### Behaviour changes
- None to your devices.
- **Simulator phase 4: equipment** (#54). New settings on the Simulator tab for a bigger battery (usable kWh, power,
  cost), more solar (your kWp now, extra kWp, cost; recorded solar scaled, same roof direction) and a second car
  (miles a year, kWh a mile, charger power; charged in each day's cheapest half-hours). Each ticked item, and all
  of them together, is run on your tariff and the best other tariff and compared with the same tariff without it,
  with payback once there's a year of history.
- **Simulator phase 5: PowerEngine's planner against the best achievable.** Your tariff and the three best others
  are also run by PowerEngine's own planner (knowing each day's load and solar). The tab shows best achievable,
  planner and the gap per month, next to what you actually paid.

## 0.6.1 (beta)

### Behaviour changes
- None to your devices.
- **Simulator phase 2: a year of history** (#54). The new set-up card on the Simulator tab reads up to a year of
  hourly energy (house, car, solar, grid) from Home Assistant's long-term statistics whenever an admin has the
  page open, a month at a time, and hands it to the app (the app has no admin token, so it can't read them
  itself). Imported days are priced on your tariff at today's rates (from the tariff code on your rate sensor)
  and marked estimated. The tab then shows a year-long ranking alongside the last 30 days; notifications use the
  year once there are 90 days or more.
- **Simulator phase 3: heat pump.** Settings on the Simulator tab (gas used a year, boiler efficiency or a
  heat-loss figure, hot water, COP at -3 and 12 C, pump size, pre-heating hours, gas prices, installed cost).
  Overnight, hourly outside temperatures for your home location come from Open-Meteo (free, no key; cached).
  Heat demand = the house's heat loss (from a year of gas use) x degrees below 15.5 C, plus hot water; hot water is
  heated in the cheapest half-hours and heating may run up to the pre-heat hours early. Your tariff, heat-pump
  tariffs and the five best others are each run with the heat pump and compared with the same tariff plus gas:
  cost a month, heat-pump kWh, seasonal COP, and payback once there's a year of history.
- Your region is now taken from your tariff code (was fixed at A).

## 0.6.0 (beta)

### Behaviour changes
- None to your devices. The Simulator only reads your records and public tariff lists.
- **Tariff Simulator, phase 1** (#54). Each night at 01:30 (spread over short slices so AppDaemon stays
  responsive) PowerEngine:
  - reads the current household tariffs from the Octopus and EDF public tariff APIs (region A), once a day;
  - fetches each tariff's historical half-hour rates and standing charges for your recorded days (cached, so
    later nights only fetch the new day);
  - replays each recorded day on each tariff with the optimiser running the battery, carrying the charge from day
    to day (your tariff with the prices you paid is the baseline; other tariffs have the car's charging moved to
    the cheapest half-hours; selling from the battery only if Arbitrage is on; battery wear counted in choices);
  - ranks them on the new **Simulator** tab (last 30 recorded days, per month, against your tariff and what you
    actually paid).
  - **Notifications** (new *Tariff opportunities* type, on by default): when a tariff would have saved at least
    £5 and 5% a month over at least 14 days (at most once a month per tariff), and when new tariffs are published.
  - Switch it off with the new *Tariff simulator* feature. Data is kept in `/homeassistant/powerengine/simulator/`.
- The optimiser can count battery wear in its choices (used by the Simulator; the Plan tab comparison is unchanged).

## 0.5.18 (beta)

### Behaviour changes
- **Smart-slot certainty** (#14). Each planned EDF slot gets a certainty from the slot history (delivered, cut
  short or cancelled), grouped by overnight/daytime and how far ahead it was announced; it starts at 70% and
  learns as slots finish. The plan prices an upcoming slot at certainty × slot price + the rest at the normal
  price, so it only relies on slots that usually happen (e.g. a slot that's often cancelled no longer counts as
  cheap for charging the battery). The slot in progress keeps its real price. Shown on the Plan tab (each slot's
  certainty and the price used) and the Health tab (overall and per group).
- **Estimated prices no longer copy yesterday's smart slots.** Beyond the published prices, each half-hour is
  estimated from the same time the day before; a cheap half-hour that was a smart slot (outside the usual
  overnight window) is now estimated at that day's normal (peak) price instead.

### Other
- **Plan history tab** (#58). Pick a day (up to 30 days back) and a plan (start of day, or the first plan of any
  hour) and see it against what happened: battery %, price, grid charging, battery and solar export, house load
  and solar (plan dashed, actual solid), the plan's actions, and the plan's expected cost against the actual
  cost for the half-hours it covered. The first plan of each hour is now kept for 60 days (plans-<date>.json);
  plans saved before this version only have the battery level.
- #15 (value charge left at the end of the plan) checked: the 36-hour horizon means the end of the plan never
  changes what happens now; a regression test covers it. No change.

## 0.5.17 (beta)

### Behaviour changes
- **Battery round-trip efficiency is now an input** (*Battery and inverter*, default 90.25%, i.e. 95% each way as
  before) with the same **Use measured** tick box as capacity. Ticked (default, as before), the measured figure is
  used once there are 14 days of data; unticked, the configured figure is always used. Changing it re-values the
  cost history.
- **Health tab: losses as a % line.** The system losses chart now plots each day's losses as a % of the energy
  handled (purple line, right axis, labelled) alongside the kWh bars. The % should be steadier than the kWh, so a
  jump points at a sensor or inverter problem.

## 0.5.16 (beta)

### Behaviour changes
- **"Use measured" for battery capacity.** A tick box next to *Usable battery capacity* on the config card, showing
  the measured figure once there is one. Ticked (the default, and what earlier versions did automatically), the
  measured capacity replaces the configured one for planning, simulation and costs once it's measured; unticked,
  the configured figure is always used. Changing it re-values the cost history with the capacity now in use.
- `sensor.pe_diag_battery_capacity` shows `use_measured` and `in_use_kwh`.

## 0.5.15 (beta)

### Behaviour changes
- None to your devices.
- **Plan chart: solar export** (#59). Forecast export of surplus solar (the battery full, or charging as fast as it
  can) is drawn as amber half-hour blocks on the kWh axis, separate from battery export (red). Battery export now
  counts only energy from the battery; before, a half-hour selling from the battery also included any solar
  exported alongside it.

## 0.5.14 (beta)

### Behaviour changes
- **Active mode is available.** Choose *Active* under Operation on the Config tab. PowerEngine then writes the
  Solis timed-slot settings (and, with smart-charge optimisation on, EDF's ready-by time), but only while every
  handover guard is safe and control isn't paused. Passive stays the default; nothing changes until you choose
  Active.
- **Inputs failing while in control:** the inverter is now returned to Self-Use (windows closed) and you're
  notified; control resumes when the inputs recover. (Before, writing just stopped.) A tripped handover guard
  still writes nothing.
- **Daily write limit** (new setting, *Inverter control*, default 150): if PowerEngine's own writes reach it in a
  day, control pauses (the pause switch turns on, returning the inverter to Self-Use) and you're notified.
  Resuming allows the limit again.
- **Inverter clock:** new inputs *Inverter clock* (`sensor.solis_rtc`) and *Sync inverter clock*
  (`button.solis_sync_rtc`). Drift is checked every 10 minutes (Health tab; a finding above 2 minutes). In
  Active mode the clock is synced weekly, and within 10 minutes if it's a minute or more out (e.g. when the clocks
  change on 25 October).

### Other
- Inverter writes: PowerEngine's own writes are counted separately (Health tab), and no longer double-counted in
  *observed*.
- `docs/ha/powerengine_handover.yaml`: an optional HA package with a PowerEngine / Predbat / Legacy selector
  that runs the right handover.
- Shorter role catalogue (sensor-only inputs no longer repeat their domain).

## 0.5.13 (beta)

### Behaviour changes
- **Supervised inverter test: the first thing that can write to the inverter.** Only when an admin starts it
  from the Config tab, ticks *I'm watching*, and the handover guards are safe. It writes one action's timed-slot
  settings (hold at 0 A, grid charge, force discharge or self-use) for 1 to 10 minutes, reads them back, logs
  battery power and SoC each minute, then returns the inverter to Self-Use (windows closed) and reads that back.
  Refused while PowerEngine is in control, while another test runs, or if any control output is unmapped. The
  window it writes ends 2 minutes after the test so a restart can't leave it open. Results on
  `sensor.pe_diag_test_write`, the log, the logbook and (if on) the health notification.
- **Handover guards.** New read-only inputs (*Handover guards*: another controller's read-only switch must be on;
  up to two other automations must be off). Active mode is refused unless at least one guard is mapped and all
  are safe, re-checked whenever one changes. If a guard trips while in control, PowerEngine stops writing and
  notifies you; it does not write anything more.
- **Pause control** (`switch.pe_ctl_pause`, top right of the Monitoring tab). In Active mode, pausing (or choosing
  Passive) returns the inverter to Self-Use once and then writes nothing until resumed; the mode shows *paused*.
  No effect in this Passive-only build other than the mode reason.
- Every build is still Passive-only for normal control.

### Other
- **Losses as a % of energy handled** (#53): system losses are also shown as a percentage of the energy supplied
  each day, labelled above each bar on the Health tab losses chart, with yesterday's and the average % in the text.
- Shorter input descriptions on the config card (the catalogue must stay under HA's 16 KB attribute limit).

## 0.5.12 (beta)

### Behaviour changes
- None to your devices: Passive-only. The control layer is built but cannot write: every build still forces
  Passive, and writing happens only in Active mode.
- **Inverter control layer (Active design).** Each decision is turned into the Solis timed-slot settings it needs
  (storage mode Self-Use; a charge window for grid charge/hold with the current from power ÷ 52 V, 0 A to hold; a
  discharge window for Axle/export; both closed for self-use; rolling windows at most 35 minutes ahead, never past
  midnight), compared with what the inverter holds now, and reduced to only the writes needed, with one press of
  the update button after any window-time change. In Active mode (not yet available) writes are rate-limited,
  read back after 6 seconds, retried once, and on a second failure control stops and you're notified; bump/boost
  entities are never written.
- **Health tab: Inverter control preview** (`sensor.pe_diag_control`): for the current decision, each setting
  PowerEngine would want next to what the inverter holds now, and how many writes that would take. Use it to check
  the control mapping before Active mode.

## 0.5.11 (beta)

### Behaviour changes
- None to your devices: Passive-only. The optimiser is for comparison only; it never drives decisions.
- **Optimiser alongside the planner.** Each re-plan, a dynamic-programming optimiser works out the lowest-cost
  plan over the same horizon (battery charge in 1% steps, the same physics, prices, limits and forecasts, charge
  left at the end valued at the cheapest price). The Plan tab says how much cheaper, if at all, its plan is than
  the planner's, and draws its battery line (thin green) on the chart. In tests it matches the planner on your
  normal tariff and finds more with arbitrage on (mainly more export cycles), which is what this comparison is
  for: seeing whether a smarter planner is worth switching to.

## 0.5.10 (beta)

### Behaviour changes
- None: documentation only.
- **Install guide consolidated (#10):** seven steps in order, with the config page walked through section by
  section as it is now, a new *First-day checks* step (what each tab should show, and when measured values take
  over), where PowerEngine keeps its files, an *Active mode* placeholder, and troubleshooting for costs, the
  unsigned battery sensor and inverter writes.

## 0.5.9 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Second write model for EEPROM wear (#44).** Alongside the rolling 35-minute window design, PowerEngine now also
  counts what a one-window-per-block design would write on the same decisions: each window runs to the end of
  the plan's block and usually expires by itself (no write to close), with an HA automation as the safety net
  instead of short windows. Both show on the Health tab against the 25-year target (about 11 writes a day).

## 0.5.8 (beta)

### Behaviour changes
- None to your devices: Passive-only. Nothing is sent to EDF in Passive mode.
- **Smart-charge optimisation (FR-8), Passive.** When it's worth it (car plugged in and not charging, no slot in
  the next 3 hours, import not already cheap, and the battery has room or arbitrage is on so cheap power could
  be sold for more), PowerEngine picks the next nearest ready-by time (different from the current one) with the
  charge target kept at 100%, checks 15 minutes later whether EDF added slots, and backs off 30 min, 1 h, 2 h,
  then 4 h if not, with at most 6 requests a day and at least 20 minutes between any two. In Passive mode these
  are recorded as "would" requests. Every other ready-by change (e.g. your four IO Schedule automations) is logged
  with whether EDF added slots, as the baseline. Health tab: *Asking EDF for slots*.

## 0.5.7 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Arbitrage planning.** With *Energy arbitrage* on (Config tab, off by default), the plan sells stored energy in
  the half-hours just before a cheap refill, latest first, while:
  selling beats buying it back (export price − refill price ÷ round trip − wear ≥ the minimum profit setting);
  the battery still reaches the refill with the reserve plus 10%; and nothing before the refill imports more.
  Uses the export limit. New plan action **Export**, shown on the Plan tab (reason gives the profit per kWh), in
  the Passive simulation, and in the Costs tab's arbitrage layer. On your tariff (15p export, 6.99p refill, 2p
  wear) that is about 5p/kWh. Check your export tariff allows exporting grid-bought energy before relying on it.

## 0.5.6 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Learned battery parameters (#6).** From the recorded half-hours PowerEngine now measures the battery's
  usable capacity (energy in/out against change in charge), the highest charge and discharge rates it has
  actually run at, and the lowest charge seen. New `sensor.pe_diag_battery_capacity`; shown on the Health tab.
  Once 14 full days and 100 samples agree with the configured capacity within 30%, the measured capacity is used
  for planning, simulation and costs (as the measured efficiency already is). Rates are shown, not used: they
  reflect how the battery has been run, not necessarily its limit. (Checked against your data for 25 Sep:
  18.1 kWh measured against 18 kWh configured.)

## 0.5.5 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Automatic cheap threshold (#12)**, on by default. What counts as cheap is worked out from the prices ahead:
  the bottom fifth of the price range, and only where storing the energy pays (the other prices x round-trip
  efficiency, less battery wear). The *Cheap import threshold* setting becomes the maximum. On today's EDF tariff
  (6.99p / 30.28p) nothing changes; it matters if the tariff changes or prices vary more. A flat tariff has nothing
  cheap; free or negative prices always count. The Plan tab shows the threshold used. Turn it off on the Config
  tab to use the fixed setting as before.

## 0.5.4 (beta)

### Behaviour changes
- None. Version kept in step with the card (config page shows suggested entities for unmapped inputs).

## 0.5.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Car charging at peak holds the battery again.** The 0.5.2 "cover the house, not the car" rule is dropped:
  a 7.4 kW boost dwarfs the house load, so it isn't worth the extra control.
- **Inverter write tracking (EEPROM wear).** New `sensor.pe_diag_inverter_writes` and a Health tab section:
  writes per day by your current setup (observed changes to the mapped control entities, e.g. Predbat pressing
  the update button) and what PowerEngine's Active design would make, each with how many years 100,000 writes
  would last at that rate.
- **Control inputs for the Solis timed slots** (Config tab, *Control outputs*): charge and discharge window
  start/end hour and minute, charge and discharge current, the update button and the storage mode. They replace
  the override inputs (pre-FB00 firmware has no override); old override mappings are dropped from config.yaml
  automatically. Map the new ones so writes can be counted.
- The settings list moved to its own sensor (`sensor.pe_map_settings`) to keep each under HA's attribute size
  limit. Needs card 0.5.3.

## 0.5.2 (beta)

### Behaviour changes
- None to your devices: Passive-only. These change what PowerEngine *would* do (decisions, Passive simulation):
- **Car charging at peak: the battery covers the house, not the car.** Previously PowerEngine would hold the
  battery; now it would self-use with the battery's discharge limited to the house's own load, so the house isn't
  pushed onto the peak rate. At the minimum reserve it still holds. (How to apply the limit to the Solis is part
  of the Active design; hold remains the fallback.)
- **Fuse limit in live decisions.** When grid charging would take house + car + battery over 90% of the main fuse,
  the decision says so and shows the reduced charge rate (the plan already allowed for it).
- **Smart-charge slot record.** Every EDF slot is tracked: ran (confirmed when EDF lists it as completed),
  cancelled before starting, or cut short, with the energy the car actually drew. Shown on the Health tab; it is
  the history for the slot-certainty score (#14).

## 0.5.1 (beta)

### Behaviour changes
- None to your devices: Passive-only. (Notifications are messages to your phone, not control.)
- **Phone notifications** through the HA companion app, set on the Config tab under *Notifications* (needs card
  0.5.1). Off until you choose a notify service. Then, each switchable: Health problems, inputs not working for
  15 minutes, Axle events scheduled, free-power sessions announced, and (off by default) a daily summary at 08:00.
  Each thing is sent once (an input that recovers can alert again later), at most 10 a day.

## 0.5.0 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Health tab** (new, between Costs and Config):
  - Findings: inputs PowerEngine's checks flag, plus sanity checks on yesterday's data: gaps in recording,
    battery power that never shows charging (an unsigned sensor), energy unaccounted for (or more used than
    supplied), and large battery-ledger corrections.
  - Battery round-trip efficiency and system losses, with losses by day for 30 days.
  - Plan vs what happened: each day's first plan is kept and compared the next day (house-load and solar
    forecasts vs actual, and how far the battery was from the plan).
  - Uptime: version, start time and heartbeat.
- New entities: `sensor.pe_diag_health` (ok / warnings / problems, with findings and accuracy as attributes) and
  `sensor.pe_diag_started`.

## 0.4.13 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Measured losses.** New diagnostic sensors `sensor.pe_diag_battery_efficiency` (round trip, measured over the
  last 30 days from the battery's energy in and out and its change in charge) and
  `sensor.pe_diag_system_losses` (yesterday's inverter/standby losses, with a per-day history). Once 14 full days
  are recorded, the measured efficiency replaces the configured 95% in the battery ledger, the default-battery
  simulation, the Passive simulation and the planner; costs are re-valued when it moves.
- **Fix: days weren't rebuilt after re-mapping the battery sensors.** Cost records now carry a flow id made from
  the method version and the inputs they were read from; when those inputs change (on save, or at start-up), the
  last 14 days are rebuilt from HA history automatically.

## 0.4.12 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: "Costs" on the left and a small **Alignment** switch at the far right of the same row, with no box
  around it. The icon shows the current alignment. Needs PowerEngine card 0.4.12 (new `powerengine-toggle-card`).

## 0.4.11 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: the alignment control is a small one-row tile at the far right, labelled "Alignment", with the toggle
  beside the name.

## 0.4.10 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: the Right-align numbers control is now a normal on/off toggle with its full name (0.4.9 showed two
  lightning-bolt buttons, HA's style for a switch whose state it can't confirm, and truncated the name).

## 0.4.9 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Dashboard title is now "Power Engine". The sidebar name comes from `configuration.yaml`: change `title:` under
  `powerengine-dash:` to `Power Engine` (the install guide now shows this).
- Costs tab:
  - The **Right-align numbers** control is now a toggle switch at the top right, and starts **on** (it came up
    off in 0.4.8 because HA starts such switches off). Set once by PowerEngine; your choice is kept after that.
  - Chart: each day's bars are centred on its label; only the last 7 days and today are drawn (no clipped group
    at the left); the legend no longer shows misleading "0 GBP" values.
  - "£-0.00" now shows as £0.00; "1 half-hour recorded" grammar; tiny Axle flags (< 0.1 kWh) are not reported as
    the last event; the method text no longer points at an old version number.
- Cost backfill now also fills **today's** missing half-hours (before PowerEngine started, or cut short by a
  restart), so today's figures cover the whole day rather than just since the last restart.

## 0.4.8 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Costs tab: a **Right-align numbers** toggle (`switch.pe_ui_right_align`, on by default) right-aligns every number
  column in the Costs tables; the day/month column always stays left-aligned. The setting is kept by HA and the MQTT
  broker, so it survives restarts.

## 0.4.7 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- With **Battery charging power** and **Battery discharging power** both mapped, the single **Battery power**
  sensor is no longer used or required (it can stay mapped or be cleared), and the pair become required. If either
  of the pair is unavailable, battery power is treated as unknown rather than falling back to the unsigned sensor.
  PowerEngine's check shows "Not used" for the single sensor. Needs card 0.4.7 for the matching config page.

## 0.4.6 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Plan chart: grid charging (blue) and Axle export (red) are drawn as blocks filling each half-hour they happen
  in, instead of thin bars at the start of the half-hour that looked like short spikes.

## 0.4.5 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Fix: battery charging was counted as discharging.** Solis (via SolaX Modbus) reports battery power without a
  sign, so PowerEngine never saw the battery charge: the cost records showed 0 kWh in and all of it out, which is
  most of the "unexplained" cost. Two new optional inputs, **Battery charging power** and **Battery discharging
  power**, are used instead when both are mapped (Solis: `sensor.solis_battery_input_energy` /
  `..._output_energy`, which are power in W). **Map them on the Config tab.** This also fixes the battery direction
  on the Monitoring tab.
- Cost records now carry a flow-method version; days recorded the old way are rebuilt from HA history
  automatically (the nightly backfill, or ~90 s after a restart).
- The plan always covers at least 36 hours, using yesterday's prices (marked *est.*) beyond what EDF has
  published, so the chart has no empty tail.

## 0.4.4 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Fix: "Carried in battery" drifted negative every day.** Whenever the battery ledger ran empty it was re-seeded
  without counting the seed, so its later use showed as a loss. The ledger is now matched to the battery's real
  state of charge every half-hour; any correction lands in Unexplained. Cost method is now version 2 and all stored
  half-hours are re-valued automatically on start-up.
- **Energy by day** table on the Costs tab (import, export, solar, house, car, battery in/out, battery → export,
  unaccounted, ledger correction) to check the figures against the inverter's counters.
- Labels: "Battery (app)" is now "Battery control", and in Passive mode the page says these layers describe what
  your current setup (e.g. Predbat) did.

## 0.4.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Plan chart: each vertical axis (labels, title and axis line) is drawn in the colour of what it measures:
  green for battery %, orange for import price, blue for kWh per half-hour (solar, load and the bars).

## 0.4.2 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Config page settings are grouped into sections (Battery and charging, Supply limits, Axle events, Arbitrage);
  the app now tells the card which section each setting belongs in. Needs card 0.4.2.

## 0.4.1 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Cost backfill:** on start-up (and nightly at 00:20) PowerEngine fills any of the last 14 days that it didn't
  record, or only partly recorded, by replaying HA history through the same code it uses live. Live half-hours are
  never overwritten. Afterwards every stored half-hour is re-valued in time order so the battery ledger is continuous.
- Re-valuing also re-decides which half-hours were smart slots using the latest overnight window, so early days
  improve as more days are seen.
- History half-hours are marked `source: history` in the cost records.

## 0.4.0 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- **Cost accounting.** Every 30 s PowerEngine splits the energy flows (solar, battery, grid → house, car, battery,
  export) and values each half-hour at the rates seen. A first-in-first-out battery ledger tracks where stored
  energy came from and what it cost. Records are kept in `/homeassistant/powerengine/costs/`.
- **Costs tab:** daily layers S0 (no solar or battery) → solar → smart charge → battery (default) → battery (app)
  → arbitrage → actual, with "carried in battery" and "unexplained" so every day reconciles; today so far; a
  7-day chart; special events (Axle, free power) by month; and the full method.
- New entities: `sensor.pe_cost_today`, `sensor.pe_cost_days`, `sensor.pe_event_last`, `sensor.pe_event_months`.
- The standing charge input is now read (for costs).
- Counting starts from install; history backfill follows in 0.4.1, measured losses in 0.4.2.

## 0.3.8 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- New feature **Top up when cheap** (on by default): the plan charges to the grid-charge target in every cheap slot,
  as a buffer in case the forecast is wrong, instead of buying only what the forecast needs.
- The plan's saving now counts the value of extra charge left in the battery at the end (at the cheapest price in
  the period), so topping up isn't shown as a loss. Part of #15.
- Plan chart's grid-charge bars show only energy going into the battery, not the house's import.
- New settings for arbitrage (not built yet): **Export limit** (6 kW default), **Battery wear cost**,
  **Arbitrage minimum profit**. The arbitrage option on the config page warns to check export tariff terms.

## 0.3.7 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Car charging is now taken from the Zappi plug status ("Charging"), not from charging power above 100 W. Charging
  power is only used if the plug status is unavailable.
- New settings **Main supply fuse** (A, default 60) and **Car charger power** (kW, default 7.4). The plan and the
  Passive simulation cap battery grid charging so house + car + battery stay under 90% of the fuse, reducing the
  battery first. **Set the fuse to 80 A on the config page.**

## 0.3.6 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Plan tab
- Summary text at the top is normal size.
- Actions table and headline show the day ("tomorrow", "Sun") for anything not today, including windows that run
  past midnight.
- Back-to-back grid-charge slots are shown as one window, and its target is the level the plan actually reaches
  (e.g. 87%) rather than the 100% ceiling.
- Charge reasons read "to avoid buying at 30.28p from 21:00 tomorrow" instead of the ambiguous "to cover 21:00 onwards".
- Chart: lines stop where the plan ends instead of being stretched flat to the edge, and the kWh axis is shown so
  the bars can be read.

## 0.3.5 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Input checks no longer mark a power sensor as stale when it is sitting at 0 W. The Zappi's charging power only
  updates when it changes, so an idle charger was flagged stale, which made the inputs "incomplete" and stopped
  PowerEngine planning and simulating until the car next charged.
- The Axle direction input is no longer a warning when it is "unknown" between events.

## 0.3.4 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Fixed
- Logbook entries failed with `got multiple values for argument 'domain'` (AppDaemon reserves `domain`). A test now
  guards the logbook call's arguments.

## 0.3.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- House-load history is now read from HA **one day at a time** in the background (values only). A busy power sensor
  logs ~15,000 readings a day, and asking for 14 days at once returned nothing ("0 house readings").
- Logbook entries are written without an entity reference (AppDaemon turned it into a target, which the logbook
  service rejected with `invalid_format`).

### Fixed
- Plan tab: costs read "this plan earns £0.52 … plain self-use costs £0.00" instead of "£-0.52 vs £0.0".
- No more "Excessive time spent in callback" warning while loading history.

## 0.3.2 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Fixed
- Load history failed to load when an input had no readings in the period (typically the car, if it hasn't
  charged recently): the error `zip() argument 2 is longer than argument 1` discarded the house-load history too.
  The load forecast now learns from HA history as intended.

## 0.3.1 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Until a load profile exists, the planner assumes a steady 500 W house load instead of the live house power
  (a momentary spike was being planned as a 36-hour load).
- PowerEngine keeps its own half-hourly record of house-only load in `/homeassistant/powerengine/load_history.json`
  (15 days), so the load forecast learns even if HA history can't be read, and survives restarts.

### Fixed
- Plan tab: the chart no longer overlaps the actions table.
- Reading load history from HA now copes with every shape AppDaemon returns, logs what it found, and retries hourly
  on failure.
- Plan tab warns while the load forecast is still learning.

## 0.3.0 (beta): "Plan"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now **plans the next 24–48 hours** and the live decision follows the plan (live overrides first:
  Axle event now, free power now, car charging now). The fixed pre-Axle rule is replaced by the plan.
- New entities: `sensor.pe_plan` (full plan in attributes), `sensor.pe_plan_headline`, `sensor.pe_plan_next_mode`,
  `sensor.pe_plan_next_start`, `sensor.pe_plan_next_target_soc`, and `sensor.pe_state_sim_soc` (simulated battery).
- On start and daily at 00:10, PowerEngine reads 14 days of house-load history (and car power) from HA.

### Added
- Forecasts: half-hourly import prices (estimated from the previous day beyond published prices), Solcast solar,
  and a learned house-load profile (weekday/weekend, recency-weighted, car removed).
- Explainable planner: defaults per half-hour, forward battery simulation, then the cheapest earlier slot is chosen
  to fix each avoidable shortfall. Multi-hour Axle events are handled as a need to be met (top-ups worth doing at
  any price below £1/kWh). Every window has a reason.
- Simulated battery (Passive): the SoC PowerEngine would have produced, re-synced at midnight; shown next to the
  real battery on the Monitoring tab.
- Dashboard **Plan** tab: headline, 36-hour chart, actions table with reasons, expected cost vs plain self-use.

### Docs
- Install guide: ApexCharts Card is now needed (Step 5.1); Plan tab described; new troubleshooting rows
  (including HACS forgetting the repositories).

## 0.2.0 (beta): "Decide"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now **decides** every 30 seconds what it would do, and why. The status sentence starts with it,
  e.g. *"PASSIVE. Would grid-charge to 100%: import is cheap (6.99p ≤ 10p)."*
- Each change of decision is added to an **Activity** log (last 20, kept across restarts) and to the HA logbook.
- New entities: `sensor.pe_state_decision` (self_use / grid_charge / hold / force_discharge) and `sensor.pe_state_activity`.
- *House power* only subtracts the car when **House load includes the car charger** is ticked (default: on).

### Added
- Rule stack, highest priority first: Axle event active → keep charge for an upcoming Axle event →
  free-power session → car charging (never let the battery charge the car) → cheap import → minimum reserve → self-use.
- Charge hysteresis so decisions don't flap near the target.
- Settings: minimum reserve, cheap-import threshold, grid-charge target, restart margin, Axle look-ahead and margin,
  house-load-includes-car.
- Dashboard: Decision tile, Activity list, and a **Config** tab (the config card now lives here).

### Docs
- Install guide: the config card is on the dashboard's Config tab (no separate dashboard); the dashboard step is now
  Step 5 with a copy-paste terminal method; Step 6 covers the new settings.

## 0.1.0 (beta): "See"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now reads every mapped input each 30 seconds and publishes a normalised picture of the
  system: 13 new `sensor.pe_state_*` entities (status sentence, battery, grid, solar, house, car, rates,
  smart charge, Axle, free power). Power is in W with fixed signs: battery + = discharging, grid + = importing.
- The car's charging power is subtracted from the house load, so *House power* is the house only.
- PowerEngine writes its dashboard to `/homeassistant/powerengine/dashboard.yaml` on start.

### Added
- Status sentence, e.g. *"PASSIVE (monitoring only). Battery 71%, discharging 1.0 kW. Solar 0.1 kW,
  house 1.2 kW, exporting 0.2 kW. Import 30.28p, 6.99p from 21:00; export 15p. Next smart slot 21:00."*
- PowerEngine dashboard (Monitoring view): status, mode, battery, energy flow, rates, car, smart charge,
  Axle, free power, solar forecast, 24-hour history, health.

### Docs
- Install guide Step 7: register the dashboard (one-off) and install Power Flow Card Plus.

## 0.0.4 (beta)

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now reads `config.yaml` written by the config card, checks every mapped input
  (exists, right domain and unit, available, recently updated) and stays **unconfigured**
  until all required inputs are OK.
- New diagnostic entities: `sensor.pe_map_config` (mapping status and per-input checks) and
  `sensor.pe_map_catalogue` (the input catalogue the card uses).

### Added
- Input catalogue: every input's description, units, sign convention and suggested entity.
- Saving from the config card: validated, written atomically, previous version backed up
  (last 10 kept), logged in the HA logbook with who saved and what changed.
- Hard rule: control outputs can never be bump/boost entities.
- Features section in `config.yaml` (smart charge, arbitrage, Axle, free power).

### Docs
- Full installation guide (`docs/INSTALL.md`), including Step 6: configure PowerEngine.

## 0.0.3 (beta)

### Behaviour changes
- None to your devices: this build is Passive-only and never controls anything.
- New: PowerEngine now creates a **PowerEngine** device in HA (via MQTT) with five entities:
  `sensor.pe_diag_version`, `sensor.pe_diag_heartbeat`, `binary_sensor.pe_diag_config_ok`,
  `sensor.pe_cfg_operation_mode` and `sensor.pe_state_operation_mode`.

### Added
- Operation mode (`operation.mode: passive | active`, default passive). Active is refused by this build.
- Optional additional solar plants (`solar_plants:` list) in `config.yaml`.
- `remove_entities: true` removes every PowerEngine entity.

### Changed
- `operation.dry_run` replaced by `operation.mode`.

### Setup
- Requires AppDaemon's MQTT plugin (see README, "MQTT (one-off)").

## 0.0.2 (beta)

### Behaviour changes
- None. Still a scaffold that never controls anything.

### Fixed
- 0.0.1 read its own AppDaemon definition file as the settings file (AppDaemon passes every app a `config_path` argument). The optional override is now called `settings_file`, and unknown top-level keys in `config.yaml` are rejected.

### Docs
- README: AppDaemon add-on setup (`app_dir`) so it loads apps installed by HACS.

## 0.0.1 (beta)

### Behaviour changes
- None. Scaffold only: loads and validates `config.yaml` and logs the result. Never controls anything.

### Added
- Repository structure, HACS metadata, CI (lint, tests, HACS validation).
