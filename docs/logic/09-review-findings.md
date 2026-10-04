# 9. Review findings: where the code, its comments and its settings disagree

Found while writing pages 0 to 8, on 5 Oct 2026, against 0.9.100. **Re-checked against 0.9.102:** the three confirmed-by-running
findings (F2, F3, F5) still reproduce, and the stale docstring (F1) is still there. The soft arbitrage band and the car-blip
fix (0.9.101 and 0.9.102) do not touch any finding. **Nothing here has been fixed.** Each item says how
sure the finding is:

* **Confirmed**: I ran the code (a small script, not committed) and saw it.
* **Read**: clear from reading the code; not run.
* **Question**: it may be deliberate; the owner decides.

"What I would do" is a suggestion, not a plan. Any change to behaviour re-records the replay
(`PE_REPLAY_UPDATE=1`) and goes through the release routine.

| ID | Finding | Sure? | Weight |
|---|---|---|---|
| [F1](#f1) | Stale comments say the optimiser is "for comparison only" and the planner is the heuristic | Confirmed (CHANGELOG 0.7.0) | Low risk, high confusion |
| [F1b](#f1b) | Three settings on the config page do nothing while a plan exists | Read | Medium: the owner can tune them to no effect |
| [F2](#f2) | "Grid-charge target" is not a ceiling for the optimiser's charges | Confirmed | Medium |
| [F3](#f3) | While the car charges, a planned grid-charge ignores its target | Confirmed | Low to medium |
| [F4](#f4) | "Cheap" is worked out three different ways | Read | Low to medium |
| [F5](#f5) | The reserve is checked live only for a planned Self-use | Confirmed | Low |
| [F6](#f6) | The solar forecast's low and high bands are read and never used | Read | Question |
| [F7](#f7) | The event value (£1/kWh) is a constant, not a setting | Read | Question |
| [F8](#f8) | An owner override can feed the car, and ignores free power | Read | Question (probably intended) |
| [F9](#f9) | The clean-up passes edit the plan without re-optimising what follows | Read | Low |
| [F10](#f10) | Wear is charged on every discharge, including Self-use covering the house | Read | Question |
| [F11](#f11) | Leftover energy is valued at the cheapest price, which can be zero or negative | Read | Low |
| [F12](#f12) | The learned reserve reaches the plan but not the live decision | Read | Low |
| [F13](#f13) | The plan is not re-optimised when learned parameters change | Read | Low |

---

<a id="f1"></a>
## F1. Stale comments: the optimiser *does* drive decisions

**Where:** `pe_core/optimiser.py` module docstring ("for comparison only (never used for decisions)"; "the gap to the
heuristic shows how much a smarter planner could be worth"); `pe_core/planner.py` module docstring (describes only the three
rule steps); `pe_core/decide.py` module docstring ("0.2 is reactive (no forward plan yet; that arrives in 0.3)");
`CHANGELOG` 0.6.x entries say the same, correctly for their time.

**What the code does:** `features.optimised_plan` defaults to **on** (since 0.7.0, #67). `make_plan(strategy="optimiser")`
takes the optimiser's actions; the rules plan supplies the baseline, the cheap threshold and the reasons. The comparison
function `compare()` and the "optimiser" figure on the Plan tab are used only when the feature is **off**.

**Why it matters:** someone reading the code to understand the plan will look in the wrong place. The rule list in
`planner.py` (default action, avoidable problem, cheapest earlier slot) is no longer what makes the plan.

**What I would do:** rewrite the three docstrings to match pages 2 and 3 of this documentation. No behaviour change.

<a id="f1b"></a>
## F1b. Settings that act nowhere while a plan exists

**Where:** `pre_axle_lookahead_h` ("Grid-event look-ahead"), `axle_margin_soc` ("Grid-event safety margin") and
`charge_hysteresis_soc` ("Charge restart margin"). Searching the code for each key finds them only in `decide.py`, inside
the reactive rule stack that runs when **there is no plan**.

**What the code does:** with a plan, preparing for a grid event is the planner's job (charge in the cheapest earlier slot
that pays against the event rate; the optimiser finds it as part of its search). The "Grid events" section of the config
page lists two settings that sound like the way to make PowerEngine more cautious before an event, but changing them
does nothing in normal running. The charge target is also reached by the optimiser, not by hysteresis.

**Why it matters:** the owner is offered a lever that is not connected. If an event ever leaves the battery short, tuning
`axle_margin_soc` will not help.

**What I would do:** either (a) remove them from the config page and keep them only for the fallback, or (b) connect them:
for example, have the planner require the event's reserve plus `axle_margin_soc`. (b) is a behaviour change.

<a id="f2"></a>
## F2. "Grid-charge target" is not a ceiling for the optimiser

**Setting text:** "How full to charge from the grid when import is cheap."

**What the code does:** `optimiser.grid_target()` returns **100%** always, and `slot_target` returns it unless arbitrage is on
or it is a cheap car slot. The setting enters the optimiser only as (a) the level the battery should reach by the end of
each overnight window (the £1/kWh "not full" cost), (b) the buffer level for cheap top-ups (`buffer_target`), and (c) the
Charge override's target.

**Confirmed by running:** a 48-slot plan (6.99p from 23:00 to 05:00, 30p otherwise, start 30%, house 0.8 kW, no solar). With
the target at 100% the plan charged to 100%. With the target at 80% it still charged up to **88%**, every slot reasoned
"charge at 6.99p to use instead of buying at 30p from 05:00 tomorrow". That is economically sensible, but it is above the
target the owner set. The rules plan (strategy "rules") respects the setting.

**Why it matters:** an owner who sets the target to 80% to protect the battery (or to leave room for solar) will still see
charging past it. The live "reached the N% target" hold uses the plan's own end-of-slot SoC, not the setting.

**What I would do:** decide what the setting means. If it is a ceiling, `slot_target` should return `min(100, target_soc)`
(and the final top-up and `FULL_PENALTY` should use it consistently); if it is a goal, rename it ("Fill target") and say so
in the help text.

<a id="f3"></a>
## F3. With the car charging, a planned grid-charge ignores its target

**Where:** `decide._with_plan`. The "plan says grid-charge and SoC is at or above the target: hold" check (rung 8) comes
*after* the car-charging branch (rung 4). In the car branch, `if ps.action == GRID_CHARGE:` returns a GRID-CHARGE decision
immediately.

**Confirmed by running:** plan grid-charge with target 94%, SoC 96%, no car: Hold ("reached the 94% target"). Same, car
charging: **Grid-charge** ("car is charging; charge at 6.99p ...").

**Why it matters:** this is the 28 Sep 2026 failure (63% to 82% against a 76% target) returning for the car case: a
force charge has no target of its own, so the inverter carries on until the plan's slot or the inverter's own full
detection stops it. It is mostly cheap energy (a smart slot), so the cost is small, but it overshoots the plan.

**What I would do:** apply the same reached check in the car branch before returning the planned charge. Add a replay or
unit case for it.

<a id="f4"></a>
## F4. "Cheap" has three definitions

| Used by | Prices considered | Efficiency | What the price is |
|---|---|---|---|
| Plan (`make_plan`, auto threshold) | Every slot's price in the horizon (36 to 48 h), including estimated prices and smart slots **weighted by certainty** | `efficiency squared` (the learned one) | The slot's expected price |
| Live (`decide.cheap_limit`) | Only the **published** rates in the next 24 h | The default (0.9) | The live import rate now |
| Car-slot charging (`optimiser.car_cheap_charge`) | n/a: compares the slot's **own** price (before certainty weighting) to the plan's threshold | n/a | Slot price |

**Why it matters:** the plan and the live decision can disagree about whether a half-hour is cheap. The live threshold is
used for the car-charging branch (page 4, 4.3) and the smart-charge request test. An uncertain smart slot is "expensive"
to the rules planner (expected price) but "cheap" to the car-slot rule (slot price). Each choice is defensible; together
they make the behaviour hard to predict.

**What I would do:** compute the threshold once per replan and store it on the plan (`plan.cheap_p` already carries it), then
have `decide` use `plan.cheap_p` when a plan exists. Behaviour change; small.

<a id="f5"></a>
## F5. The reserve is checked live only for a planned Self-use

**Where:** `decide._with_plan`, rung 6: "plan says Self-use and SoC is at or below the reserve: hold". Nothing similar
for a planned **Export** or **Force-discharge**, and rule 1 (a grid event in progress) has no reserve check at all.

**Confirmed by running:** plan Export at 10% SoC (reserve 12%): decision is Export. Plan Self-use at 10%: Hold ("reserve").

**Context:** the *plan* never plans below the reserve (`step` floors every discharge) and the optimiser excludes sales below
its floor. The live gap appears only when the real SoC has drifted below the plan's. The owner's override does check the
reserve. The inverter has its own cut-off, which I have not looked at.

**Question for the owner:** is it intended that a grid event may discharge below `min_reserve_soc`? (It earns £1/kWh.) If
so, a note in the settings text would help; if not, rung 1 needs the check.

<a id="f6"></a>
## F6. Forecast low and high bands are read and not used

`ForecastPoint` carries `low_kwh` and `high_kwh` (Solcast's 10th and 90th percentile). The Solcast adapter fills them. No
code outside the adapters reads them. The plan uses the central estimate only.

**Question:** do you want a cautious mode that plans on the low band for charge decisions (for example "charge for the
house if the P10 forecast would leave a shortfall")? The two clean-up passes (3.7) are the current answer to "what if the
sun is less than forecast"; a band would be a more general one.

<a id="f7"></a>
## F7. The event value is a constant

`Params.axle_value = 1.00` (£/kWh). `params_from` does not set it, and `costs.py` has the same constant. The text "paid
£1 + 15p per kWh exported" is built from it. If the aggregator's rate changes, the plan and the cost accounting both need a
code change.

**Question:** make it a setting (Grid events section)?

<a id="f8"></a>
## F8. An override can feed the car, and ignores free power

By design, an override sits above everything except a grid event in progress. A **Self-use** or **Export** override while
the car charges lets the battery feed the car or the grid at the car's expense, and a **Hold** override during a
free-electricity session stops the free charge. The card shows the price for the period before confirming.

**Probably intended** (the owner chose the order on 4 Oct 2026), but worth knowing: the "battery must never charge the car"
rule is not enforced under an override.

<a id="f9"></a>
## F9. The clean-up passes do not re-optimise what follows

`_solar_only_charges` and `_solar_only_holds` change an action after the optimiser has finished, then re-simulate. The
half-hours after it were chosen assuming the original action (for example, assuming the battery was charged by that slot).
The plan's reported cost and SoC are recomputed correctly; the later actions may not be the best given the change.

**Context:** the passes act on non-cheap half-hours where the model said the grid supplied nothing, so the effect on later
actions should be small. The `alternative` comparison ("the rules plan would cost £x more") is computed against the
modified plan.

**What I would do:** nothing now; if the plan's choices around a dropped charge ever look odd, re-run the optimiser with
those slots' menus restricted instead of editing after.

<a id="f10"></a>
## F10. Wear is charged on every discharge

`optimise`: `if wear and end < lv: total += (lv - end) ... * wear` for every action, including **Self-use** covering the
house. With `battery_wear_p` at 2p/kWh, the optimiser prefers Hold (import at the current price) over Self-use whenever the
import price is within about 2p of the value of the stored energy.

This is consistent ("a kWh cycled costs 2p") and probably the right bias, but it is not stated anywhere in the settings
text, which only says "wear cost per kWh cycled". The same wear figure is also inside the cheap threshold.

**Question:** is 2p right as the cost of a self-use kWh? It biases the plan towards Hold in near-ties, and Hold costs the
solar surplus (the Hold is exported, not stored).

<a id="f11"></a>
## F11. Leftover energy is valued at the cheapest price in the horizon

`end_value = min(prices)`. If any slot in the 36 to 48 hours is free or negative (a free-power session), leftover energy
is valued at zero or less. Near the end of the horizon the plan then treats the battery's contents as worthless and may
sell or use it freely. The 5-minute replan fixes this as the horizon moves on, so it is unlikely to cost money, but the end
of the horizon is not a safe place to read the plan.

**What I would do:** value leftover energy at the cheapest *non-free* price, or at the overnight rate.

<a id="f12"></a>
## F12. The learned reserve reaches the plan but not the live decision

`_learned_overrides` raises `min_reserve_soc` (up to 15 points) in the planner parameters. `decide` and `override.decide`
read the *configured* `min_reserve_soc`. If the learned reserve is 15% and the setting 12%, the plan keeps 15%, while the live
"Self-use at the reserve: hold" rung (F5) waits until 12%.

**What I would do:** pass the plan's reserve to `decide`.

<a id="f13"></a>
## F13. A change to learned parameters waits up to 5 minutes

The replan signature (page 1, 1.9) contains the settings and features but not the learned parameters or the measured
capacity. They reach the plan at the next 5-minute refresh. Not a problem in practice.

---

## Not findings, but worth having in mind

* **The plan is only as good as its load profile.** The profile is a weekday/weekend mean for each half-hour; it does not
  know a holiday, guests, or the heat pump starting (the heat-pump module is separate from the plan).
* **Hold has two meanings in two places.** In the plan, Hold = grid runs the house, surplus is exported. On the inverter,
  Hold = force charge at 0 W. That is why the Hold fix in 0.9.98 was needed.
* **RAM control and timed windows differ in more than the write budget**: the plan's switch cost (0.5p vs 5p), which makes
  the plan choppier under RAM; and damping and settling (timed only).
* **The replay is the safety net, not a specification.** `tests/test_replay.py` pins one recorded night on two control
  methods. It catches a refactor that changes behaviour; it does not catch a behaviour that was already wrong.

## If you want to act on these

A suggested order, by value for effort:

1. **F1** (docstrings): no risk, makes the code match this documentation.
2. **F1b** and **F2** (settings that mislead): decide what each setting should mean. These are the most visible to you.
3. **F3** (car and target): small fix with a test.
4. **F4** (one cheap threshold): small, but touches the replay.
5. **F5/F12** (reserve): only if you want events and the live rung to respect the same floor.
6. **F6/F7/F10** are design questions for you.
