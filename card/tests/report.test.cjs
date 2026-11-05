const test = require("node:test");
const assert = require("node:assert");
const h = require("../ha-powerengine-card.js");

test("scrubReport replaces account, meter and serial numbers, ids, emails and postcodes", () => {
  const src = JSON.stringify({
    a: "sensor.edf_energy_electricity_1012482848197_current_rate", b: "21l4582012", c: "a_c1544bc6",
    d: "0382_d0fa_324a_2586", e: "me@example.com", f: "SW1A 1AA", g: "serial 2025045812345678",
  });
  const { text, count } = h.scrubReport(src);
  for (const bad of ["1012482848197", "4582012", "c1544bc6", "0382_d0fa_324a_2586", "me@example.com", "SW1A 1AA", "2025045812345678"]) {
    assert.ok(!text.includes(bad), bad);
  }
  assert.ok(count >= 6);
  JSON.parse(text);                                    // still valid JSON
});

test("scrubReport keeps ordinary values and gives one value one placeholder", () => {
  const src = JSON.stringify({ soc: 87.5, mode: "Self-use", fw: "420044", ts: "2026-10-04T10:30:00+01:00", at: "decision", x: "12345678", y: "12345678" });
  const { text } = h.scrubReport(src);
  const o = JSON.parse(text);
  assert.strictEqual(o.soc, 87.5);
  assert.strictEqual(o.fw, "420044");
  assert.strictEqual(o.ts, "2026-10-04T10:30:00+01:00");
  assert.strictEqual(o.at, "decision");
  assert.strictEqual(o.x, o.y);
  assert.notStrictEqual(o.x, "12345678");
});

test("reportIssueUrl fills the general form and caps the description", () => {
  const u = new URL(h.reportIssueUrl({ title: " Wrong decision ", description: "x".repeat(9000), appVersion: "0.9.97", cardVersion: "0.9.97" }));
  assert.strictEqual(u.pathname, "/durkimat/ha-powerengine-controller/issues/new");
  assert.strictEqual(u.searchParams.get("template"), h.REPORT_TEMPLATE);
  assert.strictEqual(u.searchParams.get("title"), "Wrong decision");
  assert.strictEqual(u.searchParams.get("description").length, 4000);
  assert.strictEqual(u.searchParams.get("versions"), "app 0.9.97, card 0.9.97");
  assert.ok(u.toString().length < 8000);
});

test("reportFileName", () => {
  assert.match(h.reportFileName(new Date(2026, 9, 4, 10, 5)), /^powerengine-report-20261004-1005\.json$/);
});
