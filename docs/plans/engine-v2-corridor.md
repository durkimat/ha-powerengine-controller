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

## Prototype 3: a corridor is a price tolerance, so nothing in the plan's clock may end a run inside it

Prototype 2 stopped a run at the corridor edge, but three things still told the engine it was off plan, because they
read the plan by *time* where the corridor is a price tolerance read by *level*. Of 404 re-plans over the three days
only 36 were "level reached"; 250 (62%) were "The battery left its expected range".

1. `observe._band` indexes the plan's level path by clock time. A run that reaches its edge early is ahead of the clock,
   and the hold that follows is behind it; 10 minutes of either is a re-plan. Fixed (`CORRIDOR_BAND`): while the clock is
   in a charge or sale, or in the hold that only fills out its segment, the range is the whole run, from where it starts
   to the far edge of its corridor (`Observer._run_span`).
2. A run that reaches its edge is on target, so the level event it raises (`level_edge`) no longer asks for a re-plan,
   and a re-plan made for another reason while the run goes on keeps the run's edge if the new corridor still contains it
   (`CORRIDOR_STICKY`).
3. With the re-plans gone nothing broke the loop where a sale holds at its edge, the level wobbles back above it, and
   the same sale restarts (two-minute discharge/hold pairs from 08:51 to 12:00 on 8 Oct: 33 flips). The run that ended
   at its edge is now not restarted by the plan it ran under (`Executor._reached`, as for a charge target in v1); a new
   plan allows it again.

Same three days, `system.engine=v2_1`, tolerance in pence (CORRIDOR_COST_P):

| | adj GBP | flips | modes | holds | min in Hold at target | cut runs | re-plans |
|---|---|---|---|---|---|---|---|
| baseline (main) | -5.59 | 28 | 127 | 29 | 126 | 20 | 395 |
| prototype 2, 0.5p | -5.45 | 26 | 132 | 42 | 247 | 29 | 434 |
| + sticky edge and level_edge, 0.5p | -5.70 | 24 | 125 | 34 | 179 | 28 | 404 |
| + corridor range, no latch, 0.5p | -5.56 | 33 | 133 | 36 | 258 | 23 | 227 |
| prototype 3 (all three), 0p | -5.48 | 17 | 118 | 30 | 150 | 18 | |
| prototype 3, 0.5p | -5.72 | 21 | 115 | 28 | 210 | 20 | 224 |
| prototype 3, 2p | -5.61 | 17 | 109 | 24 | 205 | 25 | |

Re-plans fall from 395 to 224 (-43%): smart slots 92 and prices 39 are the hard inputs, and "left its expected range"
falls from 243 to 63. Against the baseline: cost about 0.1 GBP better (noise), flips 28 to 17-21, modes 127 to 109-118,
calculation time about 40% less (304 s against 522 s). **Minutes in Hold at a target are higher** (150-210 against 126).

### Splitting the holds (scoreboard: wait / car / idle)

A hold at a reached target is *waiting* if a price or event changes during it or at its end, *car* if the car charges
during it (the battery is held for the car whatever the prices), else *idle*. Same three days:

| | at target | wait | car | idle |
|---|---|---|---|---|
| baseline | 126 | 0 | 32 | 94 |
| prototype 3, 0.5p | 210 | 11 | 52 | 147 |

So the extra Hold is not waiting for prices (11 minutes). 53 of the 84 extra minutes are idle, 20 are car. The idle ones
are mostly on 8 Oct, 09:00-11:00: the battery sits at 89-93% with the sun refilling it, a sale of a few points
(93 to 86) runs to its near edge in minutes, holds 14-38 minutes while the sun refills it, and the band exit re-plans
and restarts it (discharge, hold, discharge four times; the baseline has one such hold that morning). A stop at the near
edge sells less than the plan's best point, and a later plan makes the rest up. Holds at 8 Oct 19:57 (33 min) were the
car and are plan holds, correctly. An earlier draft of this section quoted 169 re-plans
and 9 range exits: those were read from the default engine's variant by mistake.

Verdict so far: the prototype cuts re-plans and flips, and costs no more, but adds idle Hold at the near edge of a
corridor, and shows no measurable saving. It does not yet use the time flexibility it creates.
Still not run: 11 Oct, the synthetic day, replay, the full tier. Constants only; no setting, card or release.

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
