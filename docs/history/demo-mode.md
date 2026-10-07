# Demo mode

Moved out of CLAUDE.md (Oct 2026) to keep every session's context small. Read this file only when the task touches this area. The rules that must always hold are listed in CLAUDE.md under "Rules that live in the history files".

## Demo mode plan

Plan: `docs/plans/demo-and-easier-install.md`. A2, A3 and D1 are parked; the options are in #192.

Goal: PowerEngine runs with no MQTT broker and no real inverter (a demo), and installs more easily.

- **B1, publisher adapter. Done** (`pe_core/adapters/publish.py`): `StatePublisher` (`discover`, `publish`, `preset`,
  `retire`, `retire_all`, `available`) with `MqttPublisher` (exactly the old topics, payloads, QoS 1, retained) and
  `DirectPublisher(set_state, remove_state=None)` (HA states through AppDaemon, same entity ids as MQTT discovery's
  `default_entity_id`). System setting `publisher`: `auto` (MQTT if the AppDaemon MQTT plugin is there, else direct) /
  `mqtt` / `direct`. **Rule: all entity output goes through `self._get_publisher()`; never call `mqtt_publish` or build a
  topic in the app.** Direct-mode gaps: no entity registry (no unique ids, gone when HA restarts until the next publish),
  no retire from HA (marked `unavailable`), and no commands from HA (switches and selects are read-only) until B2.
- **B2, commands in direct mode. Done** (`pe_core/commands.py`, `powerengine.py` `_listen_for_commands` / `_on_command`).
  With MQTT the app never receives a command message: HA's switch publishes to the retained command topic, the state
  changes, and the app's `listen_state` handlers (pause, guards, history) react. Direct mode does the same by setting the
  entity's state itself: `_on_command(key, value)` validates, then `publisher.preset`. Direct mode only (no listeners in
  MQTT mode) hears HA's `call_service` event (`switch.turn_on/turn_off/toggle`, `select.select_option/select_next/
  select_previous`, `number.set_value`; `service_data.entity_id` as string, comma string or list; other entities ignored) and
  the card's fallback event **`pe_command`**: `{"entity_id": "switch.pe_ctl_pause", "value": "ON"}` (`value`: "ON" / "OFF" /
  "toggle" for a switch, one of the select's options, a number). Fire it from the card with
  `hass.connection.sendMessagePromise({type: "fire_event", event_type: "pe_command", event_data: {...}})` (admin only)
  when HA refuses a service call because the domain has no platform. Invalid values are ignored.
- **C1, demo data pack. Done** (`apps/powerengine/demo/pack.json`, about 19 KB, inside the app folder HACS installs;
  pure reader `pe_core/demo/pack.py`: `load_pack`, `days`, `day_at`, `row_at`, `smart_slots`, `axle_events`). Four
  recorded, scrubbed days, picked by rule from the owner's cost records: `sunny` (most solar), `dull` (least solar),
  `axle` (largest grid-services export; its title is `<<event>> event day`, fill it with `names.fill`) and `car` (most
  car kWh); a day that wins two rules gives the later rule its next best. It is September data: no "winter" names.
  Per half-hour: house, car, solar, a derived solar forecast (recorded solar smoothed over 2 h, scaled per day within
  10%), rates (act, std, ovn, exp, standing), recorded SoC at the start, smart-slot, axle and free flags, and a small
  `as_recorded` grid/battery section. Times are offsets from local midnight (Europe/London); `day_at` maps wall-clock
  times, so a spring-forward date has 46 rows and an autumn one 50 (the repeated hour plays twice).
  - **Rebuild:** `python3 tools/build_demo_pack.py <config>/powerengine/costs [--glob "2026-09-*.json"]`. It prints
    the chosen days and their totals and writes the pack. Only complete days count (48 consecutive records from local
    midnight, at least 95% coverage, no duplicate starts).
  - **Scrub rule:** the pack holds numbers, times and flags only. The builder refuses (exit 2) any string in a source
    record except `start`, `source: "history"`, `fv` (a rates tag, dropped) and `v.event` (`axle` or `free_power`), so an
    entity id, account number, MPAN, serial or site id can never be copied in. Never commit a pack from an unscrubbed
    source, never put private day files in tests (they use synthetic days), and don't loosen the whitelist without
    looking at what the new string is.
- **C2, the demo world. Done** (`pe_core/demo/world.py`, `gate.py`, `demo/config.template`, the `_demo_*` methods in
  `powerengine.py`). **Start it** with `demo: sunny` (or `dull`, `axle`, `car`) in the app's apps.yaml entry, and restart
  AppDaemon; remove the line to go back. The app then runs on a simulated home: `DemoWorld` (18 kWh battery, 95% each
  way, 5 kW, 12% floor, remote-control failsafe after 5 min) moves one pack day onto today, and the real Solis/Kraken/
  Zappi/Solcast/Axle adapters read its `demo_*` entities unmodified (`world.IDS` lists them; RC discovery finds the
  `battery_control_override` ones). The settings are a fresh copy of `demo/config.template` in `<config>/powerengine/demo/`
  (the folder is wiped at each demo start; nothing else there or in `<config>/powerengine/` is touched; not `.yaml`, or
  AppDaemon would load it as app config). Publishing is direct. Off: smart-charge requests, tariff simulator, cold-battery
  weather, the GitHub release check. `sensor.pe_diag_version` carries `attributes.demo` (`day`, `title` with the names
  filled, `note`) for C3's banner.
  - **The gate rule: in demo mode every call to Home Assistant goes through `DemoGate`.** The app replaces its own
    `get_state`, `get_history`, `call_service`, `set_state` and `fire_event` on the instance: service calls go only to the
    world (what it refuses, such as notifications and logbook entries, is dropped and logged once); `set_state` only for
    `pe_` entities; `fire_event` only the card's answers; `get_state` sees demo entities and PowerEngine's own `pe_`
    entities, nothing else (no `zone.home`). So **always call `self.call_service` / `self.fire_event` in `powerengine.py`;
    never call them on anything else** (an adapter's `ha`, `hass.Hass...`). `test_no_direct_call_to_home_assistant_bypasses_
    the_gate` reads the source and fails on a new call anywhere else.
- **C3, controller side. Done** (`_on_demo`, `_saved_demo_day`, `_wipe` in `powerengine.py`; `tests/test_demo_control.py`).
  The card's contract:
  - `sensor.pe_diag_version` attributes: `setup` is `"unconfigured"` (no real config.yaml) or `"configured"`; `demo` is
    `null`, or `{"day", "title" (names filled), "days": [{"key","title"}, ...all pack days], "note": "Recorded data from a
    real home. Nothing is controlled."}`. An unconfigured app publishes the sensor (direct publishing when there is no MQTT).
  - Event **`pe_demo`** (admin, over the websocket): `{"action":"start","day":"sunny"}`, `{"action":"day","day":"dull"}`,
    `{"action":"exit"}`. Answer event **`pe_demo_result`**: `{"ok": true|false, "message": "<plain words>"}`. Only that
    event name is handled. `start` is refused (`ok:false`) when a real config.yaml exists and no demo is running, so a demo
    never takes over a real system. Bad days, unknown actions and `day` with no demo running are refused. The app arg
    `demo` still wins (then start/day/exit are refused, with a message).
  - `start`/`day` write `<save dir>/demo.json` `{"day": ...}` and re-initialise; `exit` removes it and re-initialises
    (unconfigured, or back to the real config). A saved demo.json is ignored when a real config exists. Demo settings are
    discarded because the config copy is remade on every start.
  - **Re-initialise = `initialize()` again, after `_wipe()`** (no `restart_app`). From the first `initialize()` the app
    records every attribute it sets (`__setattr__` into `_touched`) and every timer/listener handle (instance wrappers on
    `run_in/run_every/run_daily/listen_event/listen_state`). `_wipe()` runs `terminate()`, cancels the handles (skipping
    `run_in` timers already due) and deletes the attributes (this also puts the real `get_state` etc. back).
    **Rule: keep `initialize()` and what it sets in `self`; don't set attributes from AppDaemon threads outside it.**
    `test_no_duplicate_timers_or_listeners_after_start_day_exit_start` compares the live handle set with the fresh one.
  - **Handles can be Tasks.** AppDaemon 4.5 returns an asyncio Task/Future (not the handle string) from `run_every`,
    `run_in`, `listen_state` etc. when called on its event loop; 4.4 returns the string. `_track` records the handle
    inside a finished Task, or records it when a pending one finishes (and cancels it at once if a wipe came first).
  - **Direct publishing and AppDaemon 4.5's `set_state`.** Its REST write cleans the payload: `true` becomes "true",
    and `false`, `null` and every 0 are dropped from attributes (series lose their zeros and shift; config booleans
    arrive as text). So `_lossless_set_state` posts attributes with such values to HA's states endpoint itself
    (`_rest_states_poster`, `pe_` entities only, so the gate rule holds) and `check_existence=False` stops the
    "Entity not found" warnings on creation (only passed where `set_state` names it: 4.4 would make it an attribute).
  - **The demo's clock is the pack's zone** (Europe/London), not AppDaemon's `time_zone` (often UTC on a fresh install,
    which put the recorded day an hour out), and the app's `tz` is set to it while a demo runs.
  - Dashboard: with no real config a demo also writes `<save dir>/dashboard.yaml` (plus C2's `demo/` copy). Every view of
    `dashboard.lovelace` starts with `- type: custom:powerengine-demo-card` (full width, no options); the golden fixture
    has exactly those 24 lines more.
