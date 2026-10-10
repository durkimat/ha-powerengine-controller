// Turns finished runs (one per day) into what the card's plan layout takes: the hours already run, the plan made at a chosen
// moment, the prices and the sun and house forecast. Pure functions (no DOM), tested by tests/sim_viewer.test.cjs.
"use strict";

const HALF_H = 30 * 60000;
const MODE_OF = { self: "self_use", hold: "hold", charge: "charge", discharge: "export" };

/** The run's 5-minute trace as [{ms, level, mode}]: the trace carries clock times only, so days are counted from the first row. */
function traceOf(res) {
  const t0 = Date.parse(res.series.rows[0].start);
  let prev = -1, dayOff = 0;
  return res.series.trace.map((e) => {
    const [h, m] = String(e[0]).split(":").map(Number);
    const mins = h * 60 + m;
    if (mins < prev) dayOff += 1;
    prev = mins;
    return { ms: t0 + (dayOff * 1440 + mins) * 60000, level: e[1], mode: MODE_OF[e[2]] || "hold" };
  });
}

/** What actually ran, a row per half-hour: the commonest mode in it and the level at its end (a grid event shows as one). */
function actualRows(res) {
  const trace = traceOf(res), out = [];
  let level = trace.length ? trace[0].level : null;
  res.series.rows.forEach((row) => {
    const a = Date.parse(row.start), b = a + HALF_H;
    const inside = trace.filter((p) => p.ms >= a && p.ms < b);
    const count = {};
    inside.forEach((p) => { count[p.mode] = (count[p.mode] || 0) + 1; });
    let mode = Object.keys(count).sort((x, y) => count[y] - count[x])[0] || (out.length ? out[out.length - 1].mode : "hold");
    if (row.axle && mode === "export") mode = "event";
    if (inside.length) level = inside[inside.length - 1].level;
    out.push({ a, b, mode, level, importP: row.act * 100, exportP: row.exp * 100, event: !!row.axle, free: !!row.free,
      solarKw: row.solar * 2, houseKw: (row.house + (row.car || 0)) * 2 });
  });
  return out;
}

/** Every day of a variant joined: {rows, plans [{atMs, ...plan}], dataStart, dataEnd, tz}. `results` in day order, ok ones only. */
function joinDays(results) {
  const ok = results.filter((r) => r && r.status === "ok");
  const rows = [], plans = [];
  ok.forEach((r) => {
    rows.push(...actualRows(r));
    r.series.plans.forEach((p) => plans.push(Object.assign({ atMs: Date.parse(p.at), day: r.day }, p)));
  });
  plans.sort((p, q) => p.atMs - q.atMs);
  return { rows, plans, dataStart: rows.length ? rows[0].a : null, dataEnd: rows.length ? rows[rows.length - 1].b : null,
    tz: ok.length ? ok[0].series.tz : "Europe/London", days: ok.map((r) => r.day) };
}

/** The plan in force at `ms`: the latest made at or before it (null before the first). */
function planAt(joined, ms) {
  let found = null;
  joined.plans.forEach((p) => { if (p.atMs <= ms) found = p; });
  return found;
}

/** The input for PL.timelineLayout at moment `nowMs`: history up to then, the plan then in force, and the prices and sun after. */
function layoutInput(joined, nowMs) {
  const plan = planAt(joined, nowMs);
  const rows = joined.rows;
  const hist = { step_min: 30, series: rows.filter((r) => r.a < nowMs).map((r) => ({ t: new Date(r.a).toISOString(), mode: r.mode,
    level: r.level, import_p: r.importP, export_p: r.exportP, sent: true })) };
  const prices = rows.filter((r) => r.b > nowMs).map((r) => ({ start: new Date(r.a).toISOString(), end: new Date(r.b).toISOString(),
    import_p: r.importP, export_p: r.exportP, event: r.event, free: r.free }));
  const sun = rows.length ? { start: new Date(rows[0].a).toISOString(), step_min: 30, mid: rows.map((r) => r.solarKw),
    house: rows.map((r) => r.houseKw) } : null;
  const items = plan ? plan.timeline.map((i) => ({ mode: i[0], start: i[1], end: i[2], level_start: i[3], level_end: i[4], until: i[5],
    reason: i[6] })) : [];
  const path = plan && plan.path && plan.path.mid && plan.path.mid.length ? plan.path : null;
  return { tl: { now: new Date(nowMs).toISOString(), items, prices, path, sun }, plan,
    opts: { now: nowMs, recent: hist, historyHours: joined.dataStart === null ? 6 : (nowMs - joined.dataStart) / 3600000 + 1,
      maxHours: 24 * 60 } };
}

/** The level that actually happened, as [{ms, level}] across the joined days. */
function actualLevels(joined) {
  return joined.rows.map((r) => ({ ms: r.b, level: r.level }));
}

/** Moments worth stepping to: when each plan was made. */
function planTimes(joined) {
  return joined.plans.map((p) => ({ ms: p.atMs, because: p.because }));
}

if (typeof module !== "undefined") module.exports = { traceOf, actualRows, joinDays, planAt, layoutInput, actualLevels, planTimes };
if (typeof window !== "undefined") window.AD = { traceOf, actualRows, joinDays, planAt, layoutInput, actualLevels, planTimes };
