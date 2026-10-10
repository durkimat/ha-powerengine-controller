# Engine v2 ablation 3: the smallest set that keeps the minimum engine calm

10 Oct 2026 · follows `engine-v2-ablation-2.md`. Same days (7, 8, 9 Oct), same seed and caveats (three days, in-sample, cash only).
Every variant starts from the MVP (all executor layers and extras off, switch cost 4p kept) with **"ask the plan before a charge
or sale starts"** (`_with_the_programmes_choice`, "pc") left on, and adds back the other pieces. The baseline is the current
engine: 43 mode changes and 5 flip-flops over the three days, adjusted cost -5.44 £.

## Result (7 to 9 Oct)

| Variant (pc plus) | Mode changes | Flip-flops | Δ adj £ vs baseline |
|---|---|---|---|
| nothing (pc alone) | 127 | 48 | +0.14 |
| dwell 120 s | 114 | 35 | +0.15 |
| level band 1 point | 102 | 34 | 0.00 |
| `_leg_going` | 119 | 48 | -0.47 |
| `_worth_the_change` | 79 | 22 | +1.23 |
| dwell + level band | 92 | 23 | +0.33 |
| dwell + `_leg_going` | 107 | 36 | -0.46 |
| level band + `_leg_going` | 94 | 30 | -0.35 |
| `_leg_going` + `_worth_the_change` | 81 | 23 | +0.53 |
| **dwell + level band + `_leg_going`** | **84** | **21** | **-0.35** |
| baseline (everything on) | 43 | 5 | 0 |

No subset reaches the baseline's calm. The nearest, **pc + dwell + level band + `_leg_going`** (four pieces, no price band, no
comfort or top-up cost, no `prefer_self_use`, no `_worth_the_change`), has about twice the mode changes (84 against 43) and four
times the flip-flops (21 against 5), and is £0.35 cheaper over three days. `_worth_the_change` is what buys most of the rest of
the calm, and it costs money: with it, 79 to 81 changes, but £0.53 to £1.23 dearer.

## What a change is worth

£0.35 saved for 41 more mode changes breaks even at **0.85p a change**. The plan's own switch cost is 4p (your archived config).
Priced at 4p a change, the current engine is better than the four-piece set by about £1.29 over the three days; priced at under
0.85p the four-piece set wins. The simulator prices cash only: what a change really costs (inverter and BMS wear, noise, the
write budget) is yours to say, and the choice between these sets turns on it.

## The synthetic day (`2026-09-03`, one day, cash only)

Every variant is cheaper than the baseline (-£0.14 to -£0.39) for 2 to 13 more mode changes; pc alone is +11 changes and +3
flip-flops for -£0.36. The synthetic day is easy (a clear sun, two clean slots), so it does not separate the sets.

## Reading it

- The plan's own choice (`pc`) is the foundation: without it the minimum engine makes 1,743 changes, with it 127.
- Of the extras, `_leg_going` saves money and cuts few changes; the dwell and the level band cut changes and cost little. Used
  together with pc they bring 127 changes down to 84.
- `_worth_the_change` is the expensive way to be calm. If the policy executor in step 4 gets near 43 changes without it, it has
  replaced a rule that was costing £0.5 to £1.2 over these days.
- Not run: the price band on top of pc (it made the unaided minimum worse), the comfort and top-up costs, `prefer_self_use`, and any
  change to `switch_cost_p` (the plan's own price of a change, which raises calm for free if the plan is allowed to honour it).

## Reproduce

`tools/sim/pe_sim.py run --days 2026-10-07..2026-10-09` with `--variant` as in ablation 2, starting from the "MVP + ask the plan"
preset in the GUI and adding `min_dwell_s=120`, `level_band_pct=1` and putting `_leg_going` back.
