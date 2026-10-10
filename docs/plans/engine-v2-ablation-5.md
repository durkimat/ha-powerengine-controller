# Engine v2 ablation 5: re-plan at the target, and follow the plan's timeline

10 Oct 2026 · follows [ablation 4](engine-v2-ablation-4.md); terms in [engine-v2-architecture.md](engine-v2-architecture.md). Same days
(7, 8, 9 Oct) and the synthetic day, same caveats (three days, in-sample, cash only).

**The idea tested** (from the owner): the plan is stable and takes about a second, so instead of a second live decision-maker, re-plan when
a target is reached or something changes, and follow the plan. Two simulator-only switches make that testable without changing the
engine's code: `engine_v2.types.EVENT_KINDS.level=@revalue` (reaching a charge or sale target now starts a re-plan; today it does not)
and `engine_v2.execute.Executor._candidate=@planned` (the mode is the one the plan's expected timeline gives for now, if the limits
allow it; the executor no longer works the mode out from the price lines).

All variants below start from the bare-minimum engine (every executor rule and extra off, the plan's own switch cost on).

## Test 1: re-plan when a target is reached, executor unchanged

| Variant | Mode changes | Flip-flops | Adj £ vs current | Calc time |
|---|---|---|---|---|
| bare engine | 1,743 | 1,631 | -0.06 | 152 s |
| bare + re-plan at target | 1,388 | 1,290 | -0.06 | 382 s |
| "ask the plan" | 127 | 48 | +0.14 | |
| "ask the plan" + re-plan at target | 115 | 41 | +0.14 | 163 s |
| ask + dwell + level band + keep-going | 84 | 21 | -0.50 | |
| the same + re-plan at target | 91 | 24 | -0.50 | |
| current engine + re-plan at target | 43 | 5 | 0.00 | unchanged |

Re-planning at the target cuts the bare engine's chatter by about 20% and costs 2.5 times the calculation, since every flip starts a
re-plan. With the other rules on it changes nothing. **The re-plan happens, but the executor then works the mode out from the price
lines again, so a better plan does not change what it does.**

## Test 2: the executor follows the plan's timeline

| Variant | Mode changes | Flip-flops | Adj £ vs current |
|---|---|---|---|
| follow the timeline, no re-plan at target | 96 | 27 | +0.16 |
| follow + re-plan at target | 89 | 20 | +0.16 |
| **follow + re-plan at target + 2 minute dwell** | **81** | **14** | **+0.02** |
| follow + re-plan + keep-going | 89 | 20 | +0.16 |
| (current engine) | 43 | 5 | 0 |
| (ask + dwell + level band + keep-going) | 84 | 21 | -0.50 |

On the synthetic day, follow + re-plan makes 15 mode changes with no flip-flops (the current engine makes 11), £0.15 cheaper.

- **This is the first variant to get near the current engine's calm with no band, no cost rule and no per-tick price comparison.** Its
  only extra is the 2 minute dwell. At 81 changes and 14 flip-flops against 84 and 21 for the four-rule set of ablation 3, it is
  calmer and simpler, though about £0.52 dearer than that set over three days; against the current engine it costs the same money
  (+£0.02) for about twice the changes.
- Keep-going (`_leg_going`) makes no difference here, as it is not on the path any more. Re-planning at the target helps only a little
  (96 to 89 changes).
- 81 changes against 43 is still nearly twice as many, and 14 flip-flops against 5 is not yet as calm.

## What this does and does not show

- The stub is not a finished executor. It replaces only the choice of mode; the charge or sale target, the limits and the dwell
  still come from the existing code, and a charge still ends where the lines or the plan's step end it. A real policy executor would
  need its own tests against the failure cases in the plan (the incident days).
- Three days of one autumn week, the same ones the rules were examined on. The ranking of ideas is more trustworthy than the figures.
- More archived days (winter, a dull week, an event) are needed before a change like this could be released, and the remaining
  flip-flops (14) are not yet explained.

## Next

1. Trace what the 14 remaining flip-flops are (the same method as ablation 4: the transitions and when they happen).
2. Run the follow-the-timeline variants against the incident scenarios in `engine-v2-mvp-and-simulator.md` (the evening of 6 Oct and
   the look-ahead-end cases) when they are built, and on each new archived day.
3. If those hold, build it as a real executor mode behind a flag (step 4 of the plan), rather than as simulator stubs.
