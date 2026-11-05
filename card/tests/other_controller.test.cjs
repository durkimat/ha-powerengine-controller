const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const h = require("../ha-powerengine-card.js");

const ver = (a) => ({ "sensor.pe_diag_version": { state: "0.9.108", attributes: a } });
const hs = (v) => ({ state: v, attributes: {} });

test("guard roles show for another controller and an older app only", () => {
  assert.deepEqual(h.guardRolesShown("other"), h.GUARD_ROLES);
  assert.deepEqual(h.guardRolesShown(null), h.GUARD_ROLES);
  assert.deepEqual(h.guardRolesShown("none"), []);
  assert.deepEqual(h.guardRolesShown("unset"), []);
});

test("effective value: draft choice wins, an older app gives null", () => {
  assert.equal(h.effectiveOtherController(null, "none"), null);
  assert.equal(h.effectiveOtherController("unset", undefined), "unset");
  assert.equal(h.effectiveOtherController("unset", "other"), "other");
  assert.equal(h.effectiveOtherController("none", "other"), "other");
  assert.equal(h.effectiveOtherController("none", undefined), "none");
  assert.equal(h.otherControllerValue(ver({ other_controller: "other" })), "other");
  assert.equal(h.otherControllerValue(ver({})), null);
});

test("unset prompt", () => {
  assert.equal(h.otherControllerPrompt("unset"),
    "Choose whether another battery controller is installed. PowerEngine won't go Active until you do.");
  for (const v of ["none", "other", null]) assert.equal(h.otherControllerPrompt(v), "");
});

test("Tests card hint names no other product", () => {
  assert.match(h.testsPauseHint(), /Pause control/);
  assert.doesNotMatch(h.testsPauseHint(), /Predbat/);
});

test("no string in the card names Predbat, AppDaemon or the owner's automations", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "ha-powerengine-card.js"), "utf8");
  for (const id of ["charge_house_battery", "discharge_house_battery", "house_battery_start_charging",
    "house_battery_stop_charging", "automation.house_battery"]) assert.ok(!src.includes(id), id);
  assert.ok(!/legacy automations/i.test(src));
  assert.ok(!/predbat/i.test(src), "Predbat");
  assert.ok(!/appdaemon/i.test(src), "AppDaemon");
});

test("guard roles are not needed to go live without another controller", () => {
  const role = { key: "guard_read_only", required: "no" };
  const draft = { features: {}, operation: { mode: "active" } };
  for (const o of ["none", "unset"]) assert.equal(h.roleNeed(role, draft, false, o).level, "unused", o);
  for (const o of ["other", null, undefined]) assert.equal(h.roleNeed(role, draft, false, o).level, "req", String(o));
  assert.equal(h.roleNeed({ key: "storage_mode", required: "no" }, draft, false, "none").level, "req");
});

test("setup checklist asks for the choice only when unset", () => {
  const base = { peVersion: "0.9.108", cardsLoaded: {}, components: [] };
  assert.ok(h.setupRows({ ...base, otherController: "unset" }).some((r) => r.key === "controller" && r.status === "missing"));
  for (const o of ["none", "other", null]) assert.ok(!h.setupRows({ ...base, otherController: o }).some((r) => r.key === "controller"));
  const rows = (o) => h.setupSummary({ ...base, otherController: o, cardsLoaded: { "apexcharts-card": true, "mushroom-card": true } });
  assert.ok(rows("unset").allSet === false);
});
