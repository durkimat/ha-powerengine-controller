# Engine v2: plan and execution that mirror each other (level corridor)

Status: **prototypes, measured on three days; not released, replay not re-recorded.** Branches (local):
`corridor-merge-segments` (prototype 1) and `corridor-level` (prototype 2), both from `engine-v2-corridor`, which holds
the diag_summary fix and the simulator's plan-following metrics. Origin: a live export of 12 Oct 2026 (engine v2, "2.1 beta"
profile, `executor=plan`).

## Principle

Plan, follow, re-plan on defined conditions. A target is a corridor (a band of levels around the planned level), not a
point; the next action starts as soon as the corridor's edge is reached, never waiting for a 30-minute boundary. Fix the
cause, not the symptom.

## What the export showed

1. `value._partial` / `_walk`: a charge or sale that stops part-way is written as the mode for share f of the segment and
   then a forced Hold for the rest. Prices are equal inside a segment, so the timing is arbitrary: it is how the plan says
   *how much*, not a decision to hold. The plan shows "Hold: the charge has reached its target" until the next :30.
2. The plan depends on the segment grid (`max_segment_min`).
3. A re-plan at a reached target makes a fresh partial stop plus Hold (reproduced with `value.solve`, not yet seen live).
4. `execute.py` `_candidate` with `executor="plan"` returned the timeline item's mode and skipped all band logic;
   nothing ended a run at its level target. Sales ended 2-3 points off target because a re-plan fired.

## Measuring

`tools/sim/scoreboard.py` now scores plan following from each run's stored series (so cached runs score too):
holds; minutes in Hold at a reached target (a hold within a point of the run's target); runs cut short (ended more than
5 min before the plan said, not scored); overshoot and undershoot of the runs that ran to their planned end, judged
against the corridor when the plan has one; plan-vs-actual level error. Resolution is the 5-minute trace. Days: 7-9 Oct
(6 Oct has no snapshot, 10 Oct is incomplete, the archive ends 10 Oct), `system.engine=v2_1`.

| | adj GBP | flips | modes | holds | min in Hold at target | cut runs |
|---|---|---|---|---|---|---|
| baseline (main) | -5.59 | 28 | 127 | 29 | 126 | 20 |
| 10-min grid, no merge | -6.00 | 31 | 123 | 27 | 91 | 18 |
| 10-min grid, merge | -5.59 | 28 | 127 | 29 | 126 | 20 |
| corridor, tolerance 0.5p | -5.45 | 26 | 132 | 42 | 247 | 29 |
| corridor, tolerance 0p | -5.59 | 20 | 124 | 35 | 152 | 22 |
| corridor, tolerance 2p | -5.28 | 37 | 146 | 47 | 220 | 46 |

(Negative adj cost is a profit. Three days is a small sample: differences of 0.1-0.2 GBP are within noise.)

## Prototype 1: segments that say the same thing are one segment

`forecast.merge_equal` joins neighbours whose prices, rules and per-hour sun and house are all equal (energies add).
Switch `engine_v2.forecast.MERGE_EQUAL`. Finding: merging alone changes nothing at the 30-minute default (nothing is
equal across half-hours). The grid dependence had a real cause in the data: a running smart slot was certain only in the
first piece of the half-hour in progress (`a <= now`), the later pieces got the "chance" probability. Fixed
(`a < hh_end`, tested). With that fixed and merging on, a 10-minute cut gives exactly the 30-minute plan.
**Open point:** without merging, the 10-minute grid is 0.41 GBP cheaper over the three days (more resolution for
partial stops), at 2.5x the calculation time. So the grid matters for value, and a plan that is independent of it is
not automatically a better plan.

## Prototype 2: corridor in the planner and in plan-mode execution

- `value._partial(corridor=True)` returns the range of f whose expected cost is within `CORRIDOR_COST_P` of the best
  stop (cost is convex, so one stretch). Computed only in the forward pass. `_walk` turns it into levels (also through the
  early-charge branch); `TimelineItem` gains `level_lo`, `level_hi`, `remainder` (not published, no card change).
- `Executor._plan_candidate` replaces the bypass: a run goes on until its level reaches the corridor's near edge
  (lower for a charge, upper for a sale) even past its planned time; then the next real item starts at once, skipping
  `remainder` holds, but only where the forecast's prices at now and at that item are the same (otherwise the clock stands).
  `_charge_target` / `_sell_floor` use the edge, so the existing "level reached" event (and `replan_on_level`) fires at the edge.
  `level_band_pct` keeps its role for the lines executor and forced charges.

Result: it works as designed (tests: runs stop at the edge, slow runs continue, price change waits) but **it does not
fix the symptoms in these three days.** Holds and minutes in Hold at a target went up, not down, and a wider tolerance
makes cost and flip-flops worse. A zero tolerance (level-driven, point target) halves nothing but cuts flip-flops 28 to 20
at the same cost; that is the only clear gain.

## Why it probably falls short, and what to look at next

- Many holds are not remainders: they follow runs cut by a re-plan, or are genuine plan holds; the metric counts both.
  Split holds into remainder / planned / unplanned before judging.
- An early stop at the near edge under-delivers by half the corridor each time, and the next re-plan then restarts a run
  to make it up (more modes and cut runs).
- Next: (1) run the zero-tolerance executor alone with the planner unchanged; (2) express the quantity without a
  remainder (end the run item at the stop and let the next item start there when prices are equal) so the planner and
  executor say the same thing; (3) days 11-12 Oct once archived, and a synthetic day; (4) the settings need card wiring
  (`CORRIDOR_COST_P` is a module constant for now), `MIN_APP_VERSION` is not touched.

## Not done

No release; replay not re-recorded (replay and the full tier were not run on the branches: `tools/check.sh` fast tier only).
