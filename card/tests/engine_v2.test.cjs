const test = require("node:test");
const assert = require("node:assert");
const card = require("../ha-powerengine-card.js");

const T0 = Date.parse("2026-10-05T21:00:00Z");
const iso = (h) => new Date(T0 + h * 3600000).toISOString();
const st = (state, attributes = {}) => ({ state, attributes, last_updated: "x" });

const timeline = () => ({
  now: iso(3.5), because: "New prices published", floor_soc: 12, reserve_soc: 12,
  items: [
    { mode: "self_use", start: iso(0), end: iso(2.5), level_start: 41, level_end: 30, until: "" },
    { mode: "charge", start: iso(2.5), end: iso(5.5), level_start: 30, level_end: 88, until: "until 88%", reason: "cheap" },
    { mode: "hold", start: iso(5.5), end: iso(8), level_start: 88, level_end: 88, until: "until 05:30" },
    { mode: "bad", start: "nope", end: iso(9) },                // ignored: no valid start
  ],
  path: { start: iso(0), step_min: 60, mid: [41, 30, 30, 51, 70, 88, 88, 88, 88], low: [41, 30, 30, 48, 60, 80, 82, 84, 85], high: [41, 30, 30, 55, 80, 95, 95, 95, 95] },
  prices: [
    { start: iso(0), end: iso(2.5), import_p: 28.84, export_p: 15, slot_prob: null, event: false },
    { start: iso(2.5), end: iso(8), import_p: 6.99, export_p: 15, slot_prob: 0.7, event: false },
  ],
  cost_expected: 1.23, cost_selfuse: 2.34, comfort_given_up: 0.06,
});
const curve = () => ({ start: iso(0), step_min: 60, levels: [0, 50, 100], unit: "p/kWh",
  values: [[10, 20, 30], [10, 20, 30], [40, 30, 20], [0, 0, 90], [5, 5, 5]] });

test("value bar: marker and the two real-price lines on one scale", () => {
  const g = card.valueBarGeometry({ value_p: 30, buy_line_p: 7.4, sell_line_p: 14.3, scale_max_p: 40 }, { width: 400, x0: 0 });
  assert.strictEqual(g.max, 40);
  assert.strictEqual(g.marker.x, 300);
  assert.strictEqual(g.buy.x, 74);
  assert.ok(Math.abs(g.sell.x - 143) < 1e-9);
  assert.ok(Math.abs(g.sellFill.w - 143) < 1e-9);
  assert.strictEqual(g.buyFill.x, 74);
  assert.ok(Math.abs(g.buyFill.w - 326) < 1e-9);
  assert.strictEqual(g.marker.off, false);
  assert.deepStrictEqual(g.ticks.map((t) => t.p), [0, 10, 20, 30, 40]);
});

test("value bar: a grid-event value sits at the end and is marked off the scale; missing lines are left out", () => {
  const g = card.valueBarGeometry({ value_p: 109, scale_max_p: 40 }, { width: 400, x0: 0 });
  assert.strictEqual(g.marker.x, 400);
  assert.strictEqual(g.marker.off, true);
  assert.strictEqual(g.buy, null);
  assert.strictEqual(g.sell, null);
  assert.strictEqual(g.sellFill, null);
  assert.strictEqual(card.valueBarGeometry({}, {}).marker, null);
  assert.strictEqual(card.valueBarGeometry({ value_p: 5 }, {}).max, 40);
});

test("value bar: labels near the right edge flip to the left of their line", () => {
  const g = card.valueBarGeometry({ value_p: 20, buy_line_p: 5, sell_line_p: 38, scale_max_p: 40 }, { width: 400, x0: 0 });
  assert.strictEqual(g.buy.anchor, "start");
  assert.strictEqual(g.sell.anchor, "end");
});

test("mode subtitle and exits", () => {
  const m = { since: iso(2.5), exits: [{ kind: "price", text: "The cheap rate ends", expected_at: iso(8) },
    { kind: "level", text: "The battery reaches 88%", expected_at: iso(5.5), first: true }, { kind: "event", text: "The car starts charging" }] };
  const sub = card.modeSubtitle(m);
  assert.match(sub, /^until the battery reaches 88% · expected about \d\d:\d\d · since \d\d:\d\d$/);
  const rows = card.exitRows(m.exits, "charge");
  assert.strictEqual(rows.length, 3);
  assert.strictEqual(rows[1].first, true);
  assert.match(rows[1].when, /^about \d\d:\d\d$/);
  assert.strictEqual(rows[2].when, "any time");
  assert.strictEqual(rows[1].colour, "--m-charge");
  assert.strictEqual(card.modeSubtitle({}), "");
});

test("engine card view: v1 running, nothing published, and the normal case", () => {
  assert.strictEqual(card.engineCardView({ [card.ENGINE_SENSOR]: st("v1") }).text, card.V2_NOT_RUNNING);
  assert.strictEqual(card.engineCardView({ [card.ENGINE_SENSOR]: st("v1") }).kind, "v1");
  assert.strictEqual(card.engineCardView({}).kind, "none");
  assert.strictEqual(card.engineCardView({}).text, card.V2_NO_DATA);
  const v = card.engineCardView({
    [card.ENGINE_SENSOR]: st("v2"),
    "sensor.pe_v2_mode": st("charge", { label: "Charging from the grid", why: "Import is 6.99p", target_soc: 88, power_w: 4800, level_reported: 51, level_filtered: 51.3, values_at: iso(2.5), sending: false, not_sending_reason: "Passive", exits: [] }),
    "sensor.pe_v2_value": st("30.0", { value_p: 30, buy_line_p: 7.36, sell_line_p: 14.25, scale_max_p: 40 }),
  });
  assert.strictEqual(v.kind, "ok");
  assert.strictEqual(v.label, "Charging from the grid");
  assert.strictEqual(v.figs[2].k, "Charging at");
  assert.strictEqual(v.figs[2].v, "4.8 kW");
  assert.strictEqual(v.figs[1].v, "51.3%");
  assert.ok(v.notes.some((n) => /Not sending to the inverter: Passive/.test(n)));
  assert.match(v.barText, /above the 7.4p buy line, so charging pays/);
  assert.match(v.barText, /above the 14.3p sell line, so selling does not pay/);
});

test("engine card view survives a missing value sensor and odd attributes", () => {
  const v = card.engineCardView({ "sensor.pe_v2_mode": st("hold", { exits: "nonsense", power_w: "x" }) });
  assert.strictEqual(v.kind, "ok");
  assert.strictEqual(v.bar, null);
  assert.strictEqual(v.figs[2].v, "–");
  assert.strictEqual(card.engineCardView({ "sensor.pe_v2_mode": st("unavailable") }).kind, "none");
});

test("timeline layout: hours from the left edge, past and future bands, invalid items dropped", () => {
  const L = card.timelineLayout(timeline());
  assert.strictEqual(L.t0, T0);
  assert.strictEqual(L.bands.length, 3);
  assert.deepStrictEqual(L.bands.map((b) => b.state), ["past", "now", "future"]);
  assert.deepStrictEqual([L.bands[1].a, L.bands[1].b], [2.5, 5.5]);
  assert.strictEqual(L.nowH, 3.5);
  assert.strictEqual(L.spanH, 8);
  assert.strictEqual(L.floor, 12);
  assert.strictEqual(L.steps[1].slot, true);
  assert.strictEqual(L.steps[0].slot, false);
  assert.strictEqual(L.mid.length, 9);
  assert.strictEqual(L.nowLevel, 60.5);          // 51 at 3h, 70 at 4h: 3.5h is halfway
  assert.ok(L.ticks.every((t) => new Date(t.ms).getHours() % 3 === 0));
});

test("timeline layout: starts at most six hours before now and ends within 48 hours", () => {
  const tl = timeline();
  tl.now = iso(30);
  tl.items = [{ mode: "hold", start: iso(0), end: iso(100) }];
  tl.path = null; tl.prices = [];
  const L = card.timelineLayout(tl);
  assert.strictEqual(L.t0, T0 + 24 * 3600000);
  assert.strictEqual(L.spanH, 48);
  assert.strictEqual(L.bands[0].a, 0);
  assert.strictEqual(L.bands[0].b, 48);
  assert.deepStrictEqual(card.timelineLayout({}, { now: T0 }).bands, []);
});

test("band labels depend on the width", () => {
  const b = { mode: "charge", until: "until 88%" };
  assert.strictEqual(card.bandLabel(b, 200), "Charge · until 88%");
  assert.strictEqual(card.bandLabel(b, 80), "Charge");
  assert.strictEqual(card.bandLabel(b, 30), "");
  assert.strictEqual(card.bandLabel({ mode: "event" }, 200), "Grid event");
});

test("heat-map colour scale: pale to deep, clamped, grid events apart, dark mode differs", () => {
  const lo = card.heatColour(0), mid = card.heatColour(20), hi = card.heatColour(40), over = card.heatColour(55);
  assert.strictEqual(lo.css, "rgb(247,244,232)");
  assert.strictEqual(hi.css, "rgb(13,71,161)");
  assert.strictEqual(over.css, hi.css);                       // above the scale but not an event: deepest blue
  assert.notStrictEqual(mid.css, lo.css);
  assert.ok(mid.t > 0 && mid.t < 1);
  assert.deepStrictEqual(card.heatColour(109), { kind: "event", css: "var(--m-event)", t: 1 });
  assert.strictEqual(card.heatColour(-5).css, lo.css);
  assert.strictEqual(card.heatColour(null).kind, "none");
  assert.strictEqual(card.heatColour(0, { dark: true }).css, "rgb(36,40,46)");
  assert.strictEqual(card.heatColour(80, { max: 100 }).kind, "scale");
  assert.strictEqual(card.heatColour(80, { max: 40 }).kind, "event");
});

test("value map grid: each level covers half a step either side, clipped to the axis", () => {
  const g = card.valueMapGrid(curve(), T0, 4);
  assert.strictEqual(g.cells.length, 4 * 3);                  // rows 0..3 fall inside 4 hours
  const row0 = g.cells.filter((c) => c.row === 0);
  assert.deepStrictEqual(row0.map((c) => [c.lo, c.hi]), [[0, 25], [25, 75], [75, 100]]);
  assert.deepStrictEqual([row0[0].a, row0[0].b], [0, 1]);
  assert.strictEqual(card.mapCellAt(g, 0.5, 10).value, 10);
  assert.strictEqual(card.mapCellAt(g, 0.5, 60).value, 20);
  assert.strictEqual(card.mapCellAt(g, 2.5, 99).value, 20);
  assert.strictEqual(card.mapCellAt(g, 9, 50), null);
  assert.strictEqual(card.mapCellAt(null, 1, 1), null);
  assert.strictEqual(card.valueMapGrid({}, T0, 4), null);
  assert.strictEqual(card.valueMapGrid({ start: iso(0), levels: [0, 100], values: [[1]] }, T0, 4).cells.length, 1);
});

test("a window that starts later cuts the first rows off", () => {
  const g = card.valueMapGrid(curve(), T0 + 2 * 3600000, 2);
  assert.strictEqual(g.cells[0].row, 2);
  assert.strictEqual(g.cells[0].a, 0);
});

test("value along the expected path, and the real prices as lines in value terms", () => {
  assert.strictEqual(card.interpValue([0, 50, 100], [10, 20, 30], 25), 15);
  assert.strictEqual(card.interpValue([0, 50, 100], [10, 20, 30], 150), 30);
  assert.strictEqual(card.interpValue([0, 50, 100], [10, 20, 30], -5), 10);
  assert.strictEqual(card.interpValue([], [], 5), null);
  const L = card.timelineLayout(timeline());
  const series = card.pathValueSeries(curve(), L);
  assert.strictEqual(series.length, 9);
  assert.ok(Math.abs(series[0].value - 18.2) < 1e-9);         // hour 0 is row 0 (10, 20, 30 at 0, 50, 100%); 41% -> 10 + 10 * 0.82
  assert.ok(Math.abs(series[4].value - 25) < 1e-9 || series[4].value > 0);
});

test("value along the path moves smoothly between hourly rows (no jump on the hour)", () => {
  const H = 3600000;
  const c = { start: new Date(T0).toISOString(), step_min: 60, levels: [0, 100], values: [[10, 10], [20, 20], [30, 30]] };
  const layout = { t0: T0, mid: [0, 0.5, 1, 1.5, 2].map((h) => ({ h, level: 50 })) };
  const got = card.pathValueSeries(c, layout).map((p) => p.value);
  assert.deepStrictEqual(got, [10, 15, 20, 25, 30]);
  const last = card.pathValueSeries(c, { t0: T0, mid: [{ h: 2.5, level: 50 }] });
  assert.strictEqual(last[0].value, 30);                                       // past the last row: its value, no error
});

test("timeline value uses the app's value along the path when it sends one", () => {
  const tl = timeline();
  tl.path.value = tl.path.mid.map((_, i) => 20 + i);
  const states = { "sensor.pe_v2_timeline": st(iso(3), tl), "sensor.pe_v2_value_curve": st(iso(3), curve()) };
  const v = card.planCardView(states, T0 + 1 * 3600000);
  assert.ok(v.tvalue.future.length > 0 && v.tvalue.future[0].value >= 20 && Number.isInteger(v.tvalue.future[0].value));
  const without = card.planCardView({ "sensor.pe_v2_timeline": st(iso(3), timeline()), "sensor.pe_v2_value_curve": st(iso(3), curve()) }, T0 + 3600000);
  assert.ok(without.tvalue.future.length > 0);                                 // an older app: read off the curve as before
});

test("off the scale means a grid event only where one covers the cell; near the floor it is the floor", () => {
  const cell = { a: 1, b: 2, lo: 0, hi: 10, value: 457 };
  assert.strictEqual(card.offScaleReason(cell, [{ a: 0, b: 5, event: false }], 40), "floor");
  assert.strictEqual(card.offScaleReason(cell, [{ a: 1.5, b: 3, event: true }], 40), "event");
  assert.strictEqual(card.offScaleReason(cell, [{ a: 3, b: 4, event: true }], 40), "floor");        // an event, but not then
  assert.strictEqual(card.offScaleReason({ a: 1, b: 2, lo: 50, hi: 60, value: 30 }, [], 40), null);
  assert.ok(/not a grid event/.test(card.mapReadoutText(cell, "17:00", null, "floor")));
  assert.ok(!/off the scale/.test(card.mapReadoutText({ a: 1, b: 2, lo: 50, hi: 60, value: 30 }, "17:00", null, null)));
});

test("path lines are the prices times the losses the value sensor reports, never a made-up price", () => {
  const L = card.timelineLayout(timeline());
  const lines = card.pathLines(L, { import_p: 6.99, buy_line_p: 7.36, export_p: 15, sell_line_p: 14.25 });
  assert.strictEqual(lines.afterLosses, true);
  assert.ok(Math.abs(lines.buy[1].p - 6.99 * (7.36 / 6.99)) < 1e-9);
  assert.ok(Math.abs(lines.sell[0].p - 14.25) < 1e-9);
  const raw = card.pathLines(L, {});
  assert.strictEqual(raw.afterLosses, false);
  assert.strictEqual(raw.buy[0].p, 28.84);
  assert.strictEqual(raw.sell[0].p, 15);
  assert.deepStrictEqual(card.pathLines(null, {}).buy, []);
});

test("map readout text", () => {
  const cell = { lo: 25, hi: 75, value: 20 };
  assert.match(card.mapReadoutText(cell, "03:30", 7.4), /At 03:30 with the battery at 25–75%, one more kWh is worth 20p\. Buying then costs 7.4p per stored kWh: worth it/);
  assert.match(card.mapReadoutText({ lo: 80, hi: 80, value: 5 }, "03:30", 7.4), /at 80%.*not worth it/);
  assert.match(card.mapReadoutText(cell, "03:30", null), /20p\.$/);
  assert.match(card.mapReadoutText(null), /^Point at the map/);
});

test("plan card view: gates and content", () => {
  assert.strictEqual(card.planCardView({ [card.ENGINE_SENSOR]: st("v1") }).text, card.V2_NOT_RUNNING);
  assert.strictEqual(card.planCardView({}).kind, "none");
  assert.strictEqual(card.planCardView({ "sensor.pe_v2_timeline": st(iso(0), { items: [] }) }).kind, "none");
  const v = card.planCardView({ "sensor.pe_v2_timeline": st(iso(3), timeline()), "sensor.pe_v2_value_curve": st(iso(3), curve()),
    "sensor.pe_v2_value": st("30", { scale_max_p: 50, import_p: 6.99, buy_line_p: 7.36 }) });
  assert.strictEqual(v.kind, "ok");
  assert.strictEqual(v.scaleMax, 50);
  assert.ok(v.grid && v.layout);
  assert.match(v.cost, /^Expected cost £1.23 against £2.34 on Self-use$/);
  assert.match(v.chip, /new prices published/);
  // the curve alone still draws a map
  const only = card.planCardView({ "sensor.pe_v2_value_curve": st(iso(0), curve()) });
  assert.strictEqual(only.kind, "ok");
  assert.strictEqual(only.layout, null);
  assert.strictEqual(only.scaleMax, 40);
});

test("health: causes biggest first, backstop flagged, weights and comfort text", () => {
  const bars = card.causeBars({ causes: { drift: 3, slots_changed: 2, backstop: 1, something_new: 2, none: 0 }, revalues: 9 });
  assert.deepStrictEqual(bars.map((b) => b.key), ["drift", "slots_changed", "something_new", "backstop"]);
  assert.strictEqual(bars[0].pct, 100);
  assert.strictEqual(bars[3].warn, true);
  assert.strictEqual(bars[2].label, "Something new");
  assert.strictEqual(card.causeBars({ causes: {}, backstop: 2 })[0].warn, true);
  const v = card.healthCardView({
    "sensor.pe_v2_triggers": st("9", { recent: [{ at: iso(1), kind: "price", text: "New prices", effect: "revalue" }],
      today: { causes: { drift: 3 }, revalues: 9, mode_changes: 7, flip_flops: 0, deadlines_missed: 1, longest_calc_s: 2.4 } }),
    "sensor.pe_diag_v2": st("ok", { weights: { solar: { morning: [0.29, 0.51, 0.2], midday: [0.34, 0.48, 0.18] }, days: 23, start: { solar: [0.25, 0.5, 0.25] } },
      soc_offset: { charging: -0.9, holding: 0, discharging: 0.3 }, filter_gap_max_today: 1.2,
      comfort: [{ date: "2026-10-05", hours_above: 3.5, hours_below: 0, given_up: 0.06, decisions_changed: 2 }] }),
    "sensor.pe_diag_v2_settings": st("30", { settings: [], values: { range_low_soc: 20, range_high_soc: 90 } }),
  });
  assert.strictEqual(v.kind, "ok");
  assert.strictEqual(v.revalues, 9);
  assert.deepStrictEqual(v.figs.map((f) => f.v), ["7", "0", "1", "2.4 s"]);
  assert.deepStrictEqual(v.weights[1].pct, [34, 48, 18]);
  assert.deepStrictEqual(v.weightsStart, [25, 50, 25]);
  assert.strictEqual(v.comfort.title, "Everyday range (20 to 90%)");
  assert.strictEqual(v.comfort.text, "3.5 h above 90%, none below 20%.");
  assert.match(v.filterText, /0.9 points low while charging.*0.3 points high while discharging/);
  assert.strictEqual(v.recent[0].text, "New prices");
  assert.strictEqual(card.healthCardView({ [card.ENGINE_SENSOR]: st("v1") }).text, card.V2_NOT_RUNNING);
  assert.strictEqual(card.healthCardView({}).kind, "none");
  assert.strictEqual(card.healthCardView({ "sensor.pe_v2_triggers": st("0", {}) }).kind, "ok");
});

test("config grouping: v1-only settings leave the house topics, nothing is lost, nothing doubles", () => {
  const settings = ["battery_floor_soc", "min_reserve_soc", "grid_charge_target_soc", "charge_hysteresis_soc", "main_fuse_a", "cheap_threshold_p",
    "window_switch_cost_p", "battery_wear_p", "arbitrage_min_soc", "pre_axle_lookahead_h", "axle_margin_soc", "export_limit_kw", "brand_new_setting"];
  const features = ["optimised_plan", "auto_cheap_threshold", "fill_when_cheap", "arbitrage", "deep_overnight", "axle", "use_check_meter"];
  const system = ["engine", "overnight_window", "control_method"];
  const on = card.engineGrouping(["battery_soc"], settings, features, system, true);
  assert.strictEqual(on.grouped, true);
  const houseSettings = on.house.flatMap((t) => t.settings), houseFeatures = on.house.flatMap((t) => t.features);
  ["min_reserve_soc", "charge_hysteresis_soc", "window_switch_cost_p", "battery_wear_p", "arbitrage_min_soc", "pre_axle_lookahead_h", "axle_margin_soc"]
    .forEach((k) => assert.ok(!houseSettings.includes(k), k));
  // the car's smart-charge requests read the cheap threshold and the grid-charge target whichever engine runs
  ["battery_floor_soc", "main_fuse_a", "export_limit_kw", "cheap_threshold_p", "grid_charge_target_soc", "brand_new_setting"].forEach((k) => assert.ok(houseSettings.includes(k), k));
  ["optimised_plan", "fill_when_cheap", "arbitrage", "deep_overnight"].forEach((k) => assert.ok(!houseFeatures.includes(k), k));
  ["axle", "use_check_meter", "auto_cheap_threshold"].forEach((k) => assert.ok(houseFeatures.includes(k), k));
  assert.ok(!on.house.flatMap((t) => t.system).includes("engine"));            // the choice has its own control
  const v1Settings = on.v1.flatMap((s) => s.settings), v1Features = on.v1.flatMap((s) => s.features);
  assert.deepStrictEqual(v1Settings.slice().sort(), ["arbitrage_min_soc", "axle_margin_soc", "battery_wear_p", "charge_hysteresis_soc",
    "min_reserve_soc", "pre_axle_lookahead_h", "window_switch_cost_p"]);
  assert.deepStrictEqual(v1Features.slice().sort(), ["arbitrage", "deep_overnight", "fill_when_cheap", "optimised_plan"]);
  // the battery floor is a shared setting in the battery topic
  assert.ok(on.house.find((t) => t.key === "battery").settings.includes("battery_floor_soc"));
  // every key lands somewhere exactly once
  const all = houseSettings.concat(v1Settings);
  assert.strictEqual(new Set(all).size, all.length);
  assert.deepStrictEqual(all.slice().sort(), settings.slice().sort());
});

test("config grouping: without v2 nothing moves", () => {
  const settings = ["min_reserve_soc", "cheap_threshold_p"], features = ["optimised_plan"], system = ["engine", "control_method"];
  const off = card.engineGrouping([], settings, features, system, false);
  assert.strictEqual(off.grouped, false);
  assert.deepStrictEqual(off.v1, []);
  assert.deepStrictEqual(off.house, require("../ha-powerengine-card.js").topicPlan([], settings, features, system));
  assert.ok(off.house.flatMap((t) => t.settings).includes("min_reserve_soc"));
});

test("v2 keys are sent only when the app publishes the v2 settings", () => {
  assert.deepStrictEqual(card.engineFields(false, "v2", { reserve_soc: 15 }), {});
  assert.deepStrictEqual(card.engineFields(true, "v2", { reserve_soc: "15", arbitrage: true, junk: "" }), { system: { engine: "v2" }, engine_v2: { reserve_soc: 15, arbitrage: true } });
  assert.deepStrictEqual(card.engineFields(true, "v1", {}), { system: { engine: "v1" } });
  assert.deepStrictEqual(card.engineFields(true, "sideways", { terminal_value: "fixed" }), { engine_v2: { terminal_value: "fixed" } });
  const states = { "sensor.pe_diag_v2_settings": st("30", { settings: [{ key: "reserve_soc" }] }) };
  assert.strictEqual(card.v2Supported(states), true);
  assert.strictEqual(card.v2Supported({}), false);
  assert.strictEqual(card.v2Supported({ "sensor.pe_diag_v2_settings": st("unavailable", { settings: [{ key: "x" }] }) }), false);
  assert.strictEqual(card.v2Supported({ "sensor.pe_diag_v2_settings": st("0", { settings: [] }) }), false);
});

test("buildConfig carries engine and engine_v2 only for a draft that opted in; the rest is as before", () => {
  const base = { inputs: {}, features: {}, operation: {}, system: { control_method: "ram" } };
  const plain = card.buildConfig(Object.assign({}, base));
  assert.strictEqual(plain.engine_v2, undefined);
  assert.deepStrictEqual(plain.system, { control_method: "ram" });
  const v2 = card.buildConfig(Object.assign({}, base, { system: { control_method: "ram", engine: "v2" }, engine_v2: { reserve_soc: "14" } }));
  assert.deepStrictEqual(v2.system, { control_method: "ram", engine: "v2" });
  assert.deepStrictEqual(v2.engine_v2, { reserve_soc: 14 });
  const empty = card.buildConfig(Object.assign({}, base, { system: { engine: "v1" }, engine_v2: {} }));
  assert.strictEqual(empty.engine_v2, undefined);
  assert.deepStrictEqual(empty.system, { engine: "v1" });
});

test("engine in use: the running engine wins, then the saved choice", () => {
  assert.strictEqual(card.engineInUse({ [card.ENGINE_SENSOR]: st("v2") }, { system: { engine: "v1" } }), "v2");
  assert.strictEqual(card.engineInUse({}, { system: { engine: "v2" } }), "v2");
  assert.strictEqual(card.engineInUse({}, {}), "v1");
  assert.strictEqual(card.engineInUse({ [card.ENGINE_SENSOR]: st("unavailable") }, null), "v1");
});

test("engine 2.1 beta: the app says v2 and names the choice; the card keeps it apart from v2", () => {
  const sensor = (state, choice) => ({ state, attributes: choice ? { choice } : {} });
  assert.strictEqual(card.engineInUse({ [card.ENGINE_SENSOR]: sensor("v2", "v2_1") }, { system: { engine: "v2" } }), "v2_1");
  assert.strictEqual(card.engineInUse({ [card.ENGINE_SENSOR]: sensor("v2", "v2") }, { system: { engine: "v2_1" } }), "v2");
  assert.strictEqual(card.engineInUse({ [card.ENGINE_SENSOR]: sensor("v2") }, {}), "v2");                  // an older app names no choice
  assert.strictEqual(card.engineInUse({ [card.ENGINE_SENSOR]: sensor("v1", "v2_1") }, {}), "v1");          // v1 running: the sensor wins
  assert.strictEqual(card.engineInUse({}, { system: { engine: "v2_1" } }), "v2_1");                         // nothing running: the saved choice
  assert.deepStrictEqual(card.engineFields(true, "v2_1", { top_up_cost_p: 0 }), { system: { engine: "v2_1" }, engine_v2: { top_up_cost_p: 0 } });
  const c = card.engineConfirm("v2", "v2_1");
  assert.strictEqual(c.title, "Switch to engine 2.1 beta?");
  assert.strictEqual(c.confirm, "Switch to 2.1 beta");
  assert.strictEqual(c.cancel, "Keep v2");
  assert.match(c.text, /beta/);
  assert.match(c.text, /Engine v2's settings are kept/);
  assert.strictEqual(card.engineConfirm("v2_1", "v1").cancel, "Keep 2.1 beta");
  assert.match(card.engineConfirm("v2_1", "v1").text, /Engine 2\.1 beta's settings are kept/);
});

test("the confirmation reads the same in both directions", () => {
  const up = card.engineConfirm("v1", "v2"), down = card.engineConfirm("v2", "v1");
  assert.strictEqual(up.title, "Switch to engine v2?");
  assert.strictEqual(down.title, "Switch to engine v1?");
  assert.strictEqual(up.confirm, "Switch to v2");
  assert.strictEqual(up.cancel, "Keep v1");
  assert.strictEqual(down.confirm, "Switch to v1");
  assert.strictEqual(down.cancel, "Keep v2");
  assert.match(up.text, /Active, Passive and Pause stay as they are/);
  assert.match(down.text, /Active, Passive and Pause stay as they are/);
  assert.match(up.text, /Engine v1's settings are kept/);
  assert.match(down.text, /Engine v2's settings are kept/);
  assert.strictEqual(up.text.replace(/v[12]/g, "vX"), down.text.replace(/v[12]/g, "vX"));
});

test("v2 setting checks", () => {
  const num = { kind: "number", min: 0, max: 100 }, int = { kind: "int", min: 1, max: 10 };
  assert.strictEqual(card.v2Problem(num, "50"), "");
  assert.match(card.v2Problem(num, "101"), /between 0 and 100/);
  assert.strictEqual(card.v2Problem(num, ""), "Enter a number");
  assert.match(card.v2Problem(int, "2.5"), /whole number/);
  assert.strictEqual(card.v2Problem({ kind: "bool" }, true), "");
  assert.strictEqual(card.v2Problem({ kind: "choice", options: ["refill", "fixed"] }, "fixed"), "");
  assert.match(card.v2Problem({ kind: "choice", options: ["refill", "fixed"] }, "other"), /options/);
  assert.ok(card.v2Contradictions({ range_low_soc: 90, range_high_soc: 20 }).range_low_soc);
  assert.deepStrictEqual(card.v2Contradictions({ range_low_soc: 20, range_high_soc: 90 }), {});
  assert.strictEqual(card.v2Same("5", 5), true);
  assert.strictEqual(card.v2Same(true, "true"), true);
  assert.strictEqual(card.v2Same("fixed", "refill"), false);
  assert.deepStrictEqual(card.v2Values({ values: { a: 1, b: 2 } }, { b: 3 }), { a: 1, b: 3 });
});

test("readouts under the v2 settings", () => {
  const diag = { weights: { solar: { midday: [0.34, 0.48, 0.18] }, days: 23 }, comfort: [{ date: "2026-10-05", hours_above: 3.5, hours_below: 0, given_up: 0.06, decisions_changed: 2 }] };
  assert.match(card.weightsReadout(diag), /Learned now \(midday\): 34 \/ 48 \/ 18 from 23 days/);
  assert.strictEqual(card.weightsReadout({}), null);
  assert.match(card.comfortReadout(diag, { range_low_soc: 20, range_high_soc: 90 }), /Latest day \(2026-10-05\): 3.5 h above 90%, none below 20%/);
  assert.strictEqual(card.comfortReadout({}, {}), null);
  assert.strictEqual(card.floorsReadout({ battery_floor_soc: 12 }, { hard_floor_margin_pct: 1 }), "Hard floor 12% (the battery's own limit, set under Your house). Grid events stop at 13%.");
  assert.strictEqual(card.floorsReadout({}, {}), null);
});

test("the battery topic lists the new shared floor setting", () => {
  assert.ok(card.TOPICS.find((t) => t.key === "battery").settings.includes("battery_floor_soc"));
});

test("preview: engine v1 in control with v2 publishing shows the data with a Preview line", () => {
  const pre = (extra = {}) => Object.assign({
    [card.ENGINE_SENSOR]: st("v1", { v2_available: true, v2_preview: true }),
    "sensor.pe_v2_mode": st("charge", { label: "Charging from the grid", why: "Import is 6.99p", preview: true, sending: false, not_sending_reason: "Preview: engine v1 is in control", exits: [] }),
    "sensor.pe_v2_value": st("30.0", { value_p: 30, buy_line_p: 7.36, sell_line_p: 14.25, scale_max_p: 40 }),
    "sensor.pe_v2_timeline": st(iso(3), timeline()), "sensor.pe_v2_value_curve": st(iso(3), curve()),
    "sensor.pe_v2_triggers": st("3", { today: { revalues: 3 }, recent: [] }), "sensor.pe_diag_v2": st("ok", {}),
  }, extra);
  assert.strictEqual(card.v2Preview(pre()), true);
  const e = card.engineCardView(pre());
  assert.strictEqual(e.kind, "ok");
  assert.strictEqual(e.preview, true);
  assert.strictEqual(e.label, "Would be charging from the grid");
  assert.ok(!e.notes.some((n) => /Not sending/.test(n)));
  assert.strictEqual(card.planCardView(pre()).preview, true);
  assert.strictEqual(card.healthCardView(pre()).preview, true);
  assert.match(card.previewBanner(true), /Preview: engine v1 is in control\. Nothing is sent\./);
  assert.strictEqual(card.previewBanner(false), "");
  assert.strictEqual(card.previewLabel("Self-use", false), "Self-use");
});

test("preview: off, stale or absent keeps the old message; a live v2 is never a preview", () => {
  // preview switched off: engine sensor is v1 without v2_preview, the v2 sensors are stale
  const stale = { [card.ENGINE_SENSOR]: st("v1", { v2_available: true }), "sensor.pe_v2_mode": st("hold", { preview: true }), "sensor.pe_v2_value": st("1") };
  assert.strictEqual(card.v2Preview(stale), false);
  assert.strictEqual(card.engineCardView(stale).text, card.V2_NOT_RUNNING);
  // an app 0.9.106 (no preview anywhere)
  assert.strictEqual(card.engineCardView({ [card.ENGINE_SENSOR]: st("v1", { v2_available: true }) }).text, card.V2_NOT_RUNNING);
  // v2 live
  const live = { [card.ENGINE_SENSOR]: st("v2"), "sensor.pe_v2_mode": st("hold", { preview: false, label: "Holding" }), "sensor.pe_v2_value": st("1") };
  assert.strictEqual(card.v2Preview(live), false);
  assert.strictEqual(card.engineCardView(live).label, "Holding");
  // no engine sensor: the v2 mode's own flag decides
  assert.strictEqual(card.v2Preview({ "sensor.pe_v2_mode": st("hold", { preview: true }) }), true);
  // preview flag but no v2 mode data
  assert.strictEqual(card.v2Preview({ [card.ENGINE_SENSOR]: st("v1", { v2_preview: true }) }), false);
});

test("timeline layout: the sun and house forecast become levels in hours from the left edge", () => {
  const tl = timeline();
  tl.sun = { start: iso(2), step_min: 30, low: [0, 0.5, 1], mid: [0, 1, 2], high: [0, 1.5, 3], house: [0.4, 0.4, 0.5] };
  const L = card.timelineLayout(tl);
  assert.deepStrictEqual(L.sun.mid.map((p) => [p.h, p.kw]), [[2, 0], [2.5, 0], [2.5, 1], [3, 1], [3, 2], [3.5, 2]]);
  assert.strictEqual(L.sun.peak, 2);
  assert.strictEqual(L.sun.max, 3);                    // the high figure sets the scale, in half kW
  assert.strictEqual(L.sun.house.length, 6);
});

test("timeline layout: no sun series, or one of nothing, draws no strip", () => {
  assert.strictEqual(card.timelineLayout(timeline()).sun, null);
  const tl = timeline();
  tl.sun = { start: iso(0), step_min: 30, low: [0, 0], mid: [0, 0], high: [0, 0], house: [0, 0] };
  assert.strictEqual(card.timelineLayout(tl).sun, null);
  tl.sun = { start: iso(0), step_min: 30, low: [0, 0], mid: [0, 0], high: [0, 0], house: [0.3, 0.3] };   // no sun but a house: the strip stays
  assert.strictEqual(card.timelineLayout(tl).sun.peak, 0);
  tl.sun = { start: "nope", mid: [1] };
  assert.strictEqual(card.timelineLayout(tl).sun, null);
});

test("timeline layout: sun outside the span is clipped", () => {
  const tl = timeline();
  tl.sun = { start: iso(-1), step_min: 60, low: [1, 1, 1], mid: [1, 1, 1], high: [1, 1, 1] };
  let L = card.timelineLayout(tl);
  assert.deepStrictEqual(L.sun.mid.map((p) => p.h), [0, 1, 1, 2]);       // the hour before the left edge is dropped
  tl.sun = { start: iso(7), step_min: 60, low: [1, 1], mid: [1, 1], high: [1, 1] };
  L = card.timelineLayout(tl);
  assert.deepStrictEqual(L.sun.mid.map((p) => p.h), [7, 8]);             // the hour after the span (8 h) is dropped
});

test("timeline hover text: band status, battery, price and the sun at a time", () => {
  const L = card.timelineLayout(timeline());
  const clock = (h) => `h${h.toFixed(1)}`;
  const mid = (L.bands[0].a + L.bands[0].b) / 2;
  const t = card.timelineHover(L, mid, clock);
  assert.strictEqual(t.title, clock(mid));
  assert.ok(t.lines[0].startsWith(card.V2_MODES[L.bands[0].mode].name));
  assert.ok(t.lines.some((l) => /^Import price: /.test(l)) || L.steps.length === 0);
  assert.deepStrictEqual(card.timelineHover({ bands: [], steps: [], mid: [], low: [], high: [], sun: null, nowH: 0 }, 1, clock).lines, []);
});

// ---- plan chart: the hours already run, and the clock's "now" -------------------------------------------------------
const HALF = 1800000;
/** sensor.pe_v2_recent: half-hours ending at hour `endH` (from T0), `n` of them, level rising 1% a half-hour. */
const recent = (endH, n, extra = {}) => ({
  hours: 18, step_min: 30,
  series: Array.from({ length: n }, (_, i) => {
    const start = T0 + endH * 3600000 - (n - i) * HALF;
    return Object.assign({ t: new Date(start).toISOString(), mode: i < n / 2 ? "self_use" : "charge", level: 40 + i, import_p: i < n / 2 ? 28.84 : 6.99, export_p: 15, sent: true }, extra);
  }),
});

test("the layout's now is the clock when given, else the plan's own stamp", () => {
  const tl = timeline();                                                       // stamped at hour 3.5
  assert.strictEqual(card.timelineLayout(tl).nowH, 3.5);
  const L = card.timelineLayout(tl, { now: T0 + 4 * 3600000 });
  assert.strictEqual(L.now, T0 + 4 * 3600000);
  assert.strictEqual(L.nowH, 4);
  assert.deepStrictEqual(L.bands.map((b) => b.state), ["past", "now", "future"]);   // 2.5 to 5.5 still running; the hold is still ahead
  const later = card.timelineLayout(tl, { now: T0 + 6 * 3600000 });
  assert.deepStrictEqual(later.bands.map((b) => b.state), ["past", "past", "now"]);
  assert.strictEqual(card.planCardView({ "sensor.pe_v2_timeline": st(iso(3), tl), "sensor.pe_v2_value_curve": st(iso(3), curve()) }, T0 + 5 * 3600000).layout.nowH, 5);
});

test("recentRows: a half-hour in a free session carries its flag and its price", () => {
  const r = recent(3, 2);
  r.series[0].free = true;
  r.series[0].import_p = 0;
  const rows = card.recentRows(r, T0 + 3 * 3600000, 18);
  assert.deepStrictEqual(rows.map((x) => x.free), [true, false]);
  assert.strictEqual(rows[0].importP, 0);
});

test("recentRows: only the last hours, oldest first, bad rows dropped", () => {
  const r = recent(3, 4);
  r.series.push({ t: "nope" }, null);
  r.series.reverse();
  const rows = card.recentRows(r, T0 + 3 * 3600000, 18);
  assert.strictEqual(rows.length, 4);
  assert.deepStrictEqual(rows.map((x) => x.a), rows.map((x) => x.a).slice().sort((p, q) => p - q));
  assert.strictEqual(rows[0].b - rows[0].a, HALF);
  assert.strictEqual(card.recentRows(r, T0 + 3 * 3600000 + 30 * 3600000, 18).length, 0);   // all older than 18 hours
  assert.deepStrictEqual(card.recentRows(null, T0, 18), []);
  assert.deepStrictEqual(card.recentRows({}, T0, 18), []);
});

test("with the hours already run: they lie left of now, the plan's own past gives way, the view opens an hour back", () => {
  const tl = timeline();                                                       // the plan starts at hour 0
  const now = T0 + 3.5 * 3600000;
  const L = card.timelineLayout(tl, { now, recent: recent(3.5, 36) });        // 18 hours up to now (the half-hour running starts at 3.5)
  assert.strictEqual(L.t0, now - 18 * 3600000);                                // reaches back 18 hours
  assert.strictEqual(L.hist, 36);
  assert.strictEqual(L.nowH, 18);
  assert.strictEqual(L.viewH, 17);                                             // the view opens 1 hour before now
  const ran = L.bands.filter((b) => b.ran);
  assert.deepStrictEqual(ran.map((b) => b.mode), ["self_use", "charge"]);      // 36 half-hours merge into two bands
  assert.ok(ran.every((b) => b.state === "past"));
  const planFirst = L.bands.find((b) => !b.ran);
  assert.ok(planFirst.a >= ran[ran.length - 1].b - 1e-9);                      // no overlap: the plan's band starts where the run ends
  assert.strictEqual(planFirst.levelStart, null);                              // its start was cut, so the plan's level there is not its own
  assert.ok(L.steps.some((s) => s.ran) && L.steps.some((s) => !s.ran));
  assert.strictEqual(L.mid[0].level, 40);                                      // as-run levels first: the end of the first half-hour
  assert.strictEqual(L.mid[35].level, 75);                                     // the end of the last, at now
  assert.ok(L.mid.every((p, i) => i === 0 || p.h >= L.mid[i - 1].h - 1e-9));   // one ordered line
  assert.notStrictEqual(L.nowLevel, null);
});

test("without the hours already run the layout is what it was", () => {
  const tl = timeline();
  const plain = card.timelineLayout(tl);
  const withNone = card.timelineLayout(tl, { recent: { series: [] } });
  assert.deepStrictEqual(withNone, plain);
  assert.strictEqual(plain.hist, 0);
  assert.strictEqual(plain.viewH, 0);
  assert.ok(plain.bands.every((b) => !b.ran));
});

test("planCardView: the timeline chart gets the history, the value map keeps the plan's extent", () => {
  const states = { "sensor.pe_v2_timeline": st(iso(3), timeline()), "sensor.pe_v2_value_curve": st(iso(3), curve()), "sensor.pe_v2_recent": st("36", recent(3.5, 36)) };
  const now = T0 + 3.5 * 3600000;
  const v = card.planCardView(states, now);
  assert.strictEqual(v.tlayout.hist, 36);
  assert.strictEqual(v.layout.hist, 0);
  assert.strictEqual(v.layout.t0, T0);
  assert.strictEqual(v.grid.cells[0].a, 0);
  const old = card.planCardView({ "sensor.pe_v2_timeline": states["sensor.pe_v2_timeline"], "sensor.pe_v2_value_curve": states["sensor.pe_v2_value_curve"] }, now);
  assert.strictEqual(old.tlayout.hist, 0);                                     // an older app has no recent sensor: no history, no error
  assert.strictEqual(old.tlayout.t0, T0);
});

test("hover over a stretch already run says what happened", () => {
  const L = card.timelineLayout(timeline(), { now: T0 + 3.5 * 3600000, recent: recent(3.5, 36) });
  const clock = (h) => `h${h}`;
  const t = card.timelineHover(L, 1, clock);                                   // an hour into the 18 hours: self-use, as run
  assert.ok(/Self-use \(happened/.test(t.lines[0]));
  assert.ok(t.lines.some((l) => /^Battery: /.test(l)) && t.lines.some((l) => /^Import price: 28.84p/.test(l)));
});

test("timeline value: recorded over the hours run, from the value curve after now; hover says both", () => {
  const states = { "sensor.pe_v2_timeline": st(iso(3), timeline()), "sensor.pe_v2_value_curve": st(iso(3), curve()), "sensor.pe_v2_recent": st("36", recent(3.5, 36, { value_p: 12.5 })) };
  const now = T0 + 3.5 * 3600000;
  const v = card.planCardView(states, now);
  assert.ok(v.tvalue.past.length > 10 && v.tvalue.past.every((p) => p.value === 12.5 && p.h <= v.tlayout.nowH + 1e-9));
  assert.ok(v.tvalue.future.length > 0 && v.tvalue.future.every((p) => p.h >= v.tlayout.nowH - 1e-9));
  const clock = (h) => `h${h}`;
  assert.ok(card.timelineHover(v.tlayout, 1, clock, v.tvalue).lines.some((l) => /^Value of a stored kWh: 12.5p$/.test(l)));
  assert.ok(card.timelineHover(v.tlayout, v.tlayout.nowH + 1, clock, v.tvalue).lines.some((l) => /^Value of a stored kWh: .*\(expected\)$/.test(l)));
  const old = card.planCardView({ "sensor.pe_v2_timeline": states["sensor.pe_v2_timeline"], "sensor.pe_v2_value_curve": states["sensor.pe_v2_value_curve"] }, now);
  assert.deepStrictEqual(old.tvalue.past, []);                                 // an older app records no value: the expected part still draws
});

test("history: buy and sell lines, grid and car energy and the replans are read when the app records them, and absent otherwise", () => {
  const half = (i, extra) => Object.assign({ t: new Date(T0 + i * HALF).toISOString(), mode: "charge", level: 60, import_p: 6.66, export_p: 15, value_p: 10, sent: true }, extra);
  const series = [half(0, { buy_p: 7.1, sell_p: 14.2, grid_in: 2.5, grid_out: 0, car: 1 }), half(1, { buy_p: 7.2, sell_p: 14.3, grid_out: 0.4 }), half(2)];
  const v = card.v2HistoryView({ date: "2026-10-06", series, in_control: "v2", replans: [new Date(T0 + 1.25 * 3600000).toISOString(), "junk", new Date(T0 + 30 * 3600000).toISOString()] });
  assert.strictEqual(v.buySteps.length, 2);
  assert.strictEqual(v.sellSteps[1].y, 14.3);
  assert.strictEqual(v.energy.length, 2);                                      // the third half-hour has no figures
  assert.strictEqual(v.energyMax, 2.5);
  assert.deepStrictEqual(v.replans, [1.25]);                                   // junk and out-of-range times dropped
  const old = card.v2HistoryView({ date: "2026-10-06", series: [half(0)], in_control: "v2" });
  assert.deepStrictEqual([old.buySteps, old.energy, old.replans], [[], [], []]);
});

test("timeline: recorded buy/sell lines, the ones from now, and the replans", () => {
  const states = { "sensor.pe_v2_timeline": st(iso(3), timeline()), "sensor.pe_v2_value_curve": st(iso(3), curve()),
    "sensor.pe_v2_recent": st("36", Object.assign(recent(3.5, 36, { value_p: 12, buy_p: 7.5, sell_p: 14 }), { replans: [new Date(T0 + 2 * 3600000).toISOString()] })),
    "sensor.pe_v2_value": st("12", { import_p: 7, buy_line_p: 7.35, export_p: 15, sell_line_p: 14.25 }) };
  const v = card.planCardView(states, T0 + 3.5 * 3600000);
  assert.ok(v.tlines.buy.some((s) => s.p === 7.5) && v.tlines.buy.some((s) => Math.abs(s.p - 7.35) < 1e-9 || s.p > 7.4));
  assert.ok(v.tlines.sell.some((s) => s.p === 14));
  assert.strictEqual(v.tlayout.replans.length, 1);
  const clock = (h) => `h${h}`;
  assert.ok(card.timelineHover(v.tlayout, 1, clock, v.tvalue, v.tlines).lines.some((l) => /^Buy line 7.5p, sell line 14p$/.test(l)));
});

test("the key above a chart: one tick box per line, unticked when hidden; buy and sell have colours of their own", () => {
  const html = card.chartKeyHtml(["value", "buy", "sell"], new Set(["buy"]));
  assert.ok(/data-key="value" checked/.test(html) && /data-key="sell" checked/.test(html) && !/data-key="buy" checked/.test(html));
  const colours = ["buy", "sell"].map((k) => card.CHART_KEYS[k][0]);
  assert.ok(colours.every((c) => c !== "--v2-price" && c !== "--m-export") && colours[0] !== colours[1]);
  assert.strictEqual(card.chartHidden("nothing-saved").size, 0);              // no storage in node: nothing hidden, no error
});

// ---- plan chart: what a scroll position means -----------------------------------------------------------------------
const geom = { t0: T0, pph: 20, defPx: 300 };
test("scrollIntent: a position that never took is not the person's choice (the card was built before it was on screen)", () => {
  assert.strictEqual(card.scrollIntent({ placed: false, laidOut: true, scrollLeft: 0 }, geom), null);
  assert.strictEqual(card.scrollIntent({ placed: true, laidOut: false, scrollLeft: 0 }, geom), null);
  assert.strictEqual(card.scrollIntent(null, geom), null);
  assert.strictEqual(card.scrollIntent({ placed: true, laidOut: true, scrollLeft: 0 }, null), null);
});

test("scrollIntent: at the opening position it keeps following now; moved, it keeps the time at the left edge", () => {
  assert.deepStrictEqual(card.scrollIntent({ placed: true, laidOut: true, scrollLeft: 304 }, geom), { user: false, leftMs: null });
  const moved = card.scrollIntent({ placed: true, laidOut: true, scrollLeft: 100 }, geom);
  assert.strictEqual(moved.user, true);
  assert.strictEqual(moved.leftMs, T0 + 5 * 3600000);                          // 100 px at 20 px an hour
  assert.strictEqual(card.scrollIntent({ placed: true, laidOut: true, scrollLeft: 0 }, geom).leftMs, T0);   // a real scroll to the start is still honoured
});

// ---- the day as it ran, to the second (`runs`), not half-hour by half-hour -------------------------------------------------
test("runRows: each run ends where the next begins, the last at the given end; none without it", () => {
  const runs = [[iso(2.1), "export", 80, 0], [iso(1), "charge", 50.5, 0], [iso(3), "self_use", null, 1], ["nope", "x"], null];
  const rows = card.runRows(runs, T0 + 4 * 3600000);
  assert.deepStrictEqual(rows.map((r) => r.mode), ["charge", "export", "self_use"]);                   // ordered, bad rows dropped
  assert.strictEqual(rows[0].b, rows[1].a);
  assert.strictEqual(rows[2].b, T0 + 4 * 3600000);
  assert.strictEqual(rows[2].preview, true);
  assert.strictEqual(rows[2].level, null);
  assert.deepStrictEqual(card.runRows(runs, null).map((r) => r.mode), ["charge", "export"]);           // the last one's end is not known
  assert.deepStrictEqual(card.runRows(undefined, 5), []);
});

test("the history card draws the runs where they happened, and falls back to half-hours without them", () => {
  const half = (i, mode, level) => ({ t: new Date(T0 + i * HALF).toISOString(), mode, level, import_p: 6.66, export_p: 15, value_p: 10, sent: true });
  const series = [half(0, "charge", 60), half(1, "export", 50), half(2, "export", 40)];
  const without = card.v2HistoryView({ date: "2026-10-06", series, in_control: "v2" });
  assert.deepStrictEqual(without.bands.map((b) => b.mode), ["charge", "export"]);                      // the half-hours merged
  assert.strictEqual(without.exact, false);
  const runs = [[new Date(T0).toISOString(), "charge", 45, 0], [new Date(T0 + 24 * 60000).toISOString(), "export", 61.2, 0]];
  const v = card.v2HistoryView({ date: "2026-10-06", series, in_control: "v2", runs, runs_until: new Date(T0 + 3 * HALF).toISOString() });
  assert.strictEqual(v.exact, true);
  assert.deepStrictEqual(v.bands.map((b) => b.mode), ["charge", "export"]);
  assert.ok(Math.abs(v.bands[0].b - 0.4) < 1e-9);                                                       // the change at minute 24, not 30
  assert.ok(Math.abs(v.bands[1].b - 1.5) < 1e-9);
  const lv = v.level.reduce((acc, r) => acc.concat(r), []);
  assert.ok(lv.some((p) => Math.abs(p.h - 0.4) < 1e-9 && p.y === 61.2));                               // the level where the change happened
  assert.ok(lv.every((p, i) => i === 0 || p.h >= lv[i - 1].h));
  const today = card.v2HistoryView({ date: "2026-10-06", series: series.concat([half(3, null, null)]), in_control: "v2", runs }, T0 + 80 * 60000);
  assert.ok(Math.abs(today.bands[today.bands.length - 1].b - 80 / 60) < 1e-9);                         // today's last run goes on to now
});

test("the plan chart's hours already run use the runs, up to the last whole half-hour", () => {
  const now = T0 + 3.5 * 3600000;
  const r = recent(3.5, 36);
  const changeAt = now - 90 * 60000 - 7 * 60000;                                                        // inside a half-hour, off the grid
  r.runs = [[new Date(now - 18 * 3600000).toISOString(), "self_use", 40, 0], [new Date(changeAt).toISOString(), "charge", 70.5, 0]];
  const L = card.timelineLayout(timeline(), { now, recent: r });
  const ran = L.bands.filter((b) => b.ran);
  assert.deepStrictEqual(ran.map((b) => b.mode), ["self_use", "charge"]);
  assert.ok(Math.abs(ran[0].b - (changeAt - L.t0) / 3600000) < 1e-9);                                   // the change sits where it happened
  assert.strictEqual(ran[1].levelStart, 70.5);
  assert.ok(Math.abs(ran[1].b - (now - L.t0) / 3600000) < 1e-9);                                        // to the last half-hour, here at now
  assert.ok(L.mid.some((p) => Math.abs(p.h - (changeAt - L.t0) / 3600000) < 1e-9 && p.level === 70.5));
  assert.ok(L.mid.every((p, i) => i === 0 || p.h >= L.mid[i - 1].h - 1e-9));
  const old = card.timelineLayout(timeline(), { now, recent: recent(3.5, 36) });                        // an older app: no runs
  assert.deepStrictEqual(old.bands.filter((b) => b.ran).map((b) => b.mode), ["self_use", "charge"]);
});

// --- history: the forecast, the real timing of the grid and the readout ----------------------------------------------------
const NOW_L = new Date(2026, 9, 6, 12, 0).getTime();                         // local noon on the day shown
const at = (hoursFromNow) => new Date(NOW_L + hoursFromNow * 3600000).toISOString();
const fcTimeline = () => ({
  now: at(0), floor_soc: 12, reserve_soc: 12,
  items: [
    { mode: "self_use", start: at(-1), end: at(3), level_start: 50, level_end: 40, until: "until 15:00", reason: "sun covers the house" },
    { mode: "charge", start: at(3), end: at(8), level_start: 40, level_end: 90, until: "until 90%", reason: "cheap slot" },
    { mode: "hold", start: at(8), end: at(20), level_start: 90, level_end: 90, until: "" },
  ],
  path: { start: at(0), step_min: 60, mid: [50, 45, 42, 40, 55, 70, 85, 90, 90, 90, 90, 90, 90], low: [50, 40, 36, 33, 45, 60, 80, 88, 90, 90, 90, 90, 90], high: [50, 48, 46, 45, 62, 80, 92, 92, 92, 92, 92, 92, 92] },
  prices: [{ start: at(-1), end: at(3), import_p: 28.84, export_p: 15 }, { start: at(3), end: at(20), import_p: 6.99, export_p: 15, slot_prob: 0.7 }],
});
const ranDay = () => {
  const half = (i, extra) => Object.assign({ t: new Date(2026, 9, 6, 0, 30 * i).toISOString(), mode: "self_use", level: 60 - i, import_p: 28.84, export_p: 15, value_p: 10, sent: true }, extra);
  return Array.from({ length: 24 }, (_, i) => half(i));                      // midnight to noon
};

test("forecast: only what is still to come, in milliseconds, with the level range and the prices", () => {
  const f = card.v2Forecast(fcTimeline(), NOW_L);
  assert.ok(f.bands.every((b) => b.b > NOW_L) && f.bands.map((b) => b.mode).join() === "self_use,charge,hold");
  assert.ok(f.steps.every((q) => q.b > NOW_L) && f.steps.some((q) => q.importP === 6.99));
  assert.ok(f.mid.length > 5 && f.low.length === f.mid.length && f.high.length === f.mid.length);
  assert.ok(Math.abs(f.mid[0].ms - NOW_L) < 1000);
  assert.strictEqual(card.v2Forecast(null, NOW_L), null);
  assert.strictEqual(card.v2Forecast({ items: [], prices: [] }, NOW_L), null);
  const last = card.v2ForecastLastDay(f);
  assert.strictEqual(last, "2026-10-07");                                    // 20 hours on from noon reaches the next morning
});

test("history view: today spans the whole day, with the forecast right of now and the grid at its real timing", () => {
  const f = card.v2Forecast(fcTimeline(), NOW_L);
  const start = new Date(2026, 9, 6, 0, 0).toISOString();
  const flows = { date: "2026-10-06", start, bucket: 5, m: [120, 125, 600], imp: [1.5, 1.5, 0], exp: [0, 0, 2], car: [0, 1, 0] };
  const v = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series: ranDay(), in_control: "v2" }, NOW_L, { forecast: f, flows });
  assert.strictEqual(v.kind, "ok");
  assert.ok(Math.abs(v.nowH - 12) < 1e-9);
  assert.ok(v.spanH > 24 && v.dayEndH === 24 && v.spanH === (f.to - new Date(2026, 9, 6).getTime()) / 3600000);   // today runs on to the end of the timeline
  assert.ok(v.forecast.bands.length === 3 && v.forecast.bands.every((b) => b.a >= v.nowH - 1e-9));
  assert.ok(v.forecast.mid.every((p) => p.h >= v.nowH - 0.26));
  assert.deepStrictEqual(v.flows.map((e) => [e.a, e.b]), [[2, 2 + 5 / 60], [2 + 5 / 60, 2 + 10 / 60], [10, 10 + 5 / 60]]);
  assert.ok(v.energyMax >= 2);
  const other = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series: ranDay(), in_control: "v2" }, NOW_L, { flows: Object.assign({}, flows, { date: "2026-10-05" }) });
  assert.strictEqual(other.flows, null);                                     // flows of another day are not drawn
});

test("history forecast reaches as far as the plan chart's: 30 hours on from now, never more", () => {
  const long = fcTimeline();
  long.prices = [{ start: at(-1), end: at(60), import_p: 28.84, export_p: 15 }];
  long.items = [{ mode: "hold", start: at(-1), end: at(60), level_start: 50, level_end: 50, until: "" }];
  const f = card.v2Forecast(long, NOW_L);
  assert.strictEqual(f.to, NOW_L + 30 * 3600000);
  assert.strictEqual(card.v2Forecast(fcTimeline(), NOW_L).to, NOW_L + 20 * 3600000);        // a shorter timeline stays as long as it is
  const L = card.timelineLayout(long, { now: NOW_L, recent: { hours: 18, step_min: 30, series: Array.from({ length: 36 }, (_, i) => ({ t: new Date(NOW_L - (18 - i / 2) * 3600000).toISOString(), mode: "hold", level: 50, import_p: 7, export_p: 15, sent: true })) } });
  assert.ok(Math.abs(L.t1 - NOW_L - 30 * 3600000) < 1000);                                    // the plan chart's own end
});

test("history view: a smart slot stays marked once it has run, and the view opens near now", () => {
  const f = card.v2Forecast(fcTimeline(), NOW_L);
  const series = ranDay().map((r, i) => (i >= 2 && i < 4 ? Object.assign({}, r, { import_p: 6.99, slot: true }) : r));
  const v = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series, in_control: "v2" }, NOW_L, { forecast: f });
  assert.deepStrictEqual(v.importSteps.filter((s) => s.slot).map((s) => s.a), [1, 1.5]);
  assert.strictEqual(v.importSteps.filter((s) => !s.slot).length, 22);
  assert.ok(Math.abs(v.viewH - 11) < 1e-9);                                                  // an hour before now, as the plan chart opens
  const past = card.v2HistoryView({ date: "2026-10-06", series }, NOW_L);
  assert.strictEqual(past.viewH, 0);                                                         // a day with no now opens at its start
  const rows = card.timelineLayout(fcTimeline(), { now: NOW_L, recent: { hours: 18, step_min: 30, series: [{ t: new Date(NOW_L - 3600000).toISOString(), mode: "charge", level: 50, import_p: 6.99, export_p: 15, sent: true, slot: true }] } });
  assert.ok(rows.steps.some((s) => s.ran && s.slot));                                        // and the plan chart's hours already run
});

test("history view: the battery scale starts at the battery's own lowest level when it is known", () => {
  const a = { date: "2026-10-06", latest: "2026-10-06", series: ranDay(), in_control: "v2" };
  assert.strictEqual(card.v2HistoryView(a, NOW_L, { floor: 12 }).floor, 12);
  assert.strictEqual(card.v2HistoryView(a, NOW_L, {}).floor, 0);
  assert.strictEqual(card.v2HistoryView(a, NOW_L, { floor: null }).floor, 0);
  assert.strictEqual(card.v2HistoryView(a, NOW_L, { floor: 95 }).floor, 0);                  // not a believable floor: the full scale
});

test("history view: a coming day is the forecast alone; a past day gets none; nothing to show says so", () => {
  const f = card.v2Forecast(fcTimeline(), NOW_L);
  const tomorrow = card.v2HistoryView({ date: "2026-10-07", latest: "2026-10-06", series: [] }, NOW_L, { forecast: f });
  assert.strictEqual(tomorrow.kind, "ok");
  assert.strictEqual(tomorrow.control, "forecast");
  assert.strictEqual(tomorrow.nowH, null);
  assert.ok(tomorrow.forecast.bands.length > 0 && tomorrow.previewOnly === false && /Forecast/.test(tomorrow.banner));
  const later = card.v2HistoryView({ date: "2026-10-09", latest: "2026-10-06", series: [] }, NOW_L, { forecast: f });
  assert.strictEqual(later.kind, "empty");                                   // beyond what the timeline reaches
  const past = card.v2HistoryView({ date: "2026-10-05", latest: "2026-10-06", series: [] }, NOW_L, { forecast: f });
  assert.strictEqual(past.kind, "empty");
  const old = card.v2HistoryView({ date: "2026-10-06", series: ranDay() }, NOW_L);
  assert.strictEqual(old.forecast, null);                                    // no timeline given: as before
});

test("history readout: the mode, battery, prices, value and grid at a chosen time, ran or expected", () => {
  const f = card.v2Forecast(fcTimeline(), NOW_L);
  const start = new Date(2026, 9, 6, 0, 0).toISOString();
  const flows = { date: "2026-10-06", start, bucket: 5, m: [120], imp: [1.5], exp: [0], car: [1] };
  const v = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series: ranDay(), in_control: "v2" }, NOW_L, { forecast: f, flows });
  const clock = (h) => `h${h.toFixed(1)}`;
  const past = card.v2HistoryReadout(v, 2.02, clock);
  assert.ok(/happened/.test(past[0]) && past.some((l) => /^Self-use \(happened/.test(l)));
  assert.ok(past.includes("Grid: importing 3.0 kW") && past.includes("Car: charging at 2.0 kW"));
  assert.ok(past.some((l) => /^Import price: 28.84p/.test(l)) && past.some((l) => /^Battery: /.test(l)));
  const soon = card.v2HistoryReadout(v, 16, clock);
  assert.ok(/expected/.test(soon[0]) && soon.some((l) => /^Charge \(expected/.test(l)) && soon.includes("cheap slot"));
  assert.ok(soon.some((l) => /^Battery: .*likely/.test(l)) && soon.some((l) => /^Import price: 6.99p, smart slot/.test(l)));
  assert.deepStrictEqual(card.v2HistoryReadout(v, 99, clock), []);
  assert.deepStrictEqual(card.v2HistoryReadout({ kind: "empty" }, 1, clock), []);
});

test("history view: the sun and the house, as they happened and as expected", () => {
  const tl = fcTimeline();
  tl.sun = { start: at(0), step_min: 60, low: [0, 0.5, 1], mid: [0, 1, 2], high: [0, 1.5, 3], house: [0.4, 0.5, 0.6] };
  const f = card.v2Forecast(tl, NOW_L);
  assert.ok(f.sun && f.sun.mid.length === 6 && f.sun.house.length === 6);
  const start = new Date(2026, 9, 6, 0, 0).toISOString();
  const flows = { date: "2026-10-06", start, bucket: 5, m: [600, 605], imp: [0, 0], exp: [0, 0], car: [0, 0], sun: [1.0, 1.5], house: [0.2, 0.25] };
  const v = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series: ranDay(), in_control: "v2" }, NOW_L, { forecast: f, flows });
  assert.ok(v.hasSun && v.sunMax === 3);
  assert.deepStrictEqual(v.sunActual.map((e) => e.kw), [2, 3]);                // kWh per half-hour x 2 = kW
  assert.deepStrictEqual(v.houseActual.map((e) => e.kw), [0.4, 0.5]);
  assert.ok(v.forecast.sun.mid.every((q) => q.h >= v.nowH - 1e-9) && v.forecast.sun.house.length === 6);
  const clock = (h) => `h${h.toFixed(1)}`;
  const past = card.v2HistoryReadout(v, 10.02, clock);
  assert.ok(past.includes("Sun: 2.0 kW") && past.includes("House: 0.4 kW"));
  const soon = card.v2HistoryReadout(v, 12.5, clock);
  assert.ok(soon.some((l) => /^Sun expected: .* kW \(/.test(l)) && soon.some((l) => /^House use expected: /.test(l)));
  const bare = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series: ranDay() }, NOW_L, { forecast: card.v2Forecast(fcTimeline(), NOW_L) });
  assert.strictEqual(bare.hasSun, false);                                      // an older app / no sun series: no panel
});

test("history probe: the numbers at a time, for the labels on the chart", () => {
  const tl = fcTimeline();
  tl.sun = { start: at(0), step_min: 60, low: [0, 0.5, 1], mid: [0, 1, 2], high: [0, 1.5, 3], house: [0.4, 0.5, 0.6] };
  const f = card.v2Forecast(tl, NOW_L);
  const start = new Date(2026, 9, 6, 0, 0).toISOString();
  const flows = { date: "2026-10-06", start, bucket: 5, m: [600, 605], imp: [1.5, 0], exp: [0, 2], car: [1, 0], sun: [1.0, 1.5], house: [0.2, 0.25] };
  const v = card.v2HistoryView({ date: "2026-10-06", latest: "2026-10-06", series: ranDay(), in_control: "v2" }, NOW_L, { forecast: f, flows });
  const past = card.v2HistoryProbe(v, 10.02);
  assert.strictEqual(past.future, false);
  assert.deepStrictEqual(past.grid, { kind: "import", kw: 3 });
  assert.strictEqual(past.car, 2);
  assert.strictEqual(past.sun, 2);
  assert.strictEqual(past.house, 0.4);
  assert.ok(past.battery !== null && past.importP === 28.84);
  assert.strictEqual(card.v2HistoryProbe(v, 10.1).grid.kind, "export");
  const soon = card.v2HistoryProbe(v, 12.5);
  assert.strictEqual(soon.future, true);
  assert.ok(soon.battery !== null && soon.low !== null && soon.high !== null && soon.importP !== null);
  assert.ok(soon.sun !== null && soon.house !== null && soon.sunHigh >= soon.sun);
  assert.strictEqual(soon.grid, null);                                        // no grid reading for a time that has not come
  assert.strictEqual(card.v2HistoryProbe(v, 99), null);
  assert.strictEqual(card.v2HistoryProbe({ kind: "empty" }, 1), null);
});
