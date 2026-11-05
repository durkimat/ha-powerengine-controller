const test = require("node:test");
const assert = require("node:assert");
const card = require("../ha-powerengine-card.js");

const st = (state, attributes = {}) => ({ state, attributes, last_updated: "x" });
const world = ({ engine = "v1", mode = "active", pause = "off", preview = false, v2mode = false } = {}) => {
  const s = {
    "sensor.pe_state_engine": st(engine, { v2_preview: preview }),
    "sensor.pe_state_operation_mode": st(mode),
    "switch.pe_ctl_pause": st(pause),
  };
  if (v2mode) s["sensor.pe_v2_mode"] = st("self_use", { preview });
  return s;
};

// ---- badge ------------------------------------------------------------------------------------------------------
test("badge: the chosen engine while Active shows Active; the other engine shows Passive", () => {
  const a = card.engineBadge(world({ engine: "v2", v2mode: true }), "v2");
  assert.strictEqual(a.kind, "active"); assert.strictEqual(a.label, "Active");
  assert.strictEqual(a.line, "Sending commands to the inverter");
  const b = card.engineBadge(world({ engine: "v2", v2mode: true }), "v1");
  assert.strictEqual(b.kind, "passive"); assert.strictEqual(b.line, "Working out what it would do; nothing is sent");
});
test("badge: Active but paused is Paused, on the chosen engine only", () => {
  assert.strictEqual(card.engineBadge(world({ pause: "on" }), "v1").kind, "paused");
  assert.strictEqual(card.engineBadge(world({ mode: "paused" }), "v1").kind, "paused");
  assert.strictEqual(card.engineBadge(world({ pause: "on" }), "v2").kind, "passive");
});
test("badge: Passive mode is Passive for both, pause or not", () => {
  assert.strictEqual(card.engineBadge(world({ mode: "passive" }), "v1").kind, "passive");
  assert.strictEqual(card.engineBadge(world({ mode: "passive", pause: "on" }), "v1").kind, "passive");
});
test("badge: engine v2's page while v1 runs says preview is off when it is", () => {
  const off = card.engineBadge(world({ engine: "v1", v2mode: false }), "v2");
  assert.strictEqual(off.kind, "passive"); assert.strictEqual(off.line, "Not running: preview is off");
  const on = card.engineBadge(world({ engine: "v1", preview: true, v2mode: true }), "v2");
  assert.strictEqual(on.line, "Working out what it would do; nothing is sent");
});
test("badge: no engine sensor means engine v1; missing or unavailable mode is Unknown; junk engine is v1", () => {
  const s = world(); delete s["sensor.pe_state_engine"];
  assert.strictEqual(card.engineBadge(s, "v1").kind, "active");
  assert.strictEqual(card.engineBadge(s, "v2").line, "Not running: preview is off");
  assert.strictEqual(card.engineBadge({}, "v1").kind, "unknown");
  assert.strictEqual(card.engineBadge(null, "v1").kind, "unknown");
  assert.strictEqual(card.engineBadge(world({ mode: "unavailable" }), "v1").label, "Unknown");
  assert.strictEqual(card.engineBadge(world(), "nonsense").engine, "v1");
});

// ---- icons ------------------------------------------------------------------------------------------------------
test("icons: both digits exist, are single paths of closed subpaths inside 24x24", () => {
  assert.deepStrictEqual(Object.keys(card.ENGINE_ICONS).sort(), ["engine-v1", "engine-v2"]);
  for (const [name, d] of Object.entries(card.ENGINE_ICONS)) {
    assert.strictEqual((d.match(/M/g) || []).length, 2, name);          // outline + digit
    assert.strictEqual((d.match(/Z/g) || []).length, 2, name);
    (d.match(/-?\d+(\.\d+)?/g) || []).forEach((n) => assert.ok(Number(n) >= 0 && Number(n) <= 24, `${name} ${n}`));
  }
  assert.notStrictEqual(card.ENGINE_ICONS["engine-v1"], card.ENGINE_ICONS["engine-v2"]);
  assert.deepStrictEqual(card.engineIcon("engine-v1"), { path: card.ENGINE_ICONS["engine-v1"] });
  assert.strictEqual(card.engineIcon("nope"), null);
});

// ---- v2 history -------------------------------------------------------------------------------------------------
const D0 = Date.parse("2026-10-06T00:00:00Z");
const at = (h) => new Date(D0 + h * 3600000).toISOString();
const histAttrs = (over = {}) => Object.assign({
  date: "2026-10-06", earliest: "2026-09-20", latest: "2026-10-06", in_control: "v2",
  series: [
    { t: at(0), level: 40, expected: 41, mode: "self_use", import_p: 28.8, export_p: 15, value_p: 20, sent: true },
    { t: at(0.5), level: 38, expected: 39, mode: "self_use", import_p: 28.8, export_p: 15, value_p: 20, sent: true },
    { t: at(1), level: 45, expected: 44, mode: "charge", import_p: 7, export_p: 15, value_p: 14, sent: true },
    { t: at(1.5), level: null, expected: 50, mode: "charge", import_p: 7, export_p: 15, value_p: null, sent: true },
    { t: at(2), level: 60, expected: 58, mode: "charge", import_p: 7, export_p: 15, value_p: 15, sent: true },
  ],
  changes: [{ at: at(0), mode: "self_use", reason: "Start" }, { at: at(1), mode: "charge", reason: "Cheap rate" }, { at: "bad", mode: "hold" }],
  note: "n" }, over);

test("v2HistoryView: missing, null and empty attributes", () => {
  assert.strictEqual(card.v2HistoryView(null).kind, "none");
  assert.strictEqual(card.v2HistoryView(undefined).kind, "none");
  assert.strictEqual(card.v2HistoryView("x").kind, "none");
  const e = card.v2HistoryView({ date: "2026-10-05", series: [] });
  assert.strictEqual(e.kind, "empty"); assert.match(e.text, /2026-10-05/);
  assert.strictEqual(card.v2HistoryView({ date: "2026-10-05", series: "junk" }).kind, "empty");
  assert.strictEqual(card.v2HistoryView({ series: [{ t: "nope" }] }).kind, "empty");
});
test("v2HistoryView: bands merge by mode, levels split at gaps, steps and axes", () => {
  const v = card.v2HistoryView(histAttrs());
  assert.strictEqual(v.kind, "ok");
  assert.deepStrictEqual(v.bands.map((b) => [b.mode, b.a, b.b, b.preview]), [["self_use", 0, 1, false], ["charge", 1, 2.5, false]]);
  assert.strictEqual(v.spanH, 2.5);
  assert.strictEqual(v.level.length, 2);                                      // the null level splits the line
  assert.strictEqual(v.level[0].length, 3);
  assert.strictEqual(v.level[0][0].h, 0.5);                                   // level is at the half-hour's end
  assert.strictEqual(v.expected, undefined);                                   // the opening plan is no longer drawn
  assert.strictEqual(v.importSteps.length, 5);
  assert.strictEqual(v.priceMax, 30);
  assert.strictEqual(v.valueMax, 20);
  assert.strictEqual(v.control, "v2"); assert.strictEqual(v.banner, ""); assert.strictEqual(v.previewOnly, false);
  assert.deepStrictEqual(v.changes.map((c) => c.mode), ["Charge", "Self-use"]);   // newest first; the change with a bad time is dropped
  assert.strictEqual(v.changes[0].reason, "Cheap rate");
});
test("v2HistoryView: a preview day and a mixed day say so", () => {
  const prev = histAttrs({ in_control: "v1", series: histAttrs().series.map((p) => Object.assign({}, p, { sent: false })) });
  const v = card.v2HistoryView(prev);
  assert.strictEqual(v.previewOnly, true); assert.ok(v.bands.every((b) => b.preview)); assert.match(v.banner, /Preview only/);
  assert.strictEqual(v.control, "v1");
  const s = histAttrs().series; s[3].sent = false;
  const m = card.v2HistoryView(histAttrs({ in_control: undefined, series: s }));
  assert.strictEqual(m.control, "mixed"); assert.strictEqual(m.partlyPreview, true); assert.match(m.banner, /dimmed/);
  assert.ok(m.bands.some((b) => b.preview) && m.bands.some((b) => !b.preview));
});
test("v2HistoryView: unsorted points, a lone point and no changes", () => {
  const v = card.v2HistoryView({ date: "d", series: [{ t: at(1), level: 50, mode: "hold" }, { t: at(0), level: 49, mode: "hold" }] });
  assert.strictEqual(v.bands.length, 1); assert.strictEqual(v.bands[0].b, 2);
  assert.deepStrictEqual(v.changes, []);
  const one = card.v2HistoryView({ series: [{ t: at(0), level: 50, mode: "weird" }] });
  assert.strictEqual(one.spanH, 0.5); assert.strictEqual(one.level[0].length, 1);
  assert.strictEqual(one.priceMax, 10); assert.strictEqual(one.valueMax, 0);
});
test("v2 history day picker fires pe_v2_history_day inside the kept days, the plan one still pe_history_day", () => {
  const p = card.v2HistoryDayPayload("2026-10-03", "2026-09-20", "2026-10-06");
  assert.deepStrictEqual(p, { type: "fire_event", event_type: "pe_v2_history_day", event_data: { date: "2026-10-03" } });
  assert.strictEqual(card.v2HistoryDayPayload("2026-10-07", "2026-09-20", "2026-10-06"), null);
  assert.strictEqual(card.v2HistoryDayPayload("junk", "", ""), null);
  assert.strictEqual(card.historyDayPayload("2026-10-03", "2026-09-20", "2026-10-06").event_type, "pe_history_day");
});

// ---- comparison -------------------------------------------------------------------------------------------------
const day = (date, over = {}) => Object.assign({ date, status: "ok", reason: "", in_control: "v1", live: true,
  v1: 1.3, v2: 1.38, bound: 1.51, best: "v2", diff: 0.08, calib: { engine: "v1", diff: -0.04, diff_pct: -2 } }, over);
const cmpAttrs = () => ({
  days: [day("2026-10-06"), day("2026-10-05", { v1: 1.0, v2: 0.7, best: "v1", diff: -0.3, in_control: "v2", live: false, calib: null }),
    day("2026-10-04", { status: "no_snapshot", reason: "", v1: null, v2: null, bound: null, best: null, diff: null }),
    day("2026-10-03", { status: "failed", reason: "Replay timed out" }),
    day("2026-10-02", { in_control: "mixed", v1: 2, v2: 2, best: "tie", diff: 0 })],
  totals: { days: 3, v1: 4.3, v2: 4.08, bound: 5.0, diff: -0.22 },
  calib: { engine: "v1", days: 4, mean_abs_diff: 0.04, mean_abs_pct: 1.2 },
  last_run: { at: "2026-10-07T03:20:00+01:00", day: "2026-10-06", status: "ok", took_s: 720 },
  note: "Rough guide." });

test("engineCompareView: missing attributes, waiting, off, error", () => {
  assert.strictEqual(card.engineCompareView(null).kind, "none");
  assert.strictEqual(card.engineCompareView(undefined, undefined).kind, "none");
  const w = card.engineCompareView({}, "waiting");
  assert.strictEqual(w.kind, "waiting"); assert.match(w.text, /one full day of forecast records/);
  assert.strictEqual(card.engineCompareView({ days: [], note: "n" }, "ok").kind, "waiting");
  assert.strictEqual(card.engineCompareView({ days: "junk" }).kind, "waiting");
  assert.strictEqual(card.engineCompareView({ days: [day("2026-10-06", { status: "no_snapshot", v1: null })] }, "waiting").kind, "waiting");
  assert.strictEqual(card.engineCompareView({}, "off").kind, "off");
  const e = card.engineCompareView({ days: [], last_run: { status: "failed", message: "boom" } }, "error");
  assert.strictEqual(e.kind, "error"); assert.match(e.text, /boom/);
  assert.strictEqual(card.engineCompareView({}, "running").running, true);
});
test("engineCompareView: rows, best marked, difference signs, in control, not-compared reasons", () => {
  const v = card.engineCompareView(cmpAttrs(), "ok");
  assert.strictEqual(v.kind, "ok");
  assert.strictEqual(v.rows.length, 5);
  const [a, b, c, d, e] = v.rows;
  assert.deepStrictEqual([a.v1, a.v2, a.bound, a.diff, a.best], ["£1.30", "£1.38", "£1.51", "+£0.08", "v2"]);
  assert.deepStrictEqual([b.diff, b.best, b.controlText], ["−£0.30", "v1", "engine v2 (passive)"]);
  assert.strictEqual(a.controlText, "engine v1");
  assert.match(a.calib, /−£0\.04 \(-2%\)/);
  assert.strictEqual(c.ok, false); assert.strictEqual(c.reason, "no forecast record yet");
  assert.strictEqual(d.ok, false); assert.strictEqual(d.reason, "Replay timed out");
  assert.strictEqual(e.best, "tie"); assert.strictEqual(e.controlText, "mixed"); assert.strictEqual(e.diff, "£0.00");
  assert.match(a.label, /Tue|Oct/);
});
test("engineCompareView: totals and the calibration line", () => {
  const v = card.engineCompareView(cmpAttrs(), "ok");
  assert.deepStrictEqual([v.totals.days, v.totals.v1, v.totals.v2, v.totals.bound, v.totals.diff, v.totals.best], [3, "£4.30", "£4.08", "£5.00", "−£0.22", "v1"]);
  assert.strictEqual(v.calibration, "On 4 days engine v1 was in control, its replay came within 4p (1.2%) of the metered cost, on average.");
  assert.strictEqual(v.note, "Rough guide.");
  assert.match(v.lastRun, /2026-10-06/);
  const none = cmpAttrs(); none.calib = null; delete none.totals;
  const w = card.engineCompareView(none, "ok");
  assert.match(w.calibration, /No day yet/); assert.strictEqual(w.totals, null);
  assert.strictEqual(card.calibrationLine({ engine: "v2", days: 1, mean_abs_diff: 1.5, mean_abs_pct: 9 }), "On the day engine v2 was in control, its replay came within £1.50 (9%) of the metered cost.");
});
test("money format", () => {
  assert.strictEqual(card.gbp(0.075, true), "+£0.08");
  assert.strictEqual(card.gbp(-1.234), "−£1.23");
  assert.strictEqual(card.gbp(null), "–");
  assert.strictEqual(card.gbp("x"), "–");
});

// ---- the config feature ---------------------------------------------------------------------------------------
test("engine_compare is a feature, on by default, listed in the Costs page extras topic", () => {
  assert.ok(card.FEATURES.some((f) => f[0] === "engine_compare"));
  assert.strictEqual(card.FEATURE_DEFAULTS.engine_compare, true);
  const plan = card.topicPlan([], [], ["engine_compare"], []);
  const t = plan.find((x) => x.key === "extras");
  assert.deepStrictEqual(t.features, ["engine_compare"]);
  assert.ok(!plan.find((x) => x.key === "other"));
});

test("refreshEngineIcons re-looks-up pe: icons stuck in legacy mode, inside shadow roots", () => {
  const { refreshEngineIcons } = require("../ha-powerengine-card.js");
  const mk = (icon, extra) => Object.assign({ localName: "ha-icon", sets: [], _legacy: true, _path: undefined,
    get icon() { return this._icon; }, set icon(v) { this.sets.push(v); this._icon = v; } }, extra || {});
  const stuck = mk(); stuck._icon = "pe:engine-v1";
  const fine = mk(); fine._icon = "pe:engine-v2"; fine._legacy = false; fine._path = "M0";
  const other = mk(); other._icon = "mdi:cog";
  const unknown = mk(); unknown._icon = "pe:nothing";
  const inner = { querySelectorAll: () => [stuck, fine, other, unknown] };
  const holder = { localName: "div", shadowRoot: inner };
  const root = { querySelectorAll: () => [holder] };
  assert.equal(refreshEngineIcons(root), 1);
  assert.equal(stuck._legacy, false);
  assert.deepEqual(stuck.sets, ["", "pe:engine-v1"]);
  assert.deepEqual(fine.sets, []);
  assert.deepEqual(other.sets, []);
  assert.deepEqual(unknown.sets, []);
});

test("engine icon digits are traced opposite to the outline, so they cut out under the non-zero fill rule", () => {
  const { ENGINE_ICONS } = require("../ha-powerengine-card.js");
  const area = (d) => {
    const pts = []; let x = 0, y = 0;
    for (const [, c, a] of d.matchAll(/([MLHVZ])([^MLHVZ]*)/g)) {
      const n = (a.match(/-?[\d.]+/g) || []).map(Number);
      if (c === "M" || c === "L") { x = n[0]; y = n[1]; pts.push([x, y]); }
      if (c === "H") { x = n[0]; pts.push([x, y]); }
      if (c === "V") { y = n[0]; pts.push([x, y]); }
    }
    return pts.reduce((s, p, i) => s + p[0] * pts[(i + 1) % pts.length][1] - pts[(i + 1) % pts.length][0] * p[1], 0) / 2;
  };
  for (const d of Object.values(ENGINE_ICONS)) {
    const parts = d.split(/(?=M)/);
    assert.ok(parts.length >= 2);
    const outline = area(parts[0]);
    parts.slice(1).forEach((p) => assert.ok(Math.sign(area(p)) === -Math.sign(outline), p));
  }
});

test("engineCompareView: a day that was scaled or folded is marked approximate", () => {
  const day = (date, extra) => Object.assign({ date, status: "ok", v1: 1, v2: 1.5, bound: 2, in_control: "v2" }, extra);
  const v = card.engineCompareView({ days: [day("2026-10-31", { scaled: 2 }), day("2026-10-30", { note: "25-hour day: the repeated hour was left out" }), day("2026-10-29", {})] }, "ok");
  assert.deepStrictEqual(v.rows.map((r) => r.approx), ["2 part-measured half-hours scaled up", "25-hour day: the repeated hour was left out", ""]);
});

test("engineCompareView: an opened day carries the app's why, or null for an older result", () => {
  const why = { gap: 2.2, end: -0.1, lines: ["Engine v2 saved £2.20 less than the best possible."],
    parts: [{ key: "event", label: "Grid event", v2: -1, bound: -3, gap: 2 }, { key: "night", label: "Overnight (00:00 to 07:00)", v2: 0, bound: 0, gap: 0 }] };
  const v = card.engineCompareView({ days: [{ date: "2026-10-29", status: "ok", v1: 1, v2: 1.5, bound: 2, why }, { date: "2026-10-28", status: "ok", v1: 1, v2: 1.5, bound: 2 }] }, "ok");
  assert.strictEqual(v.rows[1].why, null);
  assert.deepStrictEqual(v.rows[0].why.parts[0], { label: "Grid event", v2: "−£1.00", bound: "−£3.00", gap: "+£2.00", gapN: 2 });
  assert.strictEqual(v.rows[0].why.end, "−£0.10");
  assert.strictEqual(v.rows[0].why.lines.length, 1);
  assert.strictEqual(card.whyView(null), null);
});

test("eventsView: months newest first, with the days that had events, formatted", () => {
  const v = card.eventsView({ names: { event: "Axle" }, months: [
    { month: "2026-09", axle_kwh: 10, axle_gross: 20, axle_energy: 5, axle_net: 15, free_power_net: 0, actual: 40, standing: 17 },
    { month: "2026-10", axle_kwh: 3.456, axle_gross: 6.9, axle_energy: 2.1, axle_net: 4.8, free_power_net: 0.2, actual: 30, standing: 17.7,
      ev_days: [["2026-10-28", 1, 2, 1, 1, 0, 0], ["2026-10-29", 3.46, 6.9, 2.1, 4.8, 1, 0.2]] }] });
  assert.strictEqual(v.name, "Axle");
  assert.deepStrictEqual(v.months.map((m) => m.key), ["2026-10", "2026-09"]);
  assert.strictEqual(v.months[0].kwh, "3.5");
  assert.strictEqual(v.months[0].daily, "£12.30");
  assert.deepStrictEqual(v.months[0].days.map((d) => d.date), ["2026-10-29", "2026-10-28"]);   // newest day first
  assert.strictEqual(v.months[0].days[0].net, "£4.80");
  assert.strictEqual(v.months[1].days.length, 0);
  assert.strictEqual(v.hasDays, true);
  assert.deepStrictEqual(card.eventsView(null).months, []);
});

test("compareScoped: only the days in the period, totals again from them, and a note when the period is longer", () => {
  const day = (date, v1, v2) => ({ date, status: "ok", v1, v2, bound: v2 + 1, in_control: "v2" });
  const v = card.engineCompareView({ days: [day("2026-10-30", 1, 2), day("2026-10-29", 2, 3), day("2026-10-28", 3, 5)] }, "ok");
  const range = { key: "custom", label: "Mon 26 Oct to Thu 29 Oct", rows: [1, 2, 3, 4], from: "2026-10-26", to: "2026-10-29" };
  const s = card.compareScoped(v, range);
  assert.deepEqual(s.view.rows.map((r) => r.date), ["2026-10-29", "2026-10-28"]);
  assert.equal(s.view.totals.days, 2);
  assert.equal(s.view.totals.v2, "£8.00");
  assert.equal(s.view.totals.diff, "+£3.00");
  assert.match(s.note, /last 7 days/);
  const none = card.compareScoped(v, { key: "custom", label: "x", rows: [1], from: "2026-09-01", to: "2026-09-02" });
  assert.equal(none.view.totals, null);
  assert.match(none.note, /none of them are in this period/);
});

test("eventsInRange: the events inside a period, or partial when the period starts before the listed months", () => {
  const attrs = { months: [{ month: "2026-08", axle_kwh: 1 },
    { month: "2026-09", ev_days: [] },
    { month: "2026-10", ev_days: [["2026-10-28", 1, 2, 1, 1, 0, 0], ["2026-10-29", 3.46, 6.9, 2.1, 4.8, 1, 0.2]] }] };
  const t = card.eventsInRange(attrs, { from: "2026-10-29", to: "2026-10-30" });
  assert.equal(t.days, 1);
  assert.equal(t.kwh, 3.46);
  assert.equal(t.net, 4.8);
  assert.equal(card.eventsInRange(attrs, { from: "2026-10-01", to: "2026-10-30" }).days, 2);
  assert.equal(card.eventsInRange(attrs, { from: "2026-09-02", to: "2026-09-30" }).days, 0);       // listed, nothing happened
  assert.equal(card.eventsInRange(attrs, { from: "2026-08-02", to: "2026-08-30" }).partial, true);  // before the listed months
  assert.equal(card.eventsInRange(attrs, { from: null, to: null }), null);
});

test("engineCompareView: rows carry their status so a day that has no forecast record offers no retry", () => {
  const v = card.engineCompareView({ days: [{ date: "2026-10-31", status: "incomplete", reason: "x" }, { date: "2026-10-30", status: "no_snapshot", reason: "y" },
    { date: "2026-10-29", status: "ok", v1: 1, v2: 2, bound: 3 }] }, "ok");
  assert.deepStrictEqual(v.rows.map((r) => r.status), ["incomplete", "no_snapshot", "ok"]);
});

test("eventsView and eventsInRange split the event's pay from the export rate on top, when the app sends it", () => {
  const old = card.eventsView({ months: [{ month: "2026-10", axle_kwh: 3, axle_gross: 3.45, ev_days: [["2026-10-29", 3, 3.45, 2, 1.4, 0, 0]] }] });
  assert.strictEqual(old.hasPay, false);
  const now = card.eventsView({ months: [{ month: "2026-10", axle_kwh: 3, axle_pay: 3, axle_gross: 3.45, ev_days: [["2026-10-29", 3, 3.45, 2, 1.4, 0, 0, 3]] }] });
  assert.strictEqual(now.hasPay, true);
  assert.strictEqual(now.months[0].pay, "£3.00");
  assert.strictEqual(now.months[0].onTop, "£0.45");
  assert.strictEqual(now.months[0].days[0].onTop, "£0.45");
  const attrs = { months: [{ month: "2026-10", ev_days: [["2026-10-29", 3, 3.45, 2, 1.4, 0, 0, 3]] }] };
  assert.strictEqual(card.eventsInRange(attrs, { from: "2026-10-29", to: "2026-10-29" }).pay, 3);
  assert.strictEqual(card.eventsInRange({ months: [{ month: "2026-10", ev_days: [["2026-10-29", 3, 3.45, 2, 1.4, 0, 0]] }] }, { from: "2026-10-29", to: "2026-10-29" }).pay, null);
});
