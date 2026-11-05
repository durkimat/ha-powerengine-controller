const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const h = require("../ha-powerengine-card.js");

const ver = (a) => ({ "sensor.pe_diag_version": { state: "0.9.108", attributes: a } });
const hs = (v) => ({ state: v, attributes: {} });

test("handover card: shown only for predbat when the app publishes the choice", () => {
  assert.equal(h.handoverVisible(ver({ other_controller: "predbat" })), true);
  for (const v of ["none", "other", "unset"]) assert.equal(h.handoverVisible(ver({ other_controller: v })), false, v);
  // an app that says "none" wins even if Predbat's entities are still around
  assert.equal(h.handoverVisible({ ...ver({ other_controller: "none" }), "switch.predbat_set_read_only": hs("on") }), false);
});

test("handover card: older app falls back to Predbat's entities", () => {
  assert.equal(h.handoverVisible(ver({})), false);
  assert.equal(h.handoverVisible({}), false);
  assert.equal(h.handoverVisible(undefined), false);
  assert.equal(h.handoverVisible({ ...ver({}), "switch.predbat_set_read_only": hs("on") }), true);
  assert.equal(h.handoverVisible({ "input_select.battery_controller": hs("PowerEngine") }), true);
});

test("guard roles show for predbat, other and an older app only", () => {
  assert.deepEqual(h.guardRolesShown("predbat"), ["guard_read_only", "guard_off_1", "guard_off_2"]);
  assert.deepEqual(h.guardRolesShown("other"), h.GUARD_ROLES);
  assert.deepEqual(h.guardRolesShown(null), h.GUARD_ROLES);
  assert.deepEqual(h.guardRolesShown("none"), []);
  assert.deepEqual(h.guardRolesShown("unset"), []);
});

test("effective value: draft choice wins, an older app gives null", () => {
  assert.equal(h.effectiveOtherController(null, "none"), null);
  assert.equal(h.effectiveOtherController("unset", undefined), "unset");
  assert.equal(h.effectiveOtherController("unset", "predbat"), "predbat");
  assert.equal(h.effectiveOtherController("none", "other"), "other");
  assert.equal(h.effectiveOtherController("none", undefined), "none");
  assert.equal(h.otherControllerValue(ver({ other_controller: "other" })), "other");
  assert.equal(h.otherControllerValue(ver({})), null);
});

test("unset prompt", () => {
  assert.equal(h.otherControllerPrompt("unset"),
    "Choose whether another battery controller is installed. PowerEngine won't go Active until you do.");
  for (const v of ["none", "predbat", "other", null]) assert.equal(h.otherControllerPrompt(v), "");
});

test("Tests card hint names Predbat only when it is in use", () => {
  assert.match(h.testsPauseHint(ver({ other_controller: "predbat" })), /Predbat/);
  assert.doesNotMatch(h.testsPauseHint(ver({ other_controller: "none" })), /Predbat/);
});

test("handover rows no longer look at personal automations", () => {
  const r = h.handoverRows({
    "input_select.battery_controller": hs("PowerEngine"), "switch.predbat_set_read_only": hs("on"),
    "switch.pe_ctl_pause": hs("off"), "sensor.pe_state_operation_mode": hs("active") });
  assert.deepEqual(r.rows.map((x) => x.label), ["Predbat read-only", "PowerEngine paused", "PowerEngine mode"]);
  assert.equal(r.status, "live");
});

test("no string in the card names the owner's automations", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "ha-powerengine-card.js"), "utf8");
  for (const id of ["charge_house_battery", "discharge_house_battery", "house_battery_start_charging",
    "house_battery_stop_charging", "automation.house_battery"]) assert.ok(!src.includes(id), id);
  assert.ok(!/legacy automations/i.test(src));
});

test("guard roles are not needed to go live without another controller", () => {
  const role = { key: "guard_read_only", required: "no" };
  const draft = { features: {}, operation: { mode: "active" } };
  for (const o of ["none", "unset"]) assert.equal(h.roleNeed(role, draft, false, o).level, "unused", o);
  for (const o of ["predbat", "other", null, undefined]) assert.equal(h.roleNeed(role, draft, false, o).level, "req", String(o));
  assert.equal(h.roleNeed({ key: "storage_mode", required: "no" }, draft, false, "none").level, "req");
});

test("setup checklist asks for the choice only when unset", () => {
  const base = { peVersion: "0.9.108", cardsLoaded: {}, components: [] };
  assert.ok(h.setupRows({ ...base, otherController: "unset" }).some((r) => r.key === "controller" && r.status === "missing"));
  for (const o of ["none", "predbat", "other", null]) assert.ok(!h.setupRows({ ...base, otherController: o }).some((r) => r.key === "controller"));
  const rows = (o) => h.setupSummary({ ...base, otherController: o, cardsLoaded: { "apexcharts-card": true, "mushroom-card": true } });
  assert.ok(rows("unset").allSet === false);
});
