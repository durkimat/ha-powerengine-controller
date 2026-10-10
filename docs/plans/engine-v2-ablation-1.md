# Engine v2 ablation 1: what each executor rule was worth

> How the engine is built and where each setting applies: [engine-v2-architecture.md](engine-v2-architecture.md).

10 Oct 2026 · step 3 of `engine-v2-mvp-and-simulator.md`. Produced with `tools/sim` on the code at `00a6b7c`.

**Sample:** 7, 8 and 9 Oct only (6 Oct has no forecast at the start of the day; 10 Oct is partial). Seeded from the archived
10 Oct config (`switch_cost_p` is 4 there, not the catalogue's 2) and learned state. Three autumn days is a small sample and
all of it is in-sample: this ranks rules, it does not prove them.

## Result

Each row switches one thing off (a setting to 0 or off, or a method stubbed to its neutral answer) and compares with the
baseline over the three days. Negative adjusted cost is cheaper. "Worst day" is the least favourable single day.

| Variant | Δ adj £ | worst day | Δ modes | Δ flips | Δ peak kWh | min % |
|---|---|---|---|---|---|---|
| no_switch_cost (`switch_cost_p=0`) | -0.94 | -0.10 | +31 | +12 | +0.10 | 15 |
| no_executor_layers (3 layers + dwell) | -0.87 | -0.18 | +38 | +14 | +0.35 | 15 |
| no_top_up_cost | -0.71 | -0.06 | +16 | +4 | 0.00 | 15 |
| no_programme_choice (`_with_the_programmes_choice`) | -0.68 | 0.00 | +17 | +5 | -0.11 | 15 |
| no_worth_change (`_worth_the_change`) | -0.68 | 0.00 | +11 | +3 | +0.10 | 15 |
| no_comfort_cost | -0.66 | -0.01 | +13 | +4 | -0.18 | 15 |
| no_late_events | -0.26 | +0.10 | 0 | 0 | -0.18 | 15 |
| no_prefer_self_use | -0.12 | +0.01 | +3 | 0 | 0.00 | 15 |
| no_price_band | -0.10 | 0.00 | +8 | +2 | +0.10 | 15 |
| no_min_dwell | 0.00 | 0.00 | 0 | 0 | 0.00 | 15 |
| no_level_band | +0.01 | +0.01 | +2 | +1 | 0.00 | 15 |
| no_leg_going (`_leg_going`) | +0.02 | +0.16 | +9 | 0 | 0.00 | 16 |

Baseline over the three days: adjusted cost -5.44 £, 43 mode changes, 5 flip-flops.

## Reading it

1. **Removing most rules makes the simulated day cheaper and noisier, in about the same proportion.** The `switch_cost_p=0`
   row buys £0.94 with 31 more changes (about 3p a change); all executor layers off buys £0.87 with 38 more. The rules mostly
   trade money for calm. The simulator prices cash only, not the wear and noise of extra changes, so "cheaper" is not
   "better". Gate 2 (changes and flip-flops no higher) fails for every removal that saves money.
2. **Several rules look like second prices on a change the DP already prices.** `_worth_the_change` (-0.68 for +11 changes,
   about 6p a change, against a switch cost of 4p), `_with_the_programmes_choice` and the comfort and top-up costs each give
   most of the same trade as `switch_cost_p=0`. This supports the plan's reading that the stack duplicates the switch cost,
   but it does not show which one to keep: removing two of them together has not been run.
3. **`_leg_going` earns its place**: off, it is no cheaper (+0.02), changes the mode 9 more times and one day is £0.16 worse.
4. **No measurable effect here:** `min_dwell_s` (exactly zero change), `level_band_pct` (+0.01, +2 changes, +1 flip),
   `prefer_self_use` (-0.12, +3 changes) and `price_band_p` (-0.10, +8 changes, +2 flips) are small beside the others.

## Do not conclude from this

- **`min_dwell_s` and the debounces are not shown to be useless.** The simulated inverter and readings are clean; dwell and
  debounce exist for noisy live readings (the filtered level reads about a point lower while charging).
- **`late_events` cannot be judged by replay.** It insures against a grid event announced at short notice, and a replayed
  day contains only the events that really happened. Off is £0.26 cheaper on these days with no change in behaviour, and
  that says nothing about the days it is for.
- **`comfort_low_soc` and `comfort_high_soc` were not run on their own** (the comfort band was removed through its cost),
  and `reserve_latched` and the deadline logic are code with no setting; they would need further stubs.

## Candidates for step 4, in order of evidence

1. Merge `_worth_the_change` into the DP's switch cost (the strongest sign of a duplicated price).
2. Remove `level_band_pct` and `prefer_self_use` (little effect either way).
3. Keep `_leg_going` until the policy executor shows it is not needed.
4. Run pairs (for example no `_worth_the_change` plus no `_with_the_programmes_choice`) and add `comfort_low_soc` before deciding.

## Reproduce

```
E=engine_v2.execute.Executor
tools/sim/pe_sim.py run --days 2026-10-07..2026-10-09 --jobs 8 --out DIR \
  --variant "no_price_band:price_band_p=0" --variant "no_level_band:level_band_pct=0" \
  --variant "no_comfort_cost:comfort_cost_p=0" --variant "no_top_up_cost:top_up_cost_p=0" \
  --variant "no_prefer_self_use:prefer_self_use=false" --variant "no_late_events:late_events=false" \
  --variant "no_min_dwell:min_dwell_s=0" --variant "no_switch_cost:switch_cost_p=0" \
  --variant "no_leg_going:$E._leg_going=@false" \
  --variant "no_programme_choice:$E._with_the_programmes_choice=@pass" \
  --variant "no_worth_change:$E._worth_the_change=@true"
```

About 25 minutes with 8 runs at a time. Runs are cached, so changing one variant reruns only that one.
