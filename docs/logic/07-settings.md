# 7. Settings that touch the plan

Source of truth: `pe_core/config.py` (`FEATURES`, `FEATURE_DEFAULTS`, `SAFETY`, `SYSTEM_DEFAULTS`) and
`planner.params_from`. Defaults and ranges are as of 0.9.100. Where a setting is read was found by searching the code for
its key; "acts in" is what it changes:

* **Plan**: how the plan is built (rules planner, optimiser, slot build).
* **Live**: the 30-second decision (`decide`).
* **Control**: how the decision reaches the inverter.
* **Learning / other**: not the plan.

A setting marked **fallback only** is read just by the reactive rule stack that runs when there is *no plan*
([04](04-priorities.md), 4.6). With a plan it changes nothing.

## 7.1 Feature switches (on/off)

| Feature | Default | Acts in | What it does |
|---|---|---|---|
| `optimised_plan` | on | Plan | The optimiser chooses the actions. Off: the rules planner's plan is used, the optimiser only compares |
| `auto_cheap_threshold` | on | Plan, Live, smart-charge | "Cheap" is worked out from the day's prices ([02](02-rules-planner.md), 2.2), capped by `cheap_threshold_p`. Off: `cheap_threshold_p` is fixed |
| `fill_when_cheap` | on | Plan, Live | Top up to the target in every cheap half-hour as a buffer against forecast error; also makes the optimiser charge in a cheap car slot, and adds the "not full at the end of the cheap window" cost |
| `arbitrage` | off | Plan, Live | Sell stored energy before a cheap refill when it pays; enables the Export action, the arbitrage band and the buffer ceiling |
| `deep_overnight` | on | Plan | Inside the overnight window arbitrage may sell below the band's bottom, at a higher switch cost |
| `axle` | on | Plan, Live | Plan and act on grid events (force-discharge). Off: event slots are ordinary slots |
| `axle_plus_export` | on | Plan, Live | The supplier's export rate is paid on top of the event's £1 |
| `axle_stop_car` | on | Live (Active only) | While a grid event is in progress, set the car charger's mode (`ev_charge_mode`, a select) to Stopped, and put the mode it had back when the event ends (`pe_core/carstop.py`; kept in `car_stop.json` so a restart mid-event still restores it). It stops the battery's export being charged into the car (6 Oct 2026: a smart slot ran through an event and the event exported 0.12 kWh). A charger already Stopped is left alone; a mode you change during the event is not overwritten at the end; if something resets the mode it is stopped again up to 3 times, 2 minutes apart |
| `free_power_days` | on | Plan, Live | Plan and act on free-electricity sessions (charge to 100%) |
| `smart_charge_optimisation` | on | smart-charge step | Ask the supplier for extra car slots by changing the ready-by time |
| `smart_skip_full_car` | off | smart-charge step | Do not ask for more slots when the car looks full (charger says complete, or the last slot drew nothing). Since 0.9.103 this is the only thing that uses those guesses; with it off, slots are asked for even when the car is full |
| `slots_whole_house` | on | Plan, smart-charge | The supplier gives the whole house the smart-slot rate (if not: smart slots are planned at the standard rate, and extra slots are not requested) |
| `use_check_meter` | on | Readings | Trust the check meter over the inverter's grid meter |
| `learn_taper` | on | Plan | Use the learned charge and discharge tapers |
| `learn_reserve` | on | Plan | Raise the reserve to the SoC where the battery really stops |
| `learn_export` | on | Plan | Lower the export limit to the one actually hit |
| `learn_car` | on | Plan | Use the car's typical kW |
| `learn_car_min` | on | Smart-slot history (certainty, Health tab) | Move the shortest real charge towards what the supplier-confirmed slots show (see page 1) |
| `engine_compare` | on | Costs page (Engines compared, same day) | Each night (03:20) replay yesterday through the whole app once with engine v1 and once with engine v2, from the day's forecast record and the cost records, in a separate process; publishes `sensor.pe_cost_engines`. Needs the forecast records (kept 14 days) and uses about 15 minutes of one core at low priority |
| `learn_conversion` | on | Plan | Include the inverter's AC/DC losses in the efficiency |
| `cold_caution` | on | Plan, Control | Plan slower charging when the battery is cold; the BMS-limit fallback |
| `cold_learning` | on | Plan | Learn the cold threshold and factor |
| `damp_restart` | on | Control (timed) | 5-minute write hold-off after a start or resume |
| `damp_bursts` | off | Control (timed) | Hold back a second quick change until the plan is steady |
| `tariff_simulator` | on | Other | Overnight tariff comparison; does not touch the plan |

## 7.2 Numeric settings

### Battery, price and charge target

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `min_reserve_soc` | 12 % (0 to 100) | Plan, Live, learning | Floor for every discharge in the plan; a planned Self-use holds at it; the override stops there. Must be below the charge target |
| `cheap_threshold_p` | 10 p (0 to 100) | Plan, Live | The most "cheap" can be (auto), or the fixed number |
| `grid_charge_target_soc` | 100 % (10 to 100) | Plan, Live | Target of rules-plan charges, the buffer top-up, the "full at the end of the window" cost, the Charge override, and the fallback. **Not a ceiling for the optimiser's charges** ([09, F2](09-review-findings.md#f2)) |
| `charge_hysteresis_soc` | 3 % (0 to 20) | **Fallback only** | Restart a cheap charge only below target minus this |
| `battery_wear_p` | 2 p/kWh (0 to 20) | Plan, Live | Wear cost per kWh out of the battery; part of the cheap threshold |

### Supply limits

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `main_fuse_a` | 60 A (20 to 200) | Plan, Live | Import limit = 90% of the fuse at 230 V; the battery is cut first |
| `ev_charger_kw` | 7.4 kW (0 to 22) | Plan | Car draw assumed in smart slots (or the learned typical) |
| `export_limit_kw` | 6 kW (0 to 30) | Plan | DNO export limit for Export |

### Grid events

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `pre_axle_lookahead_h` | 6 h (0 to 48) | **Fallback only** | How long before an event to protect charge |
| `axle_margin_soc` | 5 % (0 to 50) | **Fallback only** | Extra charge above the event's need |

With a plan, preparing for an event is the planner's job: it charges in the cheapest earlier slot that pays against the
event rate (or the optimiser finds it). The event value (£1/kWh) is a code constant, not a setting.

### Overnight window (used only when the choice above is `fixed`)

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `overnight_start_h` | 23.5 h (0 to 24) | Plan, Cost book | When the regular cheap rate starts: hours since midnight in half-hour steps (23.5 is 23:30). A start after the end runs over midnight |
| `overnight_end_h` | 5.5 h (0 to 24) | Plan, Cost book | When it stops (5.5 is 05:30, so the 05:00 half-hour is the last). The two must differ |

### Arbitrage

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `arbitrage_min_margin_p` | 1 p (0 to 50) | Plan (rules pass) | Profit per kWh a rules-planner sale must clear after losses and wear |
| `arbitrage_min_soc` | 75 % (10 to 100) | Plan | Bottom of the band, a guide: outside the overnight window a sale may go up to 5 points below it (paying the band penalty); that lower point (70%) is the hard floor |
| `arbitrage_max_soc` | 90 % (20 to 100) | Plan, Live | Top of the band, a guide: a grid charge may run up to 5 points past it (paying the band penalty); also the live car-charge top-up ceiling and the rules planner's buffer ceiling |
| `arbitrage_band_penalty_p` | 2 p (0 to 50) | Plan | Extra cost per kWh outside the band |
| `overnight_switch_cost_p` | 3 p (0 to 100) | Plan | Minimum cost of a full switch inside the overnight window (timed windows only, with deep overnight and arbitrage; RAM control uses `ram_switch_cost_p`) |

Needs `arbitrage_min_soc` below `arbitrage_max_soc`. (`arbitrage_keep_soc`, 10 points above the reserve, is not a setting.)

### Inverter control

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `window_switch_cost_p` | 5 p (0 to 100) | Plan | Cost per change of the timed windows (timed-window control) |
| `ram_switch_cost_p` | 0.5 p (0 to 100) | Plan | Cost per change with RAM control |
| `max_writes_per_day` | 150 (20 to 2000) | Control (timed) | Pause control when the day's own writes reach this |
| `ram_refresh_min` | 1 min (0.5 to 4) | Control (RAM) | Re-send a force command this often |
| `ram_max_power_w` | 5000 W (500 to 10000) | Plan, Live, Control | The most the inverter accepts; caps the plan's power rates and every command |
| `inverter_max_output_w` | 6000 W (1000 to 30000) | Control (RAM) | Total AC output, used to judge whether a discharge is being followed |
| `damp_restart_min` | 5 min (1 to 30) | Control (timed) | Restart hold-off length |
| `damp_burst_window_min` | 10 min (2 to 60) | Control (timed) | What counts as a burst |
| `damp_burst_settle_min` | 5 min (1 to 30) | Control (timed) | How long the plan must be steady after a burst |

### Cold battery

| Setting | Default (range) | Acts in | What it does |
|---|---|---|---|
| `cold_caution_temp_c` | 4 °C (-20 to 20) | Plan | Slow charging below this battery temperature (or the learned one) |
| `cold_charge_pct` | 50 % (10 to 100) | Plan, Control | ...to this share of the normal rate |
| `cold_release_c` | 3 °C (0 to 15) | Plan | ...until the battery is this much above the threshold |
| `battery_temp_lag_h` | 24 h (1 to 96) | Plan | How long the battery takes to follow the outside temperature (when the location is Custom) |

### Smart-charge requests (car slots)

| Setting | Default (range) | What it does |
|---|---|---|
| `smart_max_requests_per_day` | 6 (4 to 10) | The most ready-by changes a day |
| `smart_min_gap_min` | 20 min (10 to 120) | The shortest wait after any ready-by change |
| `smart_lookahead_h` | 3 h (1 to 8) | No request if a slot is planned within this long |
| `car_min_charge_min` | 2 min (0.5 to 10) | Shortest unbroken run of the charger saying "charging" for a smart slot to count as used (certainty, Health tab); with `learn_car_min` on it is moved towards what confirmed slots show. Not a request setting, but it sits in the car section of the config page |

These do not enter the plan; they decide whether to ask the supplier for slots the plan can then use. The extra back-off is
fixed: 30, 60, 120, then 240 minutes (page 8).

## 7.3 System settings

| Setting | Default | Acts in | What it does |
|---|---|---|---|
| `control_method` | `timed_windows` (this install: `ram_remote`) | Plan, Control | How decisions reach the inverter; RAM caps the powers and lowers the switch cost |
| `other_controller` | not chosen (derived) | Mode | Whether another battery controller is installed: `none` (guards not needed or checked), `predbat` (switched over with the optional handover package) or `other` (the guard entities are checked). Not chosen: derived from the mapped guards. See page 4, 4.1 |
| `overnight_window` | `learned` | Plan, Cost book | Where the overnight window comes from: `learned` from the rates, or `fixed` times (next section). See page 1, 1.10 |
| `house_load_includes_ev` | true | Readings, Plan, Live | The car is part of the house load, so the battery must not feed it and the profile subtracts it |
| `battery_location` | `garage` | Plan | How fast the battery follows the outside temperature |
| `publisher` | `auto` | Other | How entities are published |

## 7.4 Fixed values in "inputs" (not on the Safety list)

Roles that can carry a fixed `value` instead of an entity: `battery_capacity` (18 kWh), `battery_round_trip` (90.25%),
`battery_max_charge_power` and `battery_max_discharge_power` (4800 W each). Each can be replaced by what PowerEngine has
measured (`use_measured`, default on).

## 7.5 Constants in code that look like settings

Changing any of these needs a code change and a release.

| Constant | Value | Where | Meaning |
|---|---|---|---|
| Event value | £1.00/kWh | `Params.axle_value` | What the aggregator pays |
| `MIN_GAIN` | 0.5 p | `planner.py` | A rules-plan charge must save this much per kWh |
| `BAND` | 20% | `tariff.py` | "Cheap" is within the bottom fifth of the day's range |
| `CHEAP_BAND` | 15% | `tariff.py` | A half-hour within 15% of the day's lowest counts as cheap when finding the overnight window |
| `FULL_PENALTY` | £1/kWh | `optimiser.py` | Cost of ending the cheap window below target |
| `HIGH_DWELL`, `EARLY_BIAS`, `SELL_BIAS` | £0.0015, £0.0005, £0.0005 | `optimiser.py` | Tie-breakers |
| `MID_SLOT_STICK` | £0.15 | `optimiser.py` | Reluctance to change a running half-hour |
| `SOFT_BAND_MARGIN` | 5 points | `optimiser.py` | How far past the arbitrage band's edges a charge or sale may go, priced by the band penalty (0.9.102) |
| `DEFAULT_MIN_CHARGE_MIN` | 2 min | `slots.py` | The default of the `car_min_charge_min` setting (one figure for certainty and the Health tab; was two copies of 0.2 kWh until 0.9.104) |
| `LEARN_MIN_SLOTS`, `LEARN_WEIGHT`, `LEARN_SHARE` | 8, 20, 0.5 | `slots.py` | How the shortest real charge is learned: confirmed charges needed, how many move it half way, and its share of the 20th-percentile run |
| `LATCH_BAND_SOC` | 2 points | `decide.py`, `override.py` | Target-reached latch |
| `MIN_LEFT_MIN` | 5 min | `earlytarget.py` | Least time left for an early-target replan |
| `DATA_GAP_GRACE_S` | 180 s | `powerengine.py` | Missing-reading bridge |
| `REPLAN_SECONDS`, `CYCLE_SECONDS` | 300 s, 30 s | `powerengine.py` | Replan and decision cadence |
| `HALF_LIFE_DAYS` | 7 days | `forecast.py` | Load-profile recency weighting |
| `PRIOR`, `GROUP_WEIGHT` | 70% x 4 slots, 4 | `certainty.py` | Smart-slot certainty |
| `arbitrage_keep_soc` | 10 points | `Params` | Arbitrage must reach the refill with this above the reserve |
| `BATTERY_VOLTS` | 52 V | `powerengine.py` | Amps to watts for BMS limits |
| `STEP_DOWN_W` / `FLOOR` | 1000 W / 3000 W | `ramcontrol.py` | Command step-down after a miss |
| `FOLLOW_ALARM`, `FOLLOW_GRACE` | 3 min, 90 s | `ramcontrol.py` | Following check |
| `MAX_HOURS` | 12 | `override.py` | Longest override |
| `SETTLE`, `URGENT` | 10 min, 30 min | `schedule.py` | Timed-window settling |
