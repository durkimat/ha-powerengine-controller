# Engine v2: build plan

> **Status (5 Oct 2026): built for 0.9.106.** Packages A to E built by sub-agents and reviewed; F (release) prepared. Additions
> beyond this page: `Lines.charge_now`/`run_target_soc` and `value.run_target` (charge at the start of a cheap stretch),
> `ValueResult.limits`, `StepInput.slots_whole_house`.
> **Preview (0.9.107):** while engine v1 is in control the v2 tick still runs with `Situation.active=False` and publishes the
> sensors above (`v2_mode` attribute `preview`, `state_engine` attribute `v2_preview`); its decision is never handed over. Setting
> `preview_when_v1` (default on). The card shows a Preview line and "Would be ..." wording.
> Design and decisions: [engine-v2.md](engine-v2.md). This page is how it is built: who owns which files, the
> interfaces, the published sensors (the card's contract), the tests, and what "done" means.

## Ground rules for every package

* **v1 does not change.** `engine` defaults to `v1`. The replay (`tests/test_replay.py`) passes **unchanged**; never
  re-record it. Every existing test passes. `ruff check .` is clean (line length 120).
* **Pure layers.** Everything in `pe_core/engine_v2/` is pure Python (standard library + `pe_core`), no Home Assistant,
  no AppDaemon, no numpy. The app (`powerengine.py`) is the only place that talks to Home Assistant.
* **Units** as in `types.py`: pence per kWh, kWh, kW, percent, aware datetimes.
* **Words:** plain British English, the owner's style (see `docs/logic/`). No supplier or device names in user text: use
  `names.N(term)` or `<<term>>` placeholders filled with `names.fill`. Sentences name the prices they used.
* **Own files only.** Each package lists the files it may create or change. Anything else: stop and say so in the
  report rather than editing.
* **Don't commit** unless told; the orchestrator reviews and commits.
* Report at the end: what was built, the test results (counts), anything not done, any design question.

## The contract (already written)

* `pe_core/engine_v2/settings.py`: `SETTINGS` (catalogue), `SECTIONS`, `SEED_FROM`, `V2Settings`, `parse_v2`,
  `catalogue()`.
* `pe_core/engine_v2/types.py`: modes (`SELF_USE`, `HOLD`, `CHARGE`, `EXPORT`, `EVENT`, `FREE`, `NONE`),
  `MODE_ACTION` (to v1's `Decision.action`), `BatteryFacts`, `Situation`, `Spread`, `Segment`, `Forecast`, `Limits`,
  `Lines`, `TimelineItem`, `ValueResult`, `EVENT_KINDS`, `Event`, `Observation`, `Exit`, `ModeState`, `StepInput`,
  `StepOutput`.
* `pe_core/config.py`: `system.engine` (`v1` | `v2`, section `engine`), `Config.engine_v2` (`V2Settings`, parsed from
  `engine_v2:`; missing keys seeded from v1), new shared safety setting `battery_floor_soc` (12 %, the battery's own hard
  floor; v1 does not read it).

A package may **add** fields with defaults to the `types.py` dataclasses or helper functions if it needs them, and must
say so in its report. It may not rename or remove anything.

## Module interfaces

```python
# forecast.py (layer 2)
def build(inp: StepInput, settings: V2Settings, learned: dict | None = None) -> Forecast
#   learned: Learner.state() (solar weights per part of day, load weights, solar bias per hour, load spread)

# value.py (layer 3)
def solve(forecast: Forecast, start_soc: float, facts: BatteryFacts, settings: V2Settings,
          limits_for: Callable[[Segment], Limits], now: datetime, because: str, tz=None) -> ValueResult
def value_at(vr: ValueResult, t: datetime, soc: float) -> float                # p per stored kWh
def lines(vr: ValueResult, t: datetime, soc: float, import_p: float, export_p: float,
          facts: BatteryFacts, settings: V2Settings) -> Lines
def segment_step(e_kwh: float, mode: str, seg: Segment, solar_kwh: float, load_kwh: float, import_p: float,
                 facts: BatteryFacts, settings: V2Settings, lim: Limits, end_kwh: float | None = None)
    -> tuple[float, float, dict]     # (end kWh, cost in pence incl. wear and comfort, flows)

# rules.py (layer 4) -- one core used by both
def limits_now(situation: Situation, obs: Observation, seg: Segment | None, facts: BatteryFacts,
               settings: V2Settings, readings) -> Limits
def limits_for(seg: Segment, facts: BatteryFacts, settings: V2Settings) -> Limits

# observe.py (layer 1)
class Observer:
    def __init__(self, settings: V2Settings, state: dict | None = None)
    def update(self, inp: StepInput, forecast: Forecast | None, vr: ValueResult | None) -> Observation
    def note_revalued(self, now: datetime) -> None          # resets the drift sum
    def state(self) -> dict

# triggers.py
class Triggers:
    def __init__(self, settings: V2Settings, state: dict | None = None)
    def due(self, now: datetime, events: tuple[Event, ...], last_value_at: datetime | None) -> str | None
        # the "because" text when a revalue should run now (coalescing, urgent events at once, backstop), else None
    def note(self, now, events, revalued: bool, mode_changed: bool, calc_s: float | None) -> None   # day counts
    def health(self, now) -> dict
    def state(self) -> dict

# execute.py (layer 5)
class Executor:
    def __init__(self, settings: V2Settings, state: dict | None = None)
    def step(self, now: datetime, obs: Observation, lim: Limits, ln: Lines | None, vr: ValueResult | None,
             events: tuple[Event, ...], facts: BatteryFacts) -> tuple[ModeState, Decision, tuple[Event, ...], bool]
        # (mode, decision for layer 6, new events it raised: "level", "reserve", "deadline", mode changed?)
    def state(self) -> dict

# learning.py
class Learner:
    def __init__(self, settings: V2Settings, state: dict | None = None)
    def observe(self, now: datetime, obs: Observation, readings, forecast: Forecast | None, tz) -> bool   # True: changed
    def state(self) -> dict

# engine.py (the facade the app calls)
class EngineV2:
    def __init__(self, settings: V2Settings, state: dict | None = None)
    def update_settings(self, settings: V2Settings) -> None
    def step(self, inp: StepInput) -> StepOutput
    def state(self) -> dict          # JSON-safe; the app saves it beside the config (engine_v2_state.json)
    def health(self, now) -> dict

# publish.py (the card's contract, below)
def entity_states(out: StepOutput, engine: EngineV2, settings: V2Settings, tz) -> dict[str, tuple[str, dict]]
def settings_state(settings: V2Settings) -> tuple[str, dict]                    # sensor.pe_diag_v2_settings
```

## Published sensors (the card's contract)

Keys are the app's publish keys; the entity is `sensor.pe_<key>`. All times ISO 8601 with offset. Prices and values in
pence per kWh rounded to 2 places, levels to 1 place. Every attribute set stays under 15,000 bytes (aim for the sizes
given).

`sensor.pe_state_engine` (key `state_engine`): state `v1` or `v2`. Attributes `{"v2_available": true}`. Always
published (both engines). The dashboard and card use the state to know which engine runs.

`sensor.pe_v2_mode` (about 1.5 KB): state = mode key (`self_use`, `hold`, `charge`, `export`, `event`, `free`, `none`).

```json
{"label": "Charging from the grid", "since": "...", "why": "Import is 6.99p ...", "rule": "v2_value",
 "chosen_by": "price", "target_soc": 88.0, "power_w": 4800,
 "exits": [{"kind": "level", "text": "The battery reaches 88%", "expected_at": "...", "first": true}, ...],
 "deadline": "...", "level_reported": 51.0, "level_filtered": 51.3, "sending": true, "not_sending_reason": null,
 "values_at": "...", "values_because": "New prices published"}
```

`sensor.pe_v2_value` (about 0.5 KB): state = value of a stored kWh at the filtered level now (p/kWh).

```json
{"value_p": 30.0, "buy_line_p": 7.36, "sell_line_p": 14.25, "use_line_p": 6.64, "store_sun_line_p": 15.79,
 "import_p": 6.99, "export_p": 15.0, "charge_target_soc": 88.0, "sell_floor_soc": null, "level": 51.3,
 "scale_max_p": 40}
```

`sensor.pe_v2_timeline` (about 6 KB): state = when the values were worked out (ISO).

```json
{"now": "...", "because": "New prices published", "floor_soc": 12, "reserve_soc": 12,
 "items": [{"mode": "charge", "start": "...", "end": "...", "level_start": 30.0, "level_end": 88.0,
            "until": "until 88%", "reason": "..."}],
 "path": {"start": "...", "step_min": 15, "mid": [..], "low": [..], "high": [..]},
 "prices": [{"start": "...", "end": "...", "import_p": 6.99, "export_p": 15.0, "slot_prob": null, "event": false,
             "free": false, "estimated": false}],
 "cost_expected": 1.23, "cost_selfuse": 2.34, "comfort_given_up": 0.06, "calc_s": 1.8}
```

(`cost_*` and `comfort_given_up` in GBP.) `prices` merges consecutive equal segments.

`sensor.pe_v2_value_curve` (about 4 KB): state = when worked out (ISO).

```json
{"start": "...", "step_min": 60, "levels": [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
 "values": [[30.1, 29.8, ...11 values...], ...one row per hour, up to 48...], "unit": "p/kWh"}
```

`sensor.pe_v2_triggers` (about 4 KB): state = number of revalues today.

```json
{"recent": [{"at": "...", "kind": "price", "text": "...", "effect": "recheck"|"revalue"|"mode_change"}],
 "today": {"causes": {"drift": 3, "slots_changed": 2, ...}, "revalues": 9, "mode_changes": 7, "flip_flops": 0,
           "deadlines_missed": 1, "backstop": 1, "longest_calc_s": 2.4}}
```

(`recent`: the last 30.)

`sensor.pe_diag_v2` (about 2 KB): state `ok` (or `learning` while fewer than 3 days are known).

```json
{"weights": {"solar": {"morning": [0.29, 0.51, 0.20], "midday": [...], "afternoon": [...]}, "load": [0.25, 0.5, 0.25],
             "days": 23, "start": {"solar": [0.25, 0.5, 0.25], "load": [0.25, 0.5, 0.25]}},
 "solar_bias": {"days": 14, "by_hour": {"9": 0.92, ...}},
 "soc_offset": {"charging": -0.9, "holding": 0.0, "discharging": 0.3},
 "filter_gap_max_today": 1.2,
 "comfort": [{"date": "2026-10-05", "hours_above": 3.5, "hours_below": 0, "given_up": 0.06, "decisions_changed": 2}]}
```

`sensor.pe_diag_v2_settings` (about 9 KB): state = number of settings; attributes = `settings.catalogue(current)`.

The diagnostics export gains a section `engine_v2`: `health`, the last 300 journal rows, the latest timeline.

## Work packages

| WP | What | Files it owns | Needs |
|---|---|---|---|
| **A** | Layers 2 and 3: `forecast.py`, `value.py` (+ tests) | `pe_core/engine_v2/forecast.py`, `value.py`, `tests/test_engine_v2_forecast.py`, `tests/test_engine_v2_value.py` | contract |
| **B** | Layers 1, 4, 5, triggers, learning, facade, publish: `observe.py`, `rules.py`, `execute.py`, `triggers.py`, `learning.py`, `engine.py`, `publish.py` (+ tests) | those modules, `tests/test_engine_v2_{observe,rules,execute,triggers,learning,engine,publish}.py` | contract; A's interfaces (stub them in tests until A lands) |
| **C** | Card and dashboard: new elements, config groups, `battery_floor_soc` in the card's battery topic, an "Engine v2" dashboard view | card repo `ha-powerengine-card.js`, `tests/*.test.cjs`; app repo `apps/powerengine/dashboard/dashboard.lovelace`, `tests/golden/dashboard_*.lovelace`, `tests/test_dashboard.py` if needed | sensor contract |
| **D** | App wiring: engine choice, v2 tick, sensors, state file, diagnostics, guard, entities | `apps/powerengine/powerengine.py`, `pe_core/entities.py`, `pe_core/diagnostics.py`, `tools/diag_summary.py`, `tests/test_engine_v2_app.py` | A, B |
| **E** | Comparison on recorded data: v1 against v2 in the closed-loop demo world, plus a perfect-foresight bound | `tools/engine_compare.py`, `tests/test_engine_compare.py` | D |
| **F** | Release: docs, notes, versions (orchestrator) | `docs/`, `CLAUDE.md`, `release-notes/`, `pe_core/version.py`, card `CARD_VERSION`/`MIN_APP_VERSION` | all |

### A: forecast and value

**forecast.build.** From `inp.readings` (rates, dispatches, grid event and free-power windows, export rate),
`inp.solar_points`, `inp.load_profile`, `inp.overnight`, `inp.situation.override`, `inp.facts.charge_factor`:

* Horizon: now to the end of published prices, at least 36 h, at most 48 h. Beyond the published prices, estimated as
  v1 does (same time yesterday; yesterday's smart-slot price not carried over: see `forecast.build_slots.price_at`).
  Reusing v1's `forecast.build_slots(..., certainty=None)` for the half-hourly import, export, sun and house values is
  allowed and preferred (it is shared data assembly, not v1's planning), as long as smart slots come out as two
  outcomes (below) and not v1's blended price.
* Segment boundaries: every half-hour where the data changes, `now`, and the **exact** start and end of every
  dispatch, grid event and free-power session; then split so no segment is longer than `max_segment_min`. Sun and
  house for a part of a half-hour are pro rata.
* Smart slots: `slot_prob` = `inp.slot_certainty(start, first_seen)` for a future dispatch, 1.0 for a running one;
  `slot_import_p` = the slot's price; `import_p` = the price without it (the half-hour's standard rate). Inside the
  overnight window: no slot (`slot_prob` None), the price is the same either way. With `slots_whole_house` off the
  slot is ignored for the house (no slot).
* Events: `event` for overlap with the grid event window (`events` setting on); `event_p` = `event_value_p` + export
  price if `event_plus_export`. `free` likewise (`free_power` on).
* Car: only the segment(s) up to the end of the current half-hour, while `readings.ev_state() == "charging"`; `car_kw` =
  live `ev_power` / 1000 (or `ev_charger_kw` if unknown). Elsewhere 0.
* Sun: `Spread(low, mid, high)` from the point's `low_kwh`/`kwh`/`high_kwh` (missing bands: 0.7x and 1.2x the middle),
  scaled by the learned solar bias for that local hour when `learn_solar_bias`; weights from `learned` for the part of
  the day (morning before 10:00, midday 10:00 to 14:00, afternoon after) or the starting weights.
* House: the profile's watts as the middle; low and high from the learned spread (`learned["load_spread"][hh]` =
  (p20, p80) residual in kWh per half-hour) or, without it, 0.8x and 1.3x the middle; load weights.
* Override: segments from now to `override.until` (or the whole horizon) carry `manual` = the v2 mode for the
  override's action, except grid-event segments.
* Notes: "prices after Tue 23:00 are estimated", etc.

**value.solve.** Stochastic dynamic programming over the battery level (section 6 of the design):

* Level grid 0 .. capacity in `level_step_kwh` steps (0.5 kWh beyond 12 h ahead is allowed, to keep the time down).
* Backwards over segments. Terminal value: `refill` = the cheapest non-free import price in the last 24 h of the
  horizon divided by `eta_charge`, per kWh; or `terminal_value_p`.
* Price outcome (slot yes / no, by `slot_prob`) is known before the choice: the minimum is taken inside it. Sun and house
  are not: the mode is chosen against their expected cost. Combining sun and house into three net-load scenarios
  (low / middle / high net, weights from the two spreads) is allowed to keep the time down.
* Modes per segment from `limits_for(seg)`; a forced mode is the only choice. **Charge and Export may stop at any grid
  level inside the segment** (the remainder held), so partial charges are possible; Self-use and Hold run the whole
  segment.
* `segment_step`: the physics, consistent with v1's `planner.step` (read it): Self-use covers a shortfall from the
  battery down to `lim.floor_soc` and stores a surplus (charge limit, taper), the rest exported up to the export limit;
  Hold: the grid covers a shortfall, a surplus is **exported** (on the inverter Hold is force charge at 0 W); Charge: the
  battery takes up to the charge limit (taper, cold factor, caps, fuse headroom after house and car) up to
  `lim.ceiling_soc`; Export: discharge at the limit down to the floor, capped by export limit plus house; Event: as
  Export, battery export paid `event_p`; Free: charge to 100%. Cost (pence): import x price - export x export price -
  event export x `event_p` + `wear_house_p` x battery to house + `wear_sale_p` x battery sold + comfort cost (kWh
  outside the band, averaged over the segment, x hours x `comfort_cost_p`). Efficiencies `eta_charge` /
  `eta_discharge`.
* `lam[k][i]`: the slope of the cost-to-go at segment k's start, per kWh stored (central differences; one-sided at the
  ends). Values may be large in event segments (about the event's pay).
* **Timeline:** forward from `start_soc` with the middle scenario, choosing each segment's mode with the same comparison
  layer 5 uses (`lines` at the segment's level and prices; charge and export stop at their exit level); merge equal
  consecutive modes into `TimelineItem`s with `until` ("until 88%", "until 05:30", "until the grid event at 17:00") and a
  plain `reason` naming the prices and the value. `path` every 15 minutes: middle, and low/high from the low and high
  net-load scenarios.
* `cost_expected_p` (policy, middle scenario) and `cost_selfuse_p` (Self-use throughout, same physics).
* `comfort_given_up_p`: when `comfort_cost_p` > 0 (or, since 0.9.113, `top_up_cost_p` > 0), a second solve at 0.5 kWh steps
  without the comfort cost and the top-up; the cash (no comfort term, no top-up) difference of the two policies'
  middle-scenario runs. The top-up is counted inside the comfort figure (`_phys` element 2), so "given up for comfort"
  includes what the soft top cost.
* **Time budget:** a 48 h horizon must solve in under 3 s in this container (measure with a test using
  `time.perf_counter`, limit 6 s to allow for slow CI). Say in the report what was done to meet it.

**value.lines.** The comparison in value terms (see `Lines`). `charge_target_soc`: the lowest level above `soc` at which
`eta_charge x value <= import_p` (scan the segment's column; None if buying is not worth it at `soc`); `sell_floor_soc`:
the highest level below `soc` at which selling stops being worth it.

**Tests (A), at least:** segments cut at a dispatch's real minutes; slot as two outcomes; estimated prices beyond the
published ones; a cheap night before a dear evening gives a charge target where the curve says and the target is lower
with a sunny day ahead (design B1, B2); a grid event raises the value before it (B8); `lam` falls with level; the comfort
cost lowers the value near the top and is not paid for a short stay (B14b); Hold exports a surplus; a partial charge
stops between grid levels; physics never below the floor or above the ceiling; timing under the budget.

### B: observe, rules, execute, triggers, learning, engine, publish

* **observe.Observer:** filtered level (coulomb counting from `battery_power` with the efficiencies, pulled to the
  reading by `soc_filter_gain` per sample, plus the learned per-mode offset; reset to the reading at start and after a
  3-point gap held 10 minutes); events from changes between ticks: `prices_published` (the set of rate windows changed),
  `slots_changed` (dispatch list changed), `slot_start`/`slot_end` and `price` (a boundary of a dispatch or a price
  change passed since the last tick), `event_changed`/`event_start`/`event_end`, `free_*`, `car_start`/`car_stop`
  (debounced on `ev_state() == "charging"`), `sun_to_short`/`short_to_sun` (net load sign, debounced), `drift` (CUSUM
  of actual minus the forecast middle net load since the last revalue passes `drift_kwh`), `band_exit` (filtered level
  outside the path's low-high range for `band_exit_min`), `forecast_update` (rest-of-today solar total moved more than
  `forecast_change_pct`), `bms` (a limit moved 0.5 kW or to 0), `mode_switch` (`situation.active` changed),
  `override` (override changed), `data_missing`/`data_back` (battery level or import rate missing longer than
  `stale_after_s`), `start` (first tick).
* **rules.limits_now / limits_for:** design section 7, one core. Order: not Active (still decided; `Situation.active`
  is only reported) / grid event in progress (forced `EVENT`, floor = hard floor + margin) / override (forced: its mode;
  floor = reserve for Self-use and Export; Charge to its target: the v1 grid-charge target is not used, take
  `charge_ceiling_soc`) / free power (forced `FREE`, ceiling 100) / car charging with `house_load_includes_ev` (allowed
  Hold and Charge only) / level at or below the reserve (no discharge modes) / otherwise Self-use, Hold, Charge, plus
  Export when `arbitrage`. Floor: max(reserve, hard floor) except events; ceiling: `charge_ceiling_soc`. Caps: fuse
  headroom live (`fuse_kw` - house - car) for a charge, BMS limits, cold factor. Reasons in plain words.
* **execute.Executor:** design section 8. Choice from `Lines` with `price_band_p` hysteresis (enter needs the band, stay
  tolerates it) and `level_band_pct` after an exit level; minimum time `min_dwell_s` except for urgent events (the
  `EVENT_KINDS` flag) and forced modes; deadlines from the timeline item's expected end + `deadline_grace_min` raise a
  `deadline` event once (they never stop a mode); missing data after `stale_after_s` gives Self-use with rule
  `v2_data_missing` (the inverter's own self-use is the safe state). The `Decision`: `action` = `MODE_ACTION`,
  `rule` = `v2_<rule>`, `reason` = the why (naming prices and value), `target_soc` = charge target or ceiling for a
  charge, `power_w` = the cap in W when below the battery's limit else None, `details` = `{"engine": "v2", "mode": ...}`.
  Never use `details["reached"]` (v1's latch). Flip-flops (A to B to A within 10 minutes) are counted.
* **triggers.Triggers:** revalue when an event with `revalue` arrives: at once for urgent ones, otherwise after
  `revalue_coalesce_s` from the first pending; backstop after `max_value_age_min`; day counts for the health sensor.
* **learning.Learner:** scenario weights by counting which of low/middle/high each half-hour came closest to (solar by
  part of day, daylight only; load all day), recency-weighted (`scenario_half_life_days`), with the starting weights
  worth `scenario_prior_days` days, each weight kept within 0.05 to 0.8; solar bias per local hour over 14 days (ratio
  within 0.5 to 1.5); load spread per half-hour of day (p20/p80 of actual minus profile); SoC offset per mode (when the
  mode changes with little energy flowing, the jump in the reading). Only when the matching setting is on. JSON-safe
  state.
* **engine.EngineV2.step:** observe -> triggers (revalue? then forecast.build + value.solve with `rules.limits_for`,
  `observer.note_revalued`) -> lines at the filtered level and live prices -> `rules.limits_now` -> `executor.step` ->
  learning -> journal rows (mode change, revalue: time, event, modes, level, prices, value, lines) -> `StepOutput`. The
  first tick always revalues. If a revalue raises, keep the previous value result, log a journal row with the error,
  and carry on (never crash the app's cycle). Without any value result yet: Self-use with rule `v2_starting`.
* **publish:** the sensors above, built from the engine's state; size-check in a test.

**Tests (B), at least:** the filter does not dip when the reading drops a point while charging (B3); car blip of 60 s
gives no event (B6); a rate unavailable for one tick gives no event (B11), for longer than `stale_after_s` gives
Self-use (B12); reserve applies to every discharge mode outside events (B14) and events go to the hard floor plus margin
(B14a); car charging allows only Hold and Charge (B7); hysteresis and minimum time (B10); deadline raises a revalue but
does not stop the charge (B22); override forced (B19); coalescing and backstop; learning moves the weights with the
prior; the engine never raises out of `step` with a broken forecast; every attribute set under 15,000 bytes.

### C: card and dashboard

Card (`ha-powerengine-card.js`, one file; pure helpers exported for `node --test`):

* `powerengine-engine-card`: the Monitoring panel for v2 (mockup: the Monitoring tab of the engine v2 card mockup):
  mode, label, "until" line, why, **the value bar always shown** (the marker = `value_p` on a 0 to `scale_max_p` scale,
  lines for `buy_line_p` "buy above" and `sell_line_p` "sell below", a note that grid-event values are off the scale),
  the exit list, reading/filtered level, power, when and why the values were worked out. Reads `sensor.pe_v2_mode`,
  `sensor.pe_v2_value`. Shows a short "Engine v2 is not running (engine v1 is)" line when `sensor.pe_state_engine` is
  `v1`, and nothing breaks when the v2 sensors don't exist.
* `powerengine-v2-plan-card`: the timeline (mode bands, past solid and expected lighter, labels with the ending
  condition, battery path with low-high range, price steps with a smart slot dashed, now line, hard floor line) and the
  value map (time x level heat map from `sensor.pe_v2_value_curve`, expected path drawn over it, hover/tap readout) with
  a toggle to the "along the expected path" line. **Phones get the map too** (horizontal scroll inside the card). SVG
  drawn by the card; colours from HA theme variables where possible, mode colours as in the mockup.
* `powerengine-v2-health-card`: recalculations by cause (backstop highlighted), mode changes, flip-flops, deadlines,
  longest calculation, comfort band summary, learned weights, filter offset. Reads `sensor.pe_v2_triggers`,
  `sensor.pe_diag_v2`.
* Config card: an **engine choice** at the top (`system.engine`), with the same inline confirmation in **both**
  directions ("Switch to engine v2/v1? ... Active, Passive and Pause stay as they are ... the other engine's settings are
  kept"); then the existing sections grouped under **Your house** (shared), **Engine v1 settings** (the v1-only planning
  topics: tariff/planning features and cheap threshold, selling/arbitrage, grid-event look-ahead and margin, minimum
  reserve, grid-charge target, charge hysteresis, wear, switch costs) and **Engine v2 settings** (from
  `sensor.pe_diag_v2_settings`: its sections, settings with kind/min/max/unit/help; saved as the `engine_v2:` block). The
  engine not in use is dimmed and labelled "not in use: can be set now" but **stays editable**. Add the new shared
  setting `battery_floor_soc` to the battery topic. Send `system.engine` and `engine_v2` **only** when the app publishes
  `sensor.pe_diag_v2_settings` (an older app rejects unknown keys). The v2 group shows readouts where the sensors give
  them (comfort band effect from `sensor.pe_diag_v2`, learned weights).
* `CARD_VERSION` stays as is (the release sets it). No `MIN_APP_VERSION` change (the card hides v2 pieces when the
  sensors are absent).
* Tests in `tests/engine_v2.test.cjs`: value-bar geometry helper, timeline item layout helper, heat-map colour scale,
  the config grouping (which keys go to which group), the "send v2 keys only when supported" rule, confirm text both
  ways.

Dashboard (`apps/powerengine/dashboard/dashboard.lovelace`): a new view **"Engine v2"** (path `engine-v2`, icon
`mdi:engine`, placed after Plan) with the demo card, `custom:powerengine-engine-card`,
`custom:powerengine-v2-plan-card`, `custom:powerengine-v2-health-card`, in the same section style as the other views.
Nothing else in the dashboard changes (automatic switching of the existing pages is a later release). Update the golden
`tests/golden/dashboard_edf_zappi_solcast_solis_axle.lovelace` to match (it is the rendered dashboard; regenerate the
same way the test describes) and keep `tests/test_dashboard.py` passing; `MIN_CARD_VERSION` is raised by the
orchestrator at release.

### D: app wiring

* Build `EngineV2` lazily (`_engine_v2()`), rebuilt on settings change (`update_settings`), state loaded from and saved
  to `engine_v2_state.json` beside the config (every 5 minutes and on terminate; a broken file is ignored with a
  warning).
* Engine choice: `self.cfg.system["engine"]`. Publish `state_engine` always. Register the v2 tick with `run_every` at
  `sample_s` **only if it does not change v1's replay** (check: the replay must pass unchanged); the tick does nothing
  unless the engine is v2.
* With v2: the 30-second `_cycle` keeps its bookkeeping (load profile, costs, grid check, events, slots, smart-charge
  step, temperatures, override expiry, entity states, v1's plan **still made and published** so v1's Plan page keeps
  working as a comparison) but **does not call `decide` or `_early_target` and does not control**. The v2 tick: read
  (same `read(...)` call), build `StepInput` (facts from `_params(r)` / `_control_params(r)` and the BMS limits; situation
  from the effective mode, `_active_override()`, `house_load_includes_ev`, `control_method`; solar points from
  `_solar_forecast()`; the load profile; `Certainty(...).score` and the first-seen map; `_overnight()`), call
  `step`, publish `publish.entity_states(...)` with `_publish_if_changed`, then hand `out.decision` to the **same**
  activity log, `_note_command` and `_control(readings, decision)` path v1 uses (so Passive shows "Would ...", and RAM
  control, BMS caps, fuse cap, following check, write budget all apply unchanged).
* **Timed windows:** engine v2 needs RAM remote control in this release. With `control_method` = timed windows and
  engine v2, v2 still decides and publishes, but nothing is sent: the effective mode reason says "Engine v2 needs RAM
  remote control" (treat like Active refused) and the Config page shows it.
* Switching engines (a config save that changes `engine`): no change to Active/Passive/Pause; log and notify; v2's first
  step revalues at once; switching back hands control back to v1's next cycle. The inverter keeps its last command in
  between (RAM refresh continues).
* `pe_core/entities.py`: EntityDefs for `state_engine`, `v2_mode`, `v2_value`, `v2_timeline`, `v2_value_curve`,
  `v2_triggers`, `diag_v2`, `diag_v2_settings` (names, icons). Publish `diag_v2_settings` on start and on config save.
* Diagnostics export: section `engine_v2` (health, last 300 journal rows, latest timeline). `tools/diag_summary.py`: a
  short v2 section (engine in use, revalues by cause, mode changes, flip-flops, last 12 timeline items).
* Demo mode runs v2 when the demo config says `system.engine: v2` (the comparison needs it).
* **Tests (D):** `tests/test_engine_v2_app.py` with the replay harness / `test_demo_app.py` loader: engine v1 by default
  publishes `state_engine` v1 and nothing else changes; engine v2 in the demo world for a few hours: decisions come from
  v2 (`rule` starts `v2_`), RAM commands reach the demo inverter, the v2 sensors are published under 15,000 bytes, the
  state file is written and reloaded, Passive sends nothing, timed windows refuse.

### E: comparison on recorded data

`tools/engine_compare.py [--days sunny dull axle car] [--hours 24] [--json]`: run the **whole app** in demo mode,
closed loop (the demo world plays the recorded house, sun, prices, smart slots and events; the battery and grid are
simulated, so both engines are judged on the same outside world), once with engine v1 and once with v2, Active, RAM
control, from the same start, for each day (starting at local midnight, running the full day; the world's own accounting
of grid import, export and grid-event export priced at the day's rates). Also a **perfect-foresight bound**: v2's
`value.solve` on the actual day (actual sun and house, no uncertainty, slots as they happened) and its forward run. Print
per day and in total: cost (GBP), against plain self-use, mode changes (inverter commands), flip-flops, energy bought at
the peak rate to charge, minimum level, and the gap to the bound. `tests/test_engine_compare.py`: a short run (2 hours of
one day) that checks the tool works; the full run is not in the default test suite (mark it, or keep it under 60 s).

## Done means

* All packages merged, `ruff check .` clean, `python3 -m pytest -q` all pass with the replay unchanged, card checks pass.
* `tools/engine_compare.py` run on all four demo days; the result reported to the owner as it is (v2 better, equal or
  worse, per day, with the reasons where v2 loses).
* Docs: this page's status, the design page's status, CLAUDE.md (current work), `docs/INSTALL.md` if setup changes.
* Release prepared through the Release workflow (app + card, `MIN_CARD_VERSION` raised because the dashboard names new
  elements), waiting for the owner's approval.
