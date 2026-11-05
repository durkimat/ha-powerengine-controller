const test = require("node:test");
const assert = require("node:assert/strict");
const m = require("../ha-powerengine-card.js");

// What the app publishes (wizard and site_options), trimmed to what these tests need.
const PARTS = [
  { part: "inverter", title: "Inverter and battery", why: "w", required: true, roles: ["battery_soc", "grid_power"] },
  { part: "tariff", title: "Electricity tariff", why: "w", required: true, roles: ["import_rate_now"] },
  { part: "ev_charger", title: "Car charger", why: "w", required: false, roles: ["ev_plug_status"] },
  { part: "forecast", title: "Solar forecast", why: "w", required: false, roles: [] },
  { part: "events", title: "Grid events", why: "w", required: false, roles: ["event_state"] },
];
const OPTIONS = {
  inverter: [{ id: "solis", name: "Solis", status: "verified", firmware_variants: ["420044"] }, { id: "other", name: "Other", status: "draft" }],
  tariff: [{ id: "edf", name: "EDF", status: "verified" }, { id: "auto", name: "Automatic", status: "" }],
  ev_charger: [{ id: "zappi", name: "Zappi", status: "verified" }, { id: "none", name: "None", status: "" }],
  forecast: [{ id: "solcast", name: "Solcast", status: "verified" }],
  events: [{ id: "axle", name: "Axle", status: "community" }],
};
const SITE = { inverter: "solis", inverter_firmware: "420044", ev_charger: "zappi", car: null, tariff: "auto", forecast: "solcast", events: "axle" };
const INFO = { parts: PARTS, options: OPTIONS, site: SITE, setup: "configured", detected: null };
const ROLES = [
  { key: "battery_soc", label: "Battery state of charge", kind: "percent", required: "yes", suggest: ["^sensor\\.solis_battery_soc$"] },
  { key: "grid_power", label: "Grid power", kind: "power", required: "yes", signed: true },
  { key: "import_rate_now", label: "Import rate now", kind: "rate", required: "yes" },
  { key: "ev_plug_status", label: "Car plug status", kind: "text", required: "yes" },
  { key: "event_state", label: "Event state", kind: "text", required: "yes" },
];
const SAVED = {
  schema_version: 1,
  inputs: { battery_soc: { entity: "sensor.solis_battery_soc" }, grid_power: { entity: "sensor.solis_grid", invert: true }, import_rate_now: { entity: "sensor.edf_rate" },
    ev_plug_status: { entity: "sensor.zappi_plug" }, event_state: { entity: "sensor.axle_event" } },
  solar_plants: [{ id: "main", name: "Main", power: { entity: "sensor.pv" }, energy_today: { entity: "sensor.pv_today" }, forecast: "solcast_site", enabled: true },
    { id: "garage", name: "Garage roof", power: { entity: "sensor.g_pv" }, energy_today: { entity: "sensor.g_pv_today" }, forecast: "none", enabled: true }],
  features: { smart_charge_optimisation: true, learn_car: true, learn_car_min: true, axle: true, arbitrage: false },
  operation: { mode: "active" }, safety: { battery_reserve_soc: 12 }, system: { poll_s: 30 },
  site: SITE,
};
const base = (extra) => m.initialDraft(Object.assign({}, SAVED, extra), [], [], {}).draft;

test("systemKinds lists the app's parts and the card's own two, other devices only when supported", () => {
  const on = m.systemKinds(INFO, true).map((k) => k.kind);
  assert.deepEqual(on, ["inverter", "plant", "device", "ev_charger", "tariff", "forecast", "events"]);
  assert.deepEqual(m.systemKinds(INFO, false).map((k) => k.kind), ["inverter", "plant", "ev_charger", "tariff", "forecast", "events"]);
  const inv = m.systemKinds(INFO, true)[0];
  assert.equal(inv.required, true);
  assert.equal(inv.single, true);
  assert.equal(m.systemKinds(INFO, true)[1].single, false);
});

test("systemItems: a row per configured part, extra plant and device; the main plant has none; none is left out", () => {
  const cfg = Object.assign({}, SAVED, { site: Object.assign({}, SITE, { forecast: "none" }), devices: [{ id: "gar", adapter: "solis", name: "Garage battery", inputs: {} }] });
  const items = m.systemItems(cfg, INFO, true);
  assert.deepEqual(items.map((i) => i.target), ["inverter", "plant:garage", "device:gar", "ev_charger", "tariff", "events"]);
  assert.equal(items[0].name, "Solis");
  assert.equal(items[0].status, "verified");
  assert.equal(items.find((i) => i.target === "tariff").name, "Automatic");
  assert.equal(items.find((i) => i.target === "plant:garage").status, "read only");
  assert.equal(m.systemItems(cfg, INFO, false).some((i) => i.kind === "device"), false);
  assert.equal(m.systemItems({ site: {}, solar_plants: [] }, INFO, true).length, 0);
});

test("systemMissingParts names the required parts that are not set up", () => {
  assert.deepEqual(m.systemMissingParts(m.systemItems({ site: { ev_charger: "zappi" }, solar_plants: [] }, INFO, true), INFO).map((p) => p.part), ["inverter", "tariff"]);
  assert.deepEqual(m.systemMissingParts(m.systemItems(SAVED, INFO, true), INFO), []);
});

test("ops: a later change to a target replaces the earlier one; removing a new item cancels it; removing a saved one can be undone", () => {
  const a = { target: "ev_charger", op: "set", kind: "ev_charger", part: "ev_charger", option: "zappi" };
  const b = Object.assign({}, a, { option: "other" });
  let ops = m.opsSet(m.opsSet([], a), b);
  assert.equal(ops.length, 1);
  assert.equal(ops[0].option, "other");
  assert.equal(m.opsTag(ops, "ev_charger", true), "changed");
  assert.equal(m.opsTag(ops, "ev_charger", false), "new");
  assert.deepEqual(m.opsRemove(ops, "ev_charger", false), []);
  const rm = m.opsRemove(ops, "ev_charger", true, { kind: "ev_charger", part: "ev_charger" });
  assert.equal(rm.length, 1);
  assert.equal(m.opsTag(rm, "ev_charger", true), "removed");
  assert.deepEqual(m.opsUndoRemove(rm, "ev_charger"), ops);              // the earlier change comes back
  assert.deepEqual(m.opsUndoRemove(m.opsRemove([], "tariff", true, {}), "tariff"), []);
  assert.equal(m.opsTag([], "tariff", true), null);
});

test("applyOps: replacing the inverter sets the option, firmware, inputs and the main plant", () => {
  const op = { target: "inverter", op: "set", kind: "inverter", part: "inverter", option: "other", firmware: null,
    inputs: { battery_soc: { entity: "sensor.new_soc" }, grid_power: null }, plant: { power: { entity: "sensor.n_pv" }, energy_today: { entity: "sensor.n_pv_today" } } };
  const d = m.applyOps(base(), [op], INFO);
  assert.equal(d.site.inverter, "other");
  assert.equal(d.site.inverter_firmware, null);
  assert.deepEqual(d.inputs.battery_soc, { entity: "sensor.new_soc" });
  assert.equal(d.inputs.grid_power, undefined);                            // a cleared input is removed
  assert.equal(d.inputs.import_rate_now.entity, "sensor.edf_rate");        // other parts' inputs are untouched
  assert.equal(d.solar_plants[0].power.entity, "sensor.n_pv");
  assert.equal(d.solar_plants[1].id, "garage");
  assert.equal(base().site.inverter, "solis");                              // the input draft is not changed
});

test("applyOps: removing the car charger leaves it out, clears its inputs and switches its features off, never on", () => {
  const op = { target: "ev_charger", op: "remove", kind: "ev_charger", part: "ev_charger", clear: ["ev_plug_status"] };
  const d = m.applyOps(base(), [op], INFO);
  assert.equal(d.site.ev_charger, "none");
  assert.equal(d.inputs.ev_plug_status, undefined);
  assert.equal(d.features.smart_charge_optimisation, false);
  assert.equal(d.features.learn_car, false);
  assert.equal(d.features.axle, true);
  assert.equal(d.features.arbitrage, false);
  assert.deepEqual(d.features, Object.assign({}, base().features, { smart_charge_optimisation: false, learn_car: false, learn_car_min: false }));   // nothing else moves
  assert.deepEqual(m.featuresLeftOut({ axle: true, axle_plus_export: false, learn_car: true }, "events"), ["axle"]);
  assert.deepEqual(m.featuresLeftOut({ learn_car: true }, "tariff"), []);
  const add = m.applyOps(base(), [{ target: "events", op: "set", kind: "events", part: "events", option: "axle", inputs: {} }], INFO);
  assert.deepEqual(add.features, base().features);                           // adding a part never switches a feature on or off
  const keep = m.applyOps(base(), [Object.assign({}, op, { clear: [] })], INFO);
  assert.equal(keep.inputs.ev_plug_status.entity, "sensor.zappi_plug");
});

test("applyOps: the forecast and the main plant stay in step; adding a forecast gives the main plant one", () => {
  const rm = m.applyOps(base(), [{ target: "forecast", op: "remove", kind: "forecast", part: "forecast", clear: [] }], INFO);
  assert.equal(rm.site.forecast, "none");
  assert.equal(rm.solar_plants[0].forecast, "none");
  const add = m.applyOps(rm, [{ target: "forecast", op: "set", kind: "forecast", part: "forecast", option: "solcast", inputs: {} }], INFO);
  assert.equal(add.site.forecast, "solcast");
  assert.equal(add.solar_plants[0].forecast, "solcast_site");
});

test("applyOps: the tariff is saved as auto; plants and devices are added, edited and removed by id", () => {
  assert.equal(m.applyOps(base(), [{ target: "tariff", op: "set", kind: "tariff", part: "tariff", option: "edf", inputs: {} }], INFO).site.tariff, "auto");
  const plant = { id: "shed", name: "Shed", forecast: "none", enabled: true, power: { entity: "sensor.s" }, energy_today: { entity: "sensor.st" } };
  let d = m.applyOps(base(), [{ target: "plant:shed", op: "set", kind: "plant", plant }], INFO);
  assert.deepEqual(d.solar_plants.map((p) => p.id), ["main", "garage", "shed"]);
  d = m.applyOps(d, [{ target: "plant:garage", op: "remove", kind: "plant" }, { target: "plant:shed", op: "set", kind: "plant", plant: Object.assign({}, plant, { name: "Shed 2" }) }], INFO);
  assert.deepEqual(d.solar_plants.map((p) => p.name), ["Main", "Shed 2"]);
  const dev = { id: "gar", adapter: "solis", name: "Garage", firmware: "", control: "read_only", inputs: { battery_soc: { entity: "sensor.gb" } } };
  d = m.applyOps(Object.assign(base(), { devices: [] }), [{ target: "device:gar", op: "set", kind: "device", device: dev }], INFO);
  assert.deepEqual(d.devices.map((x) => x.id), ["gar"]);
  d = m.applyOps(d, [{ target: "device:gar", op: "remove", kind: "device" }], INFO);
  assert.deepEqual(d.devices, []);
  const main = m.applyOps(base(), [{ target: "plant:garage", op: "remove", kind: "plant" }], INFO).solar_plants[0];
  assert.equal(main.id, "main");                                            // the main plant cannot be removed this way
});

test("buildApplyConfig changes only the equipment and carries everything else over", () => {
  const ops = [{ target: "ev_charger", op: "remove", kind: "ev_charger", part: "ev_charger", clear: ["ev_plug_status"] }];
  const cfg = m.buildApplyConfig(SAVED, ROLES, {}, ops, INFO, false);
  assert.equal(cfg.site.ev_charger, "none");
  assert.equal(cfg.site.inverter, "solis");
  assert.equal(cfg.inputs.ev_plug_status, undefined);
  assert.deepEqual(cfg.inputs.grid_power, { entity: "sensor.solis_grid", invert: true });
  assert.equal(cfg.operation.mode, "active");                               // Active, Passive and Pause stay the owner's
  assert.equal(cfg.safety.battery_reserve_soc, 12);
  assert.equal(cfg.system.poll_s, 30);
  assert.equal(cfg.solar_plants.length, 2);
  assert.equal("devices" in cfg, false);                                    // an app without devices gets no devices key
  const withDevices = m.buildApplyConfig(Object.assign({}, SAVED, { devices: [{ id: "gar", adapter: "solis", name: "G", control: "read_only", inputs: { battery_soc: { entity: "sensor.gb" } } }] }), ROLES, {}, [], INFO, true);
  assert.equal(withDevices.devices.length, 1);
  assert.deepEqual(Object.keys(cfg.site).sort(), ["car", "ev_charger", "events", "forecast", "inverter", "inverter_firmware", "tariff"]);
});

test("buildApplyConfig works on an install with nothing configured", () => {
  const info = Object.assign({}, INFO, { site: {}, setup: "unconfigured" });
  const op = { target: "inverter", op: "set", kind: "inverter", part: "inverter", option: "solis", firmware: "420044", inputs: { battery_soc: { entity: "sensor.s" } },
    plant: { power: { entity: "sensor.p" }, energy_today: { entity: "sensor.e" } } };
  const cfg = m.buildApplyConfig({}, ROLES, {}, [op], info, true);
  assert.equal(cfg.site.inverter, "solis");
  assert.equal(cfg.operation.mode, "passive");
  assert.equal(cfg.solar_plants[0].power.entity, "sensor.p");
  assert.equal(cfg.site.ev_charger, "none");                                 // optional parts never set up are left out
  assert.equal(cfg.site.forecast, "none");
  assert.equal(cfg.site.events, "none");
  assert.equal(cfg.site.tariff, null);                                       // a required part stays unset until added
  assert.equal(cfg.solar_plants[0].forecast, "none");
  assert.deepEqual(cfg.devices, []);
});

test("overlayEquipment: plants, devices and changed inputs follow the new saved config; other edits stay", () => {
  const draft = base();
  draft.safety.battery_reserve_soc = 20;                                     // an unsaved settings edit
  draft.inputs.grid_power = { entity: "sensor.mine" };                       // an unsaved input edit the panel did not touch
  const next = JSON.parse(JSON.stringify(SAVED));
  next.solar_plants = [next.solar_plants[0]];
  delete next.inputs.ev_plug_status;
  next.inputs.battery_soc = { entity: "sensor.new_soc" };
  const out = m.overlayEquipment(draft, SAVED, next, false);
  assert.equal(out.solar_plants.length, 1);
  assert.equal(out.inputs.ev_plug_status, undefined);
  assert.equal(out.inputs.battery_soc.entity, "sensor.new_soc");
  assert.equal(out.inputs.grid_power.entity, "sensor.mine");
  assert.equal(out.safety.battery_reserve_soc, 20);
  assert.equal("devices" in out, false);
  const withDev = m.overlayEquipment(draft, SAVED, Object.assign({}, next, { devices: [{ id: "g", adapter: "solis", inputs: {} }] }), true);
  assert.deepEqual(withDev.devices.map((d) => d.id), ["g"]);
});

test("systemImpact: a required part can only be replaced; an optional one lists what goes with it", () => {
  const items = m.systemItems(SAVED, INFO, true);
  const inv = m.systemImpact(items.find((i) => i.kind === "inverter"), INFO, SAVED, ROLES);
  assert.equal(inv.blocked, true);
  assert.match(inv.lead, /Replace/);
  const ev = m.systemImpact(items.find((i) => i.kind === "ev_charger"), INFO, SAVED, ROLES);
  assert.equal(ev.blocked, false);
  assert.equal(ev.level, "bad");
  assert.deepEqual(ev.roles, ["Car plug status"]);
  assert.deepEqual(ev.roleKeys, ["ev_plug_status"]);
  assert.ok(ev.features.length >= 1);
  assert.ok(ev.items.length >= 1);
  const plant = m.systemImpact(items.find((i) => i.kind === "plant"), INFO, SAVED, ROLES);
  assert.equal(plant.level, "warn");
  assert.deepEqual(plant.roles, []);
  const fc = m.systemImpact(items.find((i) => i.kind === "forecast"), INFO, SAVED, ROLES);
  assert.match(fc.items.join(" "), /forecast/);
});

test("the draft is kept in local storage, tied to the saved system, and every storage failure is survived", () => {
  const store = () => { const d = {}; return { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = v; }, removeItem: (k) => { delete d[k]; }, d }; };
  const s = store();
  const ops = [{ target: "ev_charger", op: "remove", kind: "ev_charger" }];
  assert.equal(m.systemDraftSave(s, ops, "fp1", new Date("2026-10-04T10:00:00Z")), true);
  assert.deepEqual(m.systemDraftLoad(s, "fp1"), { ops, dropped: false, at: "2026-10-04T10:00:00.000Z" });
  assert.deepEqual(m.systemDraftLoad(s, "fp2"), { ops: [], dropped: true });         // the system changed: the draft no longer fits
  assert.equal(m.systemDraftSave(s, [], "fp1"), true);                                 // nothing left: the entry is removed
  assert.deepEqual(m.systemDraftLoad(s, "fp1"), { ops: [], dropped: false });
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(m.systemDraftSave(broken, ops, "fp1"), false);
  assert.deepEqual(m.systemDraftLoad(broken, "fp1"), { ops: [], dropped: false });
  assert.equal(m.systemDraftSave(null, ops, "fp1"), false);
  s.d[m.SYSTEM_DRAFT_KEY] = "{not json";
  assert.deepEqual(m.systemDraftLoad(s, "fp1"), { ops: [], dropped: false });
});

test("systemFingerprint changes with equipment, not with settings", () => {
  const a = m.systemFingerprint(SAVED, INFO);
  assert.equal(a, m.systemFingerprint(Object.assign({}, SAVED, { safety: { battery_reserve_soc: 50 }, features: {} }), INFO));
  assert.notEqual(a, m.systemFingerprint(Object.assign({}, SAVED, { site: Object.assign({}, SITE, { ev_charger: "none" }) }), INFO));
  assert.notEqual(a, m.systemFingerprint(Object.assign({}, SAVED, { solar_plants: [SAVED.solar_plants[0]] }), INFO));
  const moved = JSON.parse(JSON.stringify(SAVED)); moved.inputs.ev_plug_status = { entity: "sensor.other" };
  assert.notEqual(a, m.systemFingerprint(moved, INFO));
});

test("opsSummary says add, change or remove in list order", () => {
  const baseItems = m.systemItems(SAVED, INFO, true);
  const ops = [{ target: "ev_charger", op: "remove", kind: "ev_charger" }, { target: "tariff", op: "set", kind: "tariff", part: "tariff", option: "edf", inputs: {} },
    { target: "plant:shed", op: "set", kind: "plant", label: "Shed", plant: { id: "shed", name: "Shed" } }];
  const work = m.systemItems(m.applyOps(base(), ops.filter((o) => o.op !== "remove"), INFO), INFO, true);
  const sum = m.opsSummary(ops, baseItems, INFO, true, work);
  assert.deepEqual(sum.map((r) => [r.tag, r.name]), [["remove", "Zappi"], ["change", "Automatic"], ["add", "Shed"]]);
});
