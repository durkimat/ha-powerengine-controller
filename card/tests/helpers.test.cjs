const test = require("node:test");
const assert = require("node:assert");
const h = require("../ha-powerengine-card.js");

const battery = { key: "battery_power", kind: "power", signed: true, sign_note: "+ discharging, - charging", domains: ["sensor"], required: "yes" };
const grid = { key: "grid_power", kind: "power", signed: true, sign_note: "+ importing, - exporting", domains: ["sensor"], required: "yes" };
const soc = { key: "battery_soc", kind: "percent", domains: ["sensor"], required: "yes", suggest: ["^sensor\\.solis_battery_soc$"] };
const rates = { key: "import_rates_today", kind: "list", attribute: "rates", domains: ["event", "sensor"], required: "yes" };
const cap = { key: "battery_capacity", kind: "static", static_ok: true, static_unit: "kWh", suggest_static: 18, required: "yes" };
const ctl = { key: "smart_target_soc", kind: "control", domains: ["number"], required: "no" };
const rateNow = { key: "import_rate_now", kind: "rate", domains: ["sensor"], required: "yes",
  suggest: ["^sensor\\.edf_energy_electricity_.*_current_rate$"], suggest_not: ["export"] };

const W = (v) => ({ state: String(v), attributes: { unit_of_measurement: "W" } });

test("sign notes parse", () => {
  assert.deepStrictEqual(h.parseSignNote("+ discharging, - charging"), { pos: "discharging", neg: "charging" });
});

test("battery reads as discharging, and invert flips it", () => {
  assert.match(h.readout(battery, { entity: "sensor.b" }, W(1040)).readsAs, /discharging 1\.04 kW/);
  assert.match(h.readout(battery, { entity: "sensor.b", invert: true }, W(1040)).readsAs, /charging 1\.04 kW/);
});

test("grid meter that reports export as negative needs invert", () => {
  // Solis meter: -240 W while exporting
  assert.match(h.readout(grid, { entity: "sensor.g" }, W(-240)).readsAs, /exporting 240 W/);
  assert.match(h.readout(grid, { entity: "sensor.g", invert: true }, W(-240)).readsAs, /importing 240 W/);
});

test("rate list summary shows count and the current rate in pence", () => {
  const now = new Date("2026-09-22T17:10:00+01:00");
  const items = [];
  for (let i = 0; i < 48; i++) {
    const s = new Date(Date.parse("2026-09-22T00:00:00+01:00") + i * 1800e3);
    items.push({ start: s.toISOString(), end: new Date(s.getTime() + 1800e3).toISOString(), value_inc_vat: i < 10 ? 0.06993 : 0.302831 });
  }
  const r = h.readout(rates, { entity: "event.r" }, { state: "x", attributes: { rates: items } }, now);
  assert.match(r.text, /48 half-hour rates, now 30\.28p/);
});

test("static values show and validate", () => {
  assert.strictEqual(h.readout(cap, { value: 18 }).text, "Fixed: 18 kWh");
  assert.strictEqual(h.instantProblem(cap, { value: "lots" }), "Enter a number");
});

test("instant problems", () => {
  assert.strictEqual(h.instantProblem(soc, { entity: "sensor.x" }, { state: "71", attributes: { unit_of_measurement: "%" } }), "");
  assert.match(h.instantProblem(soc, { entity: "sensor.x" }, { state: "71", attributes: { unit_of_measurement: "W" } }), /Unit is W/);
  assert.strictEqual(h.instantProblem(soc, { entity: "sensor.x" }, undefined), "Entity not found");
  assert.match(h.instantProblem(ctl, { entity: "number.edf_x_intelligent_bump_charge" }, {}), /Never allowed/);
  assert.strictEqual(h.instantProblem(soc, null), "Required");
});

test("suggestions respect exclusions", () => {
  const ids = ["sensor.edf_energy_electricity_1_export_current_rate", "sensor.edf_energy_electricity_1_current_rate"];
  assert.strictEqual(h.suggestEntity(rateNow, ids), "sensor.edf_energy_electricity_1_current_rate");
});

test("first use pre-fills suggestions and a main plant", () => {
  const ids = ["sensor.solis_battery_soc", "sensor.solis_pv_total_power", "sensor.solis_power_generation_today"];
  const { draft, fresh } = h.initialDraft({}, [soc, cap], ids);
  assert.ok(fresh);
  assert.deepStrictEqual(draft.inputs.battery_soc, { entity: "sensor.solis_battery_soc" });
  assert.deepStrictEqual(draft.inputs.battery_capacity, { value: 18 });
  assert.strictEqual(draft.solar_plants[0].power.entity, "sensor.solis_pv_total_power");
  assert.strictEqual(draft.operation.mode, "passive");
});

test("saved config is not overwritten by suggestions", () => {
  const saved = { inputs: { battery_soc: { entity: "sensor.other" } } };
  const { draft, fresh } = h.initialDraft(saved, [soc], ["sensor.solis_battery_soc"]);
  assert.ok(!fresh);
  assert.strictEqual(draft.inputs.battery_soc.entity, "sensor.other");
});

test("buildConfig drops empty inputs and incomplete plants, keeps invert", () => {
  const out = h.buildConfig({
    inputs: { a: { entity: "sensor.a" }, b: {}, g: { entity: "sensor.g", invert: true }, c: { value: "18" } },
    solar_plants: [{ id: "main", name: "Main", power: { entity: "sensor.p" }, energy_today: { entity: "sensor.e" } }, { id: "x", power: {}, energy_today: {} }],
    features: { axle: true }, operation: { mode: "passive" },
  });
  assert.deepStrictEqual(Object.keys(out.inputs).sort(), ["a", "c", "g"]);
  assert.strictEqual(out.inputs.c.value, 18);
  assert.strictEqual(out.inputs.g.invert, true);
  assert.strictEqual(out.solar_plants.length, 1);
  assert.strictEqual(out.solar_plants[0].forecast, "none");
});

test("plant ids are unique slugs", () => {
  assert.strictEqual(h.slugify("Garage roof", ["main"]), "garage_roof");
  assert.strictEqual(h.slugify("Garage roof", ["garage_roof"]), "garage_roof_2");
  assert.strictEqual(h.slugify("2nd array", []), "p_2nd_array");
});

test("rates show in pence, money in pounds, times are not numbers", () => {
  const rate = { key: "import_rate_now", kind: "rate", domains: ["sensor"] };
  const sc = { key: "standing_charge", kind: "money", domains: ["sensor"] };
  const t = { key: "smart_target_time", kind: "control", domains: ["select"] };
  assert.strictEqual(h.readout(rate, { entity: "s.r" }, { state: "0.302831", attributes: { unit_of_measurement: "GBP/kWh" } }).text, "30.28p/kWh");
  assert.strictEqual(h.readout(sc, { entity: "s.s" }, { state: "0.570969", attributes: { unit_of_measurement: "GBP" } }).text, "\u00a30.57 per day");
  assert.strictEqual(h.readout(t, { entity: "select.t" }, { state: "11:00", attributes: {} }).text, "11:00");
});

test("settings defaults, validation and saving", () => {
  const settings = { safety: [{ key: "min_reserve_soc", default: 12, min: 0, max: 100 }, { key: "cheap_threshold_p", default: 10, min: 0, max: 100 }],
                     system: [{ key: "house_load_includes_ev", default: true }] };
  const { draft } = h.initialDraft({ safety: { cheap_threshold_p: 8 } }, [], [], settings);
  assert.strictEqual(draft.safety.min_reserve_soc, 12);
  assert.strictEqual(draft.safety.cheap_threshold_p, 8);
  assert.strictEqual(draft.system.house_load_includes_ev, true);
  assert.strictEqual(h.settingProblem(settings.safety[0], "150"), "Must be between 0 and 100");
  assert.strictEqual(h.settingProblem(settings.safety[0], "abc"), "Enter a number");
  draft.safety.min_reserve_soc = "15";
  const out = h.buildConfig(draft);
  assert.strictEqual(out.safety.min_reserve_soc, 15);
  assert.strictEqual(out.system.house_load_includes_ev, true);
});

test("battery pair replaces the single battery power sensor", () => {
  const { effectiveRole } = require("../ha-powerengine-card.js");
  const single = { key: "battery_power", required: "yes" };
  const inRole = { key: "battery_charge_power", required: "no" };
  assert.equal(effectiveRole(single, false).required, "yes");
  assert.equal(effectiveRole(single, true).required, "unused");
  assert.equal(effectiveRole(inRole, true).required, "yes");
  assert.equal(effectiveRole(inRole, false).required, "no");
});

test("notifications default to HA's notification area; off and phone are saved", () => {
  const { initialDraft, buildConfig } = require("../ha-powerengine-card.js");
  const { draft } = initialDraft({ inputs: { a: { entity: "sensor.a" } } }, [], [], {});
  assert.equal(draft.notifications.service, "persistent_notification");
  assert.equal(draft.notifications.events.health, true);
  assert.equal(draft.notifications.events.daily, false);
  assert.equal(buildConfig(draft).notifications.service, "persistent_notification");
  draft.notifications.service = "notify.mobile_app_pixel";
  draft.notifications.events.daily = true;
  assert.deepEqual(buildConfig(draft).notifications, { service: "notify.mobile_app_pixel", events: { health: true, inputs: true, axle: true, free_power: true, daily: true, alexa: false, simulator: true }, alexa: { services: ["notify.alexa_media_everywhere"], hours_before: [8, 5, 2, 1, 0], earliest_weekday: "07:30", earliest_weekend: "09:00", latest: "21:00", intro: "Daddybot here.", finish: true } });
  const off = initialDraft({ inputs: {}, notifications: { service: "" } }, [], [], {}).draft;
  assert.equal(off.notifications.service, "off");
  assert.equal(buildConfig(off).notifications.service, "off");
});

test("testSummary: idle when the sensor has never been set", () => {
  assert.equal(h.testSummary(undefined).status, "idle");
  assert.deepEqual(h.testSummary(undefined).problems, []);
  assert.equal(h.testSummary({ state: "unknown", attributes: {} }).status, "idle");
});

test("testSummary: steps become readable lines", () => {
  const s = h.testSummary({ state: "failed", attributes: { action: "charge", minutes: 3, problems: ["start: timed_charge_current did not read back"],
    steps: [{ time: "2026-09-25T10:00:00+00:00", what: "wrote", writes: [{}, {}, {}] },
            { time: "2026-09-25T10:00:10+00:00", what: "read back (start)", ok: false, mismatched: ["timed_charge_current"], soc: 54.6, battery_w: -2100 }] } });
  assert.equal(s.status, "failed");
  assert.deepEqual(s.lines, ["10:00:00 wrote: 3 writes", "10:00:10 read back (start): MISMATCH: timed_charge_current, SoC 55%, battery charging 2100 W"]);
});

test("testSummary: RC verdict, grid and remote-control state", () => {
  const s = h.testSummary({ state: "passed", attributes: { action: "rc_charge", verdict: "worked", explanation: "battery charged",
    steps: [{ time: "2026-09-27T20:00:30+00:00", what: "reading", battery_w: 1500, grid_w: -800, rc: "Force discharge" }] } });
  assert.equal(s.verdict, "worked");
  assert.deepEqual(s.lines, ["20:00:30 reading: battery discharging 1500 W, grid export 800 W, RC Force discharge"]);
});

test("findRcEntities prefers the solis entities", () => {
  const states = { "select.x_battery_control_override": {}, "select.solis_inverter_battery_control_override": {},
    "number.solis_inverter_battery_control_override_charge_power": {} };
  assert.deepEqual(h.findRcEntities(states), { rc_mode: "select.solis_inverter_battery_control_override",
    rc_charge_power: "number.solis_inverter_battery_control_override_charge_power" });
});

test("every test has instructions", () => {
  for (const t of h.TESTS) assert.ok(t.what && t.watch && t.checks && t.label, t.key);
  assert.deepEqual(h.TESTS.filter((t) => t.group === "rc").map((t) => t.key), ["rc_charge", "rc_discharge", "rc_hold", "rc_failsafe"]);
});

test("diagnostics: config entities, states filter and private attributes, file name", () => {
  const cfg = { inputs: { battery_soc: { entity: "sensor.soc" }, x: { value: 3 } }, solar_plants: [{ inputs: { power: { entity: "sensor.pv" } } }] };
  assert.deepEqual([...h.configEntities(cfg)].sort(), ["sensor.pv", "sensor.soc"]);
  const st = { "sensor.pe_state_battery_soc": { state: "50", attributes: {} }, "sensor.soc": { state: "50", attributes: { account_number: "123", unit_of_measurement: "%" } },
    "light.kitchen": { state: "on", attributes: {} }, "number.solis_timed_charge_current": { state: "50", attributes: {} } };
  const out = h.diagStates(st, h.configEntities(cfg));
  assert.deepEqual(Object.keys(out).sort(), ["number.solis_timed_charge_current", "sensor.pe_state_battery_soc", "sensor.soc"]);
  assert.equal(out["sensor.soc"].attributes.account_number, "(removed)");
  assert.equal(out["sensor.soc"].attributes.unit_of_measurement, "%");
  assert.equal(h.diagFileName(new Date(2026, 8, 27, 19, 5)), "powerengine-diagnostics-20260927-1905.json");
});

test("dampingNote", () => {
  assert.match(h.dampingNote({}), /appear here/);
  const st = { "sensor.pe_diag_writes_today": { state: "5", attributes: { damping: { restart: true, bursts: false },
    damping_week: { days: 7, none: 60, restart: 50, both: 42, saved_restart: 10, saved_bursts: 8 } } } };
  assert.equal(h.dampingNote(st), "Last 7 days (modelled): no dampening 60 writes; restart hold-off saved 10 writes; burst damping would have saved 8 writes more.");
});

test("liveLine", () => {
  const st = { "sensor.pe_state_battery_power": { state: "-2000" }, "sensor.pe_state_grid_power": { state: "2500" }, "sensor.pe_state_battery_soc": { state: "61.2" } };
  assert.equal(h.liveLine(st), "Battery 61% · charging 2000 W · grid import 2500 W");
});

test("buildConfig keeps use_measured on a fixed value", () => {
  const cfg = h.buildConfig({ inputs: { battery_capacity: { value: "18", use_measured: false } }, solar_plants: [], features: {}, operation: {} });
  assert.deepEqual(cfg.inputs.battery_capacity, { value: 18, use_measured: false });
  const cfg2 = h.buildConfig({ inputs: { battery_capacity: { value: 18 } }, solar_plants: [], features: {}, operation: {} });
  assert.deepEqual(cfg2.inputs.battery_capacity, { value: 18 });
});

test("measuredText", () => {
  assert.equal(h.measuredText({ states: {} }, "battery_capacity"), "(not measured yet)");
  const hass = { states: { "sensor.pe_diag_battery_capacity": { state: "18.084", attributes: { measured: true, unit_of_measurement: "kWh" } } } };
  assert.equal(h.measuredText(hass, "battery_capacity"), "(18.08 kWh measured)");
  const eff = { states: { "sensor.pe_diag_battery_efficiency": { state: "90.3", attributes: { measured: true, measured_round_trip: 88.44 } } } };
  assert.equal(h.measuredText(eff, "battery_round_trip"), "(88.4% measured)");
  assert.equal(h.measuredText({ states: { "sensor.pe_diag_battery_efficiency": { state: "90.3", attributes: { measured: false } } } }, "battery_round_trip"), "(not measured yet)");
});

test("simulator history plan and month range", () => {
  const st = { attributes: { history_request: { months: ["2025-10"], entities: { house: ["sensor.h"] }, imported: [] } } };
  assert.deepEqual(h.simHistoryPlan(st).months, ["2025-10"]);
  assert.deepEqual(h.simHistoryPlan(undefined).months, []);
  const r = h.monthRange("2025-12");
  assert.equal(r.start, "2025-11-30T00:00:00.000Z");
  assert.equal(r.end, "2026-01-02T00:00:00.000Z");
});

const hs = (v, a) => ({ state: v, attributes: a || {} });

test("handoverRows: PowerEngine live", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("PowerEngine"), "switch.predbat_set_read_only": hs("on"),
    "switch.pe_ctl_pause": hs("off"), "sensor.pe_state_operation_mode": hs("active") });
  assert.deepEqual(r.rows.map((x) => x.ok), [true, true, true]);
  assert.equal(r.status, "live");
});

test("handoverRows: PowerEngine selected but Passive is not live", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("PowerEngine"), "switch.predbat_set_read_only": hs("on"),
    "switch.pe_ctl_pause": hs("off"), "sensor.pe_state_operation_mode": hs("passive", { reason: "Passive: watching" }) });
  assert.equal(r.rows[2].ok, false);
  assert.equal(r.status, "not_live");
});

test("handoverRows: paused for testing is not a fault", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("PowerEngine"), "switch.predbat_set_read_only": hs("on"),
    "switch.pe_ctl_pause": hs("on"), "sensor.pe_state_operation_mode": hs("paused") });
  assert.deepEqual(r.rows.map((x) => x.ok), [true, null, null]);
  assert.equal(r.status, "paused");
});

test("handoverRows: Predbat selected but still active", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("Predbat"), "switch.predbat_set_read_only": hs("on"),
    "switch.pe_ctl_pause": hs("off"), "sensor.pe_state_operation_mode": hs("active") });
  assert.deepEqual(r.rows.map((x) => x.ok), [false, true, false]);
  assert.equal(r.status, "not_live");
});

test("handoverRows: a missing Predbat switch is not live", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("Predbat"),
    "switch.pe_ctl_pause": hs("off"), "sensor.pe_state_operation_mode": hs("passive") });
  assert.equal(r.rows[0].ok, null);
  assert.match(r.rows[0].note, /doesn't have this entity/);
  assert.equal(r.status, "not_live");
});

test("measuredText for learned rates", () => {
  assert.equal(h.measuredText({ states: {} }, "battery_max_charge_power"), "(not measured yet)");
  const hass = { states: { "sensor.pe_diag_learned": { state: "1 learned", attributes: { raw: { max_charge_kw: 4.2, max_discharge_kw: null } } } } };
  assert.equal(h.measuredText(hass, "battery_max_charge_power"), "(4.20 kW measured)");
  assert.equal(h.measuredText(hass, "battery_max_discharge_power"), "(not measured yet)");
});

test("every feature has a default", () => {
  assert.deepEqual(h.FEATURES.map((f) => f[0]).sort(), Object.keys(h.FEATURE_DEFAULTS).sort());
});

test("topicPlan puts everything somewhere, clocks together, leftovers in Other", () => {
  const plan = h.topicPlan(["battery_soc", "inverter_clock", "inverter_clock_sync", "new_thing"], ["min_reserve_soc"],
    ["axle", "learn_taper"], ["battery_location"]);
  const where = (k) => plan.find((t) => t.roles.includes(k)).key;
  assert.equal(where("inverter_clock"), "control");
  assert.equal(where("inverter_clock_sync"), "control");
  assert.equal(where("new_thing"), "other");
  assert.deepEqual(plan.find((t) => t.key === "battery").learning, ["learn_taper"]);
  assert.deepEqual(plan.find((t) => t.key === "cold").system, ["battery_location"]);
});

test("smart-charge request options are in the car topic, not Other", () => {
  const settings = ["ev_charger_kw", "smart_max_requests_per_day", "smart_min_gap_min", "smart_lookahead_h", "car_min_charge_min"];
  const features = ["smart_charge_optimisation", "slots_whole_house", "smart_skip_full_car"];
  const plan = h.topicPlan([], settings, features, []);
  const car = plan.find((t) => t.key === "car");
  for (const k of settings) assert.ok(car.settings.includes(k), k);
  for (const k of features) assert.ok(car.features.includes(k), k);
  assert.equal(plan.find((t) => t.key === "other"), undefined);
});

test("smart-charge feature defaults and help", () => {
  assert.equal(h.FEATURE_DEFAULTS.slots_whole_house, true);
  assert.equal(h.FEATURE_DEFAULTS.smart_skip_full_car, false);
  const help = Object.fromEntries(h.FEATURES.map((f) => [f[0], f[2]]));
  assert.ok(!/at most 6/.test(help.smart_charge_optimisation));
  assert.ok(/within the limits below/.test(help.smart_charge_optimisation));
});

test("roleNeed", () => {
  const d = (mode, f) => ({ operation: { mode }, features: f || {} });
  assert.equal(h.roleNeed({ key: "battery_soc", required: "yes" }, d("passive")).level, "req");
  assert.equal(h.roleNeed({ key: "timed_charge_current", required: "no" }, d("passive")).level, "cond");
  assert.equal(h.roleNeed({ key: "timed_charge_current", required: "no" }, d("active")).badge, "Required to go live");
  assert.equal(h.roleNeed({ key: "axle_event_active", required: "axle" }, d("passive", { axle: false })).level, "cond");
  assert.equal(h.roleNeed({ key: "axle_event_active", required: "axle" }, d("passive", { axle: true })).level, "req");
  assert.equal(h.roleNeed({ key: "smart_target_soc", required: "no" }, d("passive", { smart_charge_optimisation: true })).level, "req");
  assert.equal(h.roleNeed({ key: "battery_soh", required: "no" }, d("passive")).level, "opt");
  assert.equal(h.roleNeed({ key: "battery_power", required: "yes" }, d("passive"), true).level, "unused");
});

test("matchesSearch", () => {
  assert.ok(h.matchesSearch("Inverter clock sensor.solis_rtc", "clock rtc"));
  assert.ok(!h.matchesSearch("Main supply fuse", "clock"));
  assert.ok(h.matchesSearch("anything", "  "));
});

test("handoverRows: Predbat missing is safe under PowerEngine", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("PowerEngine"),
    "switch.pe_ctl_pause": hs("off"), "sensor.pe_state_operation_mode": hs("active") });
  assert.equal(r.rows[0].ok, true);
  assert.match(r.rows[0].note, /isn't connected/);
  assert.equal(r.status, "live");
});

test("diagnostics history adds the mapped raw meters and the grid cross-check", () => {
  const ids = h.diagHistoryIds({ inputs: { grid_power: { entity: "sensor.solis_meter_active_power" },
    grid_power_reference: { entity: "sensor.myenergi_67lr_power_grid" }, battery_capacity: { value: 18 } } });
  assert.ok(ids.includes("sensor.solis_meter_active_power"));
  assert.ok(ids.includes("sensor.myenergi_67lr_power_grid"));
  assert.ok(ids.includes("sensor.pe_diag_grid_check"));
  assert.ok(ids.includes("sensor.pe_state_battery_power"));
  assert.equal(new Set(ids).size, ids.length);
  assert.ok(h.diagHistoryIds(null).includes("sensor.pe_state_grid_power"));
});

test("update card finds the PowerEngine repositories and reads the versions", () => {
  const repos = h.peRepos([{ id: 1, full_name: "durkimat/ha-powerengine-controller", installed: true },
    { id: 2, full_name: "durkimat/ha-powerengine-card", installed: true },
    { id: 3, full_name: "hacs/integration", installed: true },
    { id: 4, full_name: "someone/powerengine-fork", installed: false }]);
  assert.deepEqual(repos.map((r) => r.id), [1, 2]);
  const states = { "sensor.pe_diag_version": { state: "0.9.45" },
    "update.powerengine_update": { state: "on", attributes: { installed_version: "v0.9.45", latest_version: "v0.9.46" } },
    "update.powerengine_card_update": { state: "off", attributes: { installed_version: "v0.9.45" } } };
  const v = h.versionLine(states);
  assert.equal(v.running, "0.9.45");
  assert.deepEqual(v.parts, ["card v0.9.45", "app v0.9.45 → v0.9.46 available"]);
});

test("log card rows and times, and escaping", () => {
  const attrs = { recent: [{ t: "2026-09-28T12:00:00+01:00", l: "I", m: "a" }], warnings: [{ t: "x", l: "W", m: "b" }] };
  assert.equal(h.logRows(attrs, true)[0].m, "b");
  assert.equal(h.logRows(attrs, false)[0].m, "a");
  assert.deepEqual(h.logRows(null, true), []);
  assert.equal(h.logWhen("not a date"), "not a date");
  assert.equal(h.escHtml('<b>"x"&'), "&lt;b&gt;&quot;x&quot;&amp;");
});

test("version line shows a release HACS hasn't seen yet, with its notes", () => {
  const v = h.versionLine({ "sensor.pe_diag_version": { state: "0.9.48" },
    "sensor.pe_diag_update": { state: "available", attributes: { latest: "0.9.50", notes: "### 0.9.50\n\n- x" } } });
  assert.equal(v.released, "0.9.50");
  assert.ok(v.notes.startsWith("### 0.9.50"));
  assert.equal(h.versionLine({ "sensor.pe_diag_update": { state: "up to date", attributes: {} } }).released, null);
});

test("waterfall rows chain: totals reset the baseline, steps float between running totals", () => {
  const steps = [
    { label: "No solar or battery", kind: "total", value: 20 },
    { label: "Solar", kind: "step", value: -8 },
    { label: "EDF tariff", kind: "step", value: -4 },
    { label: "Battery on self-use", kind: "step", value: -3 },
    { label: "PowerEngine", kind: "step", value: -2 },
    { label: "Day-to-day cost", kind: "subtotal", value: 3 },
    { label: "Battery carry-over", kind: "step", value: 0.5 },
    { label: "You paid", kind: "total", value: 3.5 },
  ];
  const rows = h.waterfallRows(steps);
  assert.equal(rows.length, 8);
  assert.deepEqual([rows[0].from, rows[0].to], [0, 20]);
  assert.deepEqual([rows[1].from, rows[1].to], [20, 12]);
  assert.deepEqual([rows[2].from, rows[2].to], [12, 8]);
  assert.deepEqual([rows[3].from, rows[3].to], [8, 5]);
  assert.deepEqual([rows[4].from, rows[4].to], [5, 3]);
  assert.deepEqual([rows[5].from, rows[5].to], [0, 3]);       // subtotal resets the baseline
  assert.deepEqual([rows[6].from, rows[6].to], [3, 3.5]);
  assert.deepEqual([rows[7].from, rows[7].to], [0, 3.5]);     // final total also resets
  assert.equal(rows[rows.length - 1].to, 3.5);
});

test("waterfall rows: a day you earned money gives a negative running total", () => {
  const steps = [
    { label: "No solar or battery", kind: "total", value: 5 },
    { label: "Solar", kind: "step", value: -3 },
    { label: "EDF tariff", kind: "step", value: -2 },
    { label: "Battery on self-use", kind: "step", value: -2 },
    { label: "PowerEngine", kind: "step", value: -1 },
    { label: "Day-to-day cost", kind: "subtotal", value: -3 },
    { label: "Battery carry-over", kind: "step", value: 0 },
    { label: "You paid", kind: "total", value: -3 },
  ];
  const rows = h.waterfallRows(steps);
  const scale = h.waterfallScale(rows);
  assert.ok(scale.min < -3 && scale.max > 5);      // the scale always spans 0
  assert.equal(rows[rows.length - 1].value, -3);
});

test("waterfall rows: an all-zero day still produces a valid, non-degenerate scale", () => {
  const steps = [
    { label: "No solar or battery", kind: "total", value: 0 },
    { label: "Solar", kind: "step", value: 0 },
    { label: "Day-to-day cost", kind: "subtotal", value: 0 },
    { label: "You paid", kind: "total", value: 0 },
  ];
  const rows = h.waterfallRows(steps);
  const scale = h.waterfallScale(rows);
  assert.ok(scale.min < 0 && scale.max > 0);       // degenerate (all-equal) range still gives room either side
  assert.equal(rows.every((r) => r.from === 0 && r.to === 0), true);
});

test("waterfall scale always includes zero even when every value is positive", () => {
  const rows = h.waterfallRows([
    { label: "No solar or battery", kind: "total", value: 10 },
    { label: "You paid", kind: "total", value: 12 },
  ]);
  const scale = h.waterfallScale(rows);
  assert.ok(scale.min <= 0);
  assert.ok(scale.max >= 12);
});

test("pct: maps a value to its 0-100 position within a scale", () => {
  assert.equal(h.pct(5, { min: 0, max: 10 }), 50);
  assert.equal(h.pct(0, { min: 0, max: 10 }), 0);
  assert.equal(h.pct(10, { min: 0, max: 10 }), 100);
  assert.equal(h.pct(-5, { min: -10, max: 10 }), 25);
});

test("pct: handles a negative-only scale and a degenerate (zero-width) scale", () => {
  assert.equal(h.pct(-5, { min: -10, max: 0 }), 50);
  assert.equal(h.pct(3, { min: 3, max: 3 }), 50);   // no span: pick the middle rather than divide by zero
});


test("waterfall short labels fit narrow columns", () => {
  assert.equal(h.waterfallShortLabel("You paid (after Axle payments)"), "You paid");
  assert.equal(h.waterfallShortLabel("Battery carry-over"), "Carry-over");
  assert.equal(h.waterfallShortLabel("Solar"), "Solar");
});

test("compact £ values: whole pounds from £10, one decimal below, minus sign for savings", () => {
  assert.equal(h.compactGbp(36.9), "£37");
  assert.equal(h.compactGbp(-19.64), "−£20");
  assert.equal(h.compactGbp(-7.67), "−£7.7");
  assert.equal(h.compactGbp(0.41), "£0.4");
  assert.equal(h.compactGbp(0.02), "£0.0");
});

// --- supplier and device names (step 6): identical text for EDF / Zappi / Solcast / Solis / Axle ---

const HIS_NAMES = { supplier: "EDF", tariff: "EDF tariff", dispatch: "EDF smart slot", dispatch_short: "EDF slot",
  smart_charge: "EDF smart charge", ev_charger: "Zappi", forecast: "Solcast", inverter: "Solis", event: "Axle" };
const before = require("./card_texts_before_names.json");   // the card's texts as they were, frozen from before names

test("fillNames replaces known terms, falls back to neutral words, leaves unknown ones alone", () => {
  assert.equal(h.fillNames("Ask <<supplier>> about the <<ev_charger>>", HIS_NAMES), "Ask EDF about the Zappi");
  assert.equal(h.fillNames("Ask <<supplier>> about the <<ev_charger>>", null), "Ask your supplier about the car charger");
  assert.equal(h.fillNames("<<supplier>>", {}), "your supplier");
  assert.equal(h.fillNames("<<supplier>>", { supplier: "Octopus" }), "Octopus");
  assert.equal(h.fillNames("<<nonsense>> and << spaced >>", HIS_NAMES), "<<nonsense>> and << spaced >>");
  assert.equal(h.fillNames("no placeholders", HIS_NAMES), "no placeholders");
});

test("with his names every feature, topic and notification text is exactly what it was", () => {
  assert.deepStrictEqual(h.FEATURES.map((f) => f.map((x) => (typeof x === "string" ? h.fillNames(x, HIS_NAMES) : x))),
    before.FEATURES);
  assert.deepStrictEqual(h.NOTIFY_EVENTS.map((f) => f.map((x) => (typeof x === "string" ? h.fillNames(x, HIS_NAMES) : x))),
    before.NOTIFY_EVENTS);
  assert.deepStrictEqual(h.TOPICS.map((t) => ({ key: t.key, title: h.fillNames(t.title, HIS_NAMES),
    note: t.note ? h.fillNames(t.note, HIS_NAMES) : null })), before.TOPICS);
  assert.equal(h.fillNames(h.SCREEN, HIS_NAMES), before.SCREEN);
});

test("with another supplier's names no card text names EDF, Zappi or Axle (bar the simulator's comparison)", () => {
  const names = { ...HIS_NAMES, supplier: "Octopus", tariff: "Octopus tariff", smart_charge: "Octopus intelligent charging",
    event: "Flux", ev_charger: "Hypervolt" };
  const texts = [...h.FEATURES.flat(), ...h.NOTIFY_EVENTS.flat(), ...h.TOPICS.flatMap((t) => [t.title, t.note])]
    .filter((x) => typeof x === "string").map((x) => h.fillNames(x, names));
  texts.filter((x) => !x.startsWith("Each night at 01:30")).forEach((x) => assert.doesNotMatch(x, /EDF|Axle|Zappi/, x));
});

test("no card text has a placeholder the names map doesn't know, and none is left after filling", () => {
  const texts = [...h.FEATURES.flat(), ...h.NOTIFY_EVENTS.flat(), ...h.TOPICS.flatMap((t) => [t.title, t.note]), h.SCREEN]
    .filter((x) => typeof x === "string");
  texts.forEach((x) => {
    for (const m of x.matchAll(/<<([a-z_]+)>>/g)) assert.ok(m[1] in h.NAME_FALLBACK, `unknown placeholder ${m[0]} in ${x}`);
    assert.doesNotMatch(h.fillNames(x, null), /<<[a-z_]+>>/);
  });
});

test("any label ending in ' tariff' is the waterfall's Tariff column, and the event step keeps its source's name", () => {
  ["EDF tariff", "Octopus tariff", "tariff", "Agile tariff"].forEach((l) => assert.equal(h.waterfallShortLabel(l), "Tariff"));
  assert.equal(h.waterfallShortLabel("Axle & free power"), "Axle");
  assert.equal(h.waterfallShortLabel("Flux & free power"), "Flux");
  assert.equal(h.waterfallShortLabel("You paid (after Flux payments)"), "You paid");
  assert.equal(h.waterfallShortLabel("Tariffs explained"), "Tariffs explained");
});

// --- setup card ---------------------------------------------------------------------------------------------
// Command shapes checked against hacs/integration (custom_components/hacs/websocket/__init__.py: hacs/info;
// repositories.py: hacs/repositories/list and /add; repository.py: hacs/repository/download) and home-assistant/core
// (components/hassio/websocket_api.py: supervisor/api; the frontend's fetchHassioAddonsInfo uses /addons, method get).
const APP = "durkimat/ha-powerengine-controller";
const APEX = "RomRider/apexcharts-card";
const FLOW = "slipx06/sunsynk-power-flow-card";
const hrepo = (full, id, installed, extra) => ({ id, full_name: full, installed: !!installed, installed_version: installed ? "1.0" : null, ...extra });
const allFacts = () => ({
  isAdmin: true, hacs: true, categories: ["integration", "plugin", "appdaemon"], addon: { state: "started", version: "0.16" },
  repos: [hrepo(APP, "11", true), hrepo(APEX, "12", true), hrepo(FLOW, "13", true)],
  cardsLoaded: { "apexcharts-card": true, "sunsynk-power-flow-card": true }, components: ["hacs", "hassio", "mqtt"], peVersion: "0.9.61",
});
const emptyFacts = () => ({
  isAdmin: true, hacs: true, categories: ["integration", "plugin", "appdaemon"], addon: { state: "missing" },
  repos: [], cardsLoaded: {}, components: ["hacs", "hassio"], peVersion: null,
});
const row = (rows, key) => rows.find((r) => r.key === key);

test("setup payloads match HACS and Supervisor commands", () => {
  assert.deepStrictEqual(h.hacsInfoPayload(), { type: "hacs/info" });
  assert.deepStrictEqual(h.hacsListPayload(), { type: "hacs/repositories/list", categories: ["appdaemon", "plugin"] });
  assert.deepStrictEqual(h.hacsAddPayload(APP, "appdaemon"), { type: "hacs/repositories/add", repository: APP, category: "appdaemon" });
  assert.deepStrictEqual(h.hacsDownloadPayload(12), { type: "hacs/repository/download", repository: "12" });   // id must be text
  assert.deepStrictEqual(h.addonsPayload(), { type: "supervisor/api", endpoint: "/addons", method: "get" });
});

test("repository lookup ignores case and finds nothing in an empty list", () => {
  const list = [hrepo("romrider/ApexCharts-Card", 7, true)];
  assert.strictEqual(h.findHacsRepo(list, APEX).id, 7);
  assert.strictEqual(h.findHacsRepo(list, APP), null);
  assert.strictEqual(h.findHacsRepo(null, APP), null);
});

test("install step adds an unknown repository, then downloads it", () => {
  assert.deepStrictEqual(h.installStep("app", []), { type: "hacs/repositories/add", repository: APP, category: "appdaemon" });
  assert.deepStrictEqual(h.installStep("flow", []), { type: "hacs/repositories/add", repository: FLOW, category: "plugin" });
  assert.deepStrictEqual(h.installStep("app", [hrepo(APP, 99, false)]), { type: "hacs/repository/download", repository: "99" });
  assert.strictEqual(h.installStep("nope", []), null);
});

test("add-on lookup finds an AppDaemon slug and its state", () => {
  const r = (state) => ({ addons: [{ slug: "core_mosquitto", state: "started" }, { slug: "a0d7b954_appdaemon", state, name: "AppDaemon", version: "0.16" }] });
  assert.strictEqual(h.addonFrom(r("started")).state, "started");
  assert.strictEqual(h.addonFrom(r("stopped")).state, "stopped");
  assert.strictEqual(h.addonFrom({ addons: [{ slug: "core_mosquitto", state: "started" }] }).state, "missing");
  assert.strictEqual(h.addonFrom({ addons: [{ slug: "abc123_appdaemon4", state: "started" }] }).state, "started");   // a community fork
  assert.strictEqual(h.addonFrom(null).state, "missing");
});

test("version sensor: unavailable is not running", () => {
  assert.strictEqual(h.peRunning({ "sensor.pe_diag_version": { state: "0.9.61" } }), "0.9.61");
  assert.strictEqual(h.peRunning({ "sensor.pe_diag_version": { state: "unavailable" } }), null);
  assert.strictEqual(h.peRunning({}), null);
});

test("nothing installed, as an admin: every needed row is missing and offers its action", () => {
  const rows = h.setupRows(emptyFacts());
  assert.strictEqual(row(rows, "hacs").status, "ok");
  assert.strictEqual(row(rows, "discovery").status, "ok");
  assert.strictEqual(row(rows, "addon").status, "missing");
  assert.match(row(rows, "addon").action.href, /supervisor_addon/);
  assert.deepStrictEqual(row(rows, "app").action, { kind: "install", target: "app", label: "Install" });
  assert.strictEqual(row(rows, "running").status, "missing");
  assert.strictEqual(row(rows, "apex").action.target, "apex");
  assert.strictEqual(row(rows, "flow").action.target, "flow");
  assert.strictEqual(row(rows, "mqtt").needed, false);
  assert.match(row(rows, "mqtt").why, /not for the demo/);
  assert.strictEqual(row(rows, "mqtt").action.kind, "link");
  assert.strictEqual(h.setupSummary(emptyFacts()).allSet, false);
});

test("AppDaemon discovery off: explains the option and blocks the app install", () => {
  const f = emptyFacts();
  f.categories = ["integration", "plugin"];
  const rows = h.setupRows(f);
  assert.strictEqual(row(rows, "discovery").status, "missing");
  assert.match(row(rows, "discovery").detail, /Enable AppDaemon apps discovery/);
  assert.strictEqual(row(rows, "discovery").action.kind, "link");
  assert.strictEqual(row(rows, "app").action, null);
  assert.strictEqual(row(rows, "apex").action.target, "apex");   // plugins don't need it
});

test("HACS missing: no install buttons, link to how to install it", () => {
  const f = emptyFacts();
  f.hacs = false; f.categories = null; f.repos = null; f.components = [];
  const rows = h.setupRows(f);
  assert.strictEqual(row(rows, "hacs").status, "missing");
  assert.match(row(rows, "hacs").action.href, /hacs\.xyz/);
  ["app", "apex", "flow"].forEach((k) => assert.strictEqual(row(rows, k).action, null));
  assert.strictEqual(row(rows, "discovery").status, "unknown");
});

test("no Supervisor: the add-on row is unknown, with a docs link and no install", () => {
  const f = emptyFacts();
  f.addon = { state: "unsupervised" };
  const r = row(h.setupRows(f), "addon");
  assert.strictEqual(r.status, "unknown");
  assert.match(r.detail, /make sure AppDaemon is running/i);
  assert.match(r.action.href, /appdaemon/);
});

test("add-on installed but stopped is missing, not ok", () => {
  const f = allFacts();
  f.addon = { state: "stopped" };
  assert.strictEqual(row(h.setupRows(f), "addon").status, "missing");
});

test("chart card installed in HACS but not loaded asks for a reload", () => {
  const f = allFacts();
  f.cardsLoaded = { "apexcharts-card": false, "sunsynk-power-flow-card": true };
  const r = row(h.setupRows(f), "apex");
  assert.strictEqual(r.status, "missing");
  assert.match(r.detail, /Reload this page/);
  assert.strictEqual(r.action.kind, "reload");
});

test("everything installed: all ok, one line with the version", () => {
  const rows = h.setupRows(allFacts());
  rows.forEach((r) => assert.strictEqual(r.status, "ok", r.key));
  const s = h.setupSummary(allFacts());
  assert.strictEqual(s.allSet, true);
  assert.strictEqual(s.line, "All set. PowerEngine is running 0.9.61");
  assert.strictEqual(s.todo, 0);
});

test("all set without MQTT: still all set", () => {
  const f = allFacts();
  f.components = ["hacs", "hassio"];
  assert.strictEqual(row(h.setupRows(f), "mqtt").status, "missing");
  assert.strictEqual(h.setupSummary(f).allSet, true);
});

test("non-admin sees the list but no install buttons, and an admin note on HACS-dependent rows", () => {
  const f = emptyFacts();
  f.isAdmin = false; f.hacs = null; f.categories = null; f.repos = null; f.addon = { state: "unknown" };
  const rows = h.setupRows(f);
  ["hacs", "discovery", "addon", "app", "apex", "flow"].forEach((k) => {
    assert.strictEqual(row(rows, k).action, null, k);
    assert.strictEqual(row(rows, k).status, "unknown", k);
    assert.match(row(rows, k).detail, /admin/i, k);
  });
  assert.strictEqual(row(rows, "running").status, "missing");   // needs no admin rights to see
});

test("non-admin with everything running still sees all set", () => {
  const f = allFacts();
  f.isAdmin = false; f.hacs = null; f.categories = null; f.repos = null; f.addon = { state: "unknown" };
  assert.strictEqual(h.setupSummary(f).allSet, true);
});

// --- demo card ----------------------------------------------------------------------------------------------
const demoAttrs = (day) => ({ setup: "unconfigured", demo: { day, title: "Sunny day", note: "Recorded data from a real home. Nothing is controlled.",
  days: [{ key: "sunny", title: "Sunny day" }, { key: "axle", title: "<<event>> event day" }] } });

test("demo banner: title, note, current day, buttons for an admin", () => {
  const v = h.demoView(demoAttrs("sunny"), true);
  assert.strictEqual(v.mode, "banner");
  assert.strictEqual(v.title, "Sunny day");
  assert.match(v.note, /Nothing is controlled/);
  assert.deepStrictEqual(v.days.map((d) => [d.key, d.current]), [["sunny", true], ["axle", false]]);
  assert.strictEqual(v.canAct, true);
});

test("demo banner: a non-admin sees it but cannot act", () => {
  const v = h.demoView(demoAttrs("sunny"), false);
  assert.strictEqual(v.mode, "banner");
  assert.strictEqual(v.canAct, false);
});

test("demo banner wins over unconfigured, fills the names map and capitalises", () => {
  const a = demoAttrs("axle");
  a.demo.title = "<<event>> event day";
  a.names = { event: "Axle" };
  const v = h.demoView(a, true);
  assert.strictEqual(v.title, "Axle event day");
  assert.strictEqual(v.days[1].title, "Axle event day");
  a.names = null;
  assert.strictEqual(h.demoView(a, true).title, "Grid-services event day");
});

test("demo banner without a note or a day list still works", () => {
  const v = h.demoView({ demo: { day: "dull", title: "Dull day" } }, true);
  assert.strictEqual(v.mode, "banner");
  assert.match(v.note, /Recorded data from a real home/);
  assert.deepStrictEqual(v.days, []);
});

test("welcome: unconfigured with no demo lists the four fixed days, first selected", () => {
  const v = h.demoView({ setup: "unconfigured", demo: null }, true);
  assert.strictEqual(v.mode, "welcome");
  assert.deepStrictEqual(v.days.map((d) => d.key), ["sunny", "dull", "axle", "car"]);
  assert.deepStrictEqual(v.days.map((d) => d.title), ["Sunny day", "Dull day", "Grid-services event day", "Car charging day"]);
  assert.strictEqual(v.days[0].current, true);
  assert.strictEqual(h.demoView({ setup: "unconfigured" }, false).canAct, false);
  assert.strictEqual(h.demoView({ setup: "unconfigured", names: { event: "Axle" } }, true).days[2].title, "Axle event day");
});

test("welcome: the days the app offers carry the demo's own names, the same words as the banner", () => {
  const demoDays = [{ key: "sunny", title: "Sunny day" }, { key: "dull", title: "Dull day" },
    { key: "axle", title: "Axle event day" }, { key: "car", title: "Car charging day" }];
  // the unconfigured app's names map is neutral ("grid-services"); the days it lists are what the demo will call them
  const v = h.demoView({ setup: "unconfigured", demo: null, names: { event: "grid-services" }, demo_days: demoDays }, true);
  assert.deepStrictEqual(v.days.map((d) => d.title), ["Sunny day", "Dull day", "Axle event day", "Car charging day"]);
  const banner = h.demoView({ setup: "configured", names: { event: "Axle" },
    demo: { day: "axle", title: "<<event>> event day", days: [{ key: "axle", title: "<<event>> event day" }] } }, true);
  assert.strictEqual(banner.title, v.days[2].title);
  assert.strictEqual(h.demoView({ setup: "unconfigured", demo_days: [] }, true).days.length, 4);      // older app: fixed list
});

test("a true/false that arrived as text is a boolean again in the form and in what is saved", () => {
  const saved = { features: { auto_cheap_threshold: "true", arbitrage: "false", axle: 1, fill_when_cheap: true },
    system: { house_load_includes_ev: "true", battery_location: "garage" },
    notifications: { service: "notify.me", events: { health: "true", daily: "false" } } };
  const { draft } = h.initialDraft(saved, [], [], { safety: [], system: [] });
  assert.strictEqual(draft.features.auto_cheap_threshold, true);
  assert.strictEqual(draft.features.arbitrage, false);
  assert.strictEqual(draft.features.axle, true);
  assert.strictEqual(draft.system.house_load_includes_ev, true);
  assert.strictEqual(draft.system.battery_location, "garage");
  assert.strictEqual(draft.notifications.events.health, true);
  assert.strictEqual(draft.notifications.events.daily, false);
  const out = h.buildConfig(draft);
  assert.ok(Object.values(out.features).every((v) => typeof v === "boolean"));
  assert.strictEqual(h.asBool("maybe"), "maybe");
});

test("demo card is hidden when configured, or when the attributes are missing", () => {
  assert.strictEqual(h.demoView({ setup: "configured", demo: null }, true).mode, "hidden");
  assert.strictEqual(h.demoView({}, true).mode, "hidden");
  assert.strictEqual(h.demoView(null, false).mode, "hidden");
  assert.strictEqual(h.demoView(undefined, true).mode, "hidden");
  assert.strictEqual(h.demoView({ setup: "configured", demo: "junk" }, true).mode, "hidden");
});

test("demo event payloads", () => {
  assert.deepStrictEqual(h.demoEventPayload("start", "sunny"),
    { type: "fire_event", event_type: "pe_demo", event_data: { action: "start", day: "sunny" } });
  assert.deepStrictEqual(h.demoEventPayload("day", "dull").event_data, { action: "day", day: "dull" });
  assert.deepStrictEqual(h.demoEventPayload("exit"), { type: "fire_event", event_type: "pe_demo", event_data: { action: "exit" } });
  assert.deepStrictEqual(h.demoEventPayload("exit", "sunny").event_data, { action: "exit" });
  assert.strictEqual(h.demoEventPayload("start"), null);
  assert.strictEqual(h.demoEventPayload("day", ""), null);
  assert.strictEqual(h.demoEventPayload("explode", "sunny"), null);
});

test("configuration link follows the dashboard the visitor is on", () => {
  assert.strictEqual(h.configPath("/energy-dash/monitoring"), "/energy-dash/config");
  assert.strictEqual(h.configPath("/powerengine/"), "/powerengine/config");
  assert.strictEqual(h.configPath(""), "/powerengine/config");
});

test("the setup card offers the demo only when PowerEngine runs unconfigured", () => {
  assert.strictEqual(h.showDemoLink({ peVersion: "0.9.62", setup: "unconfigured" }), true);
  assert.strictEqual(h.showDemoLink({ peVersion: "0.9.62", setup: "configured" }), false);
  assert.strictEqual(h.showDemoLink({ peVersion: null, setup: "unconfigured" }), false);
  assert.strictEqual(h.showDemoLink(null), false);
});

// --- "Your system" block (step 8b) ---
const SITE_OPTS = {
  inverter: [
    { id: "solis", name: "Inv A", status: "verified", firmware_variants: ["420044"], verified_firmware: ["420044"] },
    { id: "other", name: "Inv B", status: "community", firmware_variants: ["1", "2"], verified_firmware: [] },
    { id: "bare", name: "Inv C", status: "draft", firmware_variants: [], verified_firmware: [] },
  ],
  tariff: [{ id: "auto", name: "Detect automatically", status: "verified", firmware_variants: [] }],
};
const SITE_NOW = { inverter: "solis", inverter_firmware: "420044", ev_charger: "zappi", car: "none", tariff: "auto", forecast: "solcast", events: "axle" };

test("site section is hidden when the app publishes no site_options, and no site is sent", () => {
  assert.equal(h.siteInfo({ names: {}, site: SITE_NOW }), null);
  assert.equal(h.siteInfo(undefined), null);
  assert.equal(h.siteInfo({ site_options: {} }), null);
  const info = h.siteInfo({ site: SITE_NOW, site_options: SITE_OPTS, firmware_detected: null, retest_required: true });
  assert.equal(info.retest, true);
  assert.equal(info.detected, null);
  assert.equal("site" in h.buildConfig({ inputs: {}, features: {}, operation: {} }), false);
  assert.deepStrictEqual(h.buildConfig({ inputs: {}, features: {}, operation: {}, site: SITE_NOW }).site, SITE_NOW);
});

test("site is built from the selections, unknown firmware is null", () => {
  assert.deepStrictEqual(h.siteFromSelection({ inverter_firmware: "" }, SITE_NOW), { ...SITE_NOW, inverter_firmware: null });
  assert.deepStrictEqual(h.siteFromSelection({ tariff: "edf" }, SITE_NOW), { ...SITE_NOW, tariff: "edf" });
  assert.deepStrictEqual(Object.keys(h.siteFromSelection({})).sort(), Object.keys(SITE_NOW).sort());
});

test("firmware options follow the chosen inverter, and changing inverter resets an unlisted firmware", () => {
  assert.deepStrictEqual(h.siteFirmwareOptions(SITE_OPTS, "other"), ["1", "2"]);
  assert.deepStrictEqual(h.siteFirmwareOptions(SITE_OPTS, "bare"), []);
  assert.deepStrictEqual(h.siteFirmwareOptions(SITE_OPTS, "nope"), []);
  assert.equal(h.siteChooseInverter(SITE_OPTS, SITE_NOW, "other").inverter_firmware, null);
  assert.equal(h.siteChooseInverter(SITE_OPTS, { ...SITE_NOW, inverter: "other", inverter_firmware: "2" }, "other").inverter_firmware, "2");
});

test("warn when the inverter or the firmware variant changes, not for the same variant or other kinds", () => {
  const w = (next) => h.siteNeedsWarning(SITE_OPTS, SITE_NOW, { ...SITE_NOW, ...next });
  assert.equal(w({}), false);
  assert.equal(w({ inverter_firmware: null }), false);                    // null <-> 420044 is the same variant
  assert.equal(h.siteNeedsWarning(SITE_OPTS, { ...SITE_NOW, inverter_firmware: null }, SITE_NOW), false);
  assert.equal(w({ inverter_firmware: "999" }), true);                    // not listed = a different variant
  assert.equal(w({ inverter: "other", inverter_firmware: null }), true);
  assert.equal(w({ tariff: "edf" }), false);
  assert.equal(w({ car: "x", ev_charger: null, forecast: null, events: null }), false);
  const two = { ...SITE_NOW, inverter: "other", inverter_firmware: "1" };
  assert.equal(h.siteNeedsWarning(SITE_OPTS, two, { ...two, inverter_firmware: "2" }), true);
  assert.equal(h.siteNeedsWarning(SITE_OPTS, two, { ...two, inverter_firmware: null }), false);   // null = default, the first variant
});

test("detected firmware line, and site texts name no supplier", () => {
  assert.match(h.siteDetectedLine(null, null), /not readable from the inverter; pick it from the list/);
  assert.match(h.siteDetectedLine("420044", "420044"), /Detected: 420044.*matches/);
  assert.match(h.siteDetectedLine("420044", "1"), /differs/);
  assert.equal(h.siteOptionLabel({ name: "X", status: "community" }), "X (community)");
  assert.equal(h.siteOptionLabel({ name: "X", status: "verified" }), "X");
  [h.SITE_WARNING, h.SITE_RETEST, ...h.SITE_KINDS.map((k) => h.fillNames(k[1], { event: "Flux" }))].forEach((t) => assert.doesNotMatch(t, /EDF|Axle|Zappi|Solis|Solcast|Octopus/));
  assert.equal(h.fillNames(h.SITE_KINDS.find((k) => k[0] === "events")[1], null), "Grid events");
});

test("the page reloads once after a demo start, day change or exit, and never on its own", () => {
  const before = JSON.stringify(["unconfigured", undefined, false]);
  const after = JSON.stringify(["unconfigured", "sunny", true]);
  assert.strictEqual(h.demoNeedsReload({ text: "x", from: before }, before, after), true);
  assert.strictEqual(h.demoNeedsReload({ text: "x", from: before }, before, before), false);   // nothing changed yet
  assert.strictEqual(h.demoNeedsReload(null, before, after), false);                          // a freshly loaded page
  assert.strictEqual(h.demoNeedsReload({ text: "x" }, before, after), false);
  assert.match(h.DEMO_WAIT, /reloads/);
});

test("versionOlder compares numbers, not text, and says nothing when it can't read a version", () => {
  assert.equal(h.versionOlder("0.9.9", "0.9.10"), true);
  assert.equal(h.versionOlder("0.9.70", "0.9.70"), false);
  assert.equal(h.versionOlder("0.10.0", "0.9.70"), false);
  assert.equal(h.versionOlder("0.9", "0.9.0"), false);
  assert.equal(h.versionOlder("v0.9.68", "0.9.69"), true);
  for (const bad of ["?", "unavailable", "", null, undefined, "0.9.70-beta"]) {
    assert.equal(h.versionOlder(bad, "0.9.69"), null);
    assert.equal(h.versionOlder("0.9.69", bad), null);
  }
});

test("MIN_APP_VERSION is a real version no newer than the card", () => {
  assert.equal(h.MIN_APP_VERSION, "0.9.188");
  assert.deepEqual(h.parseVersion(h.MIN_APP_VERSION), [0, 9, 188]);
  assert.equal(h.versionOlder(h.CARD_VERSION, h.MIN_APP_VERSION), false);
});

test("versionWarnings warns only when the other side is older than its minimum", () => {
  const st = (state, attrs) => ({ "sensor.pe_diag_version": { state, attributes: attrs || {} } });
  // a different but supported app: no warning, whichever way round
  assert.deepEqual(h.versionWarnings(st("0.9.188", { min_card_version: "0.9.110" })), []);
  assert.deepEqual(h.versionWarnings(st("0.9.190", { min_card_version: "0.9.70" })), []);
  // an app older than the card's minimum
  const old = h.versionWarnings(st("0.9.187"));
  assert.equal(old.length, 1);
  assert.match(old[0], /0\.9\.188 or newer.*0\.9\.187/);
  // a card older than the app's minimum
  const oldCard = h.versionWarnings(st("0.9.190", { min_card_version: "99.0.0" }));
  assert.equal(oldCard.length, 1);
  assert.match(oldCard[0], /card 99\.0\.0 or newer/);
  // nothing to compare: no sensor, unavailable, no attribute (an older app)
  assert.deepEqual(h.versionWarnings({}), []);
  assert.deepEqual(h.versionWarnings(st("unavailable")), []);
  assert.deepEqual(h.versionWarnings(st("0.9.190", {})), []);
});

test("history day payload accepts a date inside the range and nothing else", () => {
  assert.deepStrictEqual(h.historyDayPayload("2026-03-04", "2025-09-01", "2026-10-03"),
    { type: "fire_event", event_type: "pe_history_day", event_data: { date: "2026-03-04" } });
  assert.strictEqual(h.historyDayPayload("2025-08-31", "2025-09-01", "2026-10-03"), null);
  assert.strictEqual(h.historyDayPayload("2026-10-04", "2025-09-01", "2026-10-03"), null);
  assert.strictEqual(h.historyDayPayload("", "2025-09-01", "2026-10-03"), null);
  assert.strictEqual(h.historyDayPayload("04/03/2026", "2025-09-01", "2026-10-03"), null);
});

test("history day arrows step one day and stop at the ends", () => {
  assert.strictEqual(h.shiftHistoryDay("2026-03-28", 1, "2025-09-01", "2026-10-03"), "2026-03-29");   // spring forward
  assert.strictEqual(h.shiftHistoryDay("2026-10-25", 1, "2025-09-01", "2026-10-30"), "2026-10-26");   // autumn back
  assert.strictEqual(h.shiftHistoryDay("2026-03-01", -1, "2025-09-01", "2026-10-03"), "2026-02-28");
  assert.strictEqual(h.shiftHistoryDay("2026-10-03", 1, "2025-09-01", "2026-10-03"), null);
  assert.strictEqual(h.shiftHistoryDay("2025-09-01", -1, "2025-09-01", "2026-10-03"), null);
  assert.strictEqual(h.shiftHistoryDay(undefined, 1, "2025-09-01", "2026-10-03"), null);
});

test("the overnight window choice and its two times sit in Tariff and planning, not Other", () => {
  const plan = h.topicPlan([], ["overnight_start_h", "overnight_end_h", "cheap_threshold_p"], [], ["overnight_window"]);
  const tariff = plan.find((t) => t.key === "tariff");
  assert.deepEqual(tariff.system, ["overnight_window"]);
  assert.deepEqual(tariff.settings.slice(0, 2), ["overnight_start_h", "overnight_end_h"]);
  assert.equal(plan.find((t) => t.key === "other"), undefined);
});

test("overnightReadout says what is in use and what was learned, and stays quiet without the sensor", () => {
  assert.equal(h.overnightReadout(null), null);
  assert.equal(h.overnightReadout({ state: "unavailable", attributes: {} }), null);
  const learned = h.overnightReadout({ state: "23:30–05:30", attributes: { source: "learned", learned: "23:30–05:30", learned_days: 5, fixed: null } });
  assert.deepEqual(learned.lines.map((l) => l.text), ["23:30–05:30 (learned)", "23:30–05:30 (from 5 days of rates)"]);
  assert.deepEqual(learned.notes, []);
  const fixed = h.overnightReadout({ state: "00:30–05:30", attributes: { source: "fixed", learned: "23:30–05:30", learned_days: 1, fixed: "00:30–05:30" } });
  assert.equal(fixed.lines[0].text, "00:30–05:30 (your fixed times)");
  assert.equal(fixed.lines[1].text, "23:30–05:30 (from 1 day of rates)");
  assert.deepEqual(fixed.notes, []);                                     // fixed in use: the learned note is not needed
});

test("overnightReadout warns when nothing is learned yet or the fixed times don't make a window", () => {
  const fresh = h.overnightReadout({ state: "none yet", attributes: { source: "learned", learned: "none yet", learned_days: 0 } });
  assert.equal(fresh.lines[1].text, "nothing yet");
  assert.equal(fresh.notes.length, 1);
  assert.match(fresh.notes[0], /two full days/);
  const bad = h.overnightReadout({ state: "23:30–05:30", attributes: { source: "learned", learned: "23:30–05:30", learned_days: 4, fixed_not_valid: true } });
  assert.equal(bad.notes.length, 1);
  assert.match(bad.notes[0], /don't make a window/);
});

// ---- Re-plan now (app 0.9.135) ----
test("replanStatus: nothing asked says nothing; waiting, done and given-up read plainly", () => {
  const { replanStatus } = require("../ha-powerengine-card.js");
  assert.deepEqual(replanStatus(null, "2026-10-10T06:30:00+00:00", 1000), { text: "", busy: false, done: false });
  const ask = { at: 1000, before: "2026-10-10T06:30:00+00:00" };
  assert.equal(replanStatus(ask, "2026-10-10T06:30:00+00:00", 5000).text, "Re-planning...");
  assert.equal(replanStatus(ask, "2026-10-10T06:30:00+00:00", 5000).busy, true);
  const done = replanStatus(ask, "2026-10-10T06:41:00+00:00", 5000);
  assert.ok(/^Re-planned at \d\d:\d\d$/.test(done.text) && done.done && !done.busy);
  const late = replanStatus(ask, "2026-10-10T06:30:00+00:00", 30000);
  assert.ok(/^No new plan yet/.test(late.text) && !late.busy);
});

test("plannedImportP: a smart slot that is only a chance is priced as the chance, as the plan did", () => {
  const { plannedImportP } = require("../ha-powerengine-card.js");
  assert.ok(Math.abs(plannedImportP({ slot: true, slotProb: 0.6, importP: 6.66, standardP: 28.84 }) - 15.532) < 0.01);
  assert.equal(plannedImportP({ slot: true, slotProb: 1, importP: 6.66, standardP: 28.84 }), 6.66);
  assert.equal(plannedImportP({ slot: false, slotProb: null, importP: 28.84, standardP: null }), 28.84);
  assert.equal(plannedImportP({ slot: true, slotProb: 0.6, importP: 6.66, standardP: null }), 6.66);   // an older app: nothing to blend with
});

test("Alexa announcements: defaults, saved values survive a save, bad values fall back", () => {
  const { initialDraft, buildConfig, alexaDraft, parseHoursBefore, alexaSpeakerName } = require("../ha-powerengine-card.js");
  const saved = { inputs: {}, notifications: { service: "persistent_notification", events: { alexa: true },
    alexa: { services: ["notify.alexa_media_kitchen", "notify.alexa_media_daddy_dot"], hours_before: [1, 0], earliest_weekday: "7:45", latest: "20:30" } } };
  const { draft } = initialDraft(saved, [], [], {});
  assert.equal(draft.notifications.events.alexa, true);
  assert.deepEqual(draft.notifications.alexa.services, ["notify.alexa_media_kitchen", "notify.alexa_media_daddy_dot"]);
  const out = buildConfig(draft).notifications;
  assert.equal(out.events.alexa, true);
  assert.deepEqual(out.alexa, { services: ["notify.alexa_media_kitchen", "notify.alexa_media_daddy_dot"], hours_before: [1, 0], earliest_weekday: "07:45", earliest_weekend: "09:00", latest: "20:30", intro: "Daddybot here.", finish: true });
  assert.deepEqual(alexaDraft({ latest: "9pm", hours_before: ["x"], services: ["alexa"] }), { services: [], hours_before: [8, 5, 2, 1, 0], earliest_weekday: "07:30", earliest_weekend: "09:00", latest: "21:00", intro: "Daddybot here.", finish: true });
  assert.deepEqual(alexaDraft({ intro: "  Hello  " }).intro, "Hello");
  assert.deepEqual(alexaDraft({ intro: "" }).intro, "");
  assert.deepEqual(parseHoursBefore("2, 8 5,1 0"), [8, 5, 2, 1, 0]);
  assert.equal(parseHoursBefore("soon"), null);
  assert.equal(parseHoursBefore("99"), null);
  assert.equal(alexaSpeakerName("notify.alexa_media_juliette_s_echo_dot"), "Juliette's echo dot");
  assert.equal(alexaSpeakerName("notify.alexa_media_everywhere"), "Everywhere (every speaker)");
});

test("the Alexa test announcement fires for the ticked speakers and the typed opening words, saved or not", () => {
  const { initialDraft, alexaTestPayload, ALEXA_TEST_EVENT } = require("../ha-powerengine-card.js");
  assert.equal(ALEXA_TEST_EVENT, "pe_alexa_test");
  const { draft } = initialDraft({ inputs: {}, notifications: { alexa: { services: ["notify.alexa_media_kitchen", "notify.alexa_media_last_called", "notify.mobile_app_pixel", "notify.alexa_media_everywhere"], intro: "Hello there." } } }, [], [], {});
  assert.deepEqual(alexaTestPayload(draft), { services: ["notify.alexa_media_kitchen", "notify.alexa_media_everywhere"], intro: "Hello there." });
  assert.deepEqual(alexaTestPayload({}), { services: ["notify.alexa_media_everywhere"], intro: "Daddybot here." });   // the default
});

test("Alexa speaker list: groups (Amazon Speaker Group devices and Everywhere) are kept apart from single speakers", () => {
  const { alexaSpeakerKinds } = require("../ha-powerengine-card.js");
  const devices = { a: { name: "Kitchen", model: "Speaker Group", manufacturer: "Amazon" }, b: { name: "Daddy Dot", model: "Echo Dot (Gen4)", manufacturer: "Amazon" },
    c: { name: "Juliette's Echo Dot", model: "Echo Dot", manufacturer: "Amazon" } };
  const names = ["alexa_media_daddy_dot", "alexa_media_everywhere", "alexa_media_juliette_s_echo_dot", "alexa_media_kitchen"];
  assert.deepEqual(alexaSpeakerKinds(names, devices), { speakers: ["alexa_media_daddy_dot", "alexa_media_juliette_s_echo_dot"], groups: ["alexa_media_everywhere", "alexa_media_kitchen"] });
  // no device registry (older HA, or not an admin): only Everywhere is known to be a group
  assert.deepEqual(alexaSpeakerKinds(names, undefined), { speakers: ["alexa_media_daddy_dot", "alexa_media_juliette_s_echo_dot", "alexa_media_kitchen"], groups: ["alexa_media_everywhere"] });
});

test("the heating set-back is a grid-event feature, off by default, with its switch and pump overrun beside it", () => {
  const axle = h.TOPICS.find((t) => t.key === "axle");
  assert.ok(axle.features.includes("axle_heating_setback"));
  assert.ok(axle.roles.includes("heating_setback") && axle.settings.includes("heating_pump_overrun_min"));
  assert.equal(h.FEATURE_DEFAULTS.axle_heating_setback, false);
  assert.ok(h.FEATURES.some((f) => f[0] === "axle_heating_setback"));
});

test("the grid event summary switch is carried and defaults on", () => {
  const { alexaDraft } = require("../ha-powerengine-card.js");
  assert.equal(alexaDraft({}).finish, true);
  assert.equal(alexaDraft({ finish: false }).finish, false);
  assert.equal(alexaDraft({ finish: "no" }).finish, true);
});

test("waterfall custom range totals the app's per-day rows into the same steps the app builds", () => {
  // the expected values come from pe_core.costs.waterfall() on the same three days
  const day = (date, done, o) => Object.assign({ date, done, none: 0, solar: 0, tariff: 0, actual_adj: 0, carry: 0,
                                                 events_metered: 0, axle_income: 0 }, o);
  const daily = [
    day("2026-09-21", true, { none: 10.111, solar: 7.222, tariff: 6.333, actual_adj: 3.111, carry: 0.501 }),
    day("2026-09-22", true, { none: 12.126, solar: 8.111, tariff: 7.001, actual_adj: 4.201, carry: -0.201, events_metered: 1, axle_income: 0.4 }),
    day("2026-09-23", false, { none: 2, solar: 1, tariff: 0.5, actual_adj: 0.3 }),
  ];
  const names = { tariff: "EDF tariff", event: "Axle" };
  const two = h.waterfallCustom(daily, "2026-09-21", "2026-09-22", names);
  assert.equal(two.days, 2);
  assert.deepEqual(two.steps.map((s) => [s.label, s.value]), [
    ["No solar or battery", 22.24], ["Solar", -6.91], ["EDF tariff", -2], ["PowerEngine", -5.72],
    ["Axle & free power", 0.6], ["You paid (after Axle payments)", 8.21]]);
  const one = h.waterfallCustom(daily, "2026-09-23", "2026-09-23", names);       // today so far
  assert.deepEqual(one.steps.map((s) => [s.label, s.value]), [
    ["No solar or battery", 2], ["Solar", -1], ["EDF tariff", -0.5], ["PowerEngine", -0.2], ["You paid", 0.3]]);
  assert.deepEqual([one.from, one.to], ["2026-09-23", "2026-09-23"]);
  assert.equal(h.waterfallCustom(daily, "2027-01-01", "2027-01-02", names).days, 0);
});

test("a period you were paid for shows the amount without a minus", () => {
  assert.equal(h.shownValue({ kind: "total", label: "You were paid (after Axle payments)", value: -2.1 }), 2.1);
  assert.equal(h.shownValue({ kind: "total", label: "You paid", value: 3.5 }), 3.5);
  assert.equal(h.shownValue({ kind: "step", label: "Solar", value: -3 }), -3);
  assert.equal(h.waterfallShortLabel("You were paid (after Axle payments)"), "You were paid");
});

test("section open state: the person's choice wins, else the card's default, else closed", () => {
  assert.equal(h.sectionOpen({}, "a", true), true);
  assert.equal(h.sectionOpen({}, "a"), false);
  assert.equal(h.sectionOpen({ a: false }, "a", true), false);
  assert.equal(h.sectionOpen({ a: true }, "a", false), true);
  assert.equal(h.sectionOpen({ a: "yes" }, "a", false), false);       // not a boolean: ignored
  assert.equal(h.sectionOpen(null, "a", true), true);
  assert.deepEqual(h.sectionsLoad(), {});                              // no storage here: empty, no throw
});

const dayRow = (date, done, o) => Object.assign({ date, done, none: 0, solar: 0, tariff: 0, actual_adj: 0, carry: 0, events_metered: 0,
  axle_income: 0, self_use_adj: 0, bin: 0, bout: 0 }, o);
const sharedDaily = () => [
  dayRow("2026-09-29", true, { none: 5 }), dayRow("2026-09-30", true, { none: 6 }), dayRow("2026-10-01", true, { none: 7 }),
  dayRow("2026-10-02", true, { none: 8 }), dayRow("2026-10-03", false, { none: 2, solar: 1, tariff: 0.5, actual_adj: 0.3 }),
];

test("costsRange: each period picks the same days the app's periods do", () => {
  const d = sharedDaily();
  const dates = (sel) => h.costsRange(sel, d).rows.map((r) => r.date);
  assert.deepEqual(dates({ key: "today" }), ["2026-10-03"]);
  assert.deepEqual(dates({ key: "yesterday" }), ["2026-10-02"]);
  assert.deepEqual(dates({ key: "week" }), ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]);
  assert.deepEqual(dates({ key: "month" }), ["2026-10-01", "2026-10-02"]);                 // this month's complete days
  assert.deepEqual(dates({ key: "custom", from: "2026-09-30", to: "2026-10-03" }), ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"]);
  assert.match(h.costsRange({ key: "custom", from: "2026-09-30", to: "2026-10-01" }, d).label, /^Wed 30 Sep\w* to Thu 1 Oct$/);
  assert.equal(h.costsRange({ key: "nonsense" }, d).key, "week");
  assert.deepEqual(h.costsRange({ key: "today" }, d.slice(0, 4)).rows, []);                // no unfinished day: nothing so far
});

test("costsSummary: the sentence for a period, paid or paid-to-you, and today's events", () => {
  const d = sharedDaily();
  d[3] = dayRow("2026-10-02", true, { none: 8, solar: 4, tariff: 3, actual_adj: 1, events_metered: 1, axle_income: 4 });
  const names = { tariff: "EDF tariff", event: "Axle" };
  const one = h.costsSummary(h.costsRange({ key: "yesterday" }, d), names, null);
  assert.equal(one.length, 1);
  assert.match(one[0], /Yesterday: you were paid £2\.00\*\* after Axle's payments/);
  assert.match(one[0], /it would have been £8\.00, with solar and your EDF tariff alone £3\.00\. \*\*The battery and PowerEngine saved £2\.00\*\*/);
  const today = h.costsSummary(h.costsRange({ key: "today" }, d), names, { halfHours: 20, axle: { kwh: 3.5, gross: 6.9, net: 4.8 }, free: null, lastEvent: { time: "20:00", end: "21:00" } });
  assert.match(today[0], /^\*\*Today so far: you paid £0\.30\*\*/);
  assert.match(today[1], /covers 20 of the day's 48 half-hours/);
  assert.match(today[2], /Axle exported 3\.5 kWh today and paid you £6\.90 \(£4\.80 net.*ran from 20:00 to 21:00/);
  assert.deepEqual(h.costsSummary(h.costsRange({ key: "today" }, d.slice(0, 4)), names, null), ["Nothing recorded yet today."]);
});

test("costsBars and peParts: the day's five figures and the split of the PowerEngine step", () => {
  const r = dayRow("2026-10-02", true, { none: 8, solar: 4, tariff: 3, actual_adj: 1, carry: 0.5, events_metered: 1, axle_income: 4, self_use_adj: 1.6 });
  assert.deepEqual(h.costsBars(r).map((b) => [b.key, b.value]), [["none", 8], ["solar", 4], ["tariff", 3], ["pe", 1.5], ["net", -1.5]]);
  const p = h.peParts(r);
  assert.equal(p.carried, -0.5);
  assert.ok(Math.abs(p.plain - 1.4) < 1e-9 && Math.abs(p.timing - 0.6) < 1e-9);
  assert.ok(Math.abs(p.total - 1.5) < 1e-9);                                                // the three add up to tariff less the PowerEngine cost
});

test("catalogueAttrs joins a catalogue split across numbered sensors, and waits for a missing part", () => {
  const st = (attrs, state = "1") => ({ state, attributes: attrs });
  const one = { "sensor.pe_map_catalogue": st({ roles: [{ key: "a" }] }) };
  assert.deepStrictEqual(h.catalogueAttrs(one, "sensor.pe_map_catalogue"), { roles: [{ key: "a" }] });
  const two = { "sensor.pe_map_settings": st({ parts: 2, safety: [1], system: [], note: "n" }),
    "sensor.pe_map_settings_2": st({ safety: [2], system: [3] }) };
  assert.deepStrictEqual(h.catalogueAttrs(two, "sensor.pe_map_settings"), { safety: [1, 2], system: [3], note: "n" });
  delete two["sensor.pe_map_settings_2"];
  assert.strictEqual(h.catalogueAttrs(two, "sensor.pe_map_settings"), null);
  assert.strictEqual(h.catalogueAttrs({}, "sensor.pe_map_settings"), null);
});

test("the BMS charge and discharge limits are with the battery, not Other", () => {
  const plan = h.topicPlan(["battery_bms_charge_limit", "battery_bms_discharge_limit"], [], [], []);
  assert.deepEqual(plan.find((t) => t.key === "battery").roles, ["battery_bms_charge_limit", "battery_bms_discharge_limit"]);
  assert.equal(plan.find((t) => t.key === "other"), undefined);
});

test("dampening is part of Inverter control, the simulator and engine comparison share one topic, publisher is not in Other", () => {
  const plan = h.topicPlan([], ["damp_restart_min", "damp_burst_window_min", "damp_burst_settle_min"],
    ["damp_restart", "damp_bursts", "tariff_simulator", "engine_compare"], ["publisher"]);
  const control = plan.find((t) => t.key === "control");
  assert.deepEqual(control.damp.features, ["damp_restart", "damp_bursts"]);
  assert.deepEqual(control.damp.settings, ["damp_restart_min", "damp_burst_window_min", "damp_burst_settle_min"]);
  assert.equal(plan.find((t) => t.key === "damping"), undefined);
  assert.deepEqual(plan.find((t) => t.key === "extras").features, ["tariff_simulator", "engine_compare"]);
  assert.equal(plan.find((t) => t.key === "other"), undefined);
});

test("every topic is in a Config page group", () => {
  const groups = h.HOUSE_GROUPS.map((g) => g[0]);
  h.TOPICS.forEach((t) => assert.ok(groups.includes(h.TOPIC_GROUP[t.key]), t.key));
});

test("the event's own pay and the export rate on top are said apart; an older app keeps its one figure", () => {
  const d = [{ date: "2026-11-02", done: false }];
  const names = { event: "Axle", tariff: "EDF tariff" };
  const rows = [{ date: "2026-11-02", none: 3, solar: -1, tariff: -0.5, pe: -0.2, actual_adj: 1, carry: 0 }];
  const range = h.costsRange({ key: "today" }, rows);
  const withPay = h.costsSummary(range, names, { halfHours: 20, axle: { kwh: 3.26, pay: 3.26, gross: 3.75, net: 3.5 }, free: null, lastEvent: null }).join(" ");
  assert.match(withPay, /Axle exported 3.26 kWh today and paid £3.26, and the export rate adds £0.49/);
  const older = h.costsSummary(range, names, { halfHours: 20, axle: { kwh: 3.26, gross: 3.75, net: 3.5 }, free: null, lastEvent: null }).join(" ");
  assert.match(older, /and paid you £3.75/);
});
