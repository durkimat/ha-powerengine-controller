const test = require("node:test");
const assert = require("node:assert");
const h = require("../ha-powerengine-card.js");

const st = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [`sensor.pe_state_${k}`, { state: String(v) }]));
const NOW = st({ solar_power: 243, battery_power: -4548, battery_soc: 66, grid_power: 5548, load_power: 744, ev_power: 0 });
const CFG = { ev: true, capacity_wh: 18000, reserve_soc: 12 };

test("sizes read as W or kW and never carry a sign", () => {
  assert.strictEqual(h.flowFmt(640), "640 W");
  assert.strictEqual(h.flowFmt(-1234), "1.2 kW");
  assert.strictEqual(h.flowFmt(999.6), "1000 W");
  assert.strictEqual(h.flowFmt(null), "–");
  assert.strictEqual(h.flowNum({ "sensor.x": { state: "unavailable" } }, "sensor.x"), null);
  assert.strictEqual(h.flowNum({ "sensor.x": { state: "12.5" } }, "sensor.x"), 12.5);
  assert.strictEqual(h.flowNum({}, "sensor.x"), null);
  assert.strictEqual(h.flowNum({ "sensor.x": { state: "abc" } }, "sensor.x"), null);
});

test("the battery note: time left to the reserve while discharging, time to full while charging", () => {
  assert.strictEqual(h.flowBatteryNote(66, -4548, 18000, 12), "full in 1 h 21 min");
  assert.strictEqual(h.flowBatteryNote(50, 1900, 18000, 12), "3 h 36 min left to 12%");
  assert.strictEqual(h.flowBatteryNote(10, 500, 18000, 12), "at the 12% reserve");
  assert.strictEqual(h.flowBatteryNote(100, -500, 18000, 12), "full");
  assert.strictEqual(h.flowBatteryNote(50, 3, 18000, 12), "");                    // idle
  assert.strictEqual(h.flowBatteryNote(50, 1000, 0, 12), "");                      // size unknown
  assert.strictEqual(h.flowBatteryNote(null, 1000, 18000, 12), "");
  assert.strictEqual(h.flowDuration(0.001), "under a minute");
  assert.strictEqual(h.flowDuration(2), "2 h");
  assert.strictEqual(h.flowDuration(0.5), "30 min");
});

test("the figures follow PowerEngine's signs: battery + discharging, grid + importing", () => {
  const f = h.flowFigures(NOW, CFG);
  assert.deepStrictEqual([f.solarW, f.batteryW, f.soc, f.gridW, f.loadW, f.evW], [243, -4548, 66, 5548, 744, 0]);
  assert.strictEqual(f.batteryState, "charging");
  assert.strictEqual(f.gridState, "importing");
  assert.strictEqual(h.flowFigures(st({ battery_power: 800, grid_power: -2000 }), CFG).batteryState, "discharging");
  assert.strictEqual(h.flowFigures(st({ battery_power: 800, grid_power: -2000 }), CFG).gridState, "exporting");
  assert.strictEqual(h.flowFigures(st({ battery_power: 4, grid_power: -3 }), CFG).batteryState, "idle");
  assert.strictEqual(h.flowFigures({}, CFG).batteryState, "unknown");
});

test("options: no solar, no car, other entities, a breakdown of the plants", () => {
  assert.strictEqual(h.flowFigures(NOW, { solar: false }).hasSolar, false);
  assert.strictEqual(h.flowFigures(NOW, {}).hasEv, false);
  assert.strictEqual(h.flowFigures(NOW, {}).evW, null);
  const mine = { "sensor.my_grid": { state: "-300" } };
  assert.strictEqual(h.flowFigures(mine, { entities: { grid: "sensor.my_grid" } }).gridW, -300);
  const f = h.flowFigures(Object.assign({ "sensor.pe_state_solar_roof_power": { state: "100" } }, NOW),
    { plants: [{ name: "Roof", entity: "sensor.pe_state_solar_roof_power" }, { name: "Shed", entity: "sensor.gone" }] });
  assert.deepStrictEqual(f.plants, [{ name: "Roof", w: 100 }, { name: "Shed", w: null }]);
});

test("the model: which lines move and which way (charging runs hub to battery, export runs grid-ward)", () => {
  const m = h.flowModel(h.flowFigures(NOW, CFG));
  assert.deepStrictEqual(m.nodes.map((n) => n.id), ["solar", "battery", "grid", "home", "ev"]);
  const line = (a, b) => m.lines.find((l) => l.from === a && l.to === b);
  assert.ok(line("solar", "hub").moving && !line("solar", "hub").reverse);
  assert.ok(line("hub", "battery").moving && !line("hub", "battery").reverse);          // charging: hub -> battery
  assert.ok(line("grid", "hub").moving && !line("grid", "hub").reverse);                // importing: grid -> hub
  assert.ok(line("hub", "home").moving);
  assert.ok(!line("home", "ev").moving);                                                // the car is not charging
  const out = h.flowModel(h.flowFigures(st({ solar_power: 3000, battery_power: 2000, grid_power: -4000, load_power: 800, ev_power: 0 }), CFG));
  const l2 = (a, b) => out.lines.find((l) => l.from === a && l.to === b);
  assert.ok(l2("hub", "battery").moving && l2("hub", "battery").reverse);               // discharging: battery -> hub
  assert.ok(l2("grid", "hub").moving && l2("grid", "hub").reverse);                     // exporting: hub -> grid
  assert.strictEqual(out.nodes.find((n) => n.id === "grid").sub, "Grid exporting");
  assert.strictEqual(out.nodes.find((n) => n.id === "battery").sub, "2.0 kW discharging");
  const idle = h.flowModel(h.flowFigures({}, { solar: false }));
  assert.ok(idle.lines.every((l) => !l.moving) && !idle.nodes.some((n) => n.id === "solar" || n.id === "ev"));
  assert.strictEqual(m.plants.length, 0);                                               // no breakdown for one plant
});

test("the flow card names no inverter, no brand and no card from another install", () => {
  const text = require("node:fs").readFileSync(require.resolve("../ha-powerengine-card.js"), "utf8");
  assert.ok(!/sunsynk/i.test(text), "no Sunsynk anywhere in the card");
  assert.deepStrictEqual(Object.values(h.FLOW_ENTITIES).filter((e) => !e.startsWith("sensor.pe_state_")), []);
});
