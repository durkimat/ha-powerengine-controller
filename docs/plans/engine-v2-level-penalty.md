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

## Not yet done

- A finer top sweep with the top-up out (3 and 4 points per doubling, 100 to 300p).
- The bottom curve: its effect on the reserve-forced holds on the synthetic day.
- The tie-break (a small time preference) for the overnight holds.
- Turning the constants into settings once the shape is chosen.
