# Engine v2, "2.2 beta" profile (Oct 2026)

What the profile adds to "2.1 beta", why, and what was measured. Written when it was built (12 to 13 Oct 2026); the code is the
authority for what it does now.

## What it is

A new built-in profile, "2.2 beta" (`settings.BETA22_PROFILE`). It is "2.1 beta" plus six settings, all neutral in every
other profile:

| Setting | 2.2 beta | Neutral | What it does |
|---|---|---|---|
| `hold_only_car_reserve` | on | off | Hold is allowed only while the car charges or the battery is at its reserve. Elsewhere: charge, sell or self-use. |
| `free_arbitrage` | on | off | A free-power session may also sell what is stored (Charge or Export, up to 100%) instead of being forced to charge. |
| `reversal_levy_p` | 4 | 0 | Turning a charge into a sale, or back, needs the value this many p/kWh further past the line. |
| `charge_max_import_p` | 15 | 0 (off) | No grid charge while the import price (the cheapest a smart slot could bring) is above this, in the plan and in the engine alike (`rules._core`). Free power, grid events and overrides are not held back. Before an announced grid event that the plan would reach short of, the rule steps aside for a calculated stretch (see "The rule and an announced grid event" below). Added 0.9.145: on 7-13 Oct it cut charging at dear rates from 39 level points to 3 and the week's adjusted cost by 1.44 GBP, for about 20 more mode changes. |

Under `executor = plan` (2.1 beta and 2.2 beta) the engine also does the following. None of it is a setting, and the default
executor is unchanged:

- **A corridor in the plan.** A charge or sale that stops part-way through a segment carries the range of stop levels
  (`TimelineItem.level_lo`, `level_hi`) whose expected cost is within `CORRIDOR_COST_P` (a constant since 0.9.156) of the best. The hold that fills
  the rest of the segment is marked `remainder`: it says how much, and is not a decision to hold.
- **Following the plan by level.** A charge or sale runs until its level reaches the edge of its corridor, however early or
  late that is against the plan's clock. A remainder is skipped, so the next action starts at once.
- **A run that reached its edge is on target.** It does not ask for a re-plan (event kind `level_edge`) and is not restarted
  by the same plan (the `_reached` latch). A re-plan inside the corridor keeps the run's edge.
- **The expected range is the run's corridor** while a charge or sale goes (`Observer._run_span`), not the clock's point on
  the path, so a battery that is ahead of or behind the clock but inside the corridor does not raise `band_exit`.

## Why Hold only for the car and the reserve

On the real inverter Hold is Force charge at 0 W, and it exports spare sun (4 Oct 2026). A Hold at the target in a cheap
slot, with the battery full, stops the house drawing on it while the grid is cheap, but selling that stored energy at the
export rate is the arbitrage. The owner's rule is that Hold has no other job, so the engine cannot choose it for waiting.
Self-use is the neutral mode between the buy and sell lines (the "dead zone"; to be revisited, see Not tested).

Removing Hold removed the dead band that was absorbing Charge/Export and Export/Self-use chatter (24 swaps in 50 minutes
on 9 Oct 05:40). The two levies put a margin back: continuing a run costs nothing, turning one round or restarting
one that just ended needs the margin.

## Measurements (simulator, Hold sells spare sun, engine 2.1 settings, 7 to 9 Oct and four synthetic days)

adj GBP, more negative is better. Three real days and four synthetic days are a small sample: differences under about 0.2 GBP
(real) or 0.4 GBP (synthetic) are noise. Cost was not the aim of the earlier variants and did not move.

| | real adj | modes | flips | synthetic adj | modes | flips |
|---|---|---|---|---|---|---|
| baseline (2.1 beta) | -5.71 | 109 | 19 | -13.34 | 87 | 8 |
| 2.2 beta | -5.97 | 110 | 20 | -13.79 | 90 | 15 |

The sim's re-plan counts, Hold at target and calculation time for the stages leading here: the corridor alone changed
nothing; adding the corridor range, the sticky edge and the latch cut re-plans by about 40% and Hold at target by 60%;
removing Hold added flips, and the two levies took them back to 20 and 15 (A to B to A within 10 minutes: 29 down to 8,
and 33 down to 3).

**Ablation** (2.2 beta with one existing setting changed, same days; real / synthetic adj, modes, flips):

| Change | Result |
|---|---|
| `switch_cost_p` 2 to 4 | identical |
| `switch_cost_p` 0 | much worse (160 and 239 modes): needed |
| `replan_on_level` off | much worse on synthetic days (328 modes, 146 flips): needed |
| `prefer_self_use` on | identical: it does nothing once Hold is for the car and reserve only |
| `min_dwell_s` 0 | about the same (peak import differs): kept at 120 |
| `min_dwell_s` 300 | fewer modes and flips, dearer (-5.90, -13.52) |
| `price_band_p` 1 | steadier on real days (85 modes, 14 flips), dearer (-5.79, -13.68) |
| `level_band_pct` 1 | steadier on real days, much worse on synthetic days (121 modes, 42 flips) |

Only `prefer_self_use` is redundant; the profile leaves it as 2.1 beta has it.

## The rule and an announced grid event

`charge_max_import_p` would keep the battery from being filled for an announced event: on winter-like days (dull sun, no
daytime slots, house load x1.4) the plan bought nothing and the battery, run down to 12-42% by 17:00, sold what little it
had. An event pays `event_value_p` plus the export rate (115p) against about 34p to put a kWh in and take it out. So
`eventcharge.py` lifts the rule for the stretch in which the charge has to happen, worked out at every revalue from the
plan made with the rule in force: what the event takes from the battery (sale power x hours / discharge efficiency), the
level it needs (event floor + that + 5 points, `EVENT_MARGIN_PTS`), the level the plan reaches at its start, and the
charger's rate (85% of full power). The window opens 30 minutes before the latest safe start and the charge may not go
above the needed level (`Segment.event_charge_to`, a ceiling for those segments). Once open it stays open (`latch`). No
setting. A fixed six-hour window charged as soon as the event was announced and ran the house through the charger.

Seven winter-like days (6 usable), adjusted cost: one-hour event 17:00 announced 13:00: 2.22 (rule in force), -5.51 (six hours),
-6.76 (calculated window and cap); dear-rate charge 0 / 50.9 / 17.6 kWh. Two-hour event: -1.94 / -24.45 / -24.71, dear charge
0 / 54.3 / 40.8 kWh. Eight October days with an event added every evening (the battery already full): 20:00-21:30 -66.64
against -66.17, 17:00-18:30 -64.35 against -64.42. An unannounced event is not planned for: buying at about 34p against a
15-30% chance of 108p loses on average (`late_events`, tested in October 2026, was dearer in every run).

## Not tested (backlog item "Engine 2.2 beta: what still needs testing")

See `docs/history/backlog-and-recent-fixes.md`.
