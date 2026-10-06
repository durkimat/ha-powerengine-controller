# Engine pages and the same-day engine comparison

> **Status (6 Oct 2026): built for 0.9.110 (packages A, B and C merged; not yet run on a live HA).** Owner's decisions: the Plan page becomes "Engine v1"; both engine
> pages get an engine icon with v1 / v2 on it and an **Active / Passive** badge; Plan history moves to the bottom of the Engine v1
> page and a matching history goes at the bottom of the Engine v2 page; the Costs page keeps everything it has and gains a
> **fair same-day comparison** of the two engines (no Predbat). Work packages A (recording), B (comparison), C (card and dashboard).

## 1. What the owner sees

**Tabs.** Monitoring, **Engine v1** (was Plan), **Engine v2**, Costs, Health, Simulator, Config, Tests. The Plan history tab goes.
The two engine tabs use the card's own icons `pe:engine-v1` and `pe:engine-v2` (an engine outline with "1" / "2" cut into it),
registered by the card through HA's custom icon API (`window.customIcons`). The view paths stay `plan` and `engine-v2`.

**Badge** at the top of each engine page (one small card, `custom:powerengine-engine-badge-card` with `engine: v1|v2`):

| Engine chosen (`sensor.pe_state_engine`) | PowerEngine's mode | Badge on that engine's page | Badge on the other page |
|---|---|---|---|
| this one | Active (not paused) | **Active** (green) | **Passive** |
| this one | Active, paused | **Paused** | **Passive** |
| this one | Passive | **Passive** | **Passive** |

One line under it says what that means: "Sending commands to the inverter" / "Working out what it would do; nothing is sent"
(v2 on v1 with preview off: "Not running: preview is off").

**Engine v1 page:** as today, then a heading **History** and today's Plan history content unchanged (day picker, charts, actions).
The "As run" plan is recorded only for half-hours when engine v1 was in control; on a day engine v2 ran, the history shows
v1's start-of-day plan labelled "Engine v1 was passive: its plan at the start of the day".

**Engine v2 page:** as today, then **History**: a card in the style of the v2 timeline card, with a day picker: the level as it
ran against the level expected at the start of the day, the mode as run (coloured band), import and export prices, and the value
of a stored kWh; the mode changes listed below it (time, mode, why). Days where v2 only previewed say so (dimmed band, "Preview").

**Costs page:** everything stays. A new section after "Where the savings came from": **Engines compared, same day**: a table of
the last 7 days with the saving against plain self-use of engine v1, engine v2 and the best possible (perfect hindsight), the
better engine marked, the v2 - v1 difference, which engine was really in control, and a 7-day total. Under it the calibration
line: "On days it was in control, engine v1's replay came within 4p (1.2%) of the metered cost". A day not compared says why
("no forecast record yet", "records incomplete", "replay failed").

## 2. The comparison

### 2.1 Principle

Each night PowerEngine replays yesterday through **the whole app**, once with engine v1 and once with engine v2, in the demo
world (a simulated battery and grid) fed with **what actually happened** (house, car, sun, prices charged, smart slots that ran,
grid events) and **what was forecast at the time** (the snapshots of 2.2). Both engines start from the same battery level and the
same learned state, use the owner's current settings, and are costed from the simulated flows the same way. This is
`tools/engine_compare.py`'s method (proven on the demo days), run on real days. The whole app is used rather than a pure re-implementation
of each engine's loop, so the replay can't drift from what the app really does.

Also per day: **self-use** (the world with no commands, the baseline), and the **bound** (engine v2's `value.solve` on the actual
day with no spread, as `engine_compare` does). Savings are against self-use, adjusted for the end level (end level minus
self-use's end level, x capacity, x the day's cheapest import rate), exactly as `engine_compare`.

**Calibration:** the engine that was in control is also judged against reality: its replayed cost against the metered cost from the
cost records (grid import x rate - export x rate - event pay, no standing charge). The gap is shown so the owner knows how far to
trust the other column. Days with mixed control (switched during the day) are compared but not calibrated.

### 2.2 Forecast snapshots (work package A)

`pe_core/fcsnap.py`, stored in `<costs dir>/snapshots/YYYY-MM-DD.json` (local day), kept 14 days (pruned at the nightly run).

- **What:** the raw HA state and attributes of every mapped role an adapter reads **future** information from: the forecast
  adapter's entities (solar forecast today/tomorrow), the tariff's rates (`import_rate_now`, `import_rates_today`,
  `import_rates_tomorrow`, `export_rate`, `standing_charge`), smart-charge dispatches, free-power next start/end and active, and the
  grid-event roles. A helper `snapshot_roles(cfg)` returns `{role: entity_id}` from the config (only mapped ones). Plus the
  **house profile** in use (`LoadProfile.watts` as `{"we|hh": W}` and `days`) and the smart slots' **first seen** times
  (`self.slots.slots[k]["first_seen"]`).
- **When:** every 30 s cycle the app compares the current values with the last saved ones and appends an entry **only for what
  changed** (state or attributes), at most once per 5 minutes per entity, plus a full entry at local midnight (the first
  cycle of a day writes everything). So a file is a sequence of deltas; replaying entries up to time t gives every role's state at t.
- **Format:**
  ```json
  {"version": 1, "day": "2026-10-07", "tz": "Europe/London",
   "roles": {"import_rates_today": "event.edf_..._current_day_rates", ...},
   "entries": [{"at": "2026-10-07T00:00:12+01:00",
                "states": {"event.edf_..._current_day_rates": {"state": "...", "attributes": {...}}},
                "profile": {"days": 14.0, "watts": {"0|0": 412.0, ...}},
                "first_seen": {"2026-10-07T01:30:00+01:00": "2026-10-06T19:02:00+01:00"}}]}
  ```
  `profile` and `first_seen` appear in an entry only when they changed. Write with the costbook's atomic write. A failure warns once
  per day and never stops the cycle. Never in a demo. Size check: the file for a day must stay under 1 MB (test with synthetic
  Solcast/Kraken payloads of realistic size and 24 forecast updates).
- **Scrub:** these files hold entity ids (they never leave the HA host; they are not in the diagnostics export, which carries only
  a summary: days kept, entries, bytes).

### 2.3 Engine tag on cost records (work package A)

Each half-hour cost record gains `"engine": "v1"|"v2"` (the engine chosen when the record is written) and `"live": true|false`
(effective mode Active and not paused for most of the half-hour; simplest correct: the state at the record's write). Old records
without the key count as v1. `costbook.day_engine(day)` returns `"v1"`, `"v2"`, `"mixed"` or None (no records), from records with
`live` true (a day never live returns the chosen engine with `live: false`).

### 2.4 The replay (work package B)

`pe_core/compare/` (pure Python; nothing here touches the real Home Assistant):

- `harness.py`: the fake AppDaemon, fake clock and frozen datetime, moved from `tests/replay_harness.py` /
  `tools/engine_compare.py` into the app package so the shipped app can run it. `tests/replay_harness.py` and
  `tools/engine_compare.py` import it from there (the replay goldens must stay byte-identical).
- `day.py`: builds the replay day from the cost records (`build_demo_pack.build_day`'s conversion, moved into
  `pe_core/demo/pack.py` as `day_from_records`; the tool keeps working) plus the snapshot file. Refuses an incomplete day
  (`build_demo_pack.complete`) or a missing snapshot (no entry at or before 00:30 local) with a reason.
- `world` changes (`pe_core/demo/world.py`): an optional **snapshot feed**: when given, the forecast, rate, dispatch, free-power
  and grid-event entities the world serves are the snapshot's states at world time (mapped role -> the world's demo entity id),
  instead of the world's own derived ones. The house profile and smart slots' first-seen times are injected into the replayed app
  from the snapshot (the profile as it was at each time; the app must not learn its own profile from the world's history during a
  comparison). Battery size, power limits and efficiency come from the owner's config/last readings rather than the world's
  constants when given (defaults unchanged for the demo).
- `run.py`: `python -m pe_core.compare.run --save-dir <dir> --day YYYY-MM-DD [--out <file>]`. Copies what the replay needs into a
  temp dir (the owner's config.yaml with the inputs remapped to the world's entities the way the demo template maps them, keeping
  every setting, feature and the `engine_v2` block; `engine_v2_state.json`; the slot history), runs self-use, engine v1 and engine v2
  (Active, RAM remote control, same start level = the recorded level at 00:00), the bound, and the calibration; writes
  `<costs dir>/compare/YYYY-MM-DD.json` (2.5). Exit code 0 ok, 2 day refused (reason in the file), 1 failure.
- **In the app:** a nightly job at 03:20 local (after the 02:40 low-write job) starts the runner for yesterday as a
  **subprocess** (`sys.executable -m pe_core.compare.run`, cwd the app folder, `nice` 10 where available), so AppDaemon's threads
  are never blocked; the app polls for it every 5 minutes, kills it after 90 minutes, runs one at a time, and also fills any of the
  last 7 days that have a snapshot and no result (one day per night beyond yesterday, oldest last). Feature `engine_compare`
  (default on, under the Costs topic in the card) turns it off. Not in a demo. A failure logs one WARNING with the runner's last
  lines and marks the day `failed`.
- **Speed:** measure a day's run on this container and record it in the result (`took_s`); the target is under 15 minutes for both
  engines here (a Raspberry Pi is perhaps 4x slower). Allowed tuning, behaviour-neutral only: the world's step (10 s), skipping
  publishing work the comparison doesn't read. Report the time in the PR.

### 2.5 Result files and the sensor

`<costs dir>/compare/YYYY-MM-DD.json`, kept 30 days:
```json
{"version": 1, "day": "2026-10-07", "status": "ok", "reason": "", "took_s": 412, "made_at": "...",
 "in_control": "v1", "live": true,
 "selfuse": {"cost": 3.12, "end_soc": 41.0},
 "v1": {"cost": 1.95, "end_soc": 52.0, "saving": 1.30, "flips": 2, "modes": 9},
 "v2": {"cost": 1.80, "end_soc": 47.0, "saving": 1.38, "flips": 4, "modes": 14},
 "bound": {"cost": 1.70, "saving": 1.51},
 "metered": {"cost": 1.99},
 "calibration": {"engine": "v1", "replay": 1.95, "metered": 1.99, "diff": -0.04, "diff_pct": -2.0}}
```
Costs in GBP, positive = paid; `saving` = self-use cost - engine cost + end-level adjustment. `status`: `ok` | `no_snapshot` |
`incomplete` | `failed`.

`sensor.pe_cost_engines` (published at start and after each run): state `ok` (a result in the last 7 days), `waiting` (none yet),
`running`, `off` (feature off), `error` (last run failed). Attributes:
```
days:   [{date, status, reason, in_control, live, v1, v2, bound, best ("v1"|"v2"|"tie" within 1p), diff (v2 - v1),
          v1_flips, v2_flips, calib: {engine, diff, diff_pct} | null}]      # last 7 local days, newest first; savings in GBP
totals: {days, v1, v2, bound, diff}                                          # over the "ok" days shown
calib:  {engine, days, mean_abs_diff, mean_abs_pct} | null                   # over the shown calibrated days
last_run: {at, day, status, took_s, message}
next_run: iso
note:   "Replays use your current settings and learned state, with the forecasts as they were. A rough guide: the
         simulated battery has no taper or BMS limits."
```
Under 4 KB. The diagnostics export gets a section `engine_compare` (the last 7 result files' summaries); `tools/diag_summary.py`
prints one line per day.

## 3. Engine v2 history (work package A)

`<costs dir>/v2history/YYYY-MM-DD.json`, kept 30 days (same retention as plan history):
- `expected`: v2's expected level path for the day, saved from the first timeline after local midnight (the "start of day" view),
  and the first of each hour (`hours: {"HH:00": [[iso, soc], ...]}`, the day's half-hours only).
- `ran`: per half-hour `{start, mode (the mode kind most of the half-hour), level_end, import_p, export_p, value_p (value of a
  stored kWh at the end), sent (bool: Active on v2), preview (bool)}`, written as each half-hour ends.
- `changes`: mode changes `{at, mode, reason}` (capped at 200 a day).

Recorded whenever engine v2 is stepped (in control or preview). `sensor.pe_v2_history` (state = the date shown) with attributes
`{date, earliest, latest, in_control, series: [{t, level, expected, mode, import_p, export_p, value_p, sent}], changes: [...last 60],
note}` under 12 KB; day picker event `pe_v2_history_day {date}` (same rules as `pe_history_day`: within the kept days). Default day:
today.

v1's `_record_ran` writes only while engine v1 is the chosen engine; `sensor.pe_plan_history` gains `in_control` (from
`day_engine`) and the label "Start of day (engine v1 was passive)" when v2 ran that day.

## 4. Card and dashboard (work package C)

- Icons `pe:engine-v1`, `pe:engine-v2` (`window.customIcons.pe = {getIcon, getIconList}`; 24x24 single SVG path, engine outline with
  the digit cut out, even-odd fill). If the card isn't loaded HA shows no icon, as for any custom icon.
- `powerengine-engine-badge-card` (config `engine: v1|v2`), logic as a pure helper `engineBadge(states, engine)` with tests.
  Mode and pause from the entities the card already reads.
- Dashboard: Plan view title "Engine v1", icon `pe:engine-v1`; Engine v2 icon `pe:engine-v2`; each starts (after the demo card)
  with the badge; Plan history view removed and its cards appended to the Engine v1 view under a `heading` "History" (the
  `powerengine-history-date-card` and charts unchanged); `powerengine-v2-history-card` appended to the Engine v2 view under
  "History"; on Costs a heading "Engines compared, same day" and `powerengine-engine-compare-card` after the waterfall. Search both
  repos for links to the `history` path and point them at `plan`. Re-record `tests/golden/` dashboard renders.
- `powerengine-v2-history-card`: pure `v2HistoryView(attrs)`; same visual language as `powerengine-v2-plan-card` (reuse its chart
  helpers). `powerengine-engine-compare-card`: pure `engineCompareView(attrs)`; table, best marked, totals, calibration line, a
  "waiting for the first comparison" state explaining it needs one full day of forecast records first.
- Feature `engine_compare` in the Costs topic of the config card's lists.
- `MIN_CARD_VERSION` (app) to 0.9.110: the dashboard names new cards and icons.

## 5. Checks

- Replay (`tests/test_replay.py`) unchanged; dashboard goldens re-recorded only for the dashboard changes.
- Tests: snapshot deltas and reconstruction at time t; size bound; engine tag and `day_engine`; v2 history recording and size;
  runner on a synthetic day (two engines, self-use, bound, calibration, refused days); world snapshot feed; the nightly scheduler
  (subprocess start, timeout, one at a time, backfill order) with the subprocess faked; sensor contract and size.
- A cross-check: the runner on a demo pack day (with a snapshot synthesised from the pack's derived forecast) gives the same savings
  as `tools/engine_compare.py` on that day within 1p.

## 6. As built: work package B (the comparison)

- `pe_core/compare/`: `harness.py` (fake AppDaemon, clock and `loaded_app`, moved from `tests/replay_harness.py` and
  `tools/engine_compare.py`, which import it), `replay.py` (the whole-app closed-loop runs, costing and bound that
  `tools/engine_compare.py` is now a thin front for), `snapfeed.py` (reader of the 2.2 snapshot: `Snapshot.states_at /
  profile_at / first_seen_at`, `SnapshotFeed` for the world), `day.py`, `settings.py`, `results.py`, `run.py`,
  `schedule.py` (the nightly bookkeeping, subprocess injected for tests), `sensor.py`.
- The world takes `capacity_kwh`, `efficiency`, `charge_limit_w`, `discharge_limit_w`, `floor_pct` and a `feed`
  (defaults are the demo's). `pe_core/demo/pack.py` gained `day_from_records`, `day_complete`, `day_stats`, `forecast`
  (the pack builder uses them; its output is unchanged).
- The replayed app learns no house profile from the world: its `_rebuild_profile` is replaced by the snapshot's profile as it
  was at each time (none until the snapshot has one), the `_learn_load` and `_backfill` timers are skipped, and the smart
  slots' first-seen times are put on its slot records. The owner's `engine_v2_state.json` and slot history (cut at the start
  of the day) are copied in. Under v1 the v2 preview is switched off in the replay (it changes nothing v1 does).
- Settings: the owner's config with the inputs remapped to the world's entities (fixed values such as the battery's size stay
  the owner's), Active on RAM control, direct publishing, `house_load_includes_ev` true (the world's house load includes the
  car), and smart-charge requests, the tariff simulator, cold caution and `engine_compare` off. The world's battery is the
  owner's configured size, one-way efficiency, power limits (capped at the RAM ceiling) and `battery_floor_soc`.
- Run time on the 4-core build container, one full day, both engines, self-use and bound: about 3 minutes (sunny pack day).
