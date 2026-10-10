# PowerEngine simulator (`tools/sim`)

Replays stored days through the **real app** (engine v2 only, Active, RAM remote control, the owner's settings, the day's
recorded house, sun, prices, smart slots and grid events, and the forecasts as they were), using the same machinery as
the nightly comparison (`pe_core.compare`). It can run any branch's code and any engine settings, and shows the plan in a
GUI. Design: `docs/plans/engine-v2-mvp-and-simulator.md`.

It reads the archive (`~/pe-data`, never written, never committed) and writes only to `~/pe-sim` (cached runs, code
exported from git refs, saved variants, `last/` report). No network, no Home Assistant.

```
tools/sim/pe_sim.py serve                       # the GUI, http://127.0.0.1:8765/
tools/sim/pe_sim.py run --days 2026-10-06..2026-10-09 [--code REF] [--set key=value] [--sweep key=a,b,c] [--out DIR]
tools/sim/pe_sim.py check --days ...            # the runner against the nightly comparison's own code (exact match)
```

- `--code`: a git ref (branch, tag, sha), exported with `git archive`, never checked out; omitted, this checkout as it stands.
- `--set`: an `engine_v2` setting (`switch_cost_p=3`) or a module constant (`engine_v2.value.NAME=1`). `--sweep` makes a grid.
- A baseline (this checkout, defaults) runs beside any variant, so each result is compared with it.
- `--out` (and the GUI's `last/`) get `report.md` (the scoreboard) and `report.json` (per day: score, mode changes, the level
  every 5 minutes, one line per re-plan, prices): the small form to hand to Claude.
- Runs are cached by code, day, inputs, settings and seed; asking again is instant. `--force` ignores the cache.
- A full day is about 2 minutes; several run at once (`--jobs`).

**Fidelity.** The result of a day depends on app state that `_cycle` and `_evaluate` keep (slot tracking, the overnight
window, the mode), so the runner drives the whole app, not the engine alone. `check` proves it against the nightly code.
Seeding uses the newest archived config, learned state and slot history, so a replay of an earlier day knows slightly
more than the live app did then; the nightly comparison has the same property.

**The GUI** lays the plan out with the card's own code (`viewer/plan_layout.js`, vendored from the card by
`vendor_card_layout.sh`; rerun it when the card's chart changes). It is served on 127.0.0.1 only; requests must name that host,
and starting a run needs a header a web page on another site cannot send.
