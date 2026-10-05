"""Engine v2: an event-driven planning and control engine, chosen with the system setting `engine`.

Design: docs/plans/engine-v2.md; build plan: docs/plans/engine-v2-build.md.

Layers (each its own module, all pure: no Home Assistant, no AppDaemon):

    settings.py   the v2 settings catalogue and the parsed `V2Settings` (the `engine_v2:` block of config.yaml)
    types.py      the data passed between layers (the contract every module and the app build against)
    observe.py    layer 1: filtered battery level, events from changes in the readings, forecast drift
    forecast.py   layer 2: segments (timed by the data's own changes) with low/mid/high scenarios
    value.py      layer 3: stochastic dynamic programming for the value of a stored kWh, thresholds, expected timeline
    rules.py      layer 4: which modes are allowed or forced, floors and caps (used by layers 3 and 5 alike)
    execute.py    layer 5: the mode automaton (exit conditions, hysteresis, minimum time, deadlines) -> Decision
    triggers.py   when to re-check and when to revalue (debounce, coalescing, CUSUM drift, backstop)
    learning.py   scenario weights, solar bias, the battery-level offset per mode
    engine.py     `EngineV2`: the facade the app calls once per tick
    publish.py    the published sensors' states and attributes (the card's contract)

Layer 6 (inverter control) is shared with engine v1 and lives in the app: v2 produces the same `Decision` type.
"""
