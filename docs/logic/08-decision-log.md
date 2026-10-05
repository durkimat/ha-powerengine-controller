# 8. Decision log: why each rule is there

Each row is a rule that exists because something went wrong on the house, or because the owner decided it. Dates are from
the code comments, `CLAUDE.md` and the changelog; a row with no date is a design principle rather than an incident. If you
want to change or remove a rule, read its row first: the incident is what the rule is protecting against.

**Adding to this page:** a PR that adds a rule adds a row (the rule, the incident or decision, where it lives, the version).

## 8.1 Money rules (never lose money by surprise)

| Rule | Why | Where | Version |
|---|---|---|---|
| **A forced charge must never buy grid energy at a dear rate the plan did not count.** The plan drops a grid-charge that needs no grid energy at a non-cheap price (charges become Self-use, or Hold if nothing to charge) | 29 Sep 2026: less sun than forecast, so the forced charge bought the shortfall at 30.28p, to sell at 15p | `planner._solar_only_charges` | 0.9.64 |
| **A Hold at a dear rate that imports nothing becomes Self-use** | 4 Oct 2026: forecast 2 kW, actual 0.4 kW, battery at 84% held while the house bought at 28.84p. Hold forbids the battery to cover the house | `planner._solar_only_holds` | 0.9.96 |
| **Sentences name the price the decision used** ("tariff still 30.28p" for a planned slot not yet priced) | 29 Sep 2026, 21:17: a car slot showed the ordinary 30.28p for 30 s before it showed 6.99p, and the sentence named a peak price as the slot price | `planner._car_slot_p` | 0.9.64 |
| **A Hold does not store surplus solar** in the plan; it exports it | 4 Oct 2026: a Hold with about 1.9 kW spare sun averaged +0.19 kW battery, Self-use -1.9 kW. On the inverter Hold is a force charge at 0 W | `planner.step` (#175) | 0.9.98 |
| **Event power is the battery's full discharge power** | The aggregator pays per kWh; before 0.9.55 every event was capped at 4 kW, which left about 1 kWh an hour unsold on 28 Sep 2026 | `decide.axle_power_w`, `Params.axle_kw` | 0.9.55 |
| **Event value counts the export rate too** (EDF: £1 + 15p) | Owner's tariff; switch `axle_plus_export` | `planner.axle_rate` | |
| **The cheap threshold is worked out from the day's prices** and "storing must pay after losses and wear" | A flat tariff has nothing cheap; buying is never "cheap" if storing it cannot pay | `tariff.cheap_threshold` | |
| **A charge in the rules plan must beat the price it avoids by 0.5p/kWh after round-trip losses** | Avoid pointless cycles for tiny gains | `planner.MIN_GAIN` | |
| **Arbitrage must clear a margin after losses and wear, and reach the refill with the reserve + 10 points** | A small forecast error must not push the house onto the peak rate | `planner._add_arbitrage` | |
| **Smart slots are priced at their expected price**, `certainty x slot + (1 - certainty) x standard`, not the slot price | The supplier withdraws slots and cuts them short; the plan should not rely on a price that may vanish | `forecast.build_slots`, `certainty.py` | |
| **Beyond published prices, a smart slot's cheap price is not carried forward** | Smart slots move from day to day; "same time yesterday" would plan a cheap price that will not exist | `forecast.price_at` | |

## 8.2 Behaviour rules (stop the inverter being pushed around)

| Rule | Why | Where | Version |
|---|---|---|---|
| **A charge target reached in a half-hour holds for the rest of it** | 28 Sep 2026: a force charge has no target of its own: RAM control went 63% to 82% against a 76% target | `decide._with_plan` | |
| **That hold latches until the charge is 2 points below the target** | 29 Sep 2026, 22:38 to 22:44 and twice more: the inverter's SoC reads a point lower while charging, so Force charge and Hold alternated every 30 s | `decide._held_at_target` | 0.9.64 |
| **A target reached with 5+ minutes left replans at once** | The rest of the half-hour was wasted on a hold when charging more, or starting the next sale early, was available. One look per half-hour; every look recorded. Review due about 11 Oct 2026 | `powerengine._early_target`, `earlytarget.py` (#175) | 0.9.99 |
| **A replan part-way through a half-hour keeps the running action unless changing it saves £0.15** | Near-ties flip-flopped the inverter, and each change costs a write | `optimiser.MID_SLOT_STICK`, `powerengine._mid_slot_stick` | |
| **No such stickiness for the first plans after a start** | The first plans use a default load profile and no weather; their choice is not worth locking in | `STICK_WARMUP` | |
| **A reading missing for up to 3 minutes keeps the last decision** | 4 Oct 2026, 08:21: the import rate went unavailable for one cycle, handing the inverter to Self-use and straight back (two RAM writes) | `powerengine._bridge_data_gap` | 0.9.97 |
| **Switch cost: a full switch costs the setting; Hold to charge costs a fifth** | Each switch is a write; Hold and charge only change the current | `optimiser.switch_cost` | |
| **Inside the overnight window a full switch costs at least 3p (timed windows, with deep overnight and arbitrage; not RAM control)** | 28 Sep 2026: two cycles overnight where one would do. The refill is guaranteed there, so one deep sale and one refill beat several shallow cycles | `optimiser.switch_cost` | |
| **Charge early inside the overnight window; sell early when it ties** | Same price all night; a replan can still use the spare half-hours later. A sale banked sooner is surer. The delay counts from the window's start (from now outside one), capped at 12 half-hours (`BIAS_CAP`), so they stay tie-breakers (5 Oct 2026: counted from now they cancelled a night's sale and refill) | `optimiser.EARLY_BIAS`, `SELL_BIAS`, `bias_delay` | |
| **The battery should be full when the cheap window closes** (top-up when cheap on) | £1/kWh cost of ending the window short of the target | `optimiser.FULL_PENALTY` | |
| **The arbitrage band is a guide with 5 points of give.** A charge may run 5 points past the top; a sale may end 5 under the bottom outside the overnight window (70% by default), which is the hard floor. The band penalty prices the give | 4 Oct 2026: a full-power sale takes about 15 points, a charge restores about 13, so every cycle was a charge slot and a bit, then an idle Hold. Measured on a synthetic night: 9 sales and two idle half-hours became 13 sales and none | `optimiser.slot_target`, `sell_floor`, `SOFT_BAND_MARGIN` (`docs/plans/soft-band.md`) | 0.9.102 |
| **Inside the overnight window a sale may go lower (reserve + 10), because the refill there is guaranteed** | Elsewhere the refill may depend on optional slots the supplier can withdraw | `optimiser.sell_floor` | |
| **Leaving the plan's Self-use at the reserve holds** | Do not ask the inverter to discharge below the floor | `decide._with_plan` | |

## 8.3 The car

| Rule | Why | Where | Version |
|---|---|---|---|
| **The battery never charges the car.** In a car smart slot the battery may only Hold or Grid-charge; with the car charging it holds | A 7.4 kW car charge dwarfs the house, so covering the house but not the car "isn't worth the extra control" (tried and dropped) | `planner.car_slot`, `optimiser._untiered_actions`, `decide._car_at_peak` | 0.5.2 |
| **The plan believes only the charger: a smart slot is a car slot only for the running half-hour while the car is charging, with its live power. Every other smart slot is cheap time with no car** | 4 Oct 2026: a one-minute blip of car draw kept the plan flat at 90% from 19:00 to 04:00, because any smart slot with the car plugged in was planned as car charging. The earlier guesses (unplugged, "complete", last slot drew nothing, running dispatch with no charging) are gone | `forecast._car_expected`, `powerengine._plan_signature` | 0.9.103 |
| **The plan is remade when the car starts or stops, or is still charging in the next half-hour** | The plan assumes the charge ends by the end of the running half-hour; if the car disagrees, the plan is wrong. Without this the guessing above was the only protection | `powerengine._plan_signature` | 0.9.103 |
| **`car_idle` (the last long smart slot charged for under a minute) no longer shapes the plan**; it only gates smart-charge requests when `smart_skip_full_car` is on | History: 28 Sep 2026 (09:00 to 15:30 slot after two with no draw) added it to the plan; 4 Oct 2026 (0.9.101) added a 0.2 kWh test to it after a 0.01 kWh blip; 0.9.103 took that test out again and removed `car_idle` from the plan | `slots.SlotTracker.car_idle`, `powerengine._smart_step` | 0.9.103 |
| **If the car does start, the car rule takes over at once** | The plan's assumption can be wrong | `decide._with_plan` | |
| **A cheap smart slot with the car charging also charges the battery** (top-up on) | Cheap whole-house power while the car is drawing it | `decide._with_plan`, `optimiser.car_cheap_charge` | |
| **Slot energy is scaled by how much of the listing the slot covers** | 29 Sep 2026: a 30-minute slot read 38.5 kWh because the supplier lists one energy for a whole dispatch | `slots.planned_kwh` | |
| **Ask the supplier for extra slots only when worth it, with back-off 30, 60, 120, 240 min, max 6 a day, never within 20 minutes** | Decided with the owner, 26 Sep 2026; EDF's API must not be hammered | `smartcharge.py` | |

## 8.4 Inputs

| Rule | Why | Where |
|---|---|---|
| **The check meter is trusted over the inverter's grid meter; house load is corrected by the difference** | 27 to 28 Sep 2026: the Solis meter reads about 16% high both ways (about 2 kW more import while charging) | `readings.read`, `forecast.meter_corrected`, `gridcheck.py` |
| **The overnight window is the intersection of the cheap half-hours over the days seen; a half-hour within 15% of the day's lowest counts as cheap** | 30 Sep 2026: smart slots were priced at the new 6.66p while that night still ran at the old 6.99p, so the night dropped out, the intersection emptied, and deep overnight selling stopped | `tariff.CHEAP_BAND`, `cheap_tods` (0.9.67) |
| **The overnight window can be fixed by the owner instead of learned** (default learned) | A fresh install has no window until it has seen two full days, a smart slot at the same time every day is learned as part of it, and a tariff with no integration has no rates to learn from. Owner's decision, 5 Oct 2026; the manual-rates provider is a separate backlog item | `PowerEngine._fixed_window`, `CostBook.window`, `tariff.fixed_window` | 0.9.105 |
| **The load profile is house-only and recency-weighted (7-day half-life)** | The car is planned separately; habits drift | `forecast.profile_from_means` |
| **Learned limits only replace configured ones when plausible** (50% to 120% of rate; efficiency 0.7 to 1.0; reserve only ever raised; export only lowered) | A bad measurement must not wreck the plan | `powerengine._learned_overrides` |
| **The controller asks for the configured rate; only the plan uses the learned one** | Learning needs to see whether the full rate is reached | `_control_params` |
| **Cold battery: charge slower below a threshold, release only above threshold + margin** | The battery's own protection reduces charge when cold; the plan should not assume the full rate. Hysteresis stops flicker | `learn.caution_by_hour`, `_apply_cold` |
| **Plan windows are limited to 6000 bytes of the plan sensor's attributes** | 28 Sep 2026: 40+ windows took `sensor.pe_plan` over HA's 16 KB limit, so history was dropped | `planner.windows_within` |
| **Attributes of published sensors stay under 16 KB** | HA's recorder skips larger ones | `_publish_state` |

## 8.5 Control and safety

| Rule | Why | Where | Version |
|---|---|---|---|
| **Never write Backup or Off-Grid modes, or any entity with "bump" or "boost" in its name** | Hardware safety; the app and the definition both enforce it | `_write`, `is_forbidden_control` | |
| **Leave Active, Passive and Pause to the owner** | Owner's control; PowerEngine never changes them (except Pause at the daily write limit) | `modes.py` | |
| **RAM remote control re-sends every minute; the inverter drops it after about 5** | 27 Sep 2026 test (S5-EH1P6K-L, firmware 420044): if anything stops, the inverter returns to Self-use by itself | `ramcontrol.py` | |
| **A command that does not take steps down 1000 W at a time to 3000 W, for up to an hour** | A refused write leaves the previous power | `ramcontrol.RamController.step_down` | |
| **Command capped by the BMS limits; a believed limit of 0 gives Hold (charge) or Off (discharge); a 0 contradicted by 300 W of movement is ignored** | Cold weather and the battery's own limits; do not call a BMS-limited charge "not following" | `bms.py` (#121) | 0.9.73 |
| **Fuse limit: house + car + battery under 90% of the main fuse, battery reduced first** | Do not trip the supply | `planner.grid_charge_kw`, `decide.fuse_limited` | |
| **Inverter writes are precious: write budget, dampening, read-back** | EEPROM wear on timed windows | page 6 | |
| **Active is refused for an unverified definition or firmware** | Do not drive hardware nobody has proven | `verification.active_refusal` | |
| **The update button is pressed only after staged values read back** | Otherwise the inverter runs one change behind | `_press_buttons` | |
| **No admin token; Home Assistant calls only through the gate in demo mode** | Least privilege | CLAUDE.md | |

## 8.6 Owner decisions on the override (4 Oct 2026)

| Decision | Where |
|---|---|
| Choices: Self-use, Hold ("home": the grid runs the house), Charge, Export | `override.py` |
| Periods end on a half-hour boundary; longest 12 hours; or permanent | `override.parse` |
| **A grid event in progress always wins over an override** | `decide._decide` rung 1 |
| Override works only in Active; Pause, the fuse limit, BMS limits and the write budget still apply | `_active_override` |
| The planner treats overridden half-hours as fixed and plans the rest from what the battery will hold; set, cancel and expiry replan at once | `_mark_manual` (O3) |
| A Charge override holds at the target and resumes 2 points lower | `override.decide` |

## 8.7 Principles from `CLAUDE.md`

* **Never hard-code a supplier or device name in user text**: use the names map (`pe_core/names.py`).
* **Display wording vs mode keys**: `ModeDecision.label` is for logs; `effective == "unconfigured"` stays the key.
* **Refactoring must pass the replay unchanged**; an intended behaviour change re-records it and the PR explains the diff.
* **Do not hammer EDF's API**, or any external API.
* **Deleting files only when asked**.
