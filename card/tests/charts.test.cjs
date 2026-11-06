const test = require("node:test");
const assert = require("node:assert");
const h = require("../ha-powerengine-card.js");

const HALF = 1800000;
const NOW = new Date(2026, 10, 6, 9, 17, 40).getTime();          // Fri 6 Nov 2026 09:17:40 local

const PLAN = {
  series: { t: ["2026-11-06T09:00:00+00:00", "2026-11-06T09:30:00+00:00", "2026-11-06T10:00:00+00:00"],
    soc: [60, 58.5, 57], price_p: [28.8, 28.8, 6.66], solar_kwh: [0.1, 0.2, 0.3], load_kwh: [0.3, 0.3, 0.4],
    charge_kwh: [0, 0, 2], discharge_kwh: [1, 1, 0], solar_export_kwh: [0, null, 0.1], manual: [0, 100, 0],
    action: ["hold", "force_discharge", "self_use"] },
  windows: [{ start: "2026-11-06T09:00:00+00:00", end: "2026-11-06T09:30:00+00:00", reason: "Axle event" }],
  writes_forecast: { "2026-11-06T09:30:00+00:00": 3 },
};

test("chartPts keeps numbers, drops gaps and shifts the time", () => {
  assert.deepStrictEqual(h.chartPts(["2026-11-06T00:00:00Z", 1000, "x"], [1, null, 3], 5), [[Date.parse("2026-11-06T00:00:00Z") + 5, 1]]);
  assert.deepStrictEqual(h.chartPts([1000, 2000], [null, "2"], 0), [[2000, 2]]);
  assert.deepStrictEqual(h.chartPts(undefined, undefined), []);
});

test("a stepped series is closed with a zero half an hour after its last point", () => {
  assert.deepStrictEqual(h.chartClose([[1000, 2], [2000, 3]]), [[1000, 2], [2000, 3], [2000 + HALF, 0]]);
  assert.deepStrictEqual(h.chartClose([]), []);
});

test("the windows: ahead from this minute, today, and the days ending with today", () => {
  const def = (id) => h.CHART_DEFS[id];
  const ahead = h.chartWindow(def("plan"), NOW, false);
  assert.strictEqual(ahead.from, new Date(2026, 10, 6, 9, 17).getTime());
  assert.strictEqual(ahead.to - ahead.from, 36 * 3600000);
  assert.strictEqual(h.chartWindow(def("plan"), NOW, true).to - ahead.from, 18 * 3600000);
  const day = h.chartWindow(def("history"), NOW, false);
  assert.deepStrictEqual([day.from, day.to], [new Date(2026, 10, 6).getTime(), new Date(2026, 10, 7).getTime()]);
  const days = h.chartWindow(def("losses"), NOW, false);
  assert.strictEqual(days.to, new Date(2026, 10, 7).getTime());
  assert.strictEqual(days.from, new Date(2026, 9, 8).getTime());                     // 30 days ending with today
  assert.strictEqual(h.chartWindow(def("losses"), NOW, true).from, new Date(2026, 9, 24).getTime());
});

test("time ticks fall on whole local hours, or on days for a long window", () => {
  const w = h.chartWindow(h.CHART_DEFS.plan, NOW, false);
  const ticks = h.chartTimeTicks(w.from, w.to, false);
  assert.ok(ticks.every((t) => /^\d\d:00$/.test(t.label) && new Date(t.ms).getHours() % 3 === 0));
  assert.strictEqual(ticks[0].label, "12:00");
  assert.ok(h.chartTimeTicks(w.from, w.to, true).every((t) => new Date(t.ms).getHours() % 6 === 0));
  const d = h.chartWindow(h.CHART_DEFS.losses, NOW, false);
  const days = h.chartTimeTicks(d.from, d.to, false);
  assert.ok(days.length >= 5 && days.every((t) => /^\d+ [A-Z][a-z]{2}$/.test(t.label)));
  assert.ok(h.chartTimeTicks(d.from, d.to, true).length < days.length * 3);
});

test("nice ticks and the axis range", () => {
  assert.deepStrictEqual(h.chartNiceTicks(0, 100, 5), [0, 20, 40, 60, 80, 100]);
  assert.deepStrictEqual(h.chartNiceTicks(0, 2.4, 4), [0, 1, 2]);
  assert.deepStrictEqual(h.chartNiceTicks(0, 1.2, 4), [0, 0.5, 1]);                                          // (no float noise, no -0)
  assert.deepStrictEqual(h.chartAxisRange({ min: 0, max: 100 }, [5, 300]), { min: 0, max: 100 });          // its own range wins
  const auto = h.chartAxisRange({ min: 0 }, [0.4, 2.3]);
  assert.strictEqual(auto.min, 0);
  assert.ok(auto.max >= 2.3 && auto.max <= 3);
  assert.deepStrictEqual(h.chartAxisRange({ min: 0 }, []).min, 0);                                         // no data: still a usable axis
});

test("a smooth line never overshoots its points, and a step line holds each value", () => {
  const d = h.chartSmoothPath([[0, 50], [10, 0], [20, 0], [30, 50]]);
  const ys = [...d.matchAll(/(-?\d+\.\d),(-?\d+\.\d)/g)].map((m) => Number(m[2]));
  assert.ok(ys.every((y) => y >= -0.01 && y <= 50.01), d);
  assert.strictEqual(h.chartSmoothPath([[0, 1]]), "M0.0,1.0");
  assert.strictEqual(h.chartSmoothPath([[0, 1], [5, 2]]), "M0.0,1.0L5.0,2.0");
  assert.strictEqual(h.chartStepPath([[0, 5], [10, 7], [20, 1]]), "M0.0,5.0H10.0V7.0H20.0V1.0");
});

test("the plan chart's model: desktop has three visible axes, a phone one; Now is placed", () => {
  const wide = h.chartModel("plan", PLAN, { width: 1000, nowMs: NOW });
  assert.deepStrictEqual(wide.axes.filter((a) => a.shown).map((a) => a.id), ["soc", "price", "kwh"]);
  assert.strictEqual(wide.compact, false);
  assert.strictEqual(wide.title, "Next 36 hours");
  const narrow = h.chartModel("plan", PLAN, { width: 360, nowMs: NOW });
  assert.deepStrictEqual(narrow.axes.filter((a) => a.shown).map((a) => a.id), ["soc"]);
  assert.strictEqual(narrow.title, "Next 18 hours");
  assert.ok(wide.nowX !== null && Math.abs(wide.nowX - wide.plot.x0) < 2);
  const battery = wide.series.find((s) => s.key === "battery");
  assert.strictEqual(battery.pts[0][0], Date.parse("2026-11-06T09:00:00+00:00") + HALF);          // a battery level is at the slot's end
  assert.ok(battery.d.startsWith("M") && wide.series.find((s) => s.key === "charge").fill.endsWith("Z"));
  assert.strictEqual(wide.series.find((s) => s.key === "writes").bars.length, 1);
  assert.strictEqual(wide.series.find((s) => s.key === "action").d, "");                            // the tooltip's helper draws nothing
});

test("a line the person switched off is not drawn and does not stretch its axis", () => {
  const all = h.chartModel("plan", PLAN, { width: 1000, nowMs: NOW });
  const off = h.chartModel("plan", PLAN, { width: 1000, nowMs: NOW, hidden: new Set(["charge", "export", "solar", "load", "solarexport"]) });
  assert.strictEqual(off.series.find((s) => s.key === "charge").d, "");
  const kwh = (m) => m.axes.find((a) => a.id === "kwh").range.max;
  assert.ok(kwh(off) < kwh(all));
});

test("the readout at a time: values with their units, the action with its reason, nothing for a gap", () => {
  const m = h.chartModel("plan", PLAN, { width: 1000, nowMs: NOW });
  const at = Date.parse("2026-11-06T09:20:00+00:00");
  const rows = Object.fromEntries(h.chartReadout(m, at, PLAN).map((r) => [r.name, r.text]));
  assert.strictEqual(rows.Battery, "60%");
  assert.strictEqual(rows.Price, "28.8p");
  assert.strictEqual(rows.Action, "Hold: Axle event");
  assert.strictEqual(rows.Charge, "0 kWh");
  assert.ok(!("Manual override" in rows));                                                          // off at this time: not listed
  const later = Object.fromEntries(h.chartReadout(m, Date.parse("2026-11-06T09:40:00+00:00"), PLAN).map((r) => [r.name, r.text]));
  assert.strictEqual(later["Manual override"], "on");
  assert.strictEqual(later["Planned writes"], "3 writes");
  assert.deepStrictEqual(h.chartReadout(m, Date.parse("2026-11-08T09:40:00+00:00"), PLAN), []);
});

test("the history, load-and-solar and losses charts read the sensors' attributes", () => {
  const day = new Date(2026, 10, 6).getTime();
  const HIST = { series: { x: [day, day + HALF], actual_soc: [50, null], plan_soc: [52, 53], price_p: [10, 10], actual_charge: [1, 0],
    plan_charge: [1, 1], actual_bat_export: [0, 0], actual_solar_export: [0, 0], actual_load: [0.3, 0.4], plan_load: [0.3, 0.3],
    actual_solar: [0, 0.1], plan_solar: [0, 0.2] } };
  const hist = h.chartModel("history", HIST, { width: 1000, nowMs: NOW });
  assert.strictEqual(hist.series.find((s) => s.key === "battery").pts.length, 1);                  // a missing actual is a gap
  assert.strictEqual(hist.series.find((s) => s.key === "batteryplan").pts.length, 2);
  const ls = h.chartModel("load_solar", HIST, { width: 1000, nowMs: NOW });
  assert.deepStrictEqual(ls.axes.filter((a) => a.shown).map((a) => a.id), ["energy"]);
  const LOSS = { by_day: [["2026-11-04", 10.2, 7], ["2026-11-05", 11, null]] };
  const loss = h.chartModel("losses", LOSS, { width: 1000, nowMs: NOW });
  assert.strictEqual(loss.series.find((s) => s.key === "kwh").bars.length, 2);
  assert.strictEqual(loss.series.find((s) => s.key === "pct").pts.length, 1);
  assert.strictEqual(h.chartReadout(loss, Date.parse("2026-11-04T12:00:00"), LOSS).find((r) => r.name === "Losses kWh").text, "10.2 kWh");
  assert.strictEqual(h.chartModel("nope", {}, {}), null);
});

test("every chart's series use only axes the chart has, and the card names no other chart card", () => {
  for (const [id, def] of Object.entries(h.CHART_DEFS)) {
    const axes = new Set(def.axes);
    assert.ok(def.series.length && def.series.every((s) => axes.has(s.axis) && s.name && s.color && typeof s.points === "function"), id);
    assert.ok(def.sensor.startsWith("sensor.pe_"), id);
  }
  const text = require("node:fs").readFileSync(require.resolve("../ha-powerengine-card.js"), "utf8");
  assert.ok(!/apexcharts/i.test(text), "no chart card from another install is used or offered");
});
