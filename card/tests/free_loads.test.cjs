const test = require("node:test");
const assert = require("node:assert");
const c = require("../ha-powerengine-card.js");

test("freeLoadDraft accepts the bare-entity form and copies", () => {
  const saved = [{ entity: "switch.a", name: "A" }, "input_boolean.b"];
  assert.deepStrictEqual(c.freeLoadDraft(saved), [{ entity: "switch.a", name: "A" }, { entity: "input_boolean.b", name: "" }]);
  assert.deepStrictEqual(c.freeLoadDraft(undefined), []);
});

test("buildFreeLoads drops empty and repeated rows and blank names", () => {
  const out = c.buildFreeLoads([{ entity: "switch.a", name: " Immersion " }, { entity: "", name: "x" }, { entity: "switch.a", name: "dup" }, { entity: "switch.b", name: " " }]);
  assert.deepStrictEqual(out, [{ entity: "switch.a", name: "Immersion" }, { entity: "switch.b" }]);
});

test("buildConfig sends the list only when the draft has one", () => {
  const base = { inputs: {}, features: {}, operation: {} };
  assert.strictEqual("free_power_loads" in c.buildConfig(base), false);
  assert.deepStrictEqual(c.buildConfig({ ...base, free_power_loads: [{ entity: "switch.a", name: "" }] }).free_power_loads, [{ entity: "switch.a" }]);
});
