# Engine v2 ablation 2: pairs, the bare-minimum engine, and what stops it chattering

> How the engine is built and where each setting applies: [engine-v2-architecture.md](engine-v2-architecture.md).

10 Oct 2026 · follows `engine-v2-ablation-1.md`. Same days (7, 8, 9 Oct), same seed, same caveats: three days, in-sample, the
simulator prices cash only. Negative adjusted cost is cheaper than the baseline (current code, archived config).

## Pairs

| Variant | Δ adj £ | worst day | Δ modes | Δ flips |
|---|---|---|---|---|
| `_worth_the_change` + `_with_the_programmes_choice` off | -1.21 | -0.22 | +22 | +6 |
| `_worth_the_change` + `comfort_cost_p=0` + `top_up_cost_p=0` | -0.76 | -0.06 | +22 | +4 |
| all four "second prices" off | -0.71 | -0.06 | +39 | +13 |
| `comfort_high_soc=100` | -0.25 | -0.06 | +15 | +3 |
| `level_band_pct=0` + `prefer_self_use=false` + `price_band_p=0` | -0.22 | +0.01 | +13 | +3 |
| `level_band_pct=0` + `prefer_self_use=false` | -0.11 | +0.01 | +5 | +1 |
| `comfort_low_soc=0` | -0.01 | 0.00 | 0 | 0 |

- Removing more is not removing more cost: all four second prices off (-0.71) is less cheap than just two (-1.21) and flips
  more. The rules interact, so one-at-a-time results do not add up.
- `comfort_low_soc` changed nothing on these days: a deletion candidate.

## The bare-minimum engine ("MVP v0")

Everything an executor adds on top of the price model switched off: the three layers (`_leg_going`,
`_with_the_programmes_choice`, `_worth_the_change`), `price_band_p`, `level_band_pct`, `min_dwell_s`, `comfort_cost_p`,
`top_up_cost_p`, `prefer_self_use` and `late_events` (all 0 or off). Kept: the plan with its switch cost (4p in the archived
config), the hard limits (grid event, reserve, BMS, fuse, cold) and the reading filter.

| | adj £ | mode changes | flip-flops |
|---|---|---|---|
| baseline | -5.44 | 43 | 5 |
| MVP v0 | -5.50 | 1,743 | 1,631 |

It runs and follows the prices, and it **chatters**: 865 mode changes on 8 Oct alone, about one every 100 seconds, for the same
money. Every layer in the executor is there to stop this, not to earn money.

## What stops the chatter

Each rule put back into MVP v0 on its own (mode changes shown are against the *baseline's* 43, so +1,700 would mean no help):

| Put back | Δ modes vs baseline | Δ flips | Δ adj £ |
|---|---|---|---|
| `_with_the_programmes_choice` | **+84** | +43 | +0.14 |
| `level_band_pct=1` | +237 | +183 | -0.15 |
| `min_dwell_s=120` | +380 | +301 | +0.06 |
| `_leg_going` | +1,437 | +1,363 | -0.31 |
| `price_band_p=0.5` | +2,347 | +2,312 | +0.60 |
| `_worth_the_change` | +1,630 | +1,602 | +0.73 |

- **`_with_the_programmes_choice` alone brings 1,743 changes down to 127.** It makes a charge or sale start only when the plan
  itself (`value.choice_now`) would start it. That is the plan's own "policy reader" idea, already in the code but used only to
  approve a start. It is the strongest evidence yet for step 4: the plan, asked directly, is the anti-chatter.
- A level band or a minimum dwell helps but does not cure it; a price band **makes it worse** on its own, and `_worth_the_change`
  and `_leg_going` do little by themselves. Hysteresis bands that the plan does not know about fight the plan.
- Not run: the rules in pairs on top of MVP v0 (programme choice plus dwell or a level band), which is the next step: the smallest
  set that reaches the baseline's 43 changes.

## A synthetic day

`tools/sim/synth.py sunny` makes a day the archive does not have, from a real day's house load and prices: an east-facing array
(early, asymmetric curve, 4 kW peak, 18.9 kWh), smart slots 09:00-12:00 and 13:00-16:00 at 6.66p, no car, no grid event, and a
Solcast forecast with a narrow spread. It is `2026-09-03` (a Thursday, as the template 8 Oct is; the archive begins on 11 Sep),
written to `~/pe-sim/synthetic/` and read beside the archive, never mixed into "all days" or a gate. The current engine run on
it: cost -2.85 £, 11 mode changes, no flip-flops; it sells in the morning slot and refills in the afternoon one. Its tomorrow
and day-3 forecasts are the same sunny curve (`--tomorrow-scale` changes that).
