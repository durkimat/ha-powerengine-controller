# Changelog

Every release lists **Behaviour changes** (anything that changes what PowerEngine does to your system) first.

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
