const test = require("node:test");
const assert = require("node:assert");
const c = require("../ha-powerengine-card.js");

test("devices need app 0.9.93 or newer", () => {
  assert.strictEqual(c.devicesSupported("0.9.92"), false);
  assert.strictEqual(c.devicesSupported("0.9.93"), true);
  assert.strictEqual(c.devicesSupported("0.10.0"), true);
  assert.strictEqual(c.devicesSupported("unknown"), false);
  assert.strictEqual(c.devicesSupported(undefined), false);
});

test("deviceDraft copies saved devices and fills the gaps", () => {
  const saved = [{ id: "garage", adapter: "solis", inputs: { battery_soc: { entity: "sensor.a" } } }];
  const d = c.deviceDraft(saved);
  assert.deepStrictEqual(d, [{ id: "garage", adapter: "solis", name: "garage", firmware: "", control: "read_only", inputs: { battery_soc: { entity: "sensor.a" } } }]);
  d[0].inputs.battery_soc.entity = "sensor.b";
  assert.strictEqual(saved[0].inputs.battery_soc.entity, "sensor.a");        // a copy
  assert.deepStrictEqual(c.deviceDraft(undefined), []);
});

test("buildDevices keeps only what the app accepts", () => {
  const out = c.buildDevices([
    { id: "garage", adapter: "solis", name: "Garage", firmware: "420044", inputs: {
      battery_soc: { entity: "sensor.soc" }, battery_power: { entity: "sensor.bp", invert: true },
      solar_power: { entity: "" }, grid_power: { entity: "sensor.x" } } },
    { id: "empty", adapter: "", inputs: {} },                                 // no adapter: dropped
  ]);
  assert.deepStrictEqual(out, [{ id: "garage", adapter: "solis", name: "Garage", control: "read_only", firmware: "420044",
    inputs: { battery_soc: { entity: "sensor.soc" }, battery_power: { entity: "sensor.bp", invert: true } } }]);
  assert.deepStrictEqual(c.buildDevices([{ id: "a", adapter: "solis", inputs: { solar_power: { entity: "sensor.pv", invert: true } } }])[0].inputs,
    { solar_power: { entity: "sensor.pv" } });                                 // only battery power can be inverted
});

test("buildConfig sends devices only when the draft has the list", () => {
  const base = { inputs: {}, features: {}, operation: {} };
  assert.ok(!("devices" in c.buildConfig(base)));
  assert.deepStrictEqual(c.buildConfig({ ...base, devices: [] }).devices, []);          // an empty list removes them
});

test("deviceNewId avoids main and the ids in use", () => {
  assert.strictEqual(c.deviceNewId("Main", []), "main_2");
  assert.strictEqual(c.deviceNewId("Device 2", ["device_2"]), "device_2_2");
  assert.match(c.deviceNewId("2nd garage", []), /^[a-z][a-z0-9_]*$/);
});

test("deviceReadout reads the sensors the app publishes", () => {
  const dev = { id: "garage", inputs: { battery_soc: { entity: "sensor.a" }, battery_power: { entity: "sensor.b" }, solar_power: { entity: "sensor.c" } } };
  assert.strictEqual(c.deviceReadout({}, { id: "x", inputs: {} }), "No inputs chosen yet");
  assert.match(c.deviceReadout({}, dev), /Waiting for the first reading/);
  const states = {
    "sensor.pe_state_dev_garage_soc": { state: "55.4" },
    "sensor.pe_state_dev_garage_battery_power": { state: "-800" },
    "sensor.pe_state_dev_garage_solar_power": { state: "1200" },
  };
  assert.strictEqual(c.deviceReadout(states, dev), "battery 55%, battery charging 800 W, solar 1200 W");
  states["sensor.pe_state_dev_garage_battery_power"].state = "5";
  states["sensor.pe_state_dev_garage_solar_power"].state = "unknown";
  assert.strictEqual(c.deviceReadout(states, dev), "battery 55%, battery idle");
});
