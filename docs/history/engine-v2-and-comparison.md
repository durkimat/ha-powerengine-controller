# Engine pages, same-day comparison and engine v2

Moved out of CLAUDE.md (Oct 2026) to keep every session's context small. Read this file only when the task touches this area. The rules that must always hold are listed in CLAUDE.md under "Rules that live in the history files".

## Engine pages and same-day comparison (built for 0.9.110)

Plan `docs/plans/engine-pages-and-comparison.md` (section 6: as built). The Plan tab is titled **Engine v1** (path `plan` kept), Plan history
is at its bottom, and the Engine v2 page ends with its own history; both carry `powerengine-engine-badge-card` (Active / Paused / Passive) and
the card's icons `pe:engine-v1` / `pe:engine-v2` (`window.customIcons`).
- **Forecast snapshots** (`pe_core/fcsnap.py`, `<costs>/snapshots/YYYY-MM-DD.json`, 14 days): deltas of the raw states of the roles that
  carry future information, the house profile and smart slots' first-seen times; a full entry at the first cycle of each day. Never in a demo.
- **Snapshot size:** a real day is 1 to 1.5 MB (the EDF dispatch list is 13 KB and changes ~40 times a day, each Solcast entity 12 KB); `fcsnap.SOFT_BYTES` 3 MB / `HARD_BYTES` 5 MB (0.9.114; the first build's 800 KB / 1 MB cut a real day off in the evening).
- **Inputs missing under RAM remote control (0.9.114, `_leave_active`, `_ram_off_due`, `_ram_grace_s`):** a power sensor blipping "not ready" used to switch remote control Off at once (7 Oct 2026: 40 times overnight, one per 5 minute check, each 27 s). Now remote control carries on for `RAM_INPUT_GRACE_SECONDS` (90 s; less when `ram_refresh_min` is long, none at 4) and writes nothing if the inputs return; still Off at once for Pause, Passive, a guard or a config error. The warning now names why each input is not ready (`_why_not_ready`).
- **Cost records** carry `engine` and `live`; `costbook.day_engine(_info)`. v1's "As run" (`_record_ran`) is written only on engine v1.
- **Engine v2 history** (`pe_core/v2history.py`, `<costs>/v2history/`, 30 days; `sensor.pe_v2_history`, event `pe_v2_history_day`).
- **Same-day comparison** (`pe_core/compare/`): nightly at 03:20 the app starts `python -m pe_core.compare.run` as a **subprocess**
  (90-minute limit, one at a time, yesterday plus one missing day of the last week) that replays the whole app in the demo world on
  yesterday's records and snapshot, v1 and v2, self-use and the bound, plus calibration against the meter; results in `<costs>/compare/`,
  `sensor.pe_cost_engines`, the Costs page's "Engines compared, same day". Feature `engine_compare` (on). A day with no snapshot house
  profile by 00:30 is refused (else the replay would know the load). The fake AppDaemon moved to `pe_core/compare/harness.py`
  (`tests/replay_harness.py` and `tools/engine_compare.py` use it). About 3 minutes a day here. **Watch** the first results: the
  calibration gap, `took_s` on the owner's host, and that the first full day after release is compared.

## Current work: engine v2 (built for 0.9.106)

Design `docs/plans/engine-v2.md` (owner's decisions in its status box and section 18), build plan `docs/plans/engine-v2-build.md`.
A second, selectable engine: system setting `engine` (`v1` default, `v2`); v2's own settings are the `engine_v2:` block
(`pe_core/engine_v2/settings.py`; missing keys seeded once from v1's equivalents: arbitrage, events, free power, reserve). New shared
safety setting `battery_floor_soc` (12 %, the BMS's own limit; v2's hard floor, v1 does not read it).

- **Soft top and steady legs (0.9.113, `docs/plans/engine-v2.md` 18a, from the first live evening):** `top_up_cost_p` (5p/kWh) prices grid energy charged
  above `comfort_high_soc` (inside the comfort figure; seeded from v1 `arbitrage_max_soc`; the early charge of a stretch only goes up to the top);
  `reversal_cost_p` (3p) priced charge-to-sale turns in the plan and in `_worth_the_change` (retired in 0.9.126: `switch_cost_p`, 2p, now prices every change alike); a running leg goes on to its plan step's end when both
  pay (`Executor._leg_going`); "learned" revalues only on a 1% fact or 1 day change (`triggers.learned_moved`). Closed-loop check: `tests/evening_world.py`.
- **How it decides:** `pe_core/engine_v2/` (pure). `forecast.py` cuts the look-ahead into segments at the data's own times (a smart slot's
  real minutes) with low/mid/high sun and house; `value.py` is a stochastic dynamic programme for the value of a stored kWh (smart slots are
  two price outcomes known before the choice), giving `Lines` (value against prices turned into lines after losses), a charge target and an
  expected timeline; `rules.py` one rules core for plan and live (grid event > override > free power > car > reserve; events may go below the
  owner's reserve to the hard floor + 1); `execute.py` the mode automaton (exit conditions, price and level bands, minimum time, deadlines that
  only re-check); `observe.py` filtered battery level and events; `triggers.py` revalue only on events (coalesced; 2 h backstop);
  `learning.py` scenario weights, solar bias, load spread, level offset; `engine.py` `EngineV2.step` never raises; `publish.py` the sensors.
- **Charge early within a cheap stretch:** the DP decides how much (ties not bought); `value.run_target` / `Lines.charge_now` start it at
  the beginning of the stretch at one price, not at its end.
- **In the app:** `_engine_tick` (every `sample_s`, a no-op on v1) steps the engine; the 30 s `_cycle` keeps its bookkeeping and still makes
  v1's plan (a comparison) but does not decide with v2; both engines' decisions go through `_hand_over` (activity log, would-writes,
  `_control`), so RAM control, BMS and fuse caps, following check and write budget are shared. **v2 needs RAM remote control:** on timed
  windows Active is refused ("Engine v2 needs RAM remote control"). State in `engine_v2_state.json` beside the config. Diagnostics export
  section `engine_v2`; `tools/diag_summary.py` prints it.
- **Sensors (the card's contract, `engine-v2-build.md`):** `sensor.pe_state_engine`, `pe_v2_mode`, `pe_v2_value`, `pe_v2_timeline`,
  `pe_v2_value_curve`, `pe_v2_triggers`, `pe_diag_v2`, `pe_diag_v2_settings`. Card: `powerengine-engine-card`, `powerengine-v2-plan-card`,
  `powerengine-v2-health-card` on the dashboard's **Engine v2** page (after Plan); the config card has the engine choice (confirm both ways)
  and Your house / Engine v1 / Engine v2 groups, sending `engine`/`engine_v2` only when the app publishes `pe_diag_v2_settings`.
  `MIN_CARD_VERSION` 0.9.106. The existing pages don't switch with the engine yet (a later release).
- **Comparison:** `tools/engine_compare.py` runs the whole app closed loop in the demo world on the pack's days, v1 against v2, with a
  perfect-foresight bound.
- **Preview (0.9.107):** on engine v1 `_engine_tick` still steps v2 with `Situation.active=False` (reason "Preview: engine v1 is in control") and publishes the v2 sensors (`v2_mode.preview`, `state_engine` attribute `v2_preview`), but never reaches `_hand_over`, `_control`, `_note_command`, the activity log or `self._decision`; setting `preview_when_v1` (default on) turns it off. v1's `_cycle` decides as before.
- **Not done / to watch:** not yet run on a live HA; `sample_s` changes need a restart; `band_exit` may revalue often on real data
  (watch the Health card's causes); the design's offset sign: the learned offset is reported minus true.

## 0.9.122: the end of the look-ahead (8 Oct 2026)

The plan's last hours were unreliable twice: 7 Oct (look-ahead ended 30 min into the 28.84p peak: 25% sold to the floor) and 8 Oct
(ended inside the cheap window: 63% sold to 25% in the last two hours). The cause is one thing: energy left at the end was
valued at one flat price (the cheapest import price after losses), with no idea how much could still be bought back or what
the house would need next. 0.9.119 patched the first case with a sale-price floor; the second case was the same fault.
Fixed at the root, in two parts:

- **Solve 48 h always, show 36** (`forecast.HORIZON_H`, `DISPLAY_H`; `publish._timeline` trims items, path, prices and sun). The end
  effect now sits in hours 36 to 48 where nothing is shown and it hardly moves the near plan.
- **The end value is a curve** (`value._terminal_curve`). The last 24 h of the plan stand for what follows the end (the same times
  of day tomorrow). R = what the rest of a cheap window could still refill (charge rate, with losses); D = the house's net load in
  the dear stretch after it. Below the knee `reserve + D/eta - R` a stored kWh is worth the dear stretch's price (load-weighted);
  above it, the refill price (or, with no refill to come, the sale price: the 0.9.119 rule, now a special case). A flat tariff or a
  fixed value gives the old flat figure. Tests: `test_engine_v2_value.py` (the cheap-window end, the peak end, the control that
  shows the old value dumping).

