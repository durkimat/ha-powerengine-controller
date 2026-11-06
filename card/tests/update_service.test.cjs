const test = require("node:test");
const assert = require("node:assert");
const h = require("../ha-powerengine-card.js");

test("the update button uses the integration's service once it has one", () => {
  assert.strictEqual(h.hasUpdateService({ services: { powerengine: { update: {}, restart_engine: {} } } }), true);
  assert.strictEqual(h.hasUpdateService({ services: { powerengine: { restart_engine: {} } } }), false);
  assert.strictEqual(h.hasUpdateService({ services: {} }), false);
  assert.strictEqual(h.hasUpdateService(null), false);
});

test("the card no longer talks about a Home Assistant package", () => {
  const fs = require("node:fs");
  const text = fs.readFileSync(require.resolve("../ha-powerengine-card.js"), "utf8");
  assert.ok(!/packages folder|Load PowerEngine's Home Assistant changes|pe_diag_package/.test(text));
});
