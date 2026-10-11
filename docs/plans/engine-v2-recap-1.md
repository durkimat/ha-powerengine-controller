# Engine v2: recap of the new approach, 10 to 11 Oct 2026

What we tried, what each thing was worth, the combined result against the current engine, and what is still open. Details and
tables are in [ablation 1 to 5](engine-v2-ablation-1.md), [the level penalty](engine-v2-level-penalty.md) and
[the architecture page](engine-v2-architecture.md). All figures: the simulator on 7, 8 and 9 Oct (72 hours) and one synthetic day,
cash only, in-sample, so they rank ideas better than they price them.

## The methods, and how effective each was

| Method | What it is | Effect |
|---|---|---|
| Simulator (`tools/sim`) | Real app, engine v2, stored days, any code or settings, a GUI | Agrees with the nightly code exactly; ~2 min a day |
| Ablation | Switch executor rules off one at a time | Every rule buys calm, not money; "ask the plan" does most of the work (1,743 changes to 127) |
| Price band | A wider band the price must clear | **Does not work**: makes the chatter worse alone; the loop is at the charge target, not a price |
| Re-plan at the target | A re-plan when a charge or sale ends | Small alone (about 20%); useful only with the executor below |
| **Follow the plan** | The executor takes the mode of the plan's timeline for now (no per-tick price comparison) | **Works**: 81 changes and 14 flips against 43 and 5 for the same money, with one extra rule (2 min dwell) |
| Finer plan steps (15 or 10 min) | Shorter segments in the plan | £0.3 to £0.7 cheaper over 3 days for 2 to 2.8 times the solve time; does not reduce Hold |
| Raise the switch cost | 6p or 8p instead of 4p | Calmer (81 to 57 changes) but £0.55 to £0.68 dearer |
| **Top curve** | Exponential cost above 90% (3 pts per doubling, 200p at 100%) | **Works**: highest level 94.6%, 0.0 h at 95% or above (current engine 11.2 h); cheaper, not dearer |
| **Bottom curve** | Exponential cost below 70% (5 pts, 10p at the 15% reserve) | **Works**: lowest level about 22%; hours at or below 25% from 0.9 to 0.2 |
| **Sharper bottom in smart slots** | Rate x300, starting at 80%, in slot segments | **Works**: mean level in slots 68% to 81% (real days), 57% to 78% (synthetic); lowest 64.9% on the synthetic day |
| Hold-cost tie-break | A tiny cost per hour of Hold | **Does not work**: Hold hours unchanged |

## The combined result: "the new approach" against the current engine

New approach = follow-the-plan executor + re-plan at target + 2 min dwell, with every band, layer and extra cost off (price band,
level band, comfort cost, top-up cost), the plan's own 4p switch cost, top curve (3 pts, 200p), bottom curve (5 pts, 10p), smart-slot
bottom curve (x300, from 80%). Late-event insurance and prefer-self-use are off, as in all the tests.

| 7 to 9 Oct | Current | New approach | New + insurance on | New, 15 min steps |
|---|---|---|---|---|
| Adjusted cost | -5.44 | **-5.82** | -5.57 | -5.84 |
| Mode changes (3 days) | 43 | 118 | 120 | 114 |
| Flip-flops | 5 | 22 | 27 | 18 |
| Commands per day | 14 | 39 | 40 | 38 |
| Hold hours | 1.5 | 7.9 | 4.2 | 8.0 |
| Hours at or above 95% | 11.2 | **0.1** | 1.1 | 0.1 |
| Highest / lowest level | 100% / 14.9% | 95.0% / 14.9% | 95.5% / 16.6% | 95.8% / 18.4% |
| Hours at or below 25% | 3.2 | **0.8** | 0.6 | 0.5 |
| Slot windows: mean level / hours below 70% | 72% / 7.6 | **81% / 2.6** | 81% / 2.4 | 81% / 2.9 |
| Plan calculation time (3 days) | 194 s | 368 s | 482 s | 633 s |

Day by day (adjusted cost, mode changes): 7 Oct -1.00 (11) against -1.27 (20); 8 Oct -1.78 (15) against -1.68 (42); 9 Oct -2.66 (17)
against -2.87 (56); synthetic -2.88 (11) against -2.74 (25). The new approach is cheaper on two real days and dearer on the other and on
the synthetic one, so its £0.38 over three days is a narrow win, not a clear one.

## A cloudy day with a short-notice grid event (`2026-09-10`)

Synthetic, made by `synth.py cloudy`: the 8 Oct house load, 6.9 kWh of sun (2.0 kW peak; a clear day gives 18.9 kWh) with a drifting
cloud pattern, a forecast that expected 20% more than came, no smart slot, and a grid event 17:30 to 18:30 announced at 10:00. In every
variant the announcement triggers a re-plan at once (10:00:30) and the battery is sold into the event at 115p (from about 77 to 80% down to
about 46 to 51%).

| | Current | New approach | New + insurance on | New, 15 min steps |
|---|---|---|---|---|
| Adjusted cost | **-3.67** | -3.45 | -3.47 | -3.40 |
| Mode changes / flip-flops | 10 / 0 | 22 / 7 | 15 / 1 | 18 / 3 |
| Lowest level | 14.9% | 21.9% | 15.8% | 20.0% |
| Peak-rate grid charge (kWh) | 0.0 | 0.0 | 1.1 | 0.0 |

- The new approach is **£0.22 dearer** here. The likely reason (not isolated): the top ceiling. The current engine stored the sun up to 98.5% by
  midday (the new approach 92.8%), 1.5 kWh more for the evening peak at 28.84p instead of selling it at 15p. That is the price of keeping the
  top below about 95%, and on a day with little sun to waste it shows.
- The 7 flip-flops are discharge and hold alternating from 10:21 to 12:08 at the 90% mark as the cloud comes and goes.
- With late-event insurance on, the plan bought 1.1 kWh at 28.84p at 17:06 to 17:19, just before the event, to sell it at 115p: valid
  arbitrage into an announced event, and the reason for its 1.1 kWh of peak-rate charge.

All five days together (adjusted cost): current -11.99, new approach -12.01: **level on cost**; 64 mode changes against 165.

## Two more synthetic days: the afternoon smart slot withdrawn at 12:05

Both are the sunny day of 3 Sep (east-facing sun, slots 09:00 to 12:00 and 13:00 to 16:00) with the afternoon slot taken back at 12:05,
so the afternoon runs at the normal 28.84p. `2026-08-27` is that alone; `2026-08-20` adds a grid event 18:00 to 19:00 announced at 13:00.
(`synth.py sunny --withdraw "13:00-16:00@12:05"`, with `--events 18:00-19:00 --announce 13:00`.)

| Adjusted cost (hold hours) | Current | New approach | New, 10 min steps |
|---|---|---|---|
| 27 Aug, slot withdrawn | -1.48 (4.4 h) | **-2.14 (2.9 h)** | -2.04 (2.0 h) |
| 20 Aug, + event 18:00 to 19:00 | -4.03 (4.0 h) | **-4.91 (3.1 h)** | -4.84 (2.8 h) |

- This is the case the slot curve is for. The current engine sold the battery down to 24.8% by the end of the morning slot counting on the
  afternoon refill; with the slot withdrawn it ran flat (14.9%) by 18:30 and then held at the reserve for **4 hours at 28.84p**
  (the worst kind of hold: a hard limit, grid at the dear rate). The new approach kept 80.5% at the end of the morning slot and
  never went below 40%. It is £0.66 and £0.88 better on the two days.
- Its holds on those days are 7 to 95 minutes of "spare sun exported rather than stored" at 65 to 67%, plus short pauses. After the
  withdrawal the plan sold the surplus (76 to 66% at 15p) because more is stored than the evening needs and overnight refill is cheap; the
  marginal kWh is worth about 14p against 15.8p for exporting sun, so the plan is almost indifferent.
- **10-minute steps** (replacing 15 minutes in the saved set): over the seven days -18.76 against -19.05 at 30 minutes, so no gain with the
  curves on (it was cheaper only on the plain follow-the-plan variant), and slower. Per day it is better on 27 Aug, 20 Aug, 3 Sep and
  10 Sep by a few pence to 10p, and worse on 7 Oct (£0.14) and 8 Oct (-£0.15 better).

Seven days together (adjusted cost): current -17.49, new approach -19.05 (£1.56, 9%, better), 10-minute steps -18.76. Hold hours:
current 10.1 h, new approach 20.5 h; on the withdrawn-slot days the new approach holds less than the current engine, elsewhere more.

## Are we more effective, and is the change volume acceptable?

- **Money:** a little better on real days (£0.38, about 7%, over three days); about the same or slightly worse once late-event
  insurance and prefer-self-use are put back on (£0.13). The benefit is mostly **control**, not cash: the top of the battery is kept
  below 95% (current: 11 h at or above it), the bottom is held up, and smart slots are no longer used to sell the battery down.
- **Change volume: not yet at the level of the current engine.** 118 mode changes in three days against 43 (39 commands a day
  against 14; one change every 37 minutes against every 100) and 22 flip-flops against 5. The follow-the-plan executor alone made 81;
  adding the curves took it to 118, so the curves add changes of their own. Under RAM control the daily write budget does not apply
  (nothing is EEPROM), so this is about noise and control-path load, not wear, but it is above what we can call settled.

## What is still open

1. **Change volume**, the main one. Untried: a longer dwell (5 or 10 minutes), a switch cost of 5 to 6p, a level band on the new
   executor, a smoother way for the curves to settle at their start levels. Target: about 70 changes in three days or fewer.
2. **Hold hours** (7.9 against 1.5): mostly daytime "export the sun rather than store it" holds that the top curve causes. Valid in the
   model; a self-use mode with a charge cap, if the inverter has one, would not need Hold at all. Overnight holds are ties and a
   tie-break did not help.
3. **Smart slots that start with the battery already low** (9 Oct 11:00, 38.6%): the curve cannot raise a level a slot starts at.
4. **Late-event insurance and prefer-self-use** were off in every test and cannot be judged by replay; with them on the cost advantage
   shrinks and flip-flops rise (27).
5. **Calculation time**: about twice the current engine's (re-plans at every target); not measured on the HA host.
6. **Evidence is thin**: three real days of one autumn week and one synthetic day, in-sample. The incident days (6 Oct evening, the
   look-ahead-end cases) are not yet built as permanent scenarios, so the plan's gates are not yet runnable. Real inverter readings
   are noisier than the simulated ones, which the dwell and the filters are for.
7. **Productionising**: the follow-the-plan executor is a simulator stub; the curves are constants, not engine settings (no config
   page entries); the code PRs (#290 and what is stacked on it) are not all merged.
8. **The real test** is a few weeks running it live, in preview alongside the current engine first.
