"use strict";
// The simulator's page: pick code and settings per variant, run them over days, read the scoreboard, scroll the plan.

const $ = (id) => document.getElementById(id);
const state = { info: null, variants: [], days: new Set(), results: {}, sel: { a: null, b: null, i: 0, pph: 36 }, joined: {}, cat: {} };
const el = (tag, attrs, ...kids) => {
  const e = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  });
  kids.flat().forEach((c) => e.append(c instanceof Node ? c : document.createTextNode(String(c))));
  return e;
};
const svg = (tag, attrs, parent, text) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
};

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "X-PE-Sim": "1", "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status}`);
  return data;
}

// ---- variants ----------------------------------------------------------------------------------------------------------
const blank = (n) => ({ name: n, code: "", config: "", fresh: false, settings: {}, consts: {} });
function uniqueName(base) {
  let n = base, i = 2;
  while (state.variants.some((v) => v.name === n)) n = `${base} ${i++}`;
  return n;
}

async function catalogueFor(code) {
  const key = code || "";
  if (!state.cat[key]) state.cat[key] = api(`/api/catalogue?code=${encodeURIComponent(key)}`);
  return state.cat[key];
}

function renderVariants() {
  const host = $("variants");
  host.replaceChildren(...state.variants.map((v, idx) => {
    const refs = state.info.refs.map((r) => el("option", { value: r.name, selected: v.code === r.name }, `${r.name}  (${r.date})`));
    const code = el("select", { onchange: (e) => { v.code = e.target.value; v.open = false; renderVariants(); } },
      el("option", { value: "", selected: !v.code }, `Working tree — ${state.info.working}`), refs);
    const config = el("select", { onchange: (e) => { v.config = e.target.value; } }, el("option", { value: "" }, `Newest (${state.info.seed.config || "none"})`),
      state.info.configs.map((c) => el("option", { value: c, selected: v.config === c }, c)));
    const chips = Object.entries(v.settings).map(([k, x]) => el("span", { class: "chip" }, `${k} = ${x}`))
      .concat(Object.entries(v.consts).map(([k, x]) => el("span", { class: "chip" }, `${k} = ${x}`)));
    const consts = el("input", { type: "text", placeholder: "constants, e.g. engine_v2.value.NAME=1", value: Object.entries(v.consts).map(([k, x]) => `${k}=${x}`).join("; "),
      onchange: (e) => { v.consts = parseConsts(e.target.value); renderVariants(); } });
    const box = el("div", { class: "variant" },
      el("div", { class: "top" },
        el("input", { type: "text", value: v.name, onchange: (e) => { v.name = e.target.value.trim() || v.name; } }),
        el("button", { title: "Copy this variant", onclick: () => { state.variants.splice(idx + 1, 0, Object.assign(JSON.parse(JSON.stringify(v)), { name: uniqueName(`${v.name} copy`), open: false })); renderVariants(); } }, "Copy"),
        idx ? el("button", { title: "Remove", onclick: () => { state.variants.splice(idx, 1); renderVariants(); } }, "✕") : ""),
      el("label", {}, "Code", code), el("label", {}, "Config", config),
      el("label", {}, "State", el("span", {}, el("input", { type: "checkbox", checked: v.fresh, onchange: (e) => { v.fresh = e.target.checked; } }), " start without learned state")),
      el("label", {}, "Constants", consts),
      el("div", { class: "overrides" }, chips.length ? chips : "No overrides: the settings of the archived config.",
        el("div", {}, el("button", { onclick: async () => { v.open = !v.open; renderVariants(); } }, v.open ? "Hide settings" : "Engine settings…"))));
    if (v.open) box.append(settingsPanel(v));
    return box;
  }));
}

function parseConsts(text) {
  const out = {};
  text.split(";").map((s) => s.trim()).filter(Boolean).forEach((s) => {
    const i = s.indexOf("=");
    if (i > 0) { const raw = s.slice(i + 1).trim(); out[s.slice(0, i).trim()] = raw !== "" && !isNaN(Number(raw)) ? Number(raw) : raw; }
  });
  return out;
}

function settingsPanel(v) {
  const panel = el("div", { class: "settings" }, el("div", { class: "muted small", style: "padding:8px" }, "Loading the settings of this code…"));
  catalogueFor(v.code).then((cat) => {
    const rows = el("div", {});
    const filter = el("input", { type: "search", placeholder: "Filter settings", oninput: (e) => {
      const q = e.target.value.toLowerCase();
      rows.childNodes.forEach((r) => { r.style.display = r.dataset.text.includes(q) ? "" : "none"; });
    } });
    Object.entries(cat.settings).forEach(([key, s]) => {
      const set = key in v.settings;
      const row = el("div", { class: `srow${set ? " set" : ""}` });
      row.dataset.text = `${key} ${s.label} ${s.help}`.toLowerCase();
      const change = (val) => { if (val === "" || val === null || val === undefined) delete v.settings[key]; else v.settings[key] = val; row.classList.toggle("set", key in v.settings); };
      let input;
      if (s.kind === "bool") {
        input = el("select", { onchange: (e) => change(e.target.value === "" ? "" : e.target.value === "on") }, el("option", { value: "" }, `default (${s.default ? "on" : "off"})`),
          el("option", { value: "on", selected: set && v.settings[key] === true }, "on"), el("option", { value: "off", selected: set && v.settings[key] === false }, "off"));
      } else if (s.kind === "choice") {
        input = el("select", { onchange: (e) => change(e.target.value) }, el("option", { value: "" }, `default (${s.default})`),
          (s.min || []).map((o) => el("option", { value: o, selected: v.settings[key] === o }, o)));
      } else {
        input = el("input", { type: "number", step: s.kind === "int" ? 1 : "any", min: s.min, max: s.max, placeholder: `${s.default}${s.unit ? " " + s.unit : ""}`,
          value: set ? v.settings[key] : "", onchange: (e) => change(e.target.value === "" ? "" : Number(e.target.value)) });
      }
      row.append(el("div", {}, el("div", {}, `${s.label} `, el("span", { class: "muted small" }, key)), el("div", { class: "help" }, s.help)), input);
      rows.append(row);
    });
    panel.replaceChildren(filter, rows);
  }).catch((err) => panel.replaceChildren(el("div", { class: "err small", style: "padding:8px" }, `Could not read this code's settings: ${err.message}`)));
  return panel;
}

// ---- days ------------------------------------------------------------------------------------------------------------
function renderDays() {
  $("days").replaceChildren(...state.info.days.map((d, i) => {
    const last = i === state.info.days.length - 1;
    const box = el("input", { type: "checkbox", checked: state.days.has(d), onchange: (e) => { if (e.target.checked) state.days.add(d); else state.days.delete(d); } });
    return el("label", { class: last ? "partial" : "", title: last ? "The newest day may be partial" : "" }, box, ` ${d.slice(5)}`);
  }));
}

// ---- running ---------------------------------------------------------------------------------------------------------
async function run() {
  const days = [...state.days].sort();
  if (!days.length) { $("progress").textContent = "Choose at least one day."; return; }
  const names = new Set();
  state.variants.forEach((v) => { if (names.has(v.name)) v.name = uniqueName(v.name); names.add(v.name); });
  const payload = state.variants.map(({ name, code, config, fresh, settings, consts }) => ({ name, code, config, fresh, settings, consts }));
  $("run").disabled = true;
  try {
    let b = await api("/api/run", { variants: payload, days, force: $("force").checked });
    while (!b.done) {
      showProgress(b);
      await new Promise((r) => setTimeout(r, 1500));
      b = await api(`/api/batch/${b.id}`);
    }
    showProgress(b);
    await loadResults(b);
  } catch (err) {
    $("progress").replaceChildren(el("span", { class: "err" }, err.message));
  } finally { $("run").disabled = false; }
}

function showProgress(b) {
  const all = b.plan.flatMap((r) => r.jobs);
  const done = all.filter((j) => !["queued"].includes(j.status)).length;
  $("progress").replaceChildren(`${done} of ${all.length} runs finished${b.done ? "" : " (each takes about 2 minutes, several at a time)"}. `,
    ...all.filter((j) => j.status !== "ok" && j.status !== "queued").map((j) => el("div", { class: "err" }, `${j.day}: ${j.status} ${j.reason || ""}`)),
    b.done && b.report ? el("div", { class: "muted" }, `Report for Claude: ${b.report}/report.md and report.json`) : "");
}

async function loadResults(b) {
  state.results = {};
  state.joined = {};
  for (const row of b.plan) {
    const list = [];
    for (const j of row.jobs) {
      if (j.status === "ok") list.push(await api(`/api/result/${j.key}`));
      else list.push({ day: j.day, status: j.status, reason: j.reason });
    }
    state.results[row.variant.name] = list;
    state.joined[row.variant.name] = AD.joinDays(list);
  }
  const names = Object.keys(state.results);
  state.sel.a = names[0];
  state.sel.b = names.length > 1 ? names[1] : null;
  const times = AD.planTimes(state.joined[names[0]]);
  state.sel.i = Math.max(0, times.length - 1);
  renderScore();
  renderPlan(true);
}

// ---- the scoreboard ----------------------------------------------------------------------------------------------------
const f2 = (x, signed) => (x === null || x === undefined ? "-" : (signed && x > 0 ? "+" : "") + x.toFixed(2));
function totals(list) {
  const ok = list.filter((r) => r.status === "ok").map((r) => r.score);
  const sum = (k) => ok.reduce((a, s) => a + (s[k] || 0), 0);
  return { cost: sum("cost"), save_vs_selfuse: sum("save_vs_selfuse"), gap_to_bound: sum("gap_to_bound"), adj_cost: sum("adj_cost"), mode_changes: sum("mode_changes"),
    flip_flops: sum("flip_flops"), peak_charge_kwh: sum("peak_charge_kwh"), min_soc: ok.length ? Math.min(...ok.map((s) => s.min_soc)) : null, commands: sum("commands"),
    calc_s: sum("calc_s"), wall_s: sum("wall_s") };
}
function scoreRow(label, s, cls) {
  return el("tr", { class: cls || "" }, el("td", {}, label), el("td", {}, f2(s.cost)), el("td", {}, f2(s.save_vs_selfuse, true)), el("td", {}, f2(s.gap_to_bound)), el("td", {}, f2(s.adj_cost)),
    el("td", {}, s.mode_changes), el("td", {}, s.flip_flops), el("td", {}, f2(s.peak_charge_kwh)), el("td", {}, s.min_soc === null ? "-" : Math.round(s.min_soc)), el("td", {}, s.commands),
    el("td", {}, Math.round(s.calc_s || 0)), el("td", {}, Math.round(s.wall_s || 0)));
}
function renderScore() {
  const host = $("score");
  host.replaceChildren();
  const names = Object.keys(state.results);
  const base = names[0];
  names.forEach((name) => {
    const list = state.results[name];
    const v = state.variants.find((x) => x.name === name) || {};
    const label = (list.find((r) => r.code) || {}).code || "";
    const head = el("tr", {}, ...["Day", "Cost £", "vs self-use", "to bound", "Adj £", "Modes", "Flips", "Peak kWh", "Min %", "Cmds", "Calc s", "Wall s"].map((h) => el("th", {}, h)));
    const table = el("table", {}, el("thead", {}, head), el("tbody", {}, list.map((r) => r.status === "ok" ? scoreRow(r.day, r.score)
      : el("tr", {}, el("td", {}, r.day), el("td", { colspan: 11, class: "err" }, `not run: ${r.status} ${r.reason || ""}`))),
      list.filter((r) => r.status === "ok").length > 1 ? scoreRow("Total", totals(list), "total") : ""));
    host.append(el("h2", {}, name, " ", el("span", { class: "muted small" }, label, Object.keys(v.settings || {}).length ? ` · ${Object.entries(v.settings).map(([k, x]) => `${k}=${x}`).join(", ")}` : "")),
      el("div", { class: "tablewrap" }, table));
    if (name !== base) {
      const b = Object.fromEntries(state.results[base].filter((r) => r.status === "ok").map((r) => [r.day, r.score]));
      const days = list.filter((r) => r.status === "ok" && b[r.day]);
      if (days.length) {
        const d = days.reduce((a, r) => a + r.score.adj_cost - b[r.day].adj_cost, 0);
        const flips = days.reduce((a, r) => a + r.score.flip_flops - b[r.day].flip_flops, 0);
        const modes = days.reduce((a, r) => a + r.score.mode_changes - b[r.day].mode_changes, 0);
        host.append(el("div", { class: `vs ${d < -0.005 ? "good" : d > 0.005 ? "bad" : ""}` }, `vs ${base} over ${days.length} day(s): adjusted cost ${f2(d, true)} £ (negative is cheaper), mode changes ${modes >= 0 ? "+" : ""}${modes}, flip-flops ${flips >= 0 ? "+" : ""}${flips}`));
      }
    }
  });
}

// ---- the plan chart ----------------------------------------------------------------------------------------------------
function renderPlan(fresh) {
  const host = $("plan");
  const names = Object.keys(state.results);
  if (!names.length) { host.replaceChildren(); return; }
  const A = state.joined[state.sel.a], times = AD.planTimes(A);
  const sel = (id, value, none) => el("select", { onchange: (e) => { state.sel[id] = e.target.value || null; state.sel.i = Math.min(state.sel.i, Math.max(0, AD.planTimes(state.joined[state.sel.a]).length - 1)); renderPlan(false); } },
    none ? el("option", { value: "" }, "none") : "", names.map((n) => el("option", { value: n, selected: n === value }, n)));
  const clock = (ms) => new Intl.DateTimeFormat("en-GB", { timeZone: A.tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(ms);
  const idx = Math.min(state.sel.i, Math.max(0, times.length - 1));
  const nowMs = times.length ? times[idx].ms : A.dataEnd;
  const slider = el("input", { type: "range", min: 0, max: Math.max(0, times.length - 1), value: idx, oninput: (e) => { state.sel.i = Number(e.target.value); renderPlan(false); } });
  const step = (d) => () => { state.sel.i = Math.max(0, Math.min(times.length - 1, idx + d)); renderPlan(false); };
  const zoom = el("select", { onchange: (e) => { state.sel.pph = Number(e.target.value); renderPlan(false); } },
    [18, 36, 72].map((n) => el("option", { value: n, selected: n === state.sel.pph }, `${n} px/h`)));
  const old = host.querySelector(".scroller");
  const keep = old && !fresh ? old.scrollLeft : null;
  const chart = el("div", { class: "chart" });
  const info = el("div", { class: "muted small" }, times.length ? `Plan ${idx + 1} of ${times.length}, made ${clock(nowMs)}: ${times[idx].because}` : "No plans were recorded.");
  host.replaceChildren(el("h2", {}, "The plan, as the card draws it"),
    el("div", { class: "planbox" }, el("div", { class: "controls" }, "Variant", sel("a", state.sel.a), "Compare with", sel("b", state.sel.b, true), zoom,
      el("button", { onclick: step(-1) }, "◀ earlier plan"), el("button", { onclick: step(1) }, "later plan ▶"), slider), info, chart,
    el("div", { class: "legend" }, ...Object.entries(PL.V2_MODES).filter(([k]) => k !== "none").map(([k, m]) => el("span", {}, el("i", { style: `background:var(${m.colour})` }), m.name)),
      el("span", {}, el("i", { style: "background:var(--battery)" }), "plan level"), el("span", {}, el("i", { style: "background:var(--actual)" }), "level that happened"),
      el("span", {}, el("i", { style: "background:var(--m-event);height:2px" }), "compare variant"))));
  drawChart(chart, A, state.joined[state.sel.b] || null, nowMs, clock, keep);
}

function drawChart(host, A, B, nowMs, clock, keepScroll) {
  const inp = AD.layoutInput(A, nowMs);
  const L = PL.timelineLayout(inp.tl, inp.opts);
  const pph = state.sel.pph, W = Math.max(600, Math.ceil(L.spanH * pph));
  const H = { a: 230, b: 80, c: 70, axis: 24, gap: 10 };
  const total = H.a + H.b + H.c + H.axis + H.gap * 2;
  const x = (h) => h * pph;
  const hOf = (ms) => (ms - L.t0) / PL.HOUR_MS;
  const yA = (pct) => H.a * (1 - Math.max(0, Math.min(100, pct)) / 100);
  const axis = svg("svg", { width: 38, height: total }, null);
  [0, 25, 50, 75, 100].forEach((p) => svg("text", { x: 34, y: Math.min(H.a - 2, Math.max(10, yA(p) + 4)), "text-anchor": "end" }, axis, `${p}%`));
  const scroller = el("div", { class: "scroller" });
  const s = svg("svg", { width: W, height: total }, null);
  scroller.append(s);
  host.replaceChildren(axis, scroller);
  const oB = H.a + H.gap, oC = oB + H.b + H.gap, oAx = oC + H.c;
  const colour = (m) => `var(${PL.v2Mode(m).colour})`;
  // mode bands, drawn lighter where they are still to come
  L.bands.forEach((b) => {
    svg("rect", { x: x(b.a), y: 0, width: Math.max(1, x(b.b) - x(b.a)), height: H.a, fill: colour(b.mode), "fill-opacity": b.ran ? 0.5 : 0.24 }, s);
    const label = PL.bandLabel(b, x(b.b) - x(b.a));
    if (label) svg("text", { x: x(b.a) + 4, y: 12 }, s, label);
  });
  ticksAndGrid(s, L, x, clock, A.tz, [[0, H.a], [oB, H.b], [oC, H.c]], oAx);
  [L.floor, L.reserve].forEach((lv, i) => { if (lv !== null) svg("line", { x1: 0, x2: W, y1: yA(lv), y2: yA(lv), stroke: "var(--muted)", "stroke-dasharray": i ? "2 4" : "6 3", "stroke-width": 0.7 }, s); });
  if (L.low.length && L.high.length) {
    const pts = L.low.map((p) => `${x(p.h)},${yA(p.level)}`).concat(L.high.slice().reverse().map((p) => `${x(p.h)},${yA(p.level)}`)).join(" ");
    svg("polygon", { points: pts, fill: "var(--battery)", "fill-opacity": 0.13 }, s);
  }
  const past = L.mid.filter((p) => p.h <= L.nowH + 1e-9), fut = L.mid.filter((p) => p.h >= L.nowH - 1e-9);
  const line = (pts, attrs) => pts.length > 1 && svg("polyline", Object.assign({ points: pts.map((p) => `${x(p.h)},${yA(p.level)}`).join(" "), fill: "none" }, attrs), s);
  line(past, { stroke: "var(--battery)", "stroke-width": 2 });
  line(fut, { stroke: "var(--battery)", "stroke-width": 2, "stroke-dasharray": "5 4" });
  const actual = (J) => AD.actualLevels(J).map((p) => ({ h: hOf(p.ms), level: p.level })).filter((p) => p.h >= 0 && p.h <= L.spanH);
  line(actual(A), { stroke: "var(--actual)", "stroke-width": 1.6 });
  if (B) {
    line(actual(B), { stroke: "var(--m-event)", "stroke-width": 1.6, "stroke-dasharray": "2 3" });
    B.rows.forEach((r) => { const a = hOf(r.a), b = hOf(r.b); if (b > 0 && a < L.spanH) svg("rect", { x: x(a), y: H.a - 7, width: Math.max(1, x(b) - x(a)), height: 7, fill: colour(r.mode) }, s); });
  }
  // prices
  const pmax = Math.max(10, ...L.steps.map((p) => Math.max(p.importP || 0, p.exportP || 0))) * 1.1;
  const yB = (p) => oB + H.b * (1 - Math.max(0, p) / pmax);
  L.steps.forEach((p) => { if (p.event || p.free) svg("rect", { x: x(p.a), y: oB, width: x(p.b) - x(p.a), height: H.b, fill: p.event ? "var(--m-event)" : "var(--m-free)", "fill-opacity": 0.2 }, s); });
  const stepLine = (key, stroke) => svg("polyline", { points: L.steps.slice().sort((p, q) => p.a - q.a).flatMap((p) => [`${x(p.a)},${yB(p[key])}`, `${x(p.b)},${yB(p[key])}`]).join(" "), fill: "none", stroke, "stroke-width": 1.5 }, s);
  stepLine("importP", "var(--import)"); stepLine("exportP", "var(--export)");
  svg("text", { x: 4, y: oB + 10 }, s, "import / export price (p)");
  svg("text", { x: 4, y: oB + H.b - 3 }, s, `0 – ${Math.round(pmax)}p`);
  // sun and house
  if (L.sun) {
    const yC = (kw) => oC + H.c * (1 - Math.max(0, kw) / L.sun.max);
    const area = L.sun.mid.map((p) => `${x(p.h)},${yC(p.kw)}`);
    if (area.length) svg("polygon", { points: [`${x(L.sun.mid[0].h)},${oC + H.c}`, ...area, `${x(L.sun.mid[L.sun.mid.length - 1].h)},${oC + H.c}`].join(" "), fill: "var(--sun)", "fill-opacity": 0.35 }, s);
    svg("polyline", { points: L.sun.house.map((p) => `${x(p.h)},${yC(p.kw)}`).join(" "), fill: "none", stroke: "var(--house)", "stroke-width": 1.2 }, s);
    svg("text", { x: 4, y: oC + 10 }, s, `sun (shaded) and house (line), kW, to ${L.sun.max}`);
  }
  // now
  svg("line", { x1: x(L.nowH), x2: x(L.nowH), y1: 0, y2: oAx, stroke: "var(--text)", "stroke-width": 1.2 }, s);
  svg("text", { x: x(L.nowH) + 3, y: H.a - 12, style: "fill:var(--text);font-weight:600" }, s, "plan made");
  // hover
  const cover = svg("rect", { x: 0, y: 0, width: W, height: oAx, fill: "transparent" }, s);
  const tip = $("tip");
  const actualAt = (J, ms) => { const r = J.rows.find((q) => ms >= q.a && ms < q.b); return r ? r : null; };
  cover.addEventListener("mousemove", (e) => {
    const box = s.getBoundingClientRect();
    const hour = (e.clientX - box.left) / pph, ms = L.t0 + hour * PL.HOUR_MS;
    const hv = PL.timelineHover(L, hour, (hh) => clock(L.t0 + hh * PL.HOUR_MS));
    const lines = hv.lines.slice();
    const ra = actualAt(A, ms);
    if (ra) lines.push(`Actually: ${PL.v2Mode(ra.mode).name}, battery ${PL.v2Pct(ra.level)}`);
    const rb = B && actualAt(B, ms);
    if (rb) lines.push(`${state.sel.b}: ${PL.v2Mode(rb.mode).name}, battery ${PL.v2Pct(rb.level)}`);
    tip.replaceChildren(el("b", {}, hv.title), ...lines.map((l) => el("div", {}, l)));
    tip.style.display = "block";
    tip.style.left = `${Math.min(window.innerWidth - 350, e.clientX + 14)}px`;
    tip.style.top = `${Math.min(window.innerHeight - 140, e.clientY + 14)}px`;
  });
  cover.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  scroller.scrollLeft = keepScroll !== null && keepScroll !== undefined ? keepScroll : Math.max(0, x(L.nowH) - 160);
}

function ticksAndGrid(s, L, x, clock, tz, panels, yAxis) {
  const fmt = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false });
  const day = new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" });
  L.ticks.forEach((t) => {
    const label = fmt.format(t.ms);
    panels.forEach(([y, h]) => svg("line", { x1: x(t.h), x2: x(t.h), y1: y, y2: y + h, stroke: "var(--line)", "stroke-width": label === "00:00" ? 1.4 : 0.6 }, s));
    svg("text", { x: x(t.h) + 2, y: yAxis + 12 }, s, label);
    if (label === "00:00") svg("text", { x: x(t.h) + 2, y: yAxis + 23, style: "fill:var(--text);font-weight:600" }, s, day.format(t.ms));
  });
}

// ---- start -----------------------------------------------------------------------------------------------------------
async function start() {
  try {
    state.info = await api("/api/state");
  } catch (err) {
    document.body.prepend(el("p", { class: "err" }, `Could not reach the simulator's server: ${err.message}`));
    return;
  }
  $("where").textContent = `${state.info.days.length} days with forecasts · ${state.info.jobs} runs at a time`;
  state.variants = state.info.variants.length ? state.info.variants : [blank("base")];
  state.info.days.slice(0, -1).forEach((d) => state.days.add(d));       // the partial newest day is opt-in
  renderVariants();
  renderDays();
  $("add").onclick = () => { state.variants.push(blank(uniqueName("variant"))); renderVariants(); };
  $("save").onclick = async () => {
    await api("/api/variants", { variants: state.variants.map(({ name, code, config, fresh, settings, consts }) => ({ name, code, config, fresh, settings, consts })) });
    $("progress").textContent = "Variants saved.";
  };
  $("run").onclick = run;
}
start();
