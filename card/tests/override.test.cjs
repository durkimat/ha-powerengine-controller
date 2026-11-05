const test = require("node:test");
const assert = require("node:assert");
const card = require("../ha-powerengine-card.js");

const states = (over = {}, mode = "active", extra = {}) => ({
  "sensor.pe_state_override": { state: over.state || "none", attributes: over.attributes || {} },
  "sensor.pe_state_operation_mode": { state: mode, attributes: {} },
  "sensor.pe_state_decision": { state: "hold", attributes: {} },
  "sensor.pe_state_import_rate": { state: "0.28841", attributes: { pence: 28.84 } },
  ...extra,
});

test("hidden when the app publishes no override sensor", () => {
  assert.strictEqual(card.overrideView({}, true).shown, false);
});

test("an admin in Active mode can override; others and Passive cannot", () => {
  const v = card.overrideView(states(), true);
  assert.ok(v.shown && v.canAct && !v.active);
  assert.strictEqual(v.inverter, "Holding (grid runs the house)");
  assert.strictEqual(v.priceNow, "28.84p");
  assert.strictEqual(card.overrideView(states(), false).canAct, false);
  assert.match(card.overrideView(states({}, "passive"), true).blocked, /Active/);
});

test("an active override shows its text and can be cancelled even outside Active", () => {
  const v = card.overrideView(states({ state: "export", attributes: { text: "Export until 14:30" } }, "passive"), true);
  assert.ok(v.active && v.canAct && v.canCancel);
  assert.strictEqual(v.text, "Export until 14:30");
  assert.strictEqual(card.overrideView(states({ state: "export" }), false).canCancel, false);
});

test("payloads", () => {
  const ev = (c) => card.overridePayload(c);
  assert.deepStrictEqual(ev({ clear: true }).event_data, { action: "clear" });
  assert.deepStrictEqual(ev({ mode: "hold", period: "window" }).event_data, { action: "set", mode: "hold", window: true });
  assert.deepStrictEqual(ev({ mode: "export", period: "permanent" }).event_data, { action: "set", mode: "export", permanent: true });
  assert.deepStrictEqual(ev({ mode: "grid_charge", period: "slots", slots: 3 }).event_data, { action: "set", mode: "grid_charge", slots: 3 });
  assert.strictEqual(ev({ mode: "grid_charge", period: "slots", slots: 25 }), null);
  assert.strictEqual(ev({ mode: "grid_charge", period: "slots", slots: 0 }), null);
  assert.strictEqual(ev({ mode: "nope", period: "window" }), null);
  assert.strictEqual(ev({ mode: "hold", period: "until" }), null);
  assert.strictEqual(ev({ mode: "hold", period: "until", until: "2026-10-04T12:00:00.000Z" }).event_data.until, "2026-10-04T12:00:00.000Z");
  assert.strictEqual(ev({ mode: "hold", period: "later" }), null);
});

test("end options are the next 24 half-hours in HA's time zone", () => {
  const opts = card.overrideEndOptions(new Date("2026-10-04T09:40:00Z"), "Europe/London");
  assert.strictEqual(opts.length, 24);
  assert.strictEqual(opts[0].iso, "2026-10-04T10:00:00.000Z");
  assert.strictEqual(opts[0].label, "11:00");          // BST
  assert.strictEqual(opts[1].label, "11:30");
  assert.strictEqual(opts[23].iso, "2026-10-04T21:30:00.000Z");
  assert.strictEqual(card.overrideEndOptions(new Date("2026-10-04T09:10:00Z"), "nonsense/zone")[0].iso, "2026-10-04T09:30:00.000Z");
});

test("summary names the price for a charge and the reserve for an export", () => {
  assert.match(card.overrideSummary({ mode: "grid_charge", period: "window" }, "28.84p"), /28\.84p/);
  assert.match(card.overrideSummary({ mode: "export", period: "permanent" }, null), /minimum reserve.*until you cancel/);
  assert.match(card.overrideSummary({ mode: "hold" }, null), /grid event/);
  assert.strictEqual(card.overrideSummary({ mode: "x" }, null), "");
});

test("inverter words", () => {
  assert.strictEqual(card.inverterWords("export"), "Exporting");
  assert.strictEqual(card.inverterWords("unavailable"), "Unknown");
});
