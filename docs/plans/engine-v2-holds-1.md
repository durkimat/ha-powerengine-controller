# Engine v2: every hold of the new approach on the seven test days

The new approach (follow-the-plan executor, level curves, slot curve, 30-minute steps) on the five synthetic and real days that
hold at all: each Hold of a minute or more, the conditions at that moment, and why the engine held. See
[the recap](engine-v2-recap-1.md) for the configuration and [the architecture page](engine-v2-architecture.md) for the terms.

**53 holds, 20.8 hours** (current engine: 10.1 h over the same days, 4 h of it at the reserve at 28.84p).

## By cause

| Cause | Holds | Hours | Share |
|---|---|---|---|
| Spare sun sold, not stored | 25 | 15.9 | 76% |
| Car charging | 4 | 1.9 | 9% |
| Pause between charge and sale | 18 | 1.8 | 9% |
| Held up by the bottom curve | 1 | 1.0 | 5% |
| Stored kWh worth more than running the house | 4 | 0.1 | 1% |
| At the reserve | 1 | 0.1 | 0% |

Conditions columns: level and prices are at the start of the hold; sun and house are that half-hour's average kW; value and use line
are the engine's own figures (pence per stored kWh) in its journal when the mode changed ('-' where the journal row was not kept).

## Every hold

| Day | Time | Min | Level | Import / export p | Sun / house kW | Value / use line p | Cause | Why the engine held |
|---|---|---|---|---|---|---|---|---|
| 27 Aug | 01:49-01:51 | 2 | 89% | 6.7 / 15.0 | 0.0 / 0.8 | 8.8 / 6.3 | Stored kWh worth more than running the house | A stored kWh is worth 8.8p, above the 6.3p it would save covering the house at 6.7p, so the grid runs the house. |
| 27 Aug | 03:23-03:30 | 7 | 52% | 6.7 / 15.0 | 0.0 / 0.5 | 13.2 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 7 min). |
| 27 Aug | 03:58-04:00 | 2 | 38% | 6.7 / 15.0 | 0.0 / 0.6 | 12.8 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 27 Aug | 08:44-09:00 | 16 | 65% | 28.8 / 15.0 | 2.0 / 0.5 | 14.2 / 27.4 | Spare sun sold, not stored | Sun 2.0 kW against house 0.5 kW, export 15p; a stored kWh is worth 14.2p, under the 15.8p it needs to beat exporting the sun. |
| 27 Aug | 11:01-11:54 | 53 | 65% | 6.7 / 15.0 | 3.8 / 0.4 | 13.5 / 6.3 | Spare sun sold, not stored | Sun 3.8 kW against house 0.4 kW, export 15p; a stored kWh is worth 13.5p, under the 15.8p it needs to beat exporting the sun. |
| 27 Aug | 12:25-14:00 | 95 | 66% | 28.8 / 15.0 | 3.0 / 0.3 | 15.2 / 27.4 | Spare sun sold, not stored | Sun 3.0 kW against house 0.3 kW, export 15p; a stored kWh is worth 15.2p, under the 15.8p it needs to beat exporting the sun. |
| 20 Aug | 01:49-01:51 | 2 | 89% | 6.7 / 15.0 | 0.0 / 0.8 | 8.8 / 6.3 | Stored kWh worth more than running the house | A stored kWh is worth 8.8p, above the 6.3p it would save covering the house at 6.7p, so the grid runs the house. |
| 20 Aug | 03:23-03:30 | 7 | 52% | 6.7 / 15.0 | 0.0 / 0.5 | 13.2 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 7 min). |
| 20 Aug | 03:58-04:00 | 2 | 38% | 6.7 / 15.0 | 0.0 / 0.6 | 12.8 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 20 Aug | 08:44-09:00 | 16 | 65% | 28.8 / 15.0 | 2.0 / 0.5 | 14.2 / 27.4 | Spare sun sold, not stored | Sun 2.0 kW against house 0.5 kW, export 15p; a stored kWh is worth 14.2p, under the 15.8p it needs to beat exporting the sun. |
| 20 Aug | 11:01-11:54 | 53 | 65% | 6.7 / 15.0 | 3.8 / 0.4 | 13.5 / 6.3 | Spare sun sold, not stored | Sun 3.8 kW against house 0.4 kW, export 15p; a stored kWh is worth 13.5p, under the 15.8p it needs to beat exporting the sun. |
| 20 Aug | 12:25-13:00 | 36 | 66% | 28.8 / 15.0 | 3.0 / 0.3 | 15.2 / 27.4 | Spare sun sold, not stored | Sun 3.0 kW against house 0.3 kW, export 15p; a stored kWh is worth 15.2p, under the 15.8p it needs to beat exporting the sun. |
| 20 Aug | 19:00-20:02 | 62 | 30% | 28.8 / 15.0 | 0.0 / 1.4 | 39.1 / 27.4 | Held up by the bottom curve | Battery at 30%: a stored kWh is worth 39.1p, above the 27.4p the battery would save at 28.8p, so the grid runs the house (the bottom curve raises the value of low-level energy). |
| 3 Sep | 01:49-01:51 | 2 | 89% | 6.7 / 15.0 | 0.0 / 0.8 | 8.8 / 6.3 | Stored kWh worth more than running the house | A stored kWh is worth 8.8p, above the 6.3p it would save covering the house at 6.7p, so the grid runs the house. |
| 3 Sep | 03:23-03:30 | 7 | 52% | 6.7 / 15.0 | 0.0 / 0.5 | 13.2 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 7 min). |
| 3 Sep | 03:58-04:00 | 2 | 38% | 6.7 / 15.0 | 0.0 / 0.6 | 12.8 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 3 Sep | 08:44-09:00 | 16 | 65% | 28.8 / 15.0 | 2.0 / 0.5 | 14.2 / 27.4 | Spare sun sold, not stored | Sun 2.0 kW against house 0.5 kW, export 15p; a stored kWh is worth 14.2p, under the 15.8p it needs to beat exporting the sun. |
| 3 Sep | 11:01-11:54 | 53 | 65% | 6.7 / 15.0 | 3.8 / 0.4 | 13.5 / 6.3 | Spare sun sold, not stored | Sun 3.8 kW against house 0.4 kW, export 15p; a stored kWh is worth 13.5p, under the 15.8p it needs to beat exporting the sun. |
| 3 Sep | 12:20-13:00 | 40 | 68% | 28.8 / 15.0 | 3.0 / 0.3 | 14.3 / 27.4 | Spare sun sold, not stored | Sun 3.0 kW against house 0.3 kW, export 15p; a stored kWh is worth 14.3p, under the 15.8p it needs to beat exporting the sun. |
| 10 Sep | 03:23-03:27 | 4 | 53% | 6.7 / 15.0 | 0.0 / 0.5 | 12.8 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 4 min). |
| 10 Sep | 08:30-10:21 | 111 | 85% | 28.8 / 15.0 | 0.9 / 0.5 | 14.8 / 27.4 | Spare sun sold, not stored | Sun 0.9 kW against house 0.5 kW, export 15p; a stored kWh is worth 14.8p, under the 15.8p it needs to beat exporting the sun. |
| 10 Sep | 10:30-11:23 | 53 | 89% | 28.8 / 15.0 | 1.4 / 0.4 | 14.5 / 27.4 | Spare sun sold, not stored | Sun 1.4 kW against house 0.4 kW, export 15p; a stored kWh is worth 14.5p, under the 15.8p it needs to beat exporting the sun; the battery is at 89%, at the top curve's 90% start, where holding more is penalised. |
| 10 Sep | 11:25-11:30 | 4 | 93% | 28.8 / 15.0 | 1.7 / 0.4 | 6.1 / 27.4 | Spare sun sold, not stored | Sun 1.7 kW against house 0.4 kW, export 15p; a stored kWh is worth 6.1p, under the 15.8p it needs to beat exporting the sun; the battery is at 93%, at the top curve's 90% start, where holding more is penalised. |
| 10 Sep | 11:35-12:02 | 27 | 91% | 28.8 / 15.0 | 1.3 / 0.4 | 12.7 / 27.4 | Spare sun sold, not stored | Sun 1.3 kW against house 0.4 kW, export 15p; a stored kWh is worth 12.7p, under the 15.8p it needs to beat exporting the sun; the battery is at 91%, at the top curve's 90% start, where holding more is penalised. |
| 10 Sep | 12:08-13:30 | 82 | 90% | 28.8 / 15.0 | 0.9 / 0.3 | -5.0 / 27.4 | Spare sun sold, not stored | Sun 0.9 kW against house 0.3 kW, export 15p; a stored kWh is worth -5.0p, under the 15.8p it needs to beat exporting the sun; the battery is at 90%, at the top curve's 90% start, where holding more is penalised. |
| 7 Oct | 02:25-02:30 | 4 | 55% | 6.7 / 15.0 | 0.0 / 0.5 | 11.6 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 4 min). |
| 7 Oct | 05:58-06:00 | 2 | 93% | 6.7 / 15.0 | 0.0 / 0.5 | 4.3 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 7 Oct | 22:56-23:00 | 4 | 15% | 28.8 / 15.0 | 0.0 / 0.7 | 13.3 / 27.4 | At the reserve | The battery is at its 15% reserve: nothing may discharge it (a hard limit). |
| 8 Oct | 03:26-03:30 | 3 | 52% | 6.7 / 15.0 | 0.0 / 0.5 | 13.1 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 3 min). |
| 8 Oct | 03:57-04:00 | 2 | 38% | 6.7 / 15.0 | 0.0 / 0.6 | 12.4 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 8 Oct | 08:00-08:49 | 50 | 86% | 28.8 / 15.0 | 2.1 / 0.6 | 14.7 / 27.4 | Spare sun sold, not stored | Sun 2.1 kW against house 0.6 kW, export 15p; a stored kWh is worth 14.7p, under the 15.8p it needs to beat exporting the sun. |
| 8 Oct | 09:00-09:20 | 21 | 88% | 28.8 / 15.0 | 2.8 / 0.5 | 12.2 / 27.4 | Spare sun sold, not stored | Sun 2.8 kW against house 0.5 kW, export 15p; a stored kWh is worth 12.2p, under the 15.8p it needs to beat exporting the sun; the battery is at 88%, at the top curve's 90% start, where holding more is penalised. |
| 8 Oct | 09:31-10:02 | 31 | 88% | 28.8 / 15.0 | 3.0 / 0.5 | 12.6 / 27.4 | Spare sun sold, not stored | Sun 3.0 kW against house 0.5 kW, export 15p; a stored kWh is worth 12.6p, under the 15.8p it needs to beat exporting the sun. |
| 8 Oct | 10:47-12:01 | 74 | 73% | 28.8 / 15.0 | 3.1 / 0.4 | 15.4 / 27.4 | Spare sun sold, not stored | Sun 3.1 kW against house 0.4 kW, export 15p; a stored kWh is worth 15.4p, under the 15.8p it needs to beat exporting the sun. |
| 8 Oct | 12:04-12:25 | 21 | 90% | 28.8 / 15.0 | 2.4 / 0.3 | - | Spare sun sold, not stored | Sun 2.4 kW against house 0.3 kW, export 15p; the battery is at 90%, at the top curve's 90% start, where holding more is penalised. |
| 8 Oct | 12:26-12:30 | 3 | 93% | 28.8 / 15.0 | 2.4 / 0.3 | 10.8 / 27.4 | Spare sun sold, not stored | Sun 2.4 kW against house 0.3 kW, export 15p; a stored kWh is worth 10.8p, under the 15.8p it needs to beat exporting the sun; the battery is at 93%, at the top curve's 90% start, where holding more is penalised. |
| 8 Oct | 12:39-13:59 | 81 | 89% | 28.8 / 15.0 | 2.0 / 1.9 | -2.1 / 27.4 | Spare sun sold, not stored | Sun 2.0 kW against house 1.9 kW, export 15p; a stored kWh is worth -2.1p, under the 15.8p it needs to beat exporting the sun; the battery is at 89%, at the top curve's 90% start, where holding more is penalised. |
| 8 Oct | 17:31-17:58 | 28 | 80% | 6.7 / 15.0 | 0.1 / 2.4 | 15.8 / 27.4 | Car charging | The car is charging (2.4 kW house load includes it): the battery is held so it never feeds the car; the grid supplies at 6.7p. A hard limit. |
| 8 Oct | 19:31-20:00 | 29 | 92% | 6.7 / 15.0 | 0.0 / 8.3 | 10.3 / 6.3 | Car charging | The car is charging (8.3 kW house load includes it): the battery is held so it never feeds the car; the grid supplies at 6.7p. A hard limit. |
| 8 Oct | 20:02-20:30 | 28 | 93% | 6.7 / 15.0 | 0.0 / 5.3 | 8.0 / 6.3 | Car charging | The car is charging (5.3 kW house load includes it): the battery is held so it never feeds the car; the grid supplies at 6.7p. A hard limit. |
| 8 Oct | 21:44-22:00 | 15 | 79% | 6.7 / 15.0 | 0.0 / 0.6 | 12.8 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 15 min). |
| 9 Oct | 06:11-06:30 | 19 | 89% | 6.7 / 15.0 | 0.0 / 0.8 | 11.4 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 19 min). |
| 9 Oct | 06:53-06:56 | 3 | 77% | 6.7 / 15.0 | 0.0 / 0.5 | 12.8 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 3 min). |
| 9 Oct | 07:40-08:00 | 20 | 89% | 6.7 / 15.0 | 0.0 / 0.8 | 12.3 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 20 min). |
| 9 Oct | 09:28-09:30 | 2 | 76% | 28.8 / 15.0 | 0.4 / 0.8 | 15.0 / 27.4 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 9 Oct | 10:26-10:30 | 3 | 54% | 28.8 / 15.0 | 1.5 / 0.5 | 14.2 / 27.4 | Spare sun sold, not stored | Sun 1.5 kW against house 0.5 kW, export 15p; a stored kWh is worth 14.2p, under the 15.8p it needs to beat exporting the sun. |
| 9 Oct | 11:00-11:02 | 2 | 39% | 6.7 / 15.0 | 1.0 / 0.6 | 12.5 / 27.4 | Spare sun sold, not stored | Sun 1.0 kW against house 0.6 kW, export 15p; a stored kWh is worth 12.5p, under the 15.8p it needs to beat exporting the sun. |
| 9 Oct | 12:33-12:39 | 6 | 50% | 6.7 / 15.0 | 1.1 / 0.6 | 14.5 / 27.4 | Spare sun sold, not stored | Sun 1.1 kW against house 0.6 kW, export 15p; a stored kWh is worth 14.5p, under the 15.8p it needs to beat exporting the sun. |
| 9 Oct | 12:40-12:46 | 6 | 50% | 6.7 / 15.0 | 1.1 / 0.6 | 28.9 / 27.4 | Spare sun sold, not stored | Sun 1.1 kW against house 0.6 kW, export 15p; a stored kWh is worth 28.9p, under the 15.8p it needs to beat exporting the sun. |
| 9 Oct | 14:27-14:29 | 2 | 95% | 6.7 / 15.0 | 0.5 / 0.7 | 0.7 / 6.3 | Stored kWh worth more than running the house | A stored kWh is worth 0.7p, above the 6.3p it would save covering the house at 6.7p, so the grid runs the house. |
| 9 Oct | 15:47-15:54 | 6 | 85% | 6.7 / 15.0 | 0.4 / 0.8 | 5.1 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 6 min). |
| 9 Oct | 16:58-17:00 | 2 | 79% | 6.7 / 15.0 | 0.2 / 0.9 | 13.1 / 6.3 | Pause between charge and sale | A charge or sale reached its level inside the step; the plan idles until the next step (a tie, 2 min). |
| 9 Oct | 22:00-22:30 | 30 | 94% | 6.7 / 15.0 | 0.0 / 1.0 | 4.6 / 6.3 | Car charging | The car is charging (1.0 kW house load includes it): the battery is held so it never feeds the car; the grid supplies at 6.7p. A hard limit. |

## Reading it

- **Spare sun sold, not stored** is three quarters of the hold time. Daytime, sun above the house's load, export at 15p, and a stored kWh worth
  within a couple of pence of the 15.8p it would need to beat exporting: the engine is nearly indifferent, and exports the sun. In the 20 to 40
  holds at about 88 to 93% (10 Sep, 8 Oct) the battery is sitting at the top curve's 90% start, so storing more is penalised and Hold is how
  the ceiling is kept. The value reported there can be negative: energy above 90% costs more to hold than it is worth.
- **A hold with the sun is not a missed sale** where the stored energy has a later use at the 27.4p it saves or an announced event to sell into (10 Sep);
  it is worth checking where the battery is high with an hour of dear import behind it and no later need.
- **Car** holds are a hard limit. **Pauses** are 2 to 20 minute ties between equal-priced steps.
- **The bottom curve** holds the 20 Aug evening at 30% (an hour at 28.84p) rather than discharge further: its price, in the plan's terms.
- The overnight holds on 27 Aug, 20 Aug and 3 Sep are identical because the three days share one night (the same 8 Oct house load and prices).
