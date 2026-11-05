const test = require("node:test");
const assert = require("node:assert");
const { readout, inUseFor, nowText, floorsReadout } = require("../ha-powerengine-card.js");

const role = { key: "battery_capacity", static_unit: "kWh" };
const hassWith = (use) => ({ states: { "sensor.pe_diag_learned": { state: "3 learned", attributes: { use } } } });

test("a typed value with no in-use sentence still reads Fixed", () => {
  const r = readout(role, { value: 18 }, null);
  assert.strictEqual(r.text, "Fixed: 18 kWh");
  assert.strictEqual(nowText(r), "Now: Fixed: 18 kWh");
});

test("the app's in-use sentence replaces Fixed and is not prefixed with Now", () => {
  const use = { text: "In use: 17.02 kWh, measured (you entered 18 kWh)." };
  const r = readout(role, { value: 18 }, null, undefined, use);
  assert.strictEqual(r.text, use.text);
  assert.strictEqual(nowText(r), use.text);
});

test("inUseFor finds the sentence by role or setting key, or null", () => {
  const hass = hassWith({ battery_capacity: { text: "In use: 18 kWh, as entered (not measured yet)." } });
  assert.match(inUseFor(hass, "battery_capacity").text, /^In use: 18 kWh/);
  assert.strictEqual(inUseFor(hass, "ev_charger_kw"), null);
  assert.strictEqual(inUseFor({ states: {} }, "battery_capacity"), null);
});

test("the hard floor readout shows a raised floor and where events stop", () => {
  assert.strictEqual(floorsReadout({ battery_floor_soc: 12 }, { hard_floor_margin_pct: 1 }),
    "Hard floor 12% (the battery's own limit, set under Your house). Grid events stop at 13%.");
  assert.strictEqual(floorsReadout({ battery_floor_soc: 12 }, { hard_floor_margin_pct: 1 }, { used: "14%" }),
    "Hard floor 14% (raised from the 12% you entered to where the battery was seen to stop). Grid events stop at 15%.");
});
