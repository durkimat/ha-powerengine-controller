// node tools/sim/viewer/test_adapt.cjs : the viewer's data adapter, on a tiny made-up run (run by tests/test_sim.py).
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const AD = require("./adapt.js");
global.window = {};
eval(fs.readFileSync(path.join(__dirname, "plan_layout.js"), "utf8") + "\nglobal.PLX = { timelineLayout };");

const rows = [];
for (let i = 0; i < 48; i++) {
  const t = new Date(Date.UTC(2026, 9, 7, 0, 0) + i * 1800000 - 3600000);   // local midnight in BST = 23:00 UTC
  rows.push({ start: t.toISOString(), act: 0.07, exp: 0.15, solar: i > 14 && i < 30 ? 0.4 : 0, house: 0.3, car: 0, axle: false, free: false });
}
const trace = [];
for (let i = 0; i < 288; i++) {
  const m = i * 5;
  trace.push([`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`, 50 + (i % 10), i < 100 ? "self" : "charge", 0, 0]);
}
const plan = { at: "2026-10-07T12:00:00+01:00", because: "test", calc_s: 1, timeline: [["charge", "2026-10-07T12:00", "2026-10-07T14:00", 40, 80, "until 80%", "cheap"]],
  path: { start: "2026-10-07T12:00:00+01:00", step_min: 15, mid: [40, 50, 60, 70, 80], low: [38, 47, 57, 66, 75], high: [42, 53, 63, 74, 85] } };
const res = { day: "2026-10-07", status: "ok", series: { rows, trace, plans: [plan], tz: "Europe/London" } };

const tr = AD.traceOf(res);
assert.strictEqual(tr.length, 288);
assert.ok(tr[1].ms > tr[0].ms && tr[287].ms > tr[200].ms, "trace times increase");
const j = AD.joinDays([res, { day: "x", status: "failed" }]);
assert.strictEqual(j.rows.length, 48);
assert.strictEqual(j.plans.length, 1);
assert.strictEqual(AD.planAt(j, Date.parse("2026-10-07T11:00:00+01:00")), null, "no plan before the first");
assert.ok(AD.planAt(j, Date.parse("2026-10-07T13:00:00+01:00")));
const inp = AD.layoutInput(j, Date.parse("2026-10-07T13:00:00+01:00"));
const L = PLX.timelineLayout(inp.tl, inp.opts);
assert.ok(L.bands.some((b) => b.mode === "charge" && !b.ran), "the plan's charge band is drawn");
assert.ok(L.bands.some((b) => b.ran), "hours already run are drawn");
assert.ok(L.mid.length > 10 && L.nowH > 10, "level line and now line");
console.log("adapt ok");
