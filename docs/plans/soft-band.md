# Soft arbitrage band

**Status: built (0.9.102), to be observed on the owner's install.**

The arbitrage band (`arbitrage_min_soc` 75, `arbitrage_max_soc` 90) was meant as a guide: the optimiser's cost already had
an outside-band penalty (`arbitrage_band_penalty_p`) and an above-band dwell cost. In practice both edges were hard in the
plan: `optimiser.slot_target` capped a grid charge at the band's top, and `optimise` skipped any sale ending below the band's
bottom outside the overnight window. With remote control a sale runs at full power (15 points of an 18 kWh battery in a
half-hour), a charge restores about 13, so every cycle needed one slot and a bit of charging, and the rest of the second
slot was a Hold (4 Oct 2026, from the diagnostics export: charge 2.43 kWh then 0.69 kWh). A hold is idle arbitrage time.

## The change (planner only)

- `slot_target`: with arbitrage on, a charge may run `SOFT_BAND_MARGIN` (5) points past the band's top. Still 100% for the
  final overnight top-up and free power; a cheap car charge keeps its top-up level (the band's top inside the overnight window).
- `sell_floor`: outside the overnight window a sale may end `SOFT_BAND_MARGIN` points under the band's bottom (70% by default);
  that floor stays hard, and the band penalty prices the part below 75. Overnight with deep selling is unchanged.
- The controller needed nothing: it follows each slot's `target_soc`, which is the plan's end level for the slot, and it
  does not enforce the sell floor itself.

## Measured (synthetic, 26 cheap half-hours, remote control: no window-change cost)

Before: 9 sales, two idle half-hours, net -1.64. After: 13 sales, no idle half-hour, net -2.24 (cost is lower by 0.60).
Per cycle a sale takes 14 points and a charge restores 12.6, so the level drifts down about 1.4 points per cycle and the
optimiser adds a longer charge when the penalty outweighs the profit. Tested in `test_optimiser.py`.

## Downsides, accepted

More time a few points outside the band (small wear); less margin if a withdrawn smart slot leaves the refill short
(the 70% floor and the 12% reserve hold); charging up to the band's top plus 5 in non-cycle slots buys cheap energy that
could have waited for solar (the optimiser prices that through the same dwell and penalty costs).

## Watch

After release, the plan's windows should show no charge slot with a small rise followed by idle time; look for them in the
diagnostics export's plan and for the `early_target` records (`late` / `still_hold` should fall).
