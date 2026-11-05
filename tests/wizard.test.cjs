const test = require("node:test");
const assert = require("node:assert/strict");
const m = require("../ha-powerengine-card.js");

// What the app publishes (pe_core/wizard.py), trimmed to what these tests need.
const SOLIS = { id: "solis", integration: { name: "SolaX Modbus", url: "https://github.com/wills106/homeassistant-solax-modbus" },
  domains: ["solax_modbus"], manufacturers: ["^(solis|ginlong|solax)"], entities: ["^sensor\\.solis_"] };
const PARTS = [
  { part: "inverter", title: "Inverter and battery", why: "w", required: true, options: [SOLIS],
    roles: ["battery_soc", "battery_power", "grid_power", "house_load_power", "battery_capacity", "timed_charge_current"] },
  { part: "tariff", title: "Electricity tariff", why: "w", required: true, roles: ["import_rate_now"], options: [
    { id: "edf", integration: { name: "Octopus Energy", url: "https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy" },
      domains: ["edf_energy", "octopus_energy"], entities: ["^sensor\\.edf_energy_electricity_"] }] },
  { part: "ev_charger", title: "Car charger", why: "w", required: false, skip: "none", roles: ["ev_plug_status"],
    options: [{ id: "zappi", integration: { name: "myenergi" }, domains: ["myenergi"], manufacturers: ["myenergi"], models: ["zappi"], entities: ["^sensor\\.myenergi_zappi_"] }] },
  { part: "forecast", title: "Solar forecast", why: "w", required: false, skip: "none", roles: [], options: [] },
];
const ROLES = [
  { key: "battery_soc", label: "Battery state of charge", kind: "percent", required: "yes", suggest: ["^sensor\\.solis_battery_soc$"] },
  { key: "battery_power", label: "Battery power", kind: "power", required: "yes", signed: true, sign_note: "+ discharging, - charging", suggest: ["^sensor\\.solis_battery_power$"] },
  { key: "grid_power", label: "Grid power", kind: "power", required: "yes", signed: true, sign_note: "+ importing, - exporting", suggest: ["^sensor\\.solis_meter_active_power$"] },
  { key: "house_load_power", label: "House load", kind: "power", required: "yes", suggest: ["^sensor\\.solis_house_load$"] },
  { key: "battery_capacity", label: "Capacity", kind: "static", required: "yes", static_ok: true, suggest_static: 18 },
  { key: "timed_charge_current", label: "Charge current", kind: "control", required: "no", domains: ["number"], suggest: ["^number\\.solis_timed_charge_current$"] },
  { key: "import_rate_now", label: "Import rate now", kind: "rate", required: "yes", suggest: ["^sensor\\.edf_energy_electricity_.*_current_rate$"] },
  { key: "ev_plug_status", label: "Car plug status", kind: "text", required: "yes", suggest: ["^sensor\\.myenergi_zappi_.*_plug_status$"] },
];

function hass() {
  const st = (state, attributes) => ({ state, attributes: attributes || {} });
  return {
    config: { version: "2026.10.0" },
    states: {
      "sensor.solis_battery_soc": st("54", { unit_of_measurement: "%", device_class: "battery" }),
      "sensor.solis_battery_power": st("-1500", { unit_of_measurement: "W", device_class: "power" }),
      "sensor.solis_meter_active_power": st("1600", { unit_of_measurement: "W" }),
      "sensor.solis_house_load": st("300", { unit_of_measurement: "W" }),
      "sensor.solis_pv_total_power": st("0", { unit_of_measurement: "W" }),
      "sensor.solis_power_generation_today": st("2.1", { unit_of_measurement: "kWh" }),
      "number.solis_timed_charge_current": st("20", { min: 0, max: 100, step: 1 }),
      "select.solis_battery_control_override": st("Off", { options: ["Off", "Force charge", "Force discharge"] }),
      "sensor.edf_energy_electricity_1234567890_current_rate": st("0.07", { unit_of_measurement: "GBP/kWh" }),
      "sensor.myenergi_zappi_12345678_plug_status": st("EV Disconnected"),
      "sensor.kitchen_temp": st("20", { unit_of_measurement: "°C" }),
    },
    entities: {
      "sensor.solis_battery_soc": { device_id: "d_solis", platform: "solax_modbus" },
      "sensor.solis_battery_power": { device_id: "d_solis", platform: "solax_modbus" },
      "sensor.solis_meter_active_power": { device_id: "d_solis", platform: "solax_modbus" },
      "sensor.solis_house_load": { device_id: "d_solis", platform: "solax_modbus" },
      "sensor.solis_pv_total_power": { device_id: "d_solis", platform: "solax_modbus" },
      "sensor.solis_power_generation_today": { device_id: "d_solis", platform: "solax_modbus" },
      "number.solis_timed_charge_current": { device_id: "d_solis", platform: "solax_modbus" },
      "select.solis_battery_control_override": { device_id: "d_solis", platform: "solax_modbus" },
      "sensor.edf_energy_electricity_1234567890_current_rate": { device_id: "d_meter", platform: "octopus_energy" },
      "sensor.myenergi_zappi_12345678_plug_status": { device_id: "d_zappi", platform: "myenergi" },
      "sensor.kitchen_temp": { device_id: "d_kitchen", platform: "zha" },
    },
    devices: {
      d_solis: { name: "Solis Inverter", manufacturer: "Solis", model: "S5-EH1P6K-L", sw_version: "420044" },
      d_meter: { name: "Electricity meter", manufacturer: "Octopus Energy" },
      d_zappi: { name: "zappi", manufacturer: "myenergi", model: "Zappi 2.0" },
      d_kitchen: { name: "Kitchen", manufacturer: "Aqara" },
    },
  };
}

test("wizardInfo needs the app's wizard data and puts the parts in order; an old app gives null", () => {
  assert.equal(m.wizardInfo({}), null);
  assert.equal(m.wizardInfo({ wizard: { v: 2, parts: PARTS } }), null);
  const info = m.wizardInfo({ wizard: { v: 1, parts: [PARTS[2], PARTS[0], PARTS[1]] }, site_options: { inverter: [] }, site: { inverter: "solis" }, setup: "unconfigured" });
  assert.deepEqual(info.parts.map((p) => p.part), ["inverter", "tariff", "ev_charger"]);
  assert.equal(info.setup, "unconfigured");
});

test("wizardFacts joins entities to their platform and device", () => {
  const f = m.wizardFacts(hass());
  assert.equal(f.registry, true);
  assert.deepEqual(f.devices.d_solis.platforms, ["solax_modbus"]);
  assert.equal(f.devices.d_solis.manufacturer, "Solis");
  assert.equal(f.devices.d_solis.entities.length, 8);
  assert.equal(m.wizardFacts({ states: { "sensor.a": { state: "1", attributes: {} } } }).registry, false);
});

test("wizardMatch finds the inverter by integration, manufacturer, or entity name alone", () => {
  const facts = m.wizardFacts(hass());
  const byReg = m.wizardMatch(SOLIS, facts);
  assert.deepEqual(byReg.devices.map((d) => d.id), ["d_solis"]);
  assert.equal(byReg.devices[0].reason, "integration");
  assert.ok(byReg.found);
  // no registry at all: the entity id patterns still find it
  const bare = m.wizardMatch(SOLIS, m.wizardFacts({ states: hass().states }));
  assert.equal(bare.devices.length, 0);
  assert.ok(bare.entities.length >= 6 && bare.found);
  // a manufacturer match with an unknown integration
  const h = hass(); h.entities["sensor.solis_battery_soc"].platform = "other"; Object.keys(h.entities).forEach((k) => { if (h.entities[k].device_id === "d_solis") h.entities[k].platform = "other"; });
  assert.equal(m.wizardMatch(SOLIS, m.wizardFacts(h)).devices[0].reason, "manufacturer");
  // nothing there
  assert.equal(m.wizardMatch(SOLIS, m.wizardFacts({ states: {}, entities: {}, devices: {} })).found, false);
});

test("wizardCandidates lists devices and entities found by name, with readable labels", () => {
  const facts = m.wizardFacts(hass());
  const inv = m.wizardCandidates(PARTS[0], facts);
  assert.equal(inv.length, 1);
  assert.equal(inv[0].option, "solis");
  assert.match(inv[0].label, /Solis Inverter \(S5-EH1P6K-L\) · SolaX Modbus/);
  const tariff = m.wizardCandidates(PARTS[1], facts);
  assert.deepEqual(tariff.map((c) => c.device && c.device.id), ["d_meter"]);
  assert.deepEqual(m.wizardCandidates(PARTS[3], facts), []);
});

test("suggestions come from the picked device first, and from elsewhere only when flagged", () => {
  const facts = m.wizardFacts(hass());
  const cand = m.wizardCandidates(PARTS[0], facts)[0];
  const soc = m.wizardSuggest(ROLES[0], cand, facts);
  assert.deepEqual(soc, { entity: "sensor.solis_battery_soc", from: "device" });
  const rate = m.wizardSuggest(ROLES[6], cand, facts);        // the rate is not on the inverter's device
  assert.deepEqual(rate, { entity: "sensor.edf_energy_electricity_1234567890_current_rate", from: "other" });
  assert.deepEqual(m.wizardSuggest({ key: "x", suggest: ["^sensor\\.nothing$"] }, cand, facts), { entity: "", from: "" });
  const solar = m.wizardPlantGuess("power", cand, facts, hass().states);
  assert.equal(solar.entity, "sensor.solis_pv_total_power");
  assert.equal(m.wizardPlantGuess("energy_today", cand, facts, hass().states).entity, "sensor.solis_power_generation_today");
});

test("wizardMissing lists the required inputs of the active parts only", () => {
  const draft = { inputs: { battery_soc: { entity: "sensor.a" } }, features: {}, solar_plants: [] };
  const miss = m.wizardMissing(draft, PARTS, ROLES, ["inverter"]).map((x) => x.role.key);
  assert.ok(miss.includes("battery_power") && miss.includes("grid_power") && miss.includes("plant_power") && miss.includes("plant_energy_today"));
  assert.ok(!miss.includes("battery_soc") && !miss.includes("import_rate_now") && !miss.includes("timed_charge_current"));
  assert.ok(m.wizardMissing(draft, PARTS, ROLES, ["tariff"]).map((x) => x.role.key).includes("import_rate_now"));
  draft.inputs.battery_capacity = { value: 18 };
  assert.ok(!m.wizardMissing(draft, PARTS, ROLES, ["inverter"]).map((x) => x.role.key).includes("battery_capacity"));
});

test("sign check: the user says what it is doing, the card says whether the sign matches", () => {
  assert.equal(m.wizardSignCheck("neg", -1500, false), "ok");        // charging reads negative
  assert.equal(m.wizardSignCheck("neg", 1500, false), "flip");
  assert.equal(m.wizardSignCheck("neg", 1500, true), "ok");          // ...after Invert
  assert.equal(m.wizardSignCheck("pos", -1500, false), "flip");
  assert.equal(m.wizardSignCheck("idle", 0, false), "idle");
  assert.equal(m.wizardSignCheck("pos", 10, false), "unknown");
  assert.equal(m.wizardSignCheck("pos", null, false), "unknown");
  assert.equal(m.wizardWatts({ state: "1.5", attributes: { unit_of_measurement: "kW" } }), 1500);
  assert.equal(m.wizardWatts({ state: "unavailable", attributes: {} }), null);
});

test("balance: house = grid + solar + battery, with a tolerance", () => {
  assert.deepEqual(m.wizardBalance({ battery: -1500, solar: 0, grid: 1800, house: 300 }), { ok: true, diff: 0, expected: 300 });
  assert.equal(m.wizardBalance({ battery: 1500, solar: 0, grid: 1800, house: 300 }).ok, false);
  assert.equal(m.wizardBalance({ battery: -1500, solar: 0, grid: null, house: 300 }), null);
  assert.equal(m.wizardBalance({ battery: 0, solar: 0, grid: 100, house: 300 }).ok, true);   // within 400 W
});

test("scrubText removes long numbers, emails and postcodes", () => {
  assert.equal(m.scrubText("sensor.edf_energy_electricity_1234567890_rate"), "sensor.edf_energy_electricity_<n>_rate");
  assert.equal(m.scrubText("12345"), "12345");
  assert.equal(m.scrubText("me@example.com"), "");
  assert.equal(m.scrubText("SW1A 1AA"), "");
  assert.equal(m.scrubText(null), "");
  assert.equal(m.scrubText("420044", true), "420044");                 // a firmware version keeps its digits
});

test("the candidate export lists a device's entities, scrubbed, and nothing from other devices", () => {
  const h = hass();
  const facts = m.wizardFacts(h);
  const out = m.buildCandidateExport({ hass: h, facts, deviceIds: ["d_solis"], looseIds: ["sensor.edf_energy_electricity_1234567890_current_rate"],
    chosen: { inverter: "unlisted" }, note: "Brand X, mail me@example.com", now: new Date("2026-10-03T10:00:00Z"), appVersion: "0.9.88" });
  assert.equal(out.format, m.EXPORT_FORMAT);
  assert.equal(out.version, 1);
  assert.equal(out.generated, "2026-10-03T10:00:00.000Z");
  assert.equal(out.note, "");                                          // the email made the note unsafe, so it is dropped
  assert.deepEqual(out.devices, [{ key: "d1", integration: "solax_modbus", manufacturer: "Solis", model: "S5-EH1P6K-L", sw_version: "420044" }]);
  const ids = out.entities.map((e) => e.id);
  assert.ok(ids.includes("sensor.solis_battery_soc") && ids.includes("sensor.edf_energy_electricity_<n>_current_rate"));
  assert.ok(!ids.includes("sensor.kitchen_temp") && !ids.includes("sensor.myenergi_zappi_<n>_plug_status"));
  const sel = out.entities.find((e) => e.id === "select.solis_battery_control_override");
  assert.deepEqual(sel.options, ["Off", "Force charge", "Force discharge"]);
  const num = out.entities.find((e) => e.id === "number.solis_timed_charge_current");
  assert.deepEqual([num.min, num.max, num.step, num.device], [0, 100, 1, "d1"]);
  const soc = out.entities.find((e) => e.id === "sensor.solis_battery_soc");
  assert.deepEqual([soc.unit, soc.device_class, soc.state, soc.platform], ["%", "battery", "54", "solax_modbus"]);
  assert.equal(out.entities.find((e) => e.id.includes("current_rate")).device, null);   // a loose entity is in no listed device
  assert.ok(!JSON.stringify(out.entities).match(/\d{6,}/), "no long number survives in an entity");
  assert.equal(m.candidateFileName(new Date("2026-10-03T10:00:00Z")), "powerengine-candidates-2026-10-03.json");
});

test("states longer than the limit are cut, and energy-looking devices are the ones offered for export", () => {
  const h = hass();
  h.states["sensor.solis_battery_soc"].state = "x".repeat(200);
  const out = m.buildCandidateExport({ hass: h, facts: m.wizardFacts(h), deviceIds: ["d_solis"], looseIds: [] });
  assert.equal(out.entities.find((e) => e.id === "sensor.solis_battery_soc").state.length, m.EXPORT_STATE_MAX);
  const names = m.wizardEnergyDevices(m.wizardFacts(h), h.states).map((d) => d.name);
  assert.ok(names.includes("Solis Inverter"));
  assert.ok(!names.includes("Kitchen"));
});

test("two options that find the same device give one candidate, and each row says what it owns", () => {
  const h = hass();
  const octopus = { id: "octopus", integration: PARTS[1].options[0].integration, domains: ["octopus_energy"], entities: ["^sensor\\.octopus_energy_electricity_"] };
  const part = Object.assign({}, PARTS[1], { options: [Object.assign({ }, PARTS[1].options[0], { domains: ["edf_energy", "octopus_energy"] }), octopus] });
  const facts = m.wizardFacts(h);
  const nameOf = (id) => ({ edf: "EDF", octopus: "Octopus" }[id]);
  const c = m.wizardCandidates(part, facts, nameOf);
  assert.equal(c.length, 1);
  assert.equal(c[0].option, "edf");                                    // its name patterns match the entity
  assert.match(c[0].label, /Electricity meter · EDF/);
  // an Octopus home: the octopus option's names match, so it wins
  const o = hass();
  o.states["sensor.octopus_energy_electricity_99_current_rate"] = { state: "0.2", attributes: {} };
  o.entities["sensor.octopus_energy_electricity_99_current_rate"] = { device_id: "d_meter", platform: "octopus_energy" };
  delete o.states["sensor.edf_energy_electricity_1234567890_current_rate"]; delete o.entities["sensor.edf_energy_electricity_1234567890_current_rate"];
  assert.equal(m.wizardCandidates(part, m.wizardFacts(o), nameOf)[0].option, "octopus");
});

test("with EDF and Octopus both set up, the one in the saved config is the one in use", () => {
  const h = hass();
  h.states["sensor.octopus_energy_electricity_99_current_rate"] = { state: "0.2", attributes: {} };
  h.entities["sensor.octopus_energy_electricity_99_current_rate"] = { device_id: "d_octo", platform: "octopus_energy" };
  h.devices.d_octo = { name: "Octopus meter", manufacturer: "Octopus Energy" };
  const octopus = { id: "octopus", integration: PARTS[1].options[0].integration, domains: ["octopus_energy"], manufacturers: ["octopus"], entities: ["^sensor\\.octopus_energy_electricity_"] };
  const part = Object.assign({}, PARTS[1], { options: [PARTS[1].options[0], octopus] });
  const facts = m.wizardFacts(h);
  const nameOf = (id) => ({ edf: "EDF", octopus: "Octopus" }[id]);
  const cands = m.wizardCandidates(part, facts, nameOf);
  assert.deepEqual(cands.map((c) => c.option).sort(), ["edf", "octopus"]);
  const saved = { import_rate_now: { entity: "sensor.edf_energy_electricity_1234567890_current_rate" } };
  const inUse = m.wizardInUse(part, cands, saved, { tariff: "auto" });
  assert.equal(inUse.option, "edf");
  assert.deepEqual(cands.filter((c) => c.key !== inUse.key).map((c) => c.label), ["Octopus meter · Octopus"]);   // the other candidates
  // no saved mapping: the site names the option (the tariff's "auto" and "none" name none)
  assert.equal(m.wizardInUse(part, cands, {}, { tariff: "octopus" }).option, "octopus");
  assert.equal(m.wizardInUse(part, cands, {}, { tariff: "auto" }), null);
  assert.equal(m.wizardInUse(part, cands, {}, { tariff: "none" }), null);
});

test("another energy device (battery, power and energy sensors) that no adapter owns is reported; a phone is not", () => {
  const h = hass();
  const st = (state, attributes) => ({ state, attributes });
  Object.assign(h.states, {
    "sensor.fox_battery_soc": st("60", { unit_of_measurement: "%", device_class: "battery" }),
    "sensor.fox_load_power": st("500", { unit_of_measurement: "W", device_class: "power" }),
    "sensor.fox_total_yield": st("1200", { unit_of_measurement: "kWh", device_class: "energy" }),
    "sensor.phone_battery": st("80", { unit_of_measurement: "%", device_class: "battery" }),
  });
  Object.assign(h.entities, {
    "sensor.fox_battery_soc": { device_id: "d_fox", platform: "foxess" }, "sensor.fox_load_power": { device_id: "d_fox", platform: "foxess" },
    "sensor.fox_total_yield": { device_id: "d_fox", platform: "foxess" }, "sensor.phone_battery": { device_id: "d_phone", platform: "mobile_app" },
  });
  h.devices.d_fox = { name: "Fox H1", manufacturer: "Fox ESS", model: "H1" };
  h.devices.d_phone = { name: "Matthew's phone", manufacturer: "Apple" };
  const others = m.wizardOthers(PARTS, m.wizardFacts(h), h.states);
  assert.deepEqual(others.map((d) => d.name), ["Fox H1"]);       // the Solis device is owned by an option, the phone has no power
});

test("a solar-only inverter already set up as a plant is not 'also found'; an unconfigured one is offered as a solar plant", () => {
  const h = hass();
  const st = (state, attributes) => ({ state, attributes });
  Object.assign(h.states, {
    "sensor.fox_pv_power": st("900", { unit_of_measurement: "W", device_class: "power" }),
    "sensor.fox_yield_today": st("4.2", { unit_of_measurement: "kWh", device_class: "energy" }),
    "sensor.plug_power": st("60", { unit_of_measurement: "W", device_class: "power" }),
    "sensor.plug_energy": st("3", { unit_of_measurement: "kWh", device_class: "energy" }),
    "sensor.balcony_panels_power": st("200", { unit_of_measurement: "W", device_class: "power" }),
    "sensor.balcony_panels_energy_today": st("1.1", { unit_of_measurement: "kWh", device_class: "energy" }),
  });
  Object.assign(h.entities, {
    "sensor.fox_pv_power": { device_id: "d_fox", platform: "foxess" }, "sensor.fox_yield_today": { device_id: "d_fox", platform: "foxess" },
    "sensor.plug_power": { device_id: "d_plug", platform: "shelly" }, "sensor.plug_energy": { device_id: "d_plug", platform: "shelly" },
    "sensor.balcony_panels_power": { device_id: "d_bal", platform: "zendure" }, "sensor.balcony_panels_energy_today": { device_id: "d_bal", platform: "zendure" },
  });
  Object.assign(h.devices, { d_fox: { name: "Fox solar inverter", manufacturer: "Fox ESS" }, d_plug: { name: "Kettle plug", manufacturer: "Shelly" }, d_bal: { name: "Balcony panels", manufacturer: "Zendure" } });
  const facts = m.wizardFacts(h);
  const saved = { inputs: { battery_soc: { entity: "sensor.solis_battery_soc" } },
    solar_plants: [{ id: "main", name: "Main", power: { entity: "sensor.solis_pv_total_power" }, energy_today: { entity: "sensor.solis_power_generation_today" } },
      { id: "fox", name: "Fox", power: { entity: "sensor.fox_pv_power" }, energy_today: { entity: "sensor.fox_yield_today" } }] };
  const used = m.wizardUsedEntities(saved);
  assert.ok(used.has("sensor.fox_pv_power") && used.has("sensor.solis_battery_soc") && used.has("sensor.solis_pv_total_power"));
  const others = m.wizardOthers(PARTS, facts, h.states, used);
  assert.deepEqual(others.map((d) => [d.name, d.kind]), [["Balcony panels", "solar"]]);        // Fox is in use, the kettle plug is not solar
  const plant = m.wizardPlantFromDevice(others[0], facts, h.states);
  assert.equal(plant.power.entity, "sensor.balcony_panels_power");
  assert.equal(plant.energy_today.entity, "sensor.balcony_panels_energy_today");
  assert.deepEqual([plant.forecast, plant.enabled, plant.name], ["none", true, "Balcony panels"]);
  assert.equal(m.wizardPlantId("Balcony panels", ["main", "balcony_panels"]), "balcony_panels_2");
  assert.equal(m.wizardDeviceName({ name: "Fox H1", manufacturer: "Fox ESS", model: "H1" }), "Fox H1 (Fox ESS H1)");
  // without the plant in the config, the Fox inverter is offered
  assert.deepEqual(m.wizardOthers(PARTS, facts, h.states, new Set()).map((d) => d.name).sort(), ["Balcony panels", "Fox solar inverter"]);
});
