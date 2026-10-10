# Engine v2: a smooth cost on the battery level

10 Oct 2026 · code: `engine_v2/value.py` (`LEVEL_*` constants, `_level_penalty`); terms in [engine-v2-architecture.md](engine-v2-architecture.md).

## What it is

A cost on where the battery sits, inside the plan, replacing the hard bands and the flat comfort cost with one formula per end.
It is a cost of the level itself (pence per kWh per hour for the energy in each percent-layer beyond a start level), so the plan's
value curve and the live price lines pick it up with no rule. Both curves are **off by default** (rate 0): with them off the plan is
exactly as before (7 and 8 Oct reproduce to the penny; the whole-app replay is unchanged).

```
top, above S_top:      rate(s) = A_top * (2^((s - S_top) / D_top) - 1)        A_top set so that rate(100%) = LEVEL_TOP_RATE_AT_FULL_P
bottom, below S_bot:   rate(s) = A_bot * (2^((S_bot - s) / D_bot) - 1)        A_bot set so that rate(anchor) = LEVEL_BOT_RATE_AT_FLOOR_P
cost per hour at level s = (capacity / 100) * the integral of rate over the layers from the start to s   (closed form)
```

| Constant | Default | Meaning |
|---|---|---|
| `LEVEL_TOP_START_SOC` | 90 | where the top curve starts (zero cost here: no kink) |
| `LEVEL_TOP_RATE_AT_FULL_P` | 0 (off) | rate at 100%, p per kWh per hour |
| `LEVEL_TOP_DOUBLING_PTS` | 2 | points per doubling |
| `LEVEL_BOT_START_SOC` | 70 | where the bottom curve starts |
| `LEVEL_BOT_RATE_AT_FLOOR_P` | 0 (off) | rate at the anchor, p per kWh per hour |
| `LEVEL_BOT_ANCHOR_SOC` | 15 | the level the bottom rate is set at (the reserve) |
| `LEVEL_BOT_DOUBLING_PTS` | 5 | points per doubling |

They are numeric constants (no engine setting yet), so the simulator can dial them from its constants panel. They become engine
settings, with the card's config page entries, when the shape is decided.

## How it is applied

- **Full steps**: the cost of a segment is its hours times the average (Simpson) of the cost at the start, middle and end level.
- **Part-way charge or sale**: priced from the level it actually reaches, not as a blend of the full-step and hold costs, with the
  matching slope for the stop search (tested against the cost numerically).
- **Early charging** inside an equal-price stretch is capped at the top curve's start, because above it the hours cost.
- **The top-up cost** (`top_up_cost_p`, a one-off price on grid charge above the comfort band) is a different kind of cost: it taxes the
  act of charging, where this taxes the time spent there. Both can be on.

## First results (follow-the-plan variant, 7 to 9 Oct, 72 hours; the synthetic day agrees)

Hours at or above a level, the highest level reached, and adjusted cost (current engine: 11.2 h at or above 95%, -5.44; the
follow-the-plan baseline without a curve: 21.3 h, -5.42).

| Top curve (doubling, rate at 100%, top-up) | Highest | h at or above 95% | h at or above 93% | Adj £ | Modes | Flips | Hold h |
|---|---|---|---|---|---|---|---|
| 2 pts, 100p, top-up 4p | 94.7% | 0.0 | 0.6 | -5.60 | 98 | 20 | 9.6 |
| 3 pts, 100p, top-up out | 95.9% | 1.0 | 1.7 | -5.53 | 94 | 17 | 8.8 |
| 2 pts, 30p, top-up 4p | 96.8% | 1.3 | 2.2 | -5.49 | 90 | 15 | 7.4 |
| 2 pts, 10p, top-up 4p | 100% | 0.7 | 2.6 | -5.54 | 91 | 17 | 9.2 |
| 2 pts, 100p, top-up out | 100% | 1.8 | 3.2 | -5.58 | 96 | 19 | 8.5 |

- A rate of about 100p at 100% with doubling every 2 to 3 points puts the practical limit at 94.7 to 95.9%. It does not cost money
  on these days (every curve beats both baselines by £0.1 to £0.5 over three days).
- The 4p top-up is not redundant: at the same curve it cut the time at or above 95% from 3.9 h to 1.1 h, because it also taxes grid
  charging above 90%.
- **Hold hours rise** (1.8 h to 6 to 9.6 h). On 8 Oct about 4.7 of 7.4 hold hours are daytime "spare sun is sold at 15p": the curve makes
  stored energy near the top worth less than the 15p the sun fetches, and Hold is how the plan exports sun without storing it. No
  cheap refill is known until 23:00 (the evening smart slot is announced at 17:58), and a stored kWh used at the 28.84p peak saves
  27.4p against a 14.25p sale, so selling the battery by day would lose money. Overnight, the plan does price the sell and
  recharge cycles (21:30 to 23:00 sale, 23:00 to 01:30 recharge, more pairs after); its holds there are ties between equal-priced
  segments, which a small preference for acting sooner could remove (not yet tested).

## Results 2: the finer top sweep, the bottom curve, and the Hold-cost tie-break

Same days and variant (follow-the-plan, 7 to 9 Oct, 72 hours, synthetic day alongside). Current engine for reference: 11.2 h at or
above 95%, 3.2 h at or below 25%, -5.44. Follow-the-plan baseline: 21.3 h and 0.9 h, -5.42, hold 1.8 h.

**Top curve, top-up out** (the form you prefer):

| Doubling, rate at 100% | Highest | h at or above 95% | h at or above 93% | Adj £ | Modes | Flips | Hold h |
|---|---|---|---|---|---|---|---|
| 3 pts, 100p | 95.9% | 1.0 | 1.7 | -5.53 | 94 | 17 | 8.8 |
| **3 pts, 200p** | **94.6%** | **0.0** | **0.8** | **-5.92** | **87** | **13** | 6.7 |
| 3 pts, 300p | 94.1% | 0.0 | 1.1 | -5.59 | 88 | 16 | 7.0 |
| 4 pts, 100p | 94.4% | 0.0 | 0.4 | -5.67 | 99 | 17 | 9.1 |
| 4 pts, 200p | 94.0% | 0.0 | 0.2 | -6.06 | 93 | 20 | 7.1 |
| 4 pts, 300p | 96.7% | 0.6 | 0.9 | -5.94 | 90 | 19 | 6.4 |

Several settings land the practical limit at 94 to 96% with the top-up out; **3 points per doubling at 200p** is the calmest of
them (13 flip-flops, fewer than the baseline's 14) and cheaper than the baseline by £0.50. The results are not monotonic in the
rate (4 pts at 300p reaches 96.7%, above 200p's 94.0%): the plan's own dynamics move things by a point or so, so read a setting as a
neighbourhood, not a number. Every curve is cheaper than both baselines.

**Bottom curve** (starts at 70%, doubles every 5 points, rate set at the 15% reserve):

| Rate at 15% | Lowest | h at or below 25% | h at or below 20% | Adj £ | Modes | Hold h | Synthetic: lowest, h at or below 25% |
|---|---|---|---|---|---|---|---|
| none | 14.8% | 0.9 | 0.6 | -5.42 | 81 | 1.8 | 14.8%, 3.4 |
| 1p | 14.8% | 0.9 | 0.6 | -5.38 | 79 | 1.4 | 14.8%, 1.4 |
| 3p | 14.9% | 0.8 | 0.4 | -5.55 | 85 | 1.2 | 15.9%, 2.1 |
| **10p** | **21.9%** | **0.2** | **0.0** | **-5.66** | **77** | **1.1** | **20.2%, 0.8** |

A 10p rate at the reserve lifts the practical floor to about 20 to 22%, with no cost and no extra holding. It addresses the synthetic
day's deep discharge (3.4 h at or below 25% falls to 0.8 h). A stronger rate would raise the floor further. The top and bottom curves
together (3 pts at 100p with the bottom at 3p) did worse on the synthetic day (30 mode changes, 7 flip-flops), so they interact
and should be tuned together.

**Hold-cost tie-break** (`HOLD_COST_P_PER_H`, a tiny cost per hour of sitting in Hold, off by default):

| Hold cost | Hold h (real) | Modes | Adj £ | Synthetic hold h |
|---|---|---|---|---|
| none | 1.8 | 81 | -5.42 | 2.4 |
| 0.02 to 0.05 p/h | 2.2 | 82 | -5.29 | 0.8 |
| 0.2 p/h | 1.8 | 77 | -5.12 | 0.8 |
| with top 3 pts/100p: none, 0.05, 0.2 | 8.8, 8.4, 7.3 | 94, 97, 92 | -5.53, -5.55, -5.95 | 1.1 |

It does **not** reliably reduce Hold: on the real days hold hours stay level (1.8 to 2.2 h), and with the top curve only the largest
value moves them (8.8 to 7.3 h). The cost effect points the opposite way with and without the top curve (£0.13 to 0.30 dearer
without, £0.42 cheaper with), which is noise-level. The overnight holds are short ties (2 to 20 minutes each); the bulk of the hold
time is the daytime sun export, which this does not touch. **Not recommended as it stands**; the constant is left off.

## Not yet done

- Tuning the top and bottom curves together (and a stronger bottom rate).
- Splitting hold hours into daytime sun export and overnight ties, to say how much of Hold is a decision and how much a tie.
- Turning the constants into settings once the shape is chosen.
