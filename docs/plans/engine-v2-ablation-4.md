# Engine v2 ablation 4: does a wider price band stop the chatter?

10 Oct 2026 · follows [ablation 3](engine-v2-ablation-3.md); terms in [engine-v2-architecture.md](engine-v2-architecture.md). Same days
(7, 8, 9 Oct) and the synthetic day, same caveats (three days, in-sample, cash only).

**The idea tested.** The live read flips near a price line, so make the line a band the price must clear before the mode may change
(direction of travel being the mode already running). That is what `price_band_p` already does, in pence. The test: widen it and see
whether the chatter falls. If a 4p band (14% of a 28p price, 57% of a 7p price) cannot, a percentage band will not either.

## Result, real days (7 to 9 Oct)

Mode changes shown are the total over the three days (the current engine makes 43, with 5 flip-flops).

| Variant | Price band | Mode changes | Flip-flops | Δ adj £ vs current engine |
|---|---|---|---|---|
| bare minimum engine | 0 | 1,743 | 1,631 | -0.06 |
| bare minimum engine | 0.5p | 2,390 | 2,317 | +0.60 |
| bare minimum engine | 1p | 2,205 | 2,127 | +0.62 |
| bare minimum engine | 2p | 2,686 | 2,622 | +0.60 |
| bare minimum engine | 4p | 2,647 | 2,581 | +0.58 |
| bare + "ask the plan" | 0 | 127 | 48 | +0.14 |
| bare + "ask the plan" | 1p | 91 | 22 | -0.04 |
| bare + "ask the plan" | 2p | 85 | 21 | +0.04 |
| bare + "ask the plan" | 4p | 85 | 21 | +0.04 |

(A band of 8p was also tried and refused: the setting's maximum is 5p.)

- **On its own, a band does not tame the chatter; it makes it worse** (1,743 becomes 2,200 to 2,650) and costs about £0.60 over the three
  days. No width from 0.5p to 4p helps.
- **With "ask the plan" on, a band helps a little:** 127 changes become 91 (1p) or 85 (2p), and flip-flops fall from 48 to about 21. A
  4p band is identical to a 2p one, so the effect saturates at about 2p.

## Synthetic day (2026-09-03, one day)

Bare engine: 385 changes with no band (a current-engine day has 11); 0.5p, 1p and 2p give 179, 160 and 164 (about half); 4p gives 575
(worse). With "ask the plan": 16 changes at 1p, 15 at 2p, 14 at 4p, with no flip-flops, close to the current engine's calm.

## Why the price line looks like the wrong place

The chatter on 8 Oct (bare engine, no band) is 823 mode changes. What changes is almost entirely **charge to hold and hold to
charge**, alternating (389 and 388 times), each lasting one 10-second tick. Sixteen are charge/discharge pairs and the rest are a
handful. About 95% fall in two places in the day: **05:00** (120 changes) and **18:00 to 21:00** (about 660), when a charge is
running.

What this shows: the loop is a charge that starts, stops and starts again every tick, in the hours when charging happens, not a mode
chasing a price that wobbles. What I have **not** done is trace the exact reason in the code. The likely suspects are the comparison
of the level with the charge's target (a finished charge restarts when the level reads just under the target; the inverter's level
reads a point or so differently while charging and while holding) and the "charge now" path, which starts a charge without
consulting the price band at all. Either would explain why a price band cannot help; I have not shown which it is.

## Conclusions

1. **A percentage price band will not stop this chatter**: widening the price band, in pence, to well beyond any plausible percentage
   does not help on its own and makes it worse on real days.
2. **A modest price band (about 1 to 2p) is worth keeping on top of "ask the plan"**: it takes 127 changes to about 85 to 91.
3. **If hysteresis helps, it belongs on the level at the charge target**, where the loop is. The existing `level_band_pct` is that
   idea (default 1 point). In ablation 2 it alone brought the bare engine from 1,743 to 280 changes: a large cut, but still six times
   the current engine's 43, so one point was not enough on its own.
4. **Next tests**: sweep `level_band_pct` from 0 to 5 (its maximum) on the bare engine; and trace one 05:00 or 19:00 charge/hold
   alternation through `execute.py` to name the exact comparison that flips.

## Reproduce

`--variant` with the settings and stubs of the "MVP" preset (GUI), changing only `price_band_p`, as in ablation 2.
