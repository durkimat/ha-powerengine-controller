// VENDORED from the card (../ha-powerengine-card/ha-powerengine-card.js @ be1c8d6) by tools/sim/vendor_card_layout.sh. Do not edit: rerun the script.
// The plan chart's layout (timelineLayout, sunLayout, levelAtHour, timelineHover) and the helpers it uses.
function toNumber(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const t = String(v === null || v === undefined ? "" : v).trim();
  if (t === "") return null;
  const n = Number(t);            // whole string must be numeric: "11:00" is not 11
  return Number.isFinite(n) ? n : null;
}
const V2_MODES = {
  self_use: { name: "Self-use", colour: "--m-self", glyph: "⌂" },
  hold: { name: "Hold", colour: "--m-hold", glyph: "⏸" },
  charge: { name: "Charge", colour: "--m-charge", glyph: "⚡" },
  export: { name: "Export", colour: "--m-export", glyph: "⇧" },
  event: { name: "Grid event", colour: "--m-event", glyph: "★" },
  free: { name: "Free power", colour: "--m-free", glyph: "☀" },
  none: { name: "No mode yet", colour: "--m-hold", glyph: "–" },
};
function v2Mode(key) { return V2_MODES[key] || V2_MODES.none; }
function v2Ms(iso) { const t = Date.parse(iso); return Number.isNaN(t) ? null : t; }
function v2Hm(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function v2P(n, digits) { const v = toNumber(n); return v === null ? "" : `${(Math.round(v * Math.pow(10, digits === undefined ? 2 : digits)) / Math.pow(10, digits === undefined ? 2 : digits))}p`; }
function v2Pct(n) { const v = toNumber(n); return v === null ? "" : `${Math.round(v * 10) / 10}%`; }
// ---- plan: timeline layout ----------------------------------------------------------------------------------------
const HOUR_MS = 3600000;
const HISTORY_HOURS = 18;          // how far back the plan chart scrolls
const VIEW_PAST_HOURS = 1;         // how much of that is in view before the person scrolls (now sits near the left edge)
const TIMELINE_MIN_W = 560;          // the least width, in pixels, the hours in view are spread over

/** The half-hours of sensor.pe_v2_recent that fall in the `hours` before `nowMs`, oldest first, as
 *  {a, b (ms), mode, level (at b), importP, exportP, sent, preview}. Empty when the app publishes none (an older app). */
function recentRows(recent, nowMs, hours) {
  const r = recent && typeof recent === "object" ? recent : null;
  if (!r || !Array.isArray(r.series)) return [];
  const stepMs = (toNumber(r.step_min) > 0 ? toNumber(r.step_min) : 30) * 60000;
  const from = nowMs - (hours || HISTORY_HOURS) * HOUR_MS;
  return r.series.filter((x) => x && v2Ms(x.t) !== null)
    .map((x) => ({ a: v2Ms(x.t), b: v2Ms(x.t) + stepMs, mode: x.mode || "none", level: toNumber(x.level), importP: toNumber(x.import_p),
      exportP: toNumber(x.export_p), sent: x.sent !== false, preview: x.preview === true }))
    .filter((x) => x.b > from && x.a < nowMs).sort((p, q) => p.a - q.a);
}

/** Did the person move the chart? Read from the scroll box just before it is replaced. `box`: {placed, laidOut, scrollLeft}; `geom`: {t0, pph,
 *  defPx} of that box. Only a box whose opening position actually took (it was laid out when placed) says anything: a card built before it is
 *  on screen has no width, the browser ignores the position it is given, and that 0 must not be read back as the person scrolling to the start.
 *  Returns null (no information: keep what we had), {user: false} (still at the opening position, so keep following now) or {user: true, leftMs}. */
function scrollIntent(box, geom) {
  if (!box || !geom || !box.placed || !box.laidOut) return null;
  if (Math.abs(box.scrollLeft - geom.defPx) > 8) return { user: true, leftMs: geom.t0 + (box.scrollLeft / geom.pph) * HOUR_MS };
  return { user: false, leftMs: null };
}

/** Where everything goes on the time axis, in hours from the left edge (t0). Pure: no widths, no clock labels.
 *  `now`: opts.now (the card passes the browser's clock, so the line moves between the app's publishes), else the plan's own
 *  stamp, else the clock. `recent` (sensor.pe_v2_recent's attributes): the hours already run, drawn left of now instead of the plan's
 *  own past; the layout then reaches back HISTORY_HOURS and `viewH` is where the view opens (VIEW_PAST_HOURS before now). */
function timelineLayout(tl, opts) {
  const o = opts || {};
  const a = tl || {};
  const now = toNumber(o.now) !== null ? toNumber(o.now) : v2Ms(a.now) !== null ? v2Ms(a.now) : Date.now();
  const hist = o.recent ? recentRows(o.recent, now, o.historyHours) : [];
  const histStop = hist.length ? hist[hist.length - 1].b : null;
  const valid = (x) => x && v2Ms(x.start) !== null && v2Ms(x.end) !== null && v2Ms(x.end) > v2Ms(x.start);
  const items = (Array.isArray(a.items) ? a.items : []).filter(valid);
  const prices = (Array.isArray(a.prices) ? a.prices : []).filter(valid);
  const path = a.path && Array.isArray(a.path.mid) && v2Ms(a.path.start) !== null ? a.path : null;
  const stepMs = path ? (toNumber(path.step_min) > 0 ? toNumber(path.step_min) : 15) * 60000 : 0;
  const pathEnd = path ? v2Ms(path.start) + (path.mid.length - 1) * stepMs : null;
  const starts = items.map((x) => v2Ms(x.start)).concat(prices.map((x) => v2Ms(x.start)), path ? [v2Ms(path.start)] : [], hist.map((r) => r.a), [now]);
  const ends = items.map((x) => v2Ms(x.end)).concat(prices.map((x) => v2Ms(x.end)), pathEnd !== null ? [pathEnd] : [], [now + HOUR_MS]);
  const t0 = Math.max(Math.min(...starts), now - (hist.length ? (o.historyHours || HISTORY_HOURS) : (o.pastHours || 6)) * HOUR_MS);
  const t1 = Math.min(Math.max(...ends), t0 + (o.maxHours || 48) * HOUR_MS);
  const spanH = (t1 - t0) / HOUR_MS;
  const h = (t) => (t - t0) / HOUR_MS;
  // with the hours already run, the plan's own past (before the last recorded half-hour) gives way to them
  const lo = histStop === null ? 0 : Math.max(0, h(histStop));
  const clip = (x) => ({ a: Math.max(lo, h(v2Ms(x.start))), b: Math.min(spanH, h(v2Ms(x.end))) });
  const planBands = items.map((x) => {
    const c = clip(x);
    const s = v2Ms(x.start), e = v2Ms(x.end);
    const cut = c.a > h(s) + 1e-9;                          // its start was clipped: the level at that start is not the band's own
    return Object.assign(c, { mode: x.mode, until: x.until || "", reason: x.reason || "", levelStart: cut ? null : toNumber(x.level_start), levelEnd: toNumber(x.level_end),
      state: e <= now ? "past" : s < now ? "now" : "future", startMs: s, endMs: e });
  }).filter((b) => b.b > b.a);
  const histBands = [];
  hist.forEach((r) => {
    const last = histBands[histBands.length - 1];
    const a = h(r.a), b = h(r.b);
    if (last && last.mode === r.mode && last.preview === !r.sent && Math.abs(last.b - a) < 1e-6) { last.b = b; last.endMs = r.b; last.levelEnd = r.level; return; }
    histBands.push({ a: Math.max(0, a), b, mode: r.mode, until: "", reason: "", levelStart: null, levelEnd: r.level, state: "past", startMs: r.a, endMs: r.b,
      ran: true, preview: !r.sent });
  });
  const bands = histBands.concat(planBands);
  const histSteps = hist.filter((r) => r.importP !== null).map((r) => ({ a: Math.max(0, h(r.a)), b: h(r.b), importP: r.importP, exportP: r.exportP,
    slot: false, slotProb: null, event: false, free: false, estimated: false, ran: true }));
  const planSteps = prices.map((x) => {
    const c = clip(x);
    return Object.assign(c, { importP: toNumber(x.import_p), exportP: toNumber(x.export_p), slot: x.slot_prob !== null && x.slot_prob !== undefined,
      slotProb: toNumber(x.slot_prob), standardP: toNumber(x.standard_p), event: !!x.event, free: !!x.free, estimated: !!x.estimated });
  }).filter((p) => p.b > p.a && p.importP !== null);
  const steps = histSteps.concat(planSteps);
  const pts = (arr) => (Array.isArray(arr) ? arr : []).map((v, i) => ({ h: h(v2Ms(path.start) + i * stepMs), level: toNumber(v) }))
    .filter((p) => p.level !== null && p.h >= lo - 1e-9 && p.h <= spanH + 1e-9);
  const histLevel = hist.filter((r) => r.level !== null).map((r) => ({ h: h(r.b), level: r.level }));
  const mid = histLevel.concat(path ? pts(path.mid) : []), low = path ? pts(path.low) : [], high = path ? pts(path.high) : [];
  const sun = sunLayout(a.sun, h, spanH);
  const ticks = [];
  const d = new Date(t0); d.setMinutes(0, 0, 0);
  while (d.getTime() < t0 || d.getHours() % 3 !== 0) d.setTime(d.getTime() + HOUR_MS);
  for (let t = d.getTime(); t <= t1; t += 3 * HOUR_MS) ticks.push({ h: h(t), ms: t });
  const nowH = h(now);
  const viewH = hist.length ? Math.max(0, nowH - (o.viewPastHours || VIEW_PAST_HOURS)) : 0;
  return { t0, t1, now, spanH, nowH, bands, steps, mid, low, high, sun, ticks, hist: hist.length, viewH,
    floor: toNumber(a.floor_soc), reserve: toNumber(a.reserve_soc), nowLevel: levelAtHour(mid, nowH) };
}
/** The sun and house forecast the plan used (`sun` on the timeline sensor: average kW per step_min from `start`), as points in
 *  hours from the left edge: {mid, low, high, house, max}. Null when the app publishes none (an older app) or the sun and the house are all 0. */
function sunLayout(sun, hourOf, spanH) {
  if (!sun || v2Ms(sun.start) === null || !Array.isArray(sun.mid)) return null;
  const step = (toNumber(sun.step_min) > 0 ? toNumber(sun.step_min) : 30) * 60000;
  const t0 = v2Ms(sun.start);
  // each value is the average over its step: a point at the step's start and another at its end draws it as a level
  const pts = (arr) => {
    const out = [];
    (Array.isArray(arr) ? arr : []).forEach((v, i) => {
      const kw = toNumber(v);
      if (kw === null) return;
      const a = hourOf(t0 + i * step), b = hourOf(t0 + (i + 1) * step);
      if (b <= 0 || a >= spanH) return;
      out.push({ h: Math.max(0, a), kw }, { h: Math.min(spanH, b), kw });
    });
    return out;
  };
  const r = { mid: pts(sun.mid), low: pts(sun.low), high: pts(sun.high), house: pts(sun.house) };
  const top = Math.max(0, ...r.mid.concat(r.high, r.house).map((p) => p.kw));
  // no sun in the window (an evening plan that ends before sunrise) still draws the house, so the strip does not vanish
  if (!r.mid.length || !(Math.max(0, ...r.mid.concat(r.high, r.house).map((p) => p.kw)) > 0)) return null;
  return Object.assign(r, { max: Math.max(1, Math.ceil(top * 2) / 2), peak: Math.max(...r.mid.map((p) => p.kw)) });
}
/** Battery level at `hour` along a list of {h, level} points (linear), or null outside it. */
function levelAtHour(points, hour) {
  if (!points || points.length === 0) return null;
  if (hour < points[0].h - 1e-9 || hour > points[points.length - 1].h + 1e-9) return null;
  for (let i = 1; i < points.length; i++) {
    if (hour <= points[i].h + 1e-9) {
      const p = points[i - 1], q = points[i];
      return q.h === p.h ? q.level : p.level + (q.level - p.level) * ((hour - p.h) / (q.h - p.h));
    }
  }
  return points[points.length - 1].level;
}
const TIMELINE_HINT = "Move over the chart, or tap it, to read the mode, battery level, price and sun at that time.";
/** What the timeline says at `hour` (hours from the left edge), for the hover text: a heading with the clock time and
 *  the lines for the mode band, battery (with its likely range), import price, sun and house under it. Pure; `clock(h)` -> "HH:MM". */
function timelineHover(L, hour, clock) {
  const lines = [];
  const band = L.bands.find((b) => hour >= b.a && hour < b.b) || L.bands.find((b) => hour >= b.a && hour <= b.b);
  if (band) {
    const when = band.state === "past" ? "happened" : band.state === "now" ? "now" : "expected";
    lines.push(`${v2Mode(band.mode).name} (${when}, ${clock(band.a)} to ${clock(band.b)})`);
    if (band.until) lines.push(`Until: ${band.until}`);
    if (band.reason) lines.push(band.reason);
    if (band.levelStart !== null && band.levelEnd !== null) lines.push(`Battery over the band: ${v2Pct(band.levelStart)} to ${v2Pct(band.levelEnd)}`);
  }
  const lvl = levelAtHour(L.mid, hour);
  if (lvl !== null) {
    const lo = hour >= L.nowH ? levelAtHour(L.low, hour) : null, hi = hour >= L.nowH ? levelAtHour(L.high, hour) : null;
    lines.push(`Battery: ${v2Pct(lvl)}${lo !== null && hi !== null ? ` (likely ${v2Pct(lo)} to ${v2Pct(hi)})` : ""}`);
  }
  const step = L.steps.find((s) => hour >= s.a && hour < s.b);
  if (step) {
    const tag = step.event ? ", grid event" : step.free ? ", free power" : step.slot ? `, smart slot${step.slotProb !== null ? ` (${Math.round(step.slotProb * 100)}% likely to come)` : " that may not come"}` : step.estimated ? ", estimated" : "";
    lines.push(`Import price: ${v2P(step.importP, 2)}${tag}`);
  }
  if (L.sun) {
    const at = (pts) => { const p = pts.find((q) => q.h >= hour); return p ? p.kw : null; };
    const kw = (n) => `${Math.round(n * 100) / 100} kW`;
    const sun = at(L.sun.mid), house = at(L.sun.house), lo = at(L.sun.low), hi = at(L.sun.high);
    if (sun !== null) lines.push(`Sun forecast: ${kw(sun)}${lo !== null && hi !== null ? ` (${kw(lo)} to ${kw(hi)})` : ""}`);
    if (house !== null) lines.push(`House use expected: ${kw(house)}`);
  }
  return { title: clock(hour), lines };
}
/** The text on a mode band, by how wide it is on screen. */
function bandLabel(band, widthPx) {
  const name = v2Mode(band.mode).name;
  return widthPx > 120 ? name + (band.until ? ` · ${band.until}` : "") : widthPx > 50 ? name : "";
}

if (typeof window !== 'undefined') window.PL = { toNumber, v2Mode, v2Ms, v2P, v2Pct, timelineLayout, levelAtHour, timelineHover, bandLabel, V2_MODES, HOUR_MS };
