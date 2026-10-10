# PowerEngine v2: MVP design and simulator

10 Oct 2026 · Matthew

> **Status (10 Oct):** step 1 (archive) done. Step 2 (the runner, `tools/sim`) built: it drives the whole app, v2 only, not
> `EngineV2` alone, because v2's result depends on app state kept by `_cycle` and `_evaluate` (measured: the same day gave 5, 11 or
> 18 mode changes depending on which callbacks ran). A day takes about 2 minutes. See `tools/sim/README.md`. Step 3 first pass done: `engine-v2-ablation-1.md`. Steps 4 and 5 not started.

## Recommendation

Build the simulator first, then simplify the engine against it. The value model looks sound, and most of the churn comes from the layer that turns the model into live decisions. I'd strip that layer back to a direct read of the plan, but only after a simulator can show a before and after on stored days.

- **A simulator mostly exists already.** `tests/evening_world.py` runs the engine alone against a simulated battery: 9.5 simulated hours took 15 seconds in my sandbox. The nightly whole-app replay (`pe_core/compare`) takes about 3 minutes a day. What is missing is one fast runner that replays any stored day, swaps code or parameters from the command line, and prints a one-screen scoreboard. I estimate 2 to 4 days of work, mostly reuse.
- **The MVP engine keeps the value model and replaces the executor's patch layers.** `forecast.py`, `value.py`, `observe.py` and `learning.py` stay. The 50 engine settings become about a dozen visible ones, with the rest fixed constants the simulator can still sweep.
- **Archive the data.** Forecast snapshots are kept for only 14 days on the Home Assistant host. Every day not copied to the PC is a day the simulator can never replay. (Done on 10 Oct: see step 1.)

Nothing here changes the live engine until step 4 of the plan passes its gates.

## Why one fix breeds another

There are two decision-makers, and the patches live in the gap between them. This is my reading of the code and release notes, not something I have tested yet.

The value model (`value.solve`) plans 48 hours ahead and already knows about prices, uncertain smart slots and the cost of switching modes. But the live executor (`execute.py`) re-decides every 10 seconds by comparing live prices with one-step "lines" (`value.lines`), and then has to be made to agree with the plan. The machinery that does this:

- `_candidate` settles buy-versus-sell ties by asking which mode the plan has now.
- `_leg_going` keeps a running charge or sale going to the end of its plan step.
- `_with_the_programmes_choice` asks the plan again before a charge or sale may start.
- `_worth_the_change` prices a change a second time, on top of the plan's own switch cost.
- The price band, level band and minimum dwell time add hysteresis on top of that, and `reserve_latched` and the deadline logic add more.

The release notes show the pattern. 0.9.127: the plan did not follow a sale already running. 0.9.129: a change cost made going through Hold cheaper than turning round. 0.9.132 and 0.9.133: a smart-slot fix caused cycling, which the next release had to fix. Each is a mismatch between what the plan assumed and what the executor did, patched on one side.

Two more signs the model is carrying rules it should carry as maths. The DP already tracks the previous mode (`vk`) and charges a switch cost, so it is hysteretic by construction; the executor's extra bands probably duplicate that. And `prefer_self_use` in `rules.py` removes Hold outside cheap windows because, by its own help text, the plan otherwise finds idle Hold cheaper on sunny days. That is a gap in how the model values keeping energy, patched with a switch.

I would not delete any of these on this reading alone. The ablation in step 3 turns each one off and measures what it was worth.

## The MVP: one decision-maker

The principle: the plan is a policy, and the live engine looks it up. Nothing the live engine does should need a second opinion from a rule.

**Stays as it is**

- `forecast.py`, `value.py`, `observe.py` and `learning.py`: the segments, the 48-hour DP over three sun-and-house scenarios, the end-of-horizon value curve, late grid events, the filtered level, and the learned corrections.
- The hard limits in `rules.py`: grid event, free power, manual override, car charging, reserve, and the BMS, fuse and cold caps. These are safety and facts, not tuning.
- Control: RAM remote control, the write budget, read-back checks and the 5-minute failsafe behaviour.

**Changes**

1. **The executor becomes a policy reader.** Each tick, the mode is `value.choice_now(...)` clipped to the hard limits. `choice_now` already exists and is used today only to approve the start of a charge or sale; the MVP uses it for every choice.
2. **Anti-chatter comes from the plan.** The DP's single switch cost (`switch_cost_p`) is the only economic hysteresis. Execution keeps two protections that are about hardware and data, not money: a minimum time in a mode, and a re-plan when reality leaves the forecast (drift, or the level leaving its expected range).
3. **Plan and live read the same inputs.** Both use the same level and prices, so they cannot disagree about what a stored kWh is worth.
4. **Modes are unchanged.** Self-use, Hold, Charge and Export are the DP's choices; Event and Free stay forced. Whether each remaining rule earns its place is a question for the ablation, not an assumption.
5. **It sits behind a flag** (`executor: policy`) next to the current executor, so the simulator and preview can compare them on the same days before anything switches.

**The catch.** The DP works in 30-minute segments on a 0.1 kWh grid, while the live level is noisy and reads about a point lower while charging. A pure lookup may flicker near a boundary. The simulator's flip-flop count is the test. If it flickers, the first thing to put back is one level band, as a single parameter rather than a stack of rules.

## Parameters: 50 down to about a dozen

Engine v2 has 50 settings today (`engine_v2/settings.py`). I sorted them by what they are, not by how often they change. The visible set is what you would see on the config page; constants stay in code and the simulator can still override them for sweeps.

| Group | Settings | Count | Plan |
| --- | --- | --- | --- |
| Keep, visible | `arbitrage`, `events`, `free_power`, `event_plus_export`, `event_value_p`, `reserve_soc`, `charge_ceiling_soc`, `wear_house_p` and `wear_sale_p` (merge into one wear cost), `switch_cost_p`, `comfort_high_soc` (the one level preference), `late_events_per_week` (0 = off) | 12 | Visible. About 11 once wear is merged. |
| Fixed constants | `hard_floor_margin_pct`, `late_event_hours`, the six sun and house scenario weights, `learn_scenario_weights`, `scenario_half_life_days`, `scenario_prior_days`, `learn_solar_bias`, `learn_soc_offset`, `terminal_value`, `terminal_value_p`, and the responsiveness and model settings (`min_dwell_s`, `deadline_grace_min`, `debounce_s`, `car_start_debounce_s`, `car_stop_debounce_s`, `stale_after_s`, `soc_filter_gain`, `drift_kwh`, `band_exit_min`, `forecast_change_pct`, `revalue_coalesce_s`, `max_value_age_min`, `sample_s`, `level_step_kwh`, `max_segment_min`) | 30 | Move out of the UI into one constants file. Sweepable in the simulator. |
| Delete or merge, if the ablation agrees | `price_band_p`, `level_band_pct`, `comfort_low_soc`, `comfort_cost_p`, `top_up_cost_p`, `prefer_self_use`, `late_events` (the on/off, covered by the per-week figure), `preview_when_v1` (goes with engine v1) | 8 | Each is switched off in the simulator first. It goes only if cost and flip-flops do not move. |

House facts (capacity, efficiencies, charge and discharge limits, hard floor, fuse, export limit, car charger power) are not engine tuning and stay in the shared battery facts. The smart-slot certainty setting added in 0.9.133 sits outside this block and is not counted.

The four band settings (`comfort_low_soc`, `comfort_high_soc`, `comfort_cost_p`, `top_up_cost_p`) collapse to one visible number, the top of the cycling band, with the costs fixed. That is the "35% band" question in the next section.

## Level-dependent value, the 35% band and smart slots

**Level-dependent value is already the model.** `value.solve` produces `lam[k][i]`: the value in pence of one stored kWh at each time segment and battery level. `value.lines` turns that into a buy line (import price divided by charging efficiency) and a sell line (export price times discharge efficiency, minus wear). As the level falls, a stored kWh is worth more, and a planned discharge turns into a charge once the value crosses the buy line. So your idea does not need adding. The work is removing the rules around it.

**The top-35% default is a different thing, and testable.** It is a fixed arbitrage floor: cycle for profit only above roughly 65%. The model derives its own sell floor from the value curve, which is usually high before an evening peak anyway. From the code alone I cannot tell whether a flat band would beat the model's floor or just add a rule. It becomes the first experiment: arbitrage allowed only above X% for X from 50 to 80, against the unconstrained plan, on the same stored days. It is worth deciding up front whether the band is 65 to 100%, or something like 55 to 90%, since cycling at the very top costs wear and leaves no room for sun.

**Smart slot versus overnight is already a probability, not a rule.** Overnight half-hours count as certain. A smart slot outside the overnight window is priced as one expected price (a 60% chance of the slot price, otherwise the normal price), and a slot already running counts as certain (0.9.133). That asymmetry is real, because slots get cut short, and it lives in the price. The only place the two are treated as one thing is `_cheap()` in `rules.py`, which lets both keep Hold under `prefer_self_use`. So I would keep the probability, drop any behavioural difference, and check it by replaying the 9 Oct afternoon window that led to 0.9.132.

## The simulator: feasible, and mostly built

A dedicated simulator is feasible and worth doing. It runs entirely on your PC, with no Home Assistant and no cloud. Two of the pieces it needs already exist in the repo.

| Existing piece | What it does | Speed | What it lacks |
| --- | --- | --- | --- |
| `tests/evening_world.py` | Steps `EngineV2` directly against a simulated battery through one synthetic evening (6 Oct) | 15 s for 9.5 simulated hours in my sandbox, 8 re-plans | One hard-coded evening. No recorded days. |
| `pe_core/compare/` (`run.py`, `replay.py`, `harness.py`, `demo/world.py`) | Runs the whole app with a fake Home Assistant on one recorded day plus its forecast snapshot, for v1, v2, plain self-use and a perfect-foresight bound | About 3 min a day on your host (per the repo docs) | Heavy. Only v1 and v2. Settings come from config. Snapshots exist only from 6 Oct and expire after 14 days. |

`simulator.py` and `simjob.py` are a separate thing: they cost recorded days on other tariffs with v1's optimiser, and are not part of this.

**What the new runner adds** (working name `pe-sim`, in `tools/sim/` so it always tests the real `pe_core`):

- **Replay any stored day** with the engine alone at 10-second ticks. It reuses the demo world's battery physics and the nightly comparison's `Account` costing, so its numbers are comparable with the Costs page.
- **Swap code:** `--code <git ref or folder>` runs that checkout's `pe_core`. Two variants run side by side on identical days.
- **Change parameters:** `--set switch_cost_p=3` overrides any setting or constant, and `--sweep` runs a grid in parallel across your cores.
- **A one-screen scoreboard** per variant: cost per day against plain self-use and the perfect-foresight bound, end-of-day level value, mode changes, flip-flops, peak-rate grid charging, minimum level, commands per day, and calculation time. Detail goes to files.

**Data.** A stored day is its cost records, forecast snapshot and smart-slot history, kept in `~/pe-data` on your PC. A snapshot is 1 to 1.5 MB, so a year is roughly half a gigabyte. Cost records from 11 Sep are already in your pulled config. Forecast snapshots only began on 6 Oct, and the HA host keeps them for 14 days and engine history for 30, so an append-only archive (alongside `ha-sync.sh`) comes first. Older days from Home Assistant's statistics (12 months, hourly, no prices or forecasts) can't be replayed faithfully; they could be used with a perfect forecast plus noise, at lower fidelity.

**Token cost.** Running a program does not use tokens by itself. What costs tokens is me writing code and reading output. Local runs help because you can start a long sweep yourself and I only read the finished table. The runner prints one small table by design. Runs on your PC should also be faster than on the HA mini PC, though I have not measured your machine.

**What it cannot tell you.** It ranks variants well and prices them poorly: the battery model is a model, and the nightly calibration against your meter shows the gap. Two to four weeks of autumn days is a small sample, so a sweep can fit the weather. I would hold some days out and prefer settings that are good everywhere over ones that are best on average.

## Gates, and a permanent test for every past fix

The fix-on-fix pattern happens because each fix is checked against its own incident only. The remedy is to keep every incident as a permanent scenario and check every change against all of them plus all stored days.

| Incident | Fix | What the scenario checks |
| --- | --- | --- |
| 6 Oct evening: smart slot 19:12 to 04:00, grid event 19:30 | `tests/evening_world.py` (exists) | Few mode changes, no reversals, no flip-flops |
| 7 Oct: look-ahead ended 30 min into the 28.84p peak | 0.9.119 | No sale down to the floor at the end of the plan |
| 8 Oct: look-ahead ended inside the cheap window | 0.9.122 | No heavy discharge at the end of the plan |
| 9 Oct: afternoon smart-slot window cut short (89% down to 31%) | 0.9.132, 0.9.133 | No deep sale on an uncertain slot, and no cycling on a long window |
| 29 Sep: forced charge bought at 30.28p when sun fell short | `_solar_only_charges` | No peak-rate grid charging the plan did not count (the fix is in v1's planner; whether v2 needs its own check is unverified) |
| 27 to 28 Sep: recorded night | `tests/test_replay.py` (exists) | Whole-app behaviour unchanged for a refactor |

Cost records for 11 Sep to 6 Oct are already on your PC, but forecast snapshots only began on 6 Oct (0.9.110). Older incident days therefore have the real sun and prices but not the forecasts as they were, so they replay at lower fidelity. The first snapshots start expiring around 20 Oct unless archived.

**Proposed gates for any engine change** (my suggestions; you set the thresholds):

1. Total cost over all stored days, adjusted for end-of-day level, is no worse than the current engine by more than 1%, and no single day is worse by more than a figure you choose.
2. Mode changes and flip-flops per day are no higher.
3. No grid charge at the peak rate that the plan did not count.
4. The level never goes below the reserve except in a grid event.
5. `tests/test_replay.py` passes unchanged for a refactor, and is re-recorded with an explained diff for an intended behaviour change, as the repo rules already require.

**Validity check for the simulator itself.** Before it is trusted, its result for a day should agree with the nightly whole-app replay for the same day. I suggest within 5% on at least five days.

## Plan, risks and decisions for you

About a week to ten days of working sessions in total, by my estimate, assuming heavy reuse of the existing replay code. Steps 1 to 3 are read-only: the live engine does not change until step 4 passes its gates.

1. **Archive (half a day). DONE 10 Oct.** An append-only copy, after each `ha-sync.sh pull`, of cost records, snapshots, slot history and engine history into `~/pe-data`. `pe-archive.sh` copies and never deletes. Gate: yesterday's day is on your PC.
2. **Fast runner (2 to 3 days).** `pe-sim` over stored days, with `--set`, `--code` and the scoreboard. Gate: agrees with the nightly whole-app replay on at least five days.
3. **Incident pack and ablation (1 to 2 days).** Turn off each executor layer and each of the 8 delete candidates one at a time, and tabulate what each was worth. Gate: a ranked list of what earns its place. This is the evidence for "bare minimum", instead of a guess.
4. **MVP executor behind a flag (2 to 3 days).** Policy lookup plus the constants file, compared with the current executor in the simulator. Gate: the five gates above.
5. **Preview, then release.** Run the MVP in preview alongside the live engine, then release through the normal beta route, with your approval in GitHub.

**Risks**

- The simulator ranks well but prices poorly, so absolute pounds still come from your meter.
- Few days in one season invites tuning to the weather. Hold days out, and keep new days flowing into the archive as winter arrives.
- A pure policy lookup may flicker near level boundaries. One level band is the planned fallback.
- Some stored days may already be gone, which limits the incident pack.

**Decisions for you**

1. Is the cycling band 65 to 100%, or something like 55 to 90%?
2. Keep Hold among the DP's choices? My view is yes, since a full battery in a cheap window is better held than drained to run the house, and the ablation will confirm.
3. Put the simulator in the controller repo under `tools/sim/`, so it always tests the real code? My recommendation is yes.

## Brief for step 2: the fast runner

This section is the handoff to Claude Code. Build `tools/sim/` in the controller repo: a runner that steps `EngineV2` alone over stored days and prints a scoreboard. Do not change engine behaviour in this step.

**Data (on the owner's PC, outside the repo, never committed).** `~/pe-data/` holds an append-only archive made by `pe-archive.sh`:

- `costs/YYYY-MM-DD.json`: 48 half-hour cost records per day (house, car, solar, grid, import and export rate, battery level, grid events). 11 Sep to 10 Oct; 10 Oct is partial.
- `costs/snapshots/`: the forecasts as they were, read with `pe_core/compare/snapfeed.py`. 6 to 10 Oct only. Days before 6 Oct have no snapshot, so they replay at lower fidelity: decide whether to exclude them from the gates or run them with a perfect forecast plus noise, and say which.
- `costs/v2history/` and `costs/compare/`: engine history, and the nightly whole-app replay results (6 to 9 Oct). The compare results are the validity check.
- `versions/engine_v2_state/`, `versions/slots/`, `versions/smart_requests/`: learned state and smart-slot history, one copy per distinct content, to seed a replay.
- `versions/config/` and `versions/config-bak/`: the settings that were running. `diagnostics/`: the owner's exports.

**Reuse, do not rewrite.** `tests/evening_world.py` (the engine-only loop and battery physics), `pe_core/compare/` (`day.py` loads a day, `snapfeed.py` feeds the snapshot, `replay.py` has the `Account` costing, flip-flop counting and the perfect-foresight bound), and `pe_core/demo/world.py` (battery model). Costs must be comparable with the Costs page.

**Interface**

```
tools/sim/pe_sim.py run --data ~/pe-data --days 2026-10-06..2026-10-09
    [--code PATH_OR_GITREF]     # run that checkout's pe_core, in its own subprocess
    [--set key=value ...]       # override any engine_v2 setting or constant
    [--sweep key=a,b,c]         # grid, in parallel (--jobs N)
    [--out DIR]                 # detail files go here, not to the screen
```

Two variants cannot share a process (both import `pe_core`), so each variant runs in a subprocess with its own `PYTHONPATH`.

**Output.** One small table per variant, per day and in total: cost against plain self-use and against the perfect-foresight bound, end-level-adjusted cost, mode changes, flip-flops, peak-rate grid charging (kWh), lowest level, commands per day, calculation time. Keep screen output short; it is read by a person and by Claude.

**Acceptance**

1. Runs the days that have snapshots end to end, and reports its wall time.
2. For each day in `costs/compare/`, the runner's engine v2 cost agrees with the nightly replay's within 5%. Report the gaps honestly; do not tune the runner to hide them.
3. `--set` and `--code` are shown working on two variants over the same days, and a one-parameter `--sweep`.
4. Tests for the runner; `tools/check.sh` passes; the existing replay test is unchanged.

**Rules from the repo's `CLAUDE.md` that apply.** This is a live house: no network, no Home Assistant access, nothing written outside the repo and `--out`. Work on a branch and open a PR; this is tooling, so no release unless the owner asks. Scratch files go in `../_to_delete`. Add the design to the index in `docs/plans/README.md`. Never commit anything from `~/pe-data` (it contains settings with account details).
