# Low-write mode (issue #189)

> **Status (3 Oct 2026).** Design only. Built so far: `tools/low_write_study.py`, which runs the real optimiser over the demo
> pack's recorded days to put numbers on the options below (and a test that keeps it working). No change to what PowerEngine
> does. Nothing in this plan applies to a RAM-control install such as the owner's Solis on 420044: RAM control is not worn by
> writes and is never capped. This is for inverters whose only way of being driven is persistent (EEPROM or flash) settings.

## The problem

A dynamic plan changes its mind many times a day, and on an inverter driven through timed windows every change is a write to
memory with a limited life. No manufacturer publishes limits; the only figure found is "typically 100,000" cycles, and a community
guess puts cheaper parts at 10,000 (the UK survey in `making-it-generic.md`). At 10 writes a day per register that is about 27 years
on a 100k part and about 2.7 years on a 10k part. So the working target is **about 10 counted writes a day, as a long-run average**,
lower (4) where the brand is unknown.

Today's protection is blunt: `max_writes_per_day` (default 150) pauses control for the rest of the day when reached, which hands the
battery back to Self-Use just when the evening peak arrives. There is a price per window change in the optimiser
(`window_switch_cost_p`, 5p), a restart hold-off and burst damping, and the three-slot strategy that programs a normal night once.
None of them says what the day is *worth*, so none of them can trade writes against money.

## What counts as a write

The unit is the **counted write** the app already keeps (the journal's `own_today`, shown as "Inverter writes today" on the Health
tab): a write to an inverter control entity that is not staged in Home Assistant and not RAM. Window times are staged until the
update button sends them, so a window change costs the button press (one per changed slot) plus a current or mode write where those
change. That is **1 to 3 counted writes per window change**, depending on the strategy and how many slots change at once; the first
step below measures it on real installs instead of assuming it. Whether the inverter wears per register or per page, and whether a
button press that sends a slot's eight registers is one write or several, is unknown. Counting at the entity level is the
conservative choice. A budget of 10 therefore means roughly 5 window changes a day.

## Evidence: what survives when the plan changes its mind less often

`tools/low_write_study.py` plans each recorded demo-pack day (sunny, dull, Axle event, car) with the real optimiser, at several prices
per window change, and for five tiers of what the plan may do. Savings are against plain self-use with Axle events and free-power
sessions honoured (the cost of a day in which PowerEngine does no planning at all). 18 kWh battery, 4.8 kW, EDF-shaped tariff
(7p overnight and smart slots, 30p peak, 15p export). Window changes include the baseline's own (about 1 a day, from the two event
days).

| Tier | What the plan may do | Saves GBP/day at 10p per change | Window changes/day |
|---|---|---|---|
| T0 | Self-use, events honoured (baseline) | 0 | 1.0 |
| T1 | + grid charge in the fixed overnight window | 0.79 | 4.0 |
| **T2** | **+ arbitrage inside the overnight window (sell early in it, refill before it closes)** | **1.43** | **5.2** |
| T3 | + grid charge and car-slot holds anywhere (smart slots) | 1.37 | 6.0 |
| T4 | + arbitrage anywhere (the full plan) | 1.52 | 5.8 |

The same study at a cheap 2p per change gives the full plan GBP 1.77 for 8.5 changes; at 20p, 1.08 for 4.2; at 40p, 0.86 for 3.5.
Findings:

1. **The overnight cycle is most of the value.** T2 keeps 83% to 94% of what the full plan earns at 2p to 10p per change (and more
   than the full plan at 20p and above, which can no longer afford itself) for roughly the same number of window changes. The
   arbitrage that pays here is selling stored energy at the start of the cheap overnight window and refilling before it closes
   (`XXXXXCCCC...`), where the refill is guaranteed. It adds GBP 0.64 for 1.2 extra changes.
2. **Daytime smart-slot top-ups are worth nothing here.** T3 saves less than T2 while costing about 0.8 more changes: the battery
   is full from the night or from solar, so charging in a 7p daytime slot adds little. The car-slot rule (the battery must not feed
   the car, so it holds) is itself a write driver.
3. **Daytime arbitrage barely appears.** With a 15p export rate against a 30p peak the plan keeps the stored energy for the house.
   T4 adds 0.09 over T2 at 10p and 0.30 at 2p, for 3 more changes at 2p.
4. **Value per write falls steeply.** The first 3 changes (the overnight charge) earn about 0.26 each; the next 1.2 (overnight cycle)
   about 0.5 each; beyond that about 0.1 each or less. A flat daily quota wastes the high-value ones on low-value days.
5. **Axle days dominate.** The event day saves GBP 1.8 (T1) to 2.7 (T4) on its own: being full before an event, and discharging
   in it, is worth far more per write than anything else, and should never be refused.

Caveats: four recorded September days from one house, so an indication rather than a forecast. The cost of a window change in the
study is a price, not a measured wear cost. Winter days have no stored solar to sell at midnight, so the overnight cycle will
often not occur (the optimiser simply does not choose it). The study never reduces the standing safety: the reserve, the
refill guarantee and the sale floor are the planner's own.

## Options

**A. Overnight schedule only (T1).** One recurring night window; self-use all day, the inverter's own self-use covers the peak.
About 3 changes a day beyond events, GBP 0.79/day here (about half the full plan). Simplest, cheapest, the right default where the
brand is unknown. A night's schedule repeats, so a normal night is zero writes if the three-slot strategy leaves it programmed.

**B. Overnight cycle (T2). Recommended base.** A plus the sell-early / refill-before-close cycle inside the fixed overnight window.
About 5 changes a day, about 10 counted writes, and the bulk of the benefit. The refill is guaranteed, so the planner's own safety
(the sale floor, the refill guarantee) already applies.

**C. Priced writes with a credit balance (the user's rolling average).** A token bucket earns the budget (10 a day) continuously and
holds up to a few days' worth. Unused credit from a quiet day is spent on a valuable one, and the long-run average never exceeds
the budget. The balance is not a gate but a **price**: it sets the optimiser's price per window change, high when credit is low and
low when it is plentiful, so the plan spends credit only where the money is. That is what makes the tiers above fall out
automatically: Axle days and the overnight cycle clear the price, daytime top-ups mostly do not. Details below.

**D. Arbitrage band derived from value per write.** Replace the fixed band (75% to 90%) with the narrowest band whose cycle is worth
its writes, so the battery size and the spread decide, not a number someone guessed. Worked below. A small battery or a thin
spread switches overnight arbitrage off with the reason shown.

**E. Leave the daytime to the inverter.** Daytime charging and selling are the expensive end of the curve. Options inside that:
(i) no daytime grid charging unless the forecast says the battery will run out before the evening peak (a priced option under C);
(ii) do not hold the battery during a daytime car smart slot (let it help the car; a setting, because it costs stored energy);
(iii) merge two windows separated by a short gap when the gap is cheaper to charge through than to switch twice.

**F. Plan commitment.** Once a window is programmed, keep it unless the new plan beats it by more than the price of the writes
(a value-of-change test rather than only the existing 30-minute tolerance), and bridge short gaps. This is the same price, used on
changes to an existing window.

**G. Use a target-SoC register where the inverter has one.** Some inverters take a charge-to-SoC number as well as window times.
Changing the target is one write, instead of opening and closing a window to stop at the right level. A definition could name the
role (`timed_slots.soc_limit_role`). To be confirmed per brand; not assumed for Solis.

**H. Prove it in shadow first.** The owner's install drives the inverter by RAM, so it cannot show real EEPROM wear, but it can run
the low-write plan beside the live one and report what it *would* do: window changes, counted writes and the cost difference in
money, on the owner's own tariff and days, with no risk. The same shadow on a tester's EEPROM install gives the real writes per
window change. Built on the existing dampening shadows (`writes.would`).

Not recommended: a fixed daily quota (wastes the quiet days, forces the valuable ones to wait), and a hard gate alone (what we have:
it throws away the evening).

## Proposed design

**Profiles.** A setting `write_profile` for installs whose definition has no RAM method (`auto` picks it from the definition's
`storage`): *conservative* (A), *balanced* (B, the default) or *full* (C with all tiers priced). RAM-control installs ignore it.
Plus `write_budget_per_day` (default 10; 4 for a brand with no evidence) and `write_credit_days` (default 3).

**The credit balance.** `balance = min(cap, balance + budget * dt) - counted writes`, with `cap = write_credit_days * budget`. It is
derived from the journal's counted writes, not a second count, so the two cannot disagree, and is kept in a small file beside the
config (like `site_state.json`), so a restart keeps it. A new install starts at half the cap.

**The price.** For the optimiser: price per window change in pence `P(b) = P_min + (P_max - P_min) * (1 - b)^2`, `b = balance / cap`,
`P_min = 2`, `P_max = 60`, times the writes-per-change in use. At full credit a change costs about 2p, at half about 16p, near empty
about 55p. The balance settles where the price equals the marginal value of a change at the budgeted rate, about 10p on the
study's days, so `b` sits near 0.6. No other knob. Mid-window changes use the existing `stick` cost.

**What is never refused.** (1) Safety: leaving Active, pause, handing back to Self-Use, failsafe. (2) Paid events (Axle, free power):
may overdraw the balance down to minus one budget, repaid by later days. Everything else is priced. The hard daily
`max_writes_per_day` stays as the backstop (default 3 times the budget in this mode), and a refused or deferred discretionary
change is explained on the Health tab ("not worth 14p per change today: credit 6 of 30").

**A reserve for tonight.** Discretionary changes are not allowed to leave less than the writes the overnight cycle needs (about 6),
so a busy afternoon cannot starve the night.

**The arbitrage band.** Per kWh moved, the cycle earns `export * eta - refill / eta - wear` (here 15 * 0.95 - 7 / 0.95 - 2 = 4.9p) and
costs about 3 window changes. The band is the narrowest `delta` with `delta * capacity * 4.9p >= k * 3 * P`, `k` = 1 for break-even
(2 to be comfortable), limited to the range between the reserve and the target. At 10p per change:

| Usable battery | Break-even band (k = 1) | Comfortable band (k = 2) | Value of a 70% cycle |
|---|---|---|---|
| 5 kWh | 123% (never) | never | GBP 0.17 |
| 10 kWh | 61% | never | GBP 0.34 |
| 13.5 kWh | 46% | 91% | GBP 0.46 |
| 18 kWh | 34% | 68% | GBP 0.62 |
| 30 kWh | 20% | 41% | GBP 1.03 |

So a bigger battery or a wider spread makes a *narrower* band worth cycling, and a small battery never cycles. That is the answer
to "different battery sizes have a massive effect": the same rule gives a different band, or none, for each home.

**Daytime.** Under C a daytime grid charge or sale happens only if it clears the current price: a forecast shortfall before the
evening peak, a free-power session, an Axle event. Otherwise the inverter's own self-use does the work.

**Where it lives.** `pe_core/writecredit.py` (pure: the bucket, the price, the band, persistence helpers); the planner's `Params`
gets its price per change from it; the app computes the price once per cycle and logs the balance; `config.py` gets the settings (and
the card's section lists); `docs/INVERTERS.md` gets a definition key for the brand's default profile and budget, and an optional
`soc_limit_role`.

## Stages

Each stage is small, passes the replay unchanged for RAM installs, and can be stopped after.

- **L0. Study and plan. Done.** `tools/low_write_study.py`, this document.
- **L1. Shadow accounting.** Count real counted writes per window change on timed-window installs, and run the low-write plan in
  shadow beside the live plan (any control method), reporting would-be window changes, writes and the cost difference on the
  Health tab and in the diagnostics export. No behaviour change. Gives the owner his own numbers.
- **L2. The credit balance and the price,** for installs without RAM control, behind `write_profile`. Replaces the blunt pause with
  the price; keeps the backstop.
- **L3. The overnight-cycle tier and the derived band,** and the daytime rules.
- **L4. Card, wizard and docs.** The profile and budget in the wizard (EEPROM-only definitions default to *balanced*), the balance
  and price on Health, the install guide's "conservative plan" warning replaced by the real behaviour. Then a supervised trial on an
  EEPROM-only tester's inverter, Passive first.

## Tests

The study as a regression (the tiers order by value and by window changes; a dearer change means fewer). Property tests for the bucket
(never above the cap, never below the overdraft floor, the average over many days at or under the budget when fed a busy demand).
A multi-day run of the demo days looped through a bucket, counting writes. The replay unchanged with the mode off; a timed-window
replay with the mode on, pinned.

## Open questions for the owner

- Is **10 counted writes a day** (about 5 window changes) the right default, with 4 for unknown brands, and a credit of 3 days?
- Is it acceptable for **paid events to overdraw** the balance by a day's worth? (They are worth far more than the wear.)
- Should **daytime smart-slot holds** (the battery not feeding the car) be off by default in low-write mode?
- Do you want L1 (shadow on your own install) first, so the choice of profile is made on your own tariff and days?
- Is there a tester with an EEPROM-only inverter (GivEnergy, Sunsynk/Deye) who could run the supervised trial, or does it wait?
