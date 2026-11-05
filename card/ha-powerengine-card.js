/*
 * PowerEngine config card for Home Assistant.
 *
 * Edits PowerEngine's input mappings, features, settings and operation
 * mode. (Equipment, meaning the parts, solar plants and other devices, is changed in the Your system card.) Reads the input catalogue from the app (sensor.pe_map_catalogue), so
 * descriptions, units and sign conventions live in one place. Saves by firing
 * an HA event; the app validates, writes config.yaml (with a backup) and
 * reports the result.
 */
const CARD_VERSION = "0.9.200";
const VERSION_SENSOR = "sensor.pe_diag_version";
// The oldest app this card works with (0.9.69 added the demo_days attribute the welcome card reads). Raise it only when
// the card starts to need something a newer app publishes. The app publishes its own minimum as min_card_version.
const MIN_APP_VERSION = "0.9.188";   // 0.9.188: pe_compare_recheck (Compare this day again); 0.9.187: the Costs page period picker (daily rows with self_use_adj, bin, bout); 0.9.182: notifications.alexa.finish (the grid event summary); 0.9.179: heating set-back (axle_heating_setback); 0.9.175: pe_alexa_test; 0.9.174: notifications.alexa

/** "0.9.70" -> [0, 9, 70]; null when it isn't a plain dotted number ("?", "unavailable", "0.9.70-beta"). */
function parseVersion(v) {
  const m = /^\s*v?(\d+(?:\.\d+)*)\s*$/.exec(String(v == null ? "" : v));
  return m ? m[1].split(".").map(Number) : null;
}
/** True if version a is older than version b; false if equal or newer; null if either can't be read (so: no warning). */
function versionOlder(a, b) {
  const x = parseVersion(a), y = parseVersion(b);
  if (!x || !y) return null;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d < 0;
  }
  return false;
}
/** Plain-words warnings when one side is older than the other's minimum. Differing versions are fine. */
function versionWarnings(states) {
  const s = (states || {})[VERSION_SENSOR];
  if (!s) return [];
  const out = [];
  if (versionOlder(s.state, MIN_APP_VERSION) === true)
    out.push(`This card needs PowerEngine app ${MIN_APP_VERSION} or newer; the app is running ${s.state}. Update the app.`);
  const minCard = (s.attributes || {}).min_card_version;
  if (versionOlder(CARD_VERSION, minCard) === true)
    out.push(`The app needs card ${minCard} or newer; this page has ${CARD_VERSION}. Update the card, then reload the page.`);
  return out;
}
const MODE_SENSOR = "sensor.pe_state_operation_mode";
const CATALOGUE_SENSOR = "sensor.pe_map_catalogue";
const SETTINGS_SENSOR = "sensor.pe_map_settings";
const MAPPING_SENSOR = "sensor.pe_map_config";

/**
 * A catalogue sensor's attributes. The app puts a catalogue too big for one sensor on numbered ones (sensor.pe_map_catalogue,
 * _2, _3, ...; `parts` says how many) and this joins their lists back together. Null while a part is missing, so nothing
 * is built from half a catalogue.
 */
function catalogueAttrs(states, id) {
  const first = states[id];
  if (!first) return null;
  const out = Object.assign({}, first.attributes || {});
  const n = Number(out.parts) || 1;
  delete out.parts;
  for (let i = 2; i <= n; i++) {
    const more = states[`${id}_${i}`];
    if (!more || more.state === "unavailable") return null;
    for (const [k, v] of Object.entries(more.attributes || {})) if (Array.isArray(v)) out[k] = (out[k] || []).concat(v);
  }
  return out;
}
const SAVE_EVENT = "pe_config_save";
const NOT_SET_UP = "Not set up yet";      // what the app's Mode and Health say before there is a config
const RESULT_EVENT = "pe_config_result";
const ALEXA_TEST_EVENT = "pe_alexa_test", ALEXA_TEST_RESULT = "pe_alexa_test_result";
const ALEXA_TEST_WAIT_MS = 20000;
// How long a save waits for PowerEngine's answer. A late answer still replaces the message (the subscription stays open).
const SAVE_WAIT_MS = 45000;
const SAVE_NO_REPLY = "No reply from PowerEngine yet. It may be busy, and the change may still go through: refresh this page in a minute to check. If it hasn't, look at the AppDaemon log.";

// What this user's supplier and devices are called comes from the app (sensor.pe_diag_version, attribute "names").
// Texts here never hard-code one: they carry <<term>> placeholders, filled by fillNames.
const NAME_FALLBACK = {
  supplier: "your supplier", tariff: "tariff", dispatch: "smart-charge slot", dispatch_short: "smart slot",
  smart_charge: "smart charge", ev_charger: "car charger", forecast: "forecast", inverter: "inverter",
  event: "grid-services",
};
let currentNames = null;

/** Replaces <<term>> with the name from `names`, or a neutral word if the app doesn't say (or isn't reachable). */
function fillNames(text, names) {
  const n = names || {};
  return String(text).replace(/<<([a-z_]+)>>/g, (m, term) => n[term] || NAME_FALLBACK[term] || m);
}
function setNames(hass) {
  const a = (((hass || {}).states || {})[VERSION_SENSOR] || {}).attributes;
  currentNames = a && a.names && typeof a.names === "object" ? a.names : null;
}
function T(text) { return fillNames(text, currentNames); }

const MAIN_PLANT_SUGGEST = {
  power: [/^sensor\.solis_pv_total_power$/],
  energy_today: [/^sensor\.solis_power_generation_today$/],
};
const FEATURES = [
  ["auto_cheap_threshold", "Automatic cheap threshold", "Work out what counts as cheap from the prices ahead (the bottom fifth of the range, and only if storing it pays), capped by the Cheap import threshold setting."],
  ["fill_when_cheap", "Top up when cheap", "Charge to the grid-charge target in every cheap slot, not just what the forecast needs. A buffer in case the forecast is wrong."],
  ["smart_charge_optimisation", "Smart-charge optimisation", "Ask <<supplier>> for extra smart-charge slots by changing the car's ready-by time when it's worth it, with back-off and within the limits below. Replaces the fixed daily triggers. Sends nothing in Passive mode."],
  ["slots_whole_house", "Smart slots cover the whole house", "Tick if your supplier charges the whole house the slot rate during a smart-charge slot, even when the car isn't charging (<<supplier>> does). Off: PowerEngine plans slots at your normal rate for the house and battery, and doesn't ask for extra slots, since they'd only help the car."],
  ["smart_skip_full_car", "Don't ask when the car is full", "Skip requests while the charger says the charge is complete, or the car drew nothing in the last smart slot. Leave off if your supplier gives slots even when the car is full: the Config page's success rate shows whether requests for a full car work."],
  ["learn_car_min", "Learn: shortest real charge", "Move the Shortest real charge setting towards what the car's confirmed smart-charge slots show: half of a typical short real charge, a little at a time, and never below half or above double the setting. Until there are enough confirmed slots it uses the setting as it is."],
  ["arbitrage", "Energy arbitrage", "Sell stored energy just before a cheap refill when it pays after losses and wear, keeping enough for the house. In Passive mode this only plans and simulates it, so you can see what it would earn.",
    "Check your export tariff terms first: some only pay for exported solar, not energy bought from the grid."],
  ["deep_overnight", "Deeper selling overnight", "Inside the fixed overnight window, where the cheap refill is guaranteed, arbitrage may sell below the band's bottom (down to the reserve plus 10%): one deeper sale and one refill instead of many shallow cycles, for the same money. Off: the band's bottom holds overnight too."],
  ["use_check_meter", "Use the check meter", "When a check meter is set and reporting, use it for grid power instead of the inverter's meter, and correct the inverter's house load by the difference (also in the learned usage history). If it stops reporting for 3 minutes, the inverter's meter is used again. Off: the check meter is only compared."],
  ["axle", "Take part in grid events", "Force-discharge during grid events (run by <<event>>) and hold charge beforehand, so the battery is full when one starts."],
  ["axle_plus_export", "Grid events also earn the export rate", "Your supplier pays its normal export rate on grid-event exports as well as the event payment (<<event>>: £1/kWh; <<supplier>>: £1 + 15p). Planning and the event figures count both. Off: the event payment only."],
  ["axle_stop_car", "Stop the car charger during grid events", "While a grid event (<<event>>) runs, set the car charger (<<ev_charger>>) to Stopped, so the battery's export isn't charged into the car, and put its mode back when the event ends. Without this, a smart-charge slot or a manual charge can take the whole event's energy and the event earns almost nothing. Needs the Charger mode entity (a select) mapped. Applies in Active mode only."],
  ["axle_heating_setback", "Turn the heating down before grid events", "Turn your heating's set-back switch on shortly before a grid event (<<event>>) starts and off when it ends, so the boiler's pump isn't drawing power while the battery is exporting. Choose the switch below (for example input_boolean.heating_off) and how long before the event to start: the pump's overrun. A switch that is already on is left alone, and one you turn off during the set-back stays off. Applies in Active mode only."],
  ["free_power_days", "Free-power sessions", "Make full use of <<supplier>> free-electricity sessions."],
  ["optimised_plan", "Optimised planning", "The optimiser chooses each half-hour's action for the lowest cost (arbitrage band and safety rules included), with plain-English reasons. Off: the simpler rule-based planner."],
  ["learn_taper", "Learn: charge and discharge slow-down", "Plan with how much charging slows from 90% and 95%, and how much discharging slows below 40%, 30% and 20%, as seen: the overnight charge starts early enough to finish, and deep sales are planned at the speed they really run. Health tab, Learned from use, shows each learned figure and how many half-hours it's based on."],
  ["learn_conversion", "Learn: inverter conversion losses", "Measure how much grid energy reaches the battery when charging, and how much of the battery's output reaches the house and grid when selling (no solar, full rate). Plans then use the real grid-to-grid round trip, so arbitrage is only planned where it pays after all losses. Works for any inverter and battery, and re-learns if either changes."],
  ["learn_reserve", "Learn: where discharging stops", "Plan with the charge level where the battery has been seen to stop supplying the house. It only ever raises the reserve (engine 1) or the hard floor (engine 2), never lowers it."],
  ["learn_export", "Learn: export ceiling", "If selling is seen to top out below the battery's own rate (a grid limit), plan with that ceiling."],
  ["learn_car", "Learn: car charge rate", "Plan the car's share of smart-charge slots with its real charging kW instead of Car charger power."],
  ["cold_caution", "Cold battery caution", "Plan a slower charge when the battery is likely to be cold, estimated from the outside temperature (Open-Meteo forecast for your home's location) with a lag, so a cold spell is expected to chill it gradually and it stays cautious until the weather has been milder for a while. Settings under Cold battery."],
  ["cold_learning", "Learn cold behaviour", "Adjust the cold threshold and rate from what's seen: charging slowed at 5°C raises the threshold; charging normally at 3°C lowers it to 3°C."],
  ["damp_restart", "Restart hold-off", "After PowerEngine starts, or control resumes or goes live, write nothing for a few minutes (Restart hold-off setting) while the plan and its inputs settle. The inverter keeps running the windows already set. Safety changes (grid events, free power, the car charging, the reserve) never wait."],
  ["damp_bursts", "Burst damping", "The first change to a window slot, current or the mode goes straight through; another change to the same thing within the Burst window waits until the plan has been steady for the Burst settle time, so several quick changes become one write. Off by default while its effect is evaluated. Safety changes never wait."],
  ["engine_compare", "Compare the two engines each night", "Each night, replay yesterday with engine v1 and engine v2 (using the forecasts as they were) and show on the Costs page what each would have saved. It runs in the background on your PowerEngine and changes nothing."],
  ["tariff_simulator", "Tariff simulator", "Each night at 01:30, compare your recorded days on current Octopus and EDF tariffs (fetched from their public tariff lists) and notify you if one would save noticeably. Reads only; changes nothing."],
];
const NOTIFY_EVENTS = [
  ["health", "Health problems", "When the Health tab finds a problem (checked after start-up and each night).", true],
  ["inputs", "Inputs not working", "When a required input has been unavailable or stale for 15 minutes.", true],
  ["axle", "Grid events", "When a grid event (<<event>>) is scheduled, with its time.", true],
  ["free_power", "Free-power sessions", "When a free-electricity session is announced.", true],
  ["daily", "Daily summary", "Each morning at 08:00: yesterday's cost and savings.", false],
  ["alexa", "Alexa announcements", "Spoken reminders before a grid event, saying whether the battery looks ready. Choose the speakers and the allowed hours below.", false],
  ["simulator", "Tariff opportunities", "When the overnight Simulator finds a tariff that would have cost noticeably less (at least £5 and 5% a month), or new tariffs appear.", true],
];
// Alexa announcements (the app's ALEXA_DEFAULT): speakers, hours before the event (0 = as it starts) and the allowed times
const ALEXA_DEFAULT = { services: ["notify.alexa_media_everywhere"], hours_before: [8, 5, 2, 1, 0], earliest_weekday: "07:30", earliest_weekend: "09:00", latest: "21:00", intro: "Daddybot here.", finish: true };
const TIME_OF_DAY = /^([01]?\d|2[0-3]):[0-5]\d$/;
/** The Alexa settings as a draft holds them: defaults for anything missing, bad values dropped. */
function alexaDraft(raw) {
  const a = Object.assign({}, ALEXA_DEFAULT, raw && typeof raw === "object" ? raw : {});
  const time = (v, d) => (typeof v === "string" && TIME_OF_DAY.test(v.trim()) ? v.trim().padStart(5, "0") : d);
  const hours = Array.isArray(a.hours_before) ? a.hours_before.map(Number).filter((h) => Number.isFinite(h) && h >= 0 && h <= 48) : [];
  return { services: (Array.isArray(a.services) ? a.services : []).filter((s) => /^notify\.[a-z0-9_]+$/.test(s)),
    hours_before: hours.length ? [...new Set(hours)].sort((x, y) => y - x) : ALEXA_DEFAULT.hours_before.slice(),
    earliest_weekday: time(a.earliest_weekday, ALEXA_DEFAULT.earliest_weekday), earliest_weekend: time(a.earliest_weekend, ALEXA_DEFAULT.earliest_weekend),
    latest: time(a.latest, ALEXA_DEFAULT.latest), intro: typeof a.intro === "string" ? a.intro.trim().slice(0, 80) : ALEXA_DEFAULT.intro,
    finish: typeof a.finish === "boolean" ? a.finish : ALEXA_DEFAULT.finish,
    // the WhatsApp copy (app 0.9.193 and newer) is kept only when the saved settings or the page carry it
    ...(a.whatsapp === undefined && a.whatsapp_targets === undefined ? {} : { whatsapp: a.whatsapp === true, whatsapp_targets: whatsappTargets(a.whatsapp_targets) }) };
}
const WHATSAPP_APP_VERSION = "0.9.193";
const WHATSAPP_TARGET = /^(\d+(-\d+)?@g\.us|\d{6,15})$/;
function whatsappSupported(appVersion) { return versionOlder(appVersion, WHATSAPP_APP_VERSION) === false; }
/** The WhatsApp chats as a clean list: group ids (120363012345678901@g.us) and phone numbers with the country code, each once. */
function whatsappTargets(list) {
  return [...new Set((Array.isArray(list) ? list : []).map((x) => String(x).trim()).filter((x) => WHATSAPP_TARGET.test(x)))];
}
/** "id1, id2" typed in the page -> a list; null when any entry is not a group id or a phone number. */
function parseWhatsappTargets(text) {
  const parts = String(text).split(/[,\s]+/).filter(Boolean);
  return parts.every((x) => WHATSAPP_TARGET.test(x)) ? [...new Set(parts)] : null;
}
/** The draft with the WhatsApp settings present (off, no chats) when the app supports them, and absent when it doesn't. */
function withWhatsapp(draft, supported) {
  const n = draft && draft.notifications;
  if (!n || !n.alexa || n.channels) return draft;
  if (supported) n.alexa = { ...n.alexa, whatsapp: n.alexa.whatsapp === true, whatsapp_targets: whatsappTargets(n.alexa.whatsapp_targets) };
  else { delete n.alexa.whatsapp; delete n.alexa.whatsapp_targets; }
  return draft;
}
// Notifications as channels and rules (app 0.9.195 and newer): where messages can go, and which kinds go where.
const ROUTER_APP_VERSION = "0.9.195";
const NOTIFY_SENSOR = "sensor.pe_ui_notify";
const NOTIFY_TEST_EVENT = "pe_notify_test", NOTIFY_TEST_RESULT = "pe_notify_test_result";
function routerSupported(appVersion) { return versionOlder(appVersion, ROUTER_APP_VERSION) === false; }
const CHANNEL_TYPES = [["bell", "Home Assistant notifications"], ["notify", "Phone or other notify service"], ["alexa", "Alexa speakers"], ["whatsapp", "WhatsApp"]];
// what PowerEngine sends: [kind, label, what it is]
const NOTIFY_KINDS = [
  ["health", "Health problems", "When the Health tab finds a problem (checked after start-up and each night)."],
  ["inputs", "Inputs not working", "When a required input has been unavailable or stale for 15 minutes."],
  ["axle", "Grid event scheduled", "When a grid event (<<event>>) is scheduled, with its time."],
  ["free_power", "Free-power sessions", "When a free-electricity session is announced."],
  ["daily", "Daily summary", "Each morning at 08:00: yesterday's cost and savings."],
  ["simulator", "Tariff opportunities", "When the overnight Simulator finds a tariff that would have cost noticeably less, or new tariffs appear."],
  ["grid_reminder", "Grid event reminders", "Hours before a grid event, saying whether the battery looks ready (times below)."],
  ["grid_summary", "Grid event summary", "When a grid event ends: what the house used, what was exported and what it should pay."],
];
const ROUTER_LEGACY_KINDS = ["health", "inputs", "axle", "free_power", "daily", "simulator"];
const REMINDER_DEFAULT = [8, 5, 2, 1, 0];
function routerChannel(type, name, raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const out = { type, label: typeof r.label === "string" && r.label.trim() ? r.label.trim().slice(0, 40) : name, intro: typeof r.intro === "string" ? r.intro.trim().slice(0, 80) : "",
    hours: null, outside_hours: r.outside_hours === "skip" ? "skip" : "hold" };
  const h = r.hours;
  if (h && typeof h === "object") {
    const time = (v, d) => (typeof v === "string" && (TIME_OF_DAY.test(v.trim()) || v.trim() === "24:00") ? v.trim().padStart(5, "0") : d);
    const weekday = time(h.weekday_from, "00:00");
    out.hours = { weekday_from: weekday, weekend_from: time(h.weekend_from, weekday), until: time(h.until, "24:00") };
  }
  if (type === "notify") out.service = typeof r.service === "string" ? r.service : "";
  if (type === "alexa") out.services = (Array.isArray(r.services) ? r.services : []).filter((x) => /^notify\.[a-z0-9_]+$/.test(x));
  if (type === "whatsapp") out.targets = whatsappTargets(r.targets);
  return out;
}
/** The notification settings as the page edits them: channels, rules and the reminder hours, from the app's own form. */
function routerDraft(n) {
  const channels = {};
  Object.entries((n && n.channels) || {}).forEach(([name, spec]) => { if (spec && CHANNEL_TYPES.some(([t]) => t === spec.type)) channels[name] = routerChannel(spec.type, name, spec); });
  const rules = ((n && n.rules) || []).filter((r) => r && Array.isArray(r.events) && Array.isArray(r.to))
    .map((r) => ({ events: [...new Set(r.events.map(String))], to: [...new Set(r.to.map(String))].filter((c) => channels[c]) })).filter((r) => r.events.length && r.to.length);
  const hours = Array.isArray(n && n.reminder_hours) ? n.reminder_hours.map(Number).filter((h) => Number.isFinite(h) && h >= 0 && h <= 48) : [];
  return { channels, rules, reminder_hours: hours.length ? [...new Set(hours)].sort((x, y) => y - x) : REMINDER_DEFAULT.slice() };
}
/** The older settings (service, events, alexa) as channels and rules, the way the app turns them into the same. */
function routerFromLegacy(n) {
  const channels = {}, rules = [];
  const service = n.service || "";
  const main = service === "persistent_notification" ? "bell" : (service && service !== "off" ? "phone" : null);
  if (main === "bell") channels.bell = routerChannel("bell", "bell", { label: "HA notifications" });
  else if (main) channels.phone = routerChannel("notify", "phone", { label: "Phone", service });
  const events = n.events || {};
  const kinds = ROUTER_LEGACY_KINDS.filter((k) => events[k]);
  if (main && kinds.length) rules.push({ events: kinds, to: [main] });
  const a = alexaDraft(n.alexa);
  const hours = { weekday_from: a.earliest_weekday, weekend_from: a.earliest_weekend, until: a.latest };
  const spoken = [];
  if (a.services.length) { channels.alexa = routerChannel("alexa", "alexa", { label: "Alexa", services: a.services, intro: a.intro, hours }); spoken.push("alexa"); }
  if (a.whatsapp && a.whatsapp_targets && a.whatsapp_targets.length) { channels.whatsapp = routerChannel("whatsapp", "whatsapp", { label: "WhatsApp", targets: a.whatsapp_targets, intro: a.intro, hours }); spoken.push("whatsapp"); }
  if (events.alexa && spoken.length) rules.push({ events: a.finish ? ["grid_reminder", "grid_summary"] : ["grid_reminder"], to: spoken });
  return { channels, rules, reminder_hours: a.hours_before };
}
/** The draft with its notifications in the channels-and-rules form when the app supports it. */
function withRouter(draft, supported) {
  const n = draft && draft.notifications;
  if (n && supported && !n.channels) draft.notifications = routerFromLegacy(n);
  return draft;
}
/** One channel as the app takes it (also what the test button sends). */
function routerChannelConfig(ch) {
  const out = { type: ch.type };
  if (ch.label) out.label = ch.label;
  if (ch.intro) out.intro = ch.intro;
  if (ch.hours) { out.hours = { weekday_from: ch.hours.weekday_from, weekend_from: ch.hours.weekend_from, until: ch.hours.until }; if (ch.outside_hours === "skip") out.outside_hours = "skip"; }
  if (ch.type === "notify") out.service = ch.service;
  if (ch.type === "alexa") out.services = ch.services.slice();
  if (ch.type === "whatsapp") out.targets = ch.targets.slice();
  return out;
}
function routerConfig(n) {
  const channels = {};
  Object.entries(n.channels).forEach(([name, ch]) => { channels[name] = routerChannelConfig(ch); });
  return { channels, rules: n.rules.map((r) => ({ events: r.events.slice(), to: r.to.slice() })), reminder_hours: n.reminder_hours.slice() };
}
/** What stops a channel being saved (the app would refuse it), or "" when it is fine. */
function routerProblem(n) {
  for (const [name, ch] of Object.entries(n.channels || {})) {
    const who = `“${ch.label || name}”`;
    if (ch.type === "notify" && !/^notify\.[a-z0-9_]+$/.test(ch.service || "")) return `Choose the notify service for ${who}.`;
    if (ch.type === "alexa" && !ch.services.length) return `Tick at least one speaker for ${who}.`;
    if (ch.type === "whatsapp" && !ch.targets.length) return `Give ${who} a WhatsApp group id or a phone number.`;
    if (ch.hours && ch.hours.until !== "24:00" && ch.hours.until <= ch.hours.weekday_from && ch.hours.until <= ch.hours.weekend_from) return `${who}: the end of its hours must be after they start.`;
  }
  return "";
}
/** The ticks of the "what goes where" table: "kind|channel" for each rule. */
function routeCells(rules) {
  const cells = new Set();
  (rules || []).forEach((r) => r.events.forEach((k) => r.to.forEach((c) => cells.add(`${k}|${c}`))));
  return cells;
}
/** Rules for a set of ticks: one rule for each channel, listing the kinds it takes (in the table's order). */
function routesFromCells(cells, channelNames, kindOrder) {
  const rules = [];
  channelNames.forEach((c) => {
    const events = kindOrder.filter((k) => cells.has(`${k}|${c}`));
    if (events.length) rules.push({ events, to: [c] });
  });
  return rules;
}
/** The kinds the table lists: PowerEngine's own, then any the app has seen from elsewhere, then any a rule names. */
function notifyKinds(seen, rules) {
  const out = NOTIFY_KINDS.map(([kind, label, desc]) => ({ kind, label, desc, source: "powerengine" }));
  const have = new Set(out.map((k) => k.kind));
  (Array.isArray(seen) ? seen : []).forEach((k) => { if (k && k.kind && !have.has(k.kind)) { have.add(k.kind); out.push({ kind: k.kind, label: k.label || k.kind, desc: "", source: k.source || "external" }); } });
  (rules || []).forEach((r) => r.events.forEach((k) => { if (!have.has(k)) { have.add(k); out.push({ kind: k, label: k, desc: "", source: "external" }); } }));
  return out;
}
/** A name for a new channel of this type that no channel has yet: alexa, alexa_2, ... */
function newChannelName(type, channels) {
  let name = type, i = 2;
  while (channels[name]) name = `${type}_${i++}`;
  return name;
}
/** "8, 5, 2, 1, 0" -> [8, 5, 2, 1, 0]; null when it is not a list of hours between 0 and 48. */
function parseHoursBefore(text) {
  const parts = String(text).split(/[,\s]+/).filter(Boolean).map(Number);
  return parts.length && parts.every((h) => Number.isFinite(h) && h >= 0 && h <= 48) ? [...new Set(parts)].sort((x, y) => y - x) : null;
}
/** A speaker's name from its notify service: notify.alexa_media_kitchen -> "Kitchen". */
/** What the test button fires: the speakers ticked now (saved or not) and the opening words as typed. */
function alexaTestPayload(draft) {
  const a = alexaDraft(((draft || {}).notifications || {}).alexa);
  const out = { services: a.services.filter((x) => /^notify\.alexa_media_[a-z0-9_]+$/.test(x) && x !== "notify.alexa_media_last_called"), intro: a.intro };
  if (a.whatsapp) out.whatsapp = a.whatsapp_targets;
  return out;
}
function alexaSpeakerName(service) {
  const name = String(service).replace(/^notify\.(alexa_media_)?/, "").replace(/_s_/g, "'s ").replace(/_/g, " ").trim();
  return name === "everywhere" ? "Everywhere (every speaker)" : (name ? name.charAt(0).toUpperCase() + name.slice(1) : service);
}
/** Split Alexa Media Player's notify names (without "notify.") into single speakers and speaker groups. A group is an
 *  Amazon "Speaker Group" device (found by the name AMP slugs for its notify service); "everywhere" is all speakers. */
function alexaSpeakerKinds(names, devices) {
  const slug = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const groups = new Set(["alexa_media_everywhere"]);
  Object.values(devices || {}).forEach((d) => {
    if (d && /speaker group/i.test(String(d.model || "")) && /amazon/i.test(String(d.manufacturer || "amazon"))) groups.add(`alexa_media_${slug(d.name || d.name_by_user)}`);
  });
  return { speakers: names.filter((n) => !groups.has(n)), groups: names.filter((n) => groups.has(n)) };
}
const FEATURE_DEFAULTS = { auto_cheap_threshold: true, fill_when_cheap: true, smart_charge_optimisation: true, arbitrage: false, axle: true, free_power_days: true, tariff_simulator: true, engine_compare: true, optimised_plan: true,
  learn_taper: true, learn_conversion: true, learn_reserve: true, learn_export: true, learn_car: true, learn_car_min: true, cold_caution: true, cold_learning: true,
  damp_restart: true, damp_bursts: false, deep_overnight: true,
  use_check_meter: true, axle_plus_export: true, axle_stop_car: true, axle_heating_setback: false, slots_whole_house: true, smart_skip_full_car: false };

/* ------------------------------------------------------------------ helpers
 * Pure functions (no DOM), exported for tests at the bottom of the file.
 */

function toNumber(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const t = String(v === null || v === undefined ? "" : v).trim();
  if (t === "") return null;
  const n = Number(t);            // whole string must be numeric: "11:00" is not 11
  return Number.isFinite(n) ? n : null;
}

function fmtNumber(n, digits) {
  return n.toLocaleString(undefined, { maximumFractionDigits: digits === undefined ? 2 : digits });
}

/** Split "+ discharging, - charging" into { pos: "discharging", neg: "charging" }. */
function parseSignNote(note) {
  const out = { pos: "positive", neg: "negative" };
  String(note || "").split(",").forEach((part) => {
    const p = part.trim();
    if (p.startsWith("+")) out.pos = p.slice(1).trim();
    if (p.startsWith("-") || p.startsWith("−")) out.neg = p.slice(1).trim();
  });
  return out;
}

function currentItem(items, now) {
  const t = now.getTime();
  return (items || []).find((it) => {
    const s = Date.parse(it.start || it.period_start);
    const e = Date.parse(it.end) || s + 30 * 60 * 1000;
    return s <= t && t < e;
  });
}

function fmtTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function summariseAttribute(attr, value, now) {
  if (!Array.isArray(value)) return `${attr}: present`;
  if (attr === "rates") {
    const cur = currentItem(value, now);
    const price = cur ? toNumber(cur.value_inc_vat !== undefined ? cur.value_inc_vat : cur.value) : null;
    return `${value.length} half-hour rates` + (price !== null ? `, now ${fmtNumber(price * 100, 2)}p` : "");
  }
  if (attr === "detailedForecast") {
    const kwh = value.reduce((s, it) => s + (toNumber(it.pv_estimate) || 0) * 0.5, 0);
    return `${value.length} half-hours, ${fmtNumber(kwh, 1)} kWh forecast`;
  }
  if (attr === "planned_dispatches") {
    if (!value.length) return "no planned slots";
    return `${value.length} planned slot(s), next ${fmtTime(value[0].start)}`;
  }
  return `${value.length} items`;
}

/** The app's "In use" sentence for a figure that can be measured or learned (sensor.pe_diag_learned, attribute use), or null. */
function inUseFor(hass, key) {
  const st = hass && hass.states && hass.states["sensor.pe_diag_learned"];
  const u = st && st.attributes && st.attributes.use && st.attributes.use[key];
  return u && u.text ? u : null;
}
/** "Now: ..." for a readout; the app's "In use: ..." sentence already says what it is. */
function nowText(r) { return /^In use/.test(r.text) ? r.text : "Now: " + r.text; }

/**
 * What to show next to an input: the raw value, and for signed inputs how
 * PowerEngine will read it after the optional invert. `inUse` is the app's sentence for a measurable figure: with a
 * typed value, it says which figure the plan really uses (typed, measured or learned) and why.
 */
function readout(role, spec, stateObj, now, inUse) {
  now = now || new Date();
  if (!spec) return { text: "", readsAs: "" };
  if (spec.value !== undefined && spec.value !== "") {
    if (inUse && inUse.text) return { text: inUse.text, readsAs: "" };
    return { text: `Fixed: ${spec.value} ${role.static_unit || ""}`.trim(), readsAs: "" };
  }
  if (!stateObj) return { text: "", readsAs: "" };
  const attrs = stateObj.attributes || {};
  const attr = spec.attribute || role.attribute;
  if (attr) {
    if (!(attr in attrs)) return { text: `No '${attr}' attribute`, readsAs: "" };
    return { text: summariseAttribute(attr, attrs[attr], now), readsAs: "" };
  }
  if (role.kind === "control" && stateObj.entity_id && stateObj.entity_id.startsWith("button.")) {
    return { text: "Button (no value to show)", readsAs: "" };
  }
  const unit = attrs.unit_of_measurement || "";
  const num = toNumber(stateObj.state);
  let text = num !== null ? `${fmtNumber(num)} ${unit}`.trim() : String(stateObj.state);
  if (num !== null && role.kind === "rate" && /GBP|\u00a3/.test(unit)) text = `${fmtNumber(num * 100, 2)}p/kWh`;
  if (num !== null && role.kind === "money" && /GBP|\u00a3/.test(unit)) text = `\u00a3${fmtNumber(num, 2)}${role.key === "standing_charge" ? " per day" : ""}`;
  if (role.kind === "timestamp") text = ["unknown", "unavailable", ""].includes(stateObj.state) ? "none scheduled" : `${fmtTime(stateObj.state)}`;
  let readsAs = "";
  if (role.signed && num !== null) {
    const v = spec.invert ? -num : num;
    const words = parseSignNote(role.sign_note);
    const mag = Math.abs(v);
    const shown = unit === "W" && mag >= 1000 ? `${fmtNumber(mag / 1000, 2)} kW` : `${fmtNumber(mag)} ${unit}`.trim();
    readsAs = v === 0 ? "reads as: idle (0)" : `reads as: ${v > 0 ? words.pos : words.neg} ${shown}`;
  }
  return { text, readsAs };
}

const UNITS = {
  power: ["W", "kW"], energy: ["kWh", "Wh"], percent: ["%"],
  rate: ["GBP/kWh", "£/kWh", "p/kWh"], money: ["GBP", "GBP/day", "£"],
};

/** Instant client-side problem for an input, or "" if it looks fine. */
const BATTERY_PAIR = ["battery_charge_power", "battery_discharge_power"];

/** A role as it applies now: with the battery pair mapped, the single sensor is unused and the pair is required. */
function effectiveRole(role, pairMapped) {
  if (!pairMapped) return role;
  if (role.key === "battery_power") return Object.assign({}, role, { required: "unused" });
  if (BATTERY_PAIR.includes(role.key)) return Object.assign({}, role, { required: "yes" });
  return role;
}

// --- Config page layout: one section per topic, holding its switches, inputs, settings and learning ---------
// Anything the app adds that isn't listed here still appears, in "Other".
const TOPICS = [
  { key: "battery", title: "Battery",
    roles: ["battery_soc", "battery_power", "battery_charge_power", "battery_discharge_power", "battery_capacity",
      "battery_max_charge_power", "battery_max_discharge_power", "battery_charge_today", "battery_discharge_today",
      "battery_round_trip", "battery_soh", "battery_bms_charge_limit", "battery_bms_discharge_limit", "inverter_min_soc"],
    settings: ["battery_floor_soc", "min_reserve_soc", "grid_charge_target_soc", "charge_hysteresis_soc"],
    learning: ["learn_taper", "learn_conversion", "learn_reserve"] },
  { key: "grid", title: "Grid and house",
    roles: ["grid_power", "grid_import_today", "grid_export_today", "house_load_power", "house_load_today",
      "grid_power_reference", "grid_import_today_check", "grid_export_today_check"],
    settings: ["main_fuse_a"], system: ["house_load_includes_ev"], features: ["use_check_meter"] },
  { key: "solar", title: "Solar", roles: ["solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3"],
    plants: true },
  { key: "tariff", title: "Tariff and planning",
    roles: ["import_rate_now", "import_rates_today", "import_rates_tomorrow", "export_rate", "standing_charge", "offpeak_now"],
    features: ["optimised_plan", "auto_cheap_threshold", "fill_when_cheap"],
    system: ["overnight_window"],
    settings: ["overnight_start_h", "overnight_end_h", "cheap_threshold_p", "window_switch_cost_p"] },
  { key: "car", title: "Car and smart charging",
    roles: ["ev_plug_status", "ev_charger_status", "ev_charge_power", "ev_energy_today", "ev_charge_mode",
      "ev_session_energy", "smart_dispatches", "smart_state", "smart_target_soc", "smart_target_time"],
    features: ["smart_charge_optimisation", "slots_whole_house", "smart_skip_full_car"],
    settings: ["ev_charger_kw", "smart_max_requests_per_day", "smart_min_gap_min", "smart_lookahead_h", "car_min_charge_min"],
    learning: ["learn_car", "learn_car_min"] },
  { key: "selling", title: "Selling (arbitrage and export)", features: ["arbitrage", "deep_overnight"],
    settings: ["export_limit_kw", "battery_wear_p", "arbitrage_min_margin_p", "arbitrage_min_soc", "arbitrage_max_soc",
      "arbitrage_band_penalty_p", "overnight_switch_cost_p"],
    roles: ["inverter_export_limit"], learning: ["learn_export"] },
  { key: "axle", title: "Grid events", main: "axle", features: ["axle", "axle_plus_export", "axle_stop_car", "axle_heating_setback"],
    roles: ["axle_event_active", "axle_event_start", "axle_event_end", "axle_direction", "heating_setback"],
    settings: ["pre_axle_lookahead_h", "axle_margin_soc", "heating_pump_overrun_min"] },
  { key: "free", title: "Free-power sessions", main: "free_power_days", features: ["free_power_days"],
    roles: ["free_power_active", "free_power_next_start", "free_power_next_end", "free_power_events"] },
  { key: "cold", title: "Cold battery", main: "cold_caution", features: ["cold_caution"], learning: ["cold_learning"],
    system: ["battery_location"],
    settings: ["cold_caution_temp_c", "cold_charge_pct", "cold_release_c", "battery_temp_lag_h"],
    roles: ["outside_temperature", "battery_temperature"] },
  { key: "control", title: "Inverter control (needed to go live)",
    note: "Written only when PowerEngine is live. Map them now so it can show what it would set (Health tab) and count your current setup's writes. Windows 2 and 3 are found from window 1's entities. RAM remote control uses the inverter integration's Battery control override entities, found by name (the timed windows are still mapped: they're closed when it takes over, and used if those entities go missing).",
    roles: ["timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour", "timed_charge_end_minute",
      "timed_charge_current", "timed_discharge_start_hour", "timed_discharge_start_minute", "timed_discharge_end_hour",
      "timed_discharge_end_minute", "timed_discharge_current", "timed_update_button", "storage_mode",
      "inverter_clock", "inverter_clock_sync", "guard_read_only", "guard_off_1", "guard_off_2"],
    system: ["other_controller", "control_method"], settings: ["max_writes_per_day", "ram_refresh_min", "ram_switch_cost_p", "ram_max_power_w", "inverter_max_output_w"],
    damp: { note: "Holding inverter writes back briefly when the settings are likely to change again, to save writes. Health tab, Inverter writes today, shows how many changes were held back.",
      features: ["damp_restart", "damp_bursts"], settings: ["damp_restart_min", "damp_burst_window_min", "damp_burst_settle_min"] } },
  { key: "extras", title: "Costs page extras",
    features: ["tariff_simulator", "engine_compare"] },
];
// which group of the Config page each topic sits in (anything unlisted, such as "Other", is with the equipment)
const TOPIC_GROUP = { battery: "equipment", grid: "equipment", solar: "equipment", car: "equipment", cold: "equipment",
  control: "equipment", tariff: "tariff", selling: "tariff", axle: "tariff", free: "tariff", extras: "tools" };
const HOUSE_GROUPS = [["equipment", "Your equipment"], ["tariff", "Tariff and events"], ["tools", "Tools and messages"]];
// needed before PowerEngine can go live (the rest of the control group is optional)
const GO_LIVE = ["timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour", "timed_charge_end_minute",
  "timed_charge_current", "timed_discharge_start_hour", "timed_discharge_start_minute", "timed_discharge_end_hour",
  "timed_discharge_end_minute", "timed_discharge_current", "timed_update_button", "storage_mode", "guard_read_only"];
const NEEDED_FOR = { axle: "axle", free_power: "free_power_days" };
const ROLE_FEATURE = { smart_target_soc: "smart_charge_optimisation", smart_target_time: "smart_charge_optimisation" };

/** The lines under the "Overnight window" choice: what the plan is using now, and what PowerEngine has learned from the
 *  rates. `state` is sensor.pe_diag_overnight (app 0.9.105+); null when it isn't there, so an older app shows nothing. */
function overnightReadout(state) {
  const a = (state && state.attributes) || null;
  if (!a || !state.state || state.state === "unknown" || state.state === "unavailable") return null;
  const fixed = a.source === "fixed";
  const learned = !a.learned || a.learned === "none yet" ? "nothing yet" : a.learned;
  const days = Number(a.learned_days) || 0;
  const lines = [{ label: "In use now", text: `${state.state} (${fixed ? "your fixed times" : "learned"})` },
    { label: "Learned from the rates", text: days ? `${learned} (from ${days} day${days === 1 ? "" : "s"} of rates)` : learned }];
  const notes = [];
  if (a.fixed_not_valid) notes.push("The fixed times don't make a window (the start and end must differ), so the learned window is being used.");
  if (!fixed && days < 2) notes.push("It needs at least two full days of published rates before it can tell the regular overnight rate from a smart-charge slot at the same time.");
  return { lines, notes };
}

/** Where every role, setting and feature goes: TOPICS with anything unlisted gathered into "Other". */
function topicPlan(roleKeys, settingKeys, featureKeys, systemKeys) {
  const used = { roles: new Set(), settings: new Set(), features: new Set(), system: new Set() };
  const topics = TOPICS.map((t) => {
    const pick = (list, have, kind) => (list || []).filter((k) => have.includes(k) && !used[kind].has(k) && used[kind].add(k));
    return Object.assign({}, t, {
      roles: pick(t.roles, roleKeys, "roles"), settings: pick(t.settings, settingKeys, "settings"),
      features: pick(t.features, featureKeys, "features"), learning: pick(t.learning, featureKeys, "features"),
      system: pick(t.system, systemKeys, "system"),
      damp: t.damp && { note: t.damp.note, features: pick(t.damp.features, featureKeys, "features"), settings: pick(t.damp.settings, settingKeys, "settings") } });
  });
  const other = { key: "other", title: "Other",
    roles: roleKeys.filter((k) => !used.roles.has(k)), settings: settingKeys.filter((k) => !used.settings.has(k)),
    features: featureKeys.filter((k) => !used.features.has(k)), learning: [],
    system: systemKeys.filter((k) => !used.system.has(k) && k !== "publisher") };     // "publisher" is in Operation
  if (other.roles.length || other.settings.length || other.features.length || other.system.length) topics.push(other);
  return topics;
}

/** How much a role matters right now: "req" (required), "cond" (required only for something not in use),
 *  "opt" (optional) or "unused"; with the badge text. */
function roleNeed(role, draft, pairMapped, other) {
  const r = effectiveRole(role, pairMapped);
  const features = (draft && draft.features) || {};
  const live = ((draft && draft.operation) || {}).mode === "active";
  if (r.required === "unused") return { level: "unused", badge: "Not used" };
  if (r.required === "yes") return { level: "req", badge: "Required" };
  const f = NEEDED_FOR[r.required] || ROLE_FEATURE[r.key];
  if (f) {
    const label = T((FEATURES.find((x) => x[0] === f) || [0, f])[1]);
    return features[f] ? { level: "req", badge: `Required for ${label}` } : { level: "cond", badge: `Needed for ${label}` };
  }
  if (GUARD_ROLES.includes(r.key) && guardRolesShown(other).length === 0) return { level: "unused", badge: "Not used" };
  if (GO_LIVE.includes(r.key)) return live ? { level: "req", badge: "Required to go live" } : { level: "cond", badge: "Needed to go live" };
  return { level: "opt", badge: "Optional" };
}

/** Does a row's text match the search (every word, any order, ignoring case)? */
function matchesSearch(text, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  const t = String(text || "").toLowerCase();
  return words.every((w) => t.includes(w));
}

function instantProblem(role, spec, stateObj) {
  if (!spec) return role.required === "yes" ? "Required" : "";
  if (spec.value !== undefined) {
    if (!role.static_ok) return "Must be an entity";
    return toNumber(spec.value) === null ? "Enter a number" : "";
  }
  const id = spec.entity || "";
  if (!id) return role.required === "yes" ? "Required" : "";
  const domain = id.split(".")[0];
  if (role.kind === "control" && /bump|boost/i.test(id)) return "Never allowed: bump/boost charging";
  if (!(role.domains || ["sensor"]).includes(domain)) return `Must be a ${(role.domains || ["sensor"]).join(" or ")} entity`;
  if (!stateObj) return "Entity not found";
  if (["unavailable"].includes(stateObj.state)) return "Entity is unavailable";
  const attr = spec.attribute || role.attribute;
  if (attr && !(attr in (stateObj.attributes || {}))) return `No '${attr}' attribute`;
  const allowed = UNITS[role.kind];
  if (allowed && !attr) {
    const unit = (stateObj.attributes || {}).unit_of_measurement;
    if (!allowed.includes(unit)) return `Unit is ${unit || "none"}, expected ${allowed.join(" or ")}`;
  }
  return "";
}

/** Problem with a numeric setting, or "". */
function settingProblem(setting, value) {
  const n = toNumber(value);
  if (n === null) return "Enter a number";
  if (n < setting.min || n > setting.max) return `Must be between ${setting.min} and ${setting.max}`;
  return "";
}

function suggestEntity(role, entityIds) {
  const pats = (role.suggest || []).map((p) => new RegExp(p));
  const nots = (role.suggest_not || []).map((p) => new RegExp(p));
  return entityIds.find((id) => pats.some((p) => p.test(id)) && !nots.some((p) => p.test(id))) || "";
}

function settingDefaults(list) {
  const out = {};
  (list || []).forEach((s) => { out[s.key] = s.default; });
  return out;
}

/** A true/false that reached us as the text "true"/"false" (or 1/0) is the boolean it means; anything else is left alone. */
function asBool(v) {
  if (typeof v === "string") { const t = v.trim().toLowerCase(); if (t === "true") return true; if (t === "false") return false; }
  else if (v === 1 || v === 0) return v === 1;
  return v;
}
function asBools(obj) {
  const out = {};
  Object.entries(obj || {}).forEach(([k, v]) => { out[k] = asBool(v); });
  return out;
}

function initialDraft(saved, roles, entityIds, settings) {
  const draft = JSON.parse(JSON.stringify(saved || {}));
  settings = settings || {};
  draft.inputs = draft.inputs || {};
  draft.features = Object.assign({}, FEATURE_DEFAULTS, asBools(draft.features));
  draft.operation = Object.assign({ mode: "passive" }, draft.operation || {});
  draft.safety = Object.assign(settingDefaults(settings.safety), draft.safety || {});
  const system = Object.assign({}, draft.system || {});
  Object.keys(system).forEach((k) => { if (typeof system[k] === "string" || typeof system[k] === "number") system[k] = system[k] === "true" || system[k] === "false" ? asBool(system[k]) : system[k]; });
  draft.system = Object.assign(settingDefaults(settings.system), system);
  const n = draft.notifications || {};
  const events = {};
  NOTIFY_EVENTS.forEach(([k, , , d]) => { events[k] = (n.events || {})[k] !== undefined ? !!asBool(n.events[k]) : d; });
  // no choice saved yet: HA's notification area (the app's default); "off" = none
  if (n.channels || n.rules || n.reminder_hours) draft.notifications = routerDraft(n);       // saved as channels and rules
  else draft.notifications = { service: n.service === undefined ? "persistent_notification" : (n.service || "off"), events, alexa: alexaDraft(n.alexa) };
  const fresh = !saved || !saved.inputs || !Object.keys(saved.inputs).length;
  if (fresh) {
    roles.forEach((r) => {
      if (r.kind === "static" && r.suggest_static !== undefined) draft.inputs[r.key] = { value: r.suggest_static };
      else {
        const id = suggestEntity(r, entityIds);
        if (id) draft.inputs[r.key] = { entity: id };
      }
    });
    if (!draft.solar_plants || !draft.solar_plants.length) {
      const find = (pats) => entityIds.find((id) => pats.some((p) => p.test(id))) || "";
      draft.solar_plants = [{
        id: "main", name: "Main", forecast: "solcast_site",
        power: { entity: find(MAIN_PLANT_SUGGEST.power) },
        energy_today: { entity: find(MAIN_PLANT_SUGGEST.energy_today) },
      }];
    }
  }
  draft.solar_plants = draft.solar_plants || [];
  return { draft, fresh };
}

function slugify(name, taken) {
  let base = String(name || "plant").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 20) || "plant";
  if (!/^[a-z]/.test(base)) base = "p_" + base;
  let id = base, n = 2;
  while (taken.includes(id)) id = `${base}_${n++}`;
  return id;
}

/** "(18.08 kWh measured)" / "(not measured yet)" for a measurable input, from PowerEngine's diagnostic sensor. */
function measuredText(hass, key) {
  const src = {
    battery_capacity: ["sensor.pe_diag_battery_capacity", (st) => `${Number(st.state).toFixed(2)} kWh`],
    battery_round_trip: ["sensor.pe_diag_battery_efficiency", (st) => st.attributes.measured_round_trip != null
      ? `${Number(st.attributes.measured_round_trip).toFixed(1)}%` : null],
  }[key];
  const learnedKw = (field) => {
    const st = hass && hass.states["sensor.pe_diag_learned"];
    const v = st && st.attributes && st.attributes.raw ? st.attributes.raw[field] : null;
    return v != null ? `${Number(v).toFixed(2)} kW` : null;
  };
  if (key === "battery_max_charge_power" || key === "battery_max_discharge_power") {
    const text = learnedKw(key === "battery_max_charge_power" ? "max_charge_kw" : "max_discharge_kw");
    return text ? `(${text} measured)` : "(not measured yet)";
  }
  const st = hass && src && hass.states[src[0]];
  const text = st && st.attributes && st.attributes.measured ? src[1](st) : null;
  return text ? `(${text} measured)` : "(not measured yet)";
}

/** The config object to save: drop empty inputs, keep everything else. */
function buildConfig(draft) {
  const inputs = {};
  Object.entries(draft.inputs || {}).forEach(([k, spec]) => {
    if (!spec) return;
    if (spec.value !== undefined && spec.value !== "") {
      inputs[k] = { value: toNumber(spec.value) ?? spec.value };
      if (typeof spec.use_measured === "boolean") inputs[k].use_measured = spec.use_measured;
    }
    else if (spec.entity) inputs[k] = spec.invert ? { entity: spec.entity, invert: true } : { entity: spec.entity };
  });
  const plants = (draft.solar_plants || []).filter((p) => p.power && p.power.entity && p.energy_today && p.energy_today.entity)
    .map((p) => ({ id: p.id, name: p.name || p.id, power: { entity: p.power.entity }, energy_today: { entity: p.energy_today.entity },
      forecast: p.forecast || "none", enabled: p.enabled !== false }));
  const out = { schema_version: 1, inputs, solar_plants: plants, features: draft.features, operation: { mode: (draft.operation || {}).mode || "passive" } };
  const safety = {};
  Object.entries(draft.safety || {}).forEach(([k, v]) => { const n = toNumber(v); if (n !== null) safety[k] = n; });
  if (Object.keys(safety).length) out.safety = safety;
  if (draft.system && Object.keys(draft.system).length) out.system = Object.assign({}, draft.system);
  if (draft.notifications && draft.notifications.channels) out.notifications = routerConfig(draft.notifications);
  else if (draft.notifications && draft.notifications.service) {
    out.notifications = { service: draft.notifications.service, events: Object.assign({}, draft.notifications.events), alexa: alexaDraft(draft.notifications.alexa) };
  }
  if (draft.site) out.site = draft.site;      // only set when the app publishes site_options
  if (Array.isArray(draft.devices)) out.devices = buildDevices(draft.devices);   // only set when the app supports devices
  if (Array.isArray(draft.free_power_loads)) out.free_power_loads = buildFreeLoads(draft.free_power_loads);   // only set when the app supports them
  if (draft.engine_v2 && typeof draft.engine_v2 === "object") {      // only set when the app publishes the v2 settings
    const f = engineFields(true, (draft.system || {}).engine, draft.engine_v2);
    if (f.system) out.system = Object.assign({}, out.system, f.system);
    if (f.engine_v2) out.engine_v2 = f.engine_v2;
    if (typeof draft.v2_profile === "string" && draft.v2_profile) out.v2_profile = draft.v2_profile;      // only set when the app has profiles
    if (draft.v2_profiles && Object.keys(draft.v2_profiles).length) out.v2_profiles = draft.v2_profiles;
  }
  if (draft.remove_entities) out.remove_entities = true;
  return out;
}

/* ------------------------------------------------------------ "Your system" block (step 8b)
 * The app publishes sensor.pe_diag_version attributes site, site_options, firmware_detected and retest_required.
 * Option names are data from site_options; labels here are neutral words or <<term>> placeholders. */
const SITE_KINDS = [
  ["inverter", "Inverter"], ["inverter_firmware", "Firmware"], ["ev_charger", "Car charger"], ["car", "Car"],
  ["tariff", "Electricity tariff"], ["forecast", "Solar forecast"], ["events", "Grid events"],
];
const SITE_WARNING = "Changing the inverter switches PowerEngine to Passive. Run the supervised tests on the Tests page before going Active again.";
const SITE_RETEST = "Inverter changed: run the supervised tests on the Tests page before going Active.";
const SITE_UNKNOWN_FW = "Not listed / unknown";
const SITE_NOT_TESTED = "Not fully tested";

/** The site attributes the section works from, or null when the app is too old to publish site_options (section hidden). */
function siteInfo(attrs) {
  const a = attrs || {};
  const opts = a.site_options;
  if (!opts || typeof opts !== "object" || Array.isArray(opts) || !Object.keys(opts).length) return null;
  const site = a.site && typeof a.site === "object" ? a.site : {};
  return { options: opts, site, detected: a.firmware_detected || null, retest: a.retest_required === true };
}

function siteRows(options, kind) { return Array.isArray((options || {})[kind]) ? options[kind] : []; }
function siteRow(options, kind, id) { return siteRows(options, kind).find((r) => r.id === id) || null; }

/** Firmware ids offered for an inverter (the "Not listed / unknown" choice, null, is added by the card). */
function siteFirmwareOptions(options, inverterId) {
  const row = siteRow(options, "inverter", inverterId);
  return row && Array.isArray(row.firmware_variants) ? row.firmware_variants.slice() : [];
}

/** Which variant a firmware value means: null is the inverter's default (first listed); a value not listed is its own variant. */
function siteVariant(options, inverterId, fw) {
  const list = siteFirmwareOptions(options, inverterId);
  if (fw === null || fw === undefined || fw === "") return list.length ? list[0] : null;
  return list.includes(fw) ? fw : `other:${fw}`;
}

/** The site to save, from the selections. Missing kinds keep the saved value. */
function siteFromSelection(sel, fallback) {
  const s = Object.assign({}, fallback || {}, sel || {});
  const out = {};
  SITE_KINDS.forEach(([k]) => { out[k] = s[k] === undefined || s[k] === "" ? null : s[k]; });
  return out;
}

/** Selection after the inverter dropdown changes: keep the firmware only if the new inverter lists it, else unknown. */
function siteChooseInverter(options, sel, id) {
  const fw = sel.inverter_firmware;
  return Object.assign({}, sel, { inverter: id, inverter_firmware: fw && siteFirmwareOptions(options, id).includes(fw) ? fw : null });
}

/** Warn when the inverter changes, or the firmware's variant changes (app rule: it switches to Passive and needs a retest). */
function siteNeedsWarning(options, saved, next) {
  const a = saved || {}, b = next || {};
  if ((a.inverter || null) !== (b.inverter || null)) return true;
  return siteVariant(options, a.inverter, a.inverter_firmware) !== siteVariant(options, b.inverter, b.inverter_firmware);
}

/** The "Detected: X" line: a match note, or the pick-from-the-list hint when the inverter can't say. */
function siteDetectedLine(detected, chosen) {
  if (!detected) return "Firmware not readable from the inverter; pick it from the list";
  if (!chosen) return `Detected: ${detected} (no firmware chosen below)`;
  return chosen === detected ? `Detected: ${detected} (matches your choice)` : `Detected: ${detected} (differs from your choice, ${chosen})`;
}

/** Option label: name, plus the status when it is not verified. */
function siteOptionLabel(row) { return row.status && row.status !== "verified" ? `${row.name} (${row.status})` : row.name; }

/* ------------------------------------------------------------ Switches to run during free power
 * `free_power_loads` in the app's config (app 0.9.191 and newer): each switch is turned on when a free-electricity session
 * starts and off when it ends. Entries are {entity, name}; only switches and input booleans can be turned on and off. */
const FREE_LOADS_APP_VERSION = "0.9.191";
const FREE_LOAD_DOMAINS = ["switch", "input_boolean"];
function freeLoadsSupported(appVersion) { return versionOlder(appVersion, FREE_LOADS_APP_VERSION) === false; }

/** The saved list as an editable draft: every entry an {entity, name} (the config also allows a bare entity id). */
function freeLoadDraft(saved) {
  return (Array.isArray(saved) ? saved : []).map((x) => (typeof x === "string" ? { entity: x, name: "" } : { entity: (x && x.entity) || "", name: (x && x.name) || "" }));
}

/** The `free_power_loads` list to save: rows with an entity, each listed once. */
function buildFreeLoads(list) {
  const seen = new Set();
  return (list || []).filter((x) => x && x.entity && !seen.has(x.entity) && seen.add(x.entity))
    .map((x) => (x.name && x.name.trim() ? { entity: x.entity, name: x.name.trim() } : { entity: x.entity }));
}

/* ------------------------------------------------------------ Other devices (multiple-devices plan, M1)
 * Read-only devices beyond the main inverter: the app (0.9.93 and newer) reads their battery and solar, publishes one sensor per
 * mapped input (sensor.pe_state_dev_<id>_soc / _battery_power / _solar_power) and counts their solar. Nothing is controlled. */
const DEVICES_APP_VERSION = "0.9.93";
const DEVICE_INPUTS = [          // [input key, label, can be inverted]
  ["battery_soc", "Battery state of charge (%)", false],
  ["battery_power", "Battery power (W); + discharging, - charging", true],
  ["solar_power", "Solar power (W)", false],
];
const DEVICE_NOTE = "Read only: PowerEngine measures these and counts their solar, but only the inverter chosen above is controlled.";

function devicesSupported(appVersion) { return versionOlder(appVersion, DEVICES_APP_VERSION) === false; }

/** The saved devices as an editable draft (a copy with every field present). */
function deviceDraft(saved) {
  return (Array.isArray(saved) ? saved : []).map((d) => ({
    id: d.id, adapter: d.adapter, name: d.name || d.id, firmware: d.firmware || "", control: "read_only",
    inputs: JSON.parse(JSON.stringify(d.inputs || {})),
  }));
}

function deviceNewId(name, taken) { return slugify(name, ["main"].concat(taken || [])); }

/** The `devices` list to save: drop a device with no adapter, and inputs with no entity. */
function buildDevices(list) {
  return (list || []).filter((d) => d && d.id && d.adapter).map((d) => {
    const inputs = {};
    DEVICE_INPUTS.forEach(([key, , invertible]) => {
      const spec = (d.inputs || {})[key];
      if (spec && spec.entity) inputs[key] = invertible && spec.invert ? { entity: spec.entity, invert: true } : { entity: spec.entity };
    });
    const out = { id: d.id, adapter: d.adapter, name: d.name || d.id, control: "read_only", inputs };
    if (d.firmware) out.firmware = d.firmware;
    return out;
  });
}

/** One line of live readings for a device, from the sensors the app publishes for it. */
function deviceReadout(states, dev) {
  const num = (field) => {
    const st = (states || {})[`sensor.pe_state_dev_${dev.id}_${field}`];
    const n = st ? parseFloat(st.state) : NaN;
    return Number.isFinite(n) ? n : null;
  };
  if (!Object.keys(dev.inputs || {}).some((k) => dev.inputs[k] && dev.inputs[k].entity)) return "No inputs chosen yet";
  const parts = [];
  const soc = num("soc"), bp = num("battery_power"), pv = num("solar_power");
  if (soc !== null) parts.push(`battery ${Math.round(soc)}%`);
  if (bp !== null) parts.push(Math.abs(bp) < 10 ? "battery idle" : `battery ${bp > 0 ? "discharging" : "charging"} ${Math.round(Math.abs(bp))} W`);
  if (pv !== null) parts.push(`solar ${Math.round(pv)} W`);
  return parts.length ? parts.join(", ") : "Waiting for the first reading (save, then PowerEngine reads it within a minute)";
}

/* --------------------------------------------------------------------- card */

async function ensureEntityPicker() {
  if (typeof customElements === "undefined") return false;
  if (customElements.get("ha-entity-picker")) return true;
  try {
    const helpers = await window.loadCardHelpers();
    const card = await helpers.createCardElement({ type: "entities", entities: [] });
    if (card && card.constructor && card.constructor.getConfigElement) await card.constructor.getConfigElement();
  } catch (e) { /* fall back to a plain input */ }
  await Promise.race([customElements.whenDefined("ha-entity-picker"), new Promise((r) => setTimeout(r, 3000))]);
  return !!customElements.get("ha-entity-picker");
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  });
  children.flat().forEach((c) => { if (c !== null && c !== undefined) node.append(c instanceof Node ? c : document.createTextNode(String(c))); });
  return node;
}

// --- PowerEngine's Home Assistant package (app 0.9.109+) --------------------------------------------------------
// The app writes its package files into <config>/packages/ and publishes sensor.pe_diag_package: state ok |
// reload_needed | no_packages_dir | unmanaged | error; attributes {files, reload_needed, message}. Older apps publish
// nothing, so the card shows nothing about it.
const PACKAGE_SENSOR = "sensor.pe_diag_package";
const PACKAGE_BANNER_TEXT = "PowerEngine has updated its Home Assistant package.";
const PACKAGE_BUTTON = "Load PowerEngine's Home Assistant changes";
const PACKAGE_ASK_ADMIN = "Ask an administrator to load PowerEngine's Home Assistant changes.";
const PACKAGE_PROBLEMS = ["no_packages_dir", "unmanaged", "error"];
const INSTALL_GUIDE_URL = "https://github.com/durkimat/ha-powerengine-controller/blob/main/docs/INSTALL.md";

/** What the app publishes, or null when there is no sensor (older app). */
function packageInfo(states) {
  const st = (states || {})[PACKAGE_SENSOR];
  if (!st || st.state === "unavailable" || st.state === "unknown") return null;
  const a = st.attributes || {};
  return { state: String(st.state), reloadNeeded: a.reload_needed === true || st.state === "reload_needed",
    message: typeof a.message === "string" ? a.message : "", files: Array.isArray(a.files) ? a.files : [] };
}

/** The service call the button makes: a reload of everything, no restart. */
function packageReloadPayload() { return { domain: "homeassistant", service: "reload_all", data: {} }; }

/** Should the card prompt for a reload straight after a save? Only when the app publishes the sensor and the
 *  other controller was changed (the value sent differs from the one the app was using). */
function packagePromptAfterSave(info, sentValue, usedValue) {
  if (!info) return false;
  if (typeof sentValue !== "string" || !sentValue) return false;
  return sentValue !== usedValue;
}

/** The banner: {show, text, button (label or null), busy, error}. ctx: {info, isAdmin, forced, status, error}.
 *  status: "" | "loading" | "loaded" | "error". It leaves by itself when the app reports ok. */
function packageBannerView(ctx) {
  const c = ctx || {};
  const info = c.info || null;
  const off = { show: false, text: "", button: null, busy: false, error: "" };
  if (!info) return off;
  const wanted = info.reloadNeeded || (!!c.forced && !PACKAGE_PROBLEMS.includes(info.state));
  if (!wanted) return off;
  if (info.state === "ok" && c.status === "loaded") return off;
  if (!c.isAdmin) return { show: true, text: `${PACKAGE_BANNER_TEXT} ${PACKAGE_ASK_ADMIN}`, button: null, busy: false, error: "" };
  if (c.status === "loading") return { show: true, text: PACKAGE_BANNER_TEXT, button: "Loading…", busy: true, error: "" };
  if (c.status === "loaded") return { show: true, text: PACKAGE_BANNER_TEXT, button: "Loaded", busy: true, error: "" };
  return { show: true, text: PACKAGE_BANNER_TEXT, button: PACKAGE_BUTTON, busy: false, error: c.status === "error" ? (c.error || "Could not load.") : "" };
}

/** A line for the states the app can't fix itself: {text, href, label}, or null for ok / reload_needed / no sensor. */
function packageProblemLine(info) {
  if (!info || !PACKAGE_PROBLEMS.includes(info.state)) return null;
  const fallback = { no_packages_dir: "Home Assistant has no packages folder for PowerEngine to write to.",
    unmanaged: "PowerEngine's Home Assistant package was set up by hand, so PowerEngine leaves it alone.",
    error: "PowerEngine couldn't write its Home Assistant package." }[info.state];
  return { text: info.message || fallback, href: INSTALL_GUIDE_URL, label: "Install guide" };
}

/** The banner as a DOM node with an update(hass, forced) method; one per card. */
function makePackageBox() {
  const box = document.createElement("div");
  box.style.cssText = "display:none;margin:8px 0;padding:8px 12px;border-radius:6px;background:rgba(249,168,37,.15);align-items:center;gap:10px;flex-wrap:wrap";
  const text = document.createElement("span");
  text.style.cssText = "flex:1 1 220px";
  const err = document.createElement("div");
  err.style.cssText = "flex:1 1 100%;color:var(--error-color,#db4437);font-size:.9em";
  const btn = document.createElement("button");
  btn.style.cssText = "font:inherit;padding:6px 14px;border-radius:6px;border:none;background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff);cursor:pointer";
  box.append(text, btn, err);
  const st = { status: "", error: "", hass: null, forced: false };
  const draw = () => {
    const hass = st.hass;
    const v = packageBannerView({ info: packageInfo(hass && hass.states), isAdmin: !!(hass && hass.user && hass.user.is_admin),
      forced: st.forced, status: st.status, error: st.error });
    box.style.display = v.show ? "flex" : "none";
    text.textContent = v.text;
    btn.style.display = v.button ? "" : "none";
    btn.textContent = v.button || "";
    btn.disabled = v.busy;
    err.textContent = v.error;
    err.style.display = v.error ? "" : "none";
  };
  btn.addEventListener("click", async () => {
    if (st.status === "loading" || !st.hass) return;
    st.status = "loading"; st.error = ""; draw();
    try {
      const p = packageReloadPayload();
      await st.hass.callService(p.domain, p.service, p.data);
      st.status = "loaded";
    } catch (e) { st.status = "error"; st.error = "Could not load: " + ((e && e.message) || e); }
    draw();
  });
  box.update = (hass, forced) => {
    st.hass = hass;
    if (forced !== undefined) st.forced = !!forced;
    const info = packageInfo(hass && hass.states);
    if (st.status === "loaded" && info && info.state === "ok") { st.status = ""; st.forced = false; }
    draw();
  };
  return box;
}

class PowerEngineConfigCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._rows = [];
    this._pickers = [];
  }

  getCardSize() { return 12; }

  set hass(hass) {
    this._hass = hass;
    setNames(hass);
    (this._pickers || []).forEach((p) => { p.hass = hass; });
    if (!this._built) this._maybeBuild();
    else this._refresh();
  }

  async _maybeBuild() {
    if (this._building) return;
    const s = this._hass.states;
    const ver = s[VERSION_SENSOR];
    const cat = catalogueAttrs(s, CATALOGUE_SENSOR);
    if (!ver || ver.state === "unavailable" || !cat || !cat.roles) {
      this._renderMessage("PowerEngine app not detected. Check it is installed and AppDaemon's MQTT plugin is set up (install guide, Step 3).");
      return;
    }
    this._building = true;
    this._usePicker = await ensureEntityPicker();
    // roles leave out "required" when it is "yes" (keeps the published catalogue under HA's 16 KB limit)
    this._catalogue = Object.assign({}, cat,
      { roles: (cat.roles || []).map((r) => Object.assign({ required: "yes" }, r)) });
    const mapping = s[MAPPING_SENSOR];
    this._saved = ((mapping && mapping.attributes) || {}).config || {};
    // settings moved to their own sensor in app 0.5.3 (older apps sent them inside the catalogue)
    this._settings = this._catalogue.settings || catalogueAttrs(s, SETTINGS_SENSOR) || {};
    this._readOnly = !(this._hass.user && this._hass.user.is_admin);
    const { draft, fresh } = initialDraft(this._saved, this._catalogue.roles, Object.keys(s).sort(), this._settings);
    this._site = siteInfo(ver.attributes);
    this._siteSaved = this._site ? siteFromSelection(this._site.site) : null;
    if (this._site) draft.site = Object.assign({}, this._siteSaved); else delete draft.site;
    this._devices = devicesSupported(ver.state);
    if (this._devices) draft.devices = deviceDraft(draft.devices); else delete draft.devices;
    this._freeLoads = freeLoadsSupported(ver.state);
    this._whatsapp = whatsappSupported(ver.state);
    withWhatsapp(draft, this._whatsapp);
    this._router = routerSupported(ver.state);
    withRouter(draft, this._router);
    if (this._freeLoads) draft.free_power_loads = freeLoadDraft(draft.free_power_loads); else delete draft.free_power_loads;
    this._v2 = v2Supported(s) ? v2WithProfiles(s) : null;
    if (this._v2) {
      draft.engine_v2 = Object.assign({}, this._saved.engine_v2 || {}); draft.system.engine = uiEngine(this._v2, engineInUse(s, this._saved)); this._engine = draft.system.engine;
      Object.assign(draft, profileDraft(this._v2, this._saved));
    } else { delete draft.engine_v2; delete draft.v2_profile; delete draft.v2_profiles; this._engine = null; }
    // no choice saved yet: the draft shows what the app is using (derived from the mapped guards), or carries none when
    // that is "unset", so the catalogue's empty default is never shown as a real choice or saved by accident
    if (!((this._saved.system || {}).other_controller)) {
      const used = otherControllerValue(s);
      if (used && used !== "unset") draft.system.other_controller = used; else delete draft.system.other_controller;
    }
    this._draft = draft;
    this._prefilled = fresh;
    this._build();
    this._built = true;
    this._building = false;
    this._subscribe();
  }

  async _subscribe() {
    try {
      this._unsub = await this._hass.connection.subscribeEvents((ev) => {
        const d = ev.data || {};
        this._saving = false;
        if (d.ok && this._pkgPending) { this._pkgForced = true; if (this._pkgBox) this._pkgBox.update(this._hass, true); }
        this._pkgPending = false;
        this._setBanner(d.ok ? "ok" : "error", d.ok ? `Saved. PowerEngine is reloading. ${d.message || ""}` : `Not saved: ${d.message}`);
        this._refresh();
      }, RESULT_EVENT);
    } catch (e) { /* non-admin users can't subscribe; saving is admin-only anyway */ }
    try {
      this._unsubTest = await this._hass.connection.subscribeEvents((ev) => {
        const d = ev.data || {};
        clearTimeout(this._testTimer);
        this._setTestStatus(d.ok ? "ok" : "error", d.message || (d.ok ? "Played." : "Could not play."));
      }, ALEXA_TEST_RESULT);
    } catch (e) { /* the test button is admin-only too */ }
    try {
      this._unsubNotify = await this._hass.connection.subscribeEvents((ev) => {
        const d = ev.data || {};
        clearTimeout(this._notifyTestTimer);
        const status = (this._chanStatus || {})[this._testingChannel];
        if (status) { status.textContent = d.message || (d.ok ? "Sent." : "Could not send."); status.className = d.ok ? "muted" : "warning"; }
      }, NOTIFY_TEST_RESULT);
    } catch (e) { /* admin only */ }
  }

  _setTestStatus(kind, text) {
    if (!this._testStatus) return;
    this._testStatus.textContent = text;
    this._testStatus.className = kind === "error" ? "warning" : "muted";
  }

  /** Play "<opening words> Test message." on the ticked speakers now, whatever the allowed hours say. */
  async _alexaTest() {
    const payload = alexaTestPayload(this._draft);
    if (!payload.services.length && !(payload.whatsapp || []).length) { this._setTestStatus("error", "Tick at least one speaker, or switch on WhatsApp with a chat, first."); return; }
    this._setTestStatus("ok", "Sending…");
    clearTimeout(this._testTimer);
    this._testTimer = setTimeout(() => this._setTestStatus("error",
      "No reply from PowerEngine. Check it is running version 0.9.175 or later, then look at the AppDaemon log."), ALEXA_TEST_WAIT_MS);
    try {
      await this._hass.callWS({ type: "fire_event", event_type: ALEXA_TEST_EVENT, event_data: payload });
    } catch (e) {
      clearTimeout(this._testTimer);
      this._setTestStatus("error", `Could not send the test: ${e && e.message ? e.message : e}`);
    }
  }

  /** Send "Test message." to one channel as it is set on the page (saved or not), whatever its hours say. */
  async _notifyTest(name) {
    const ch = this._draft.notifications.channels[name];
    const status = (this._chanStatus || {})[name];
    const say = (kind, text) => { if (status) { status.textContent = text; status.className = kind === "error" ? "warning" : "muted"; } };
    const problem = routerProblem({ channels: { [name]: ch } });
    if (problem) { say("error", problem); return; }
    say("ok", "Sending…");
    this._testingChannel = name;
    clearTimeout(this._notifyTestTimer);
    this._notifyTestTimer = setTimeout(() => say("error", `No reply from PowerEngine. Check it is running version ${ROUTER_APP_VERSION} or later, then look at the AppDaemon log.`), ALEXA_TEST_WAIT_MS);
    try {
      await this._hass.callWS({ type: "fire_event", event_type: NOTIFY_TEST_EVENT, event_data: { channel: routerChannelConfig(ch) } });
    } catch (e) {
      clearTimeout(this._notifyTestTimer);
      say("error", `Could not send the test: ${e && e.message ? e.message : e}`);
    }
  }

  /** The Notifications section for channels and rules: the channels, then the table of what goes where, then the
   *  reminder times and what has been sent lately. */
  _notifyRouterUi(nb, track) {
    const n = this._draft.notifications;
    const attrs = ((this._hass.states || {})[NOTIFY_SENSOR] || {}).attributes || {};
    const names = () => Object.keys(n.channels);
    this._chanStatus = {};
    const channelNodes = [];                                   // the channels go below the table of what goes where
    const field = (label, desc, ...ctl) => el("div", { class: "row sub" }, el("div", { class: "head" }, el("span", { class: "label" }, label)), desc ? el("div", { class: "desc" }, desc) : null, el("div", { class: "ctl" }, ...ctl));
    const timeInput = (ch, key) => {
      const input = el("input", { type: "time", onchange: (ev) => { if (TIME_OF_DAY.test(ev.target.value)) { ch.hours[key] = ev.target.value; this._refresh(); } } });
      input.value = ch.hours[key] === "24:00" ? "23:59" : ch.hours[key];
      return input;
    };
    // --- the channels
    names().forEach((name) => {
      const ch = n.channels[name];
      const typeName = (CHANNEL_TYPES.find(([t]) => t === ch.type) || [])[1] || ch.type;
      const label = el("input", { type: "text", size: 22, maxlength: 40, onchange: (ev) => { ch.label = ev.target.value.trim() || name; this._build(); } });
      label.value = ch.label;
      const remove = el("button", { onclick: () => {
        delete n.channels[name];
        n.rules = n.rules.map((r) => ({ events: r.events, to: r.to.filter((c) => c !== name) })).filter((r) => r.to.length);
        this._build();
      } }, "Remove");
      const box = el("div", { class: "row channel" }, el("div", { class: "head" }, el("span", { class: "label" }, label), el("span", { class: "badge sw" }, typeName), remove));
      if (ch.type === "bell") box.append(el("div", { class: "desc" }, "Shown in Home Assistant's notification area (the bell). Nothing to set up; each one clears itself when the problem is over."));
      if (ch.type === "notify") {
        const services = Object.keys((this._hass.services || {}).notify || {}).filter((x) => !/^alexa_media_/.test(x)).sort();
        const sel = el("select", { onchange: (ev) => { ch.service = ev.target.value; this._refresh(); } }, el("option", { value: "" }, "Choose…"),
          services.map((x) => el("option", { value: `notify.${x}` }, `notify.${x}`)));
        if (ch.service && !services.includes(ch.service.slice(7))) sel.append(el("option", { value: ch.service }, `${ch.service} (not found)`));
        sel.value = ch.service;
        box.append(field("Notify service", "Usually notify.mobile_app_<phone name> for the companion app.", sel));
      }
      if (ch.type === "alexa") {
        const speakers = Object.keys((this._hass.services || {}).notify || {}).filter((x) => /^alexa_media_/.test(x) && x !== "alexa_media_last_called").sort();
        ch.services.forEach((x) => { if (!speakers.includes(x.slice(7))) speakers.push(x.slice(7)); });
        const kinds = alexaSpeakerKinds(speakers, this._hass.devices);
        const pick = (x) => {
          const cb = el("input", { type: "checkbox", onchange: (ev) => {
            const set = new Set(ch.services); ev.target.checked ? set.add(`notify.${x}`) : set.delete(`notify.${x}`);
            ch.services = [...set]; this._refresh(); } });
          cb.checked = ch.services.includes(`notify.${x}`);
          return el("label", { class: "head" }, cb, el("span", { class: "label" }, alexaSpeakerName(`notify.${x}`)));
        };
        const picks = el("div", { class: "ctl" });
        if (!speakers.length) picks.append(el("span", { class: "muted" }, "No Alexa speakers found. Install Alexa Media Player in Home Assistant."));
        if (kinds.speakers.length) picks.append(el("div", { class: "muted" }, "Single speakers"), ...kinds.speakers.map(pick));
        if (kinds.groups.length) picks.append(el("div", { class: "muted" }, "Speaker groups (each plays on all of its speakers)"), ...kinds.groups.map(pick));
        box.append(el("div", { class: "row sub" }, el("div", { class: "head" }, el("span", { class: "label" }, "Speakers")),
          el("div", { class: "desc" }, "Played on every speaker ticked. Don't tick a group as well as a speaker that is in it, or that speaker says it twice. \"Everywhere\" is all of them."), picks));
      }
      if (ch.type === "whatsapp") {
        const input = el("input", { type: "text", size: 34, placeholder: "120363012345678901@g.us", onchange: (ev) => {
          const list = parseWhatsappTargets(ev.target.value);
          if (list) { ch.targets = list; ev.target.classList.remove("bad"); } else ev.target.classList.add("bad");
          this._refresh(); } });
        input.value = ch.targets.join(", ");
        box.append(field("Chats", "A group id (find it with the whatsapp.search_groups action) or a phone number with its country code; separate several with commas. Needs the WhatsApp integration in Home Assistant.", input));
      }
      const intro = el("input", { type: "text", size: 30, maxlength: 80, onchange: (ev) => { ch.intro = ev.target.value.trim(); this._refresh(); } });
      intro.value = ch.intro;
      box.append(field("Opening words", "Put at the start of every message to this channel, so the household knows who it is from. Leave empty for none.", intro));
      const hoursOn = el("input", { type: "checkbox", onchange: (ev) => {
        ch.hours = ev.target.checked ? { weekday_from: "07:30", weekend_from: "09:00", until: "21:00" } : null; this._build(); } });
      hoursOn.checked = !!ch.hours;
      box.append(el("div", { class: "row sub" }, el("label", { class: "head" }, hoursOn, el("span", { class: "label" }, "Only send between certain hours")),
        el("div", { class: "desc" }, "Outside them a message is held and sent when they open (marked as held), or skipped.")));
      if (ch.hours) {
        const outside = el("select", { onchange: (ev) => { ch.outside_hours = ev.target.value; this._refresh(); } },
          el("option", { value: "hold" }, "Hold it until the hours open"), el("option", { value: "skip" }, "Skip it"));
        outside.value = ch.outside_hours;
        box.append(el("div", { class: "row sub" }, el("div", { class: "ctl" }, "Weekdays from ", timeInput(ch, "weekday_from"), " weekends from ", timeInput(ch, "weekend_from"), " until ", timeInput(ch, "until"))),
          field("Outside those hours", "", outside));
      }
      const status = el("span", { class: "muted" });
      this._chanStatus[name] = status;
      box.append(el("div", { class: "row sub" }, el("div", { class: "ctl" }, el("button", { onclick: () => this._notifyTest(name) }, "Send a test message"), " ", status)));
      channelNodes.push(track(box, "setting", `notifications channel ${ch.label} ${typeName} opening words hours hold skip test`));
    });
    // add a channel
    const typeSel = el("select", {}, CHANNEL_TYPES.map(([t, text]) => el("option", { value: t }, text)));
    channelNodes.push(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Add a channel")),
      el("div", { class: "ctl" }, typeSel, el("button", { onclick: () => {
        const type = typeSel.value, name = newChannelName(type, n.channels);
        n.channels[name] = routerChannel(type, name, { label: (CHANNEL_TYPES.find(([t]) => t === type) || [])[1] });
        this._build();
      } }, "Add"))), "setting", "notifications add a channel"));
    // --- what goes where
    const kinds = notifyKinds(attrs.kinds, n.rules);
    const cells = routeCells(n.rules);
    const table = el("table", { class: "matrix" });
    if (!names().length) nb.append(el("div", { class: "desc" }, "Add a channel to choose what goes where."));
    else {
      table.append(el("thead", {}, el("tr", {}, el("th", {}, "Send"), names().map((name) => el("th", {}, n.channels[name].label)))));
      const body = el("tbody", {});
      let group = null;
      kinds.forEach((k) => {
        if (k.source !== group) { group = k.source; body.append(el("tr", { class: "group" }, el("th", { colspan: String(names().length + 1) }, k.source === "powerengine" ? "PowerEngine" : `From ${k.source}`))); }
        const row = el("tr", {}, el("td", { title: k.desc || "" }, k.label));
        names().forEach((name) => {
          const cb = el("input", { type: "checkbox", onchange: (ev) => {
            const next = routeCells(n.rules);
            ev.target.checked ? next.add(`${k.kind}|${name}`) : next.delete(`${k.kind}|${name}`);
            n.rules = routesFromCells(next, names(), kinds.map((x) => x.kind));
            this._refresh(); } });
          cb.checked = cells.has(`${k.kind}|${name}`);
          row.append(el("td", {}, cb));
        });
        body.append(row);
      });
      table.append(body);
      nb.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "What goes where")),
        el("div", { class: "desc" }, "Tick the channels each kind of message goes to. A message from elsewhere (the family dashboard, an automation) appears here once it has been sent for the first time."),
        el("div", { class: "scroll" }, table)), "setting", `notifications what goes where rules ${kinds.map((k) => k.label).join(" ")}`));
    }
    const hoursInput = el("input", { type: "text", size: 14, onchange: (ev) => { const h = parseHoursBefore(ev.target.value); if (h) n.reminder_hours = h; this._refresh(); } });
    hoursInput.value = n.reminder_hours.join(", ");
    nb.append(track(field("Grid event reminders: hours before", "When each reminder is sent, in hours before the event starts; 0 is as it starts.", hoursInput),
      "setting", "notifications grid event reminders hours before times"));
    nb.append(el("h4", { class: "sub" }, "Channels"), ...channelNodes);
    // --- what has been sent lately
    this._notifyLog = el("div", { class: "desc" });
    nb.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Recent messages")), this._notifyLog), "setting", "notifications recent messages held log"));
    this._updateNotifyLog();
  }

  /** The held count and the last few deliveries, from the app's published state. */
  _updateNotifyLog() {
    if (!this._notifyLog || !this._draft || !this._draft.notifications || !this._draft.notifications.channels) return;
    const attrs = ((this._hass.states || {})[NOTIFY_SENSOR] || {}).attributes || {};
    const labels = {}; Object.entries(this._draft.notifications.channels).forEach(([k, c]) => { labels[k] = c.label; });
    const held = Array.isArray(attrs.held) ? attrs.held : [];
    const log = Array.isArray(attrs.log) ? attrs.log.slice(-8).reverse() : [];
    const when = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
    const lines = [];
    if (held.length) lines.push(el("div", {}, `${held.length} held until a channel's hours open: ${held.map((h) => `${h.title} (${labels[h.channel] || h.channel})`).join(", ")}.`));
    if (!log.length && !held.length) lines.push(el("div", { class: "muted" }, "Nothing sent yet."));
    log.forEach((e) => lines.push(el("div", { class: "muted" }, `${when(e.at)}: ${e.title} → ${labels[e.channel] || e.channel}, ${e.outcome}${e.note ? ` (${e.note})` : ""}`)));
    this._notifyLog.replaceChildren(...lines);
  }

  disconnectedCallback() {
    if (this._unsub) { this._unsub(); this._unsub = null; }
    if (this._unsubTest) { this._unsubTest(); this._unsubTest = null; }
    if (this._unsubNotify) { this._unsubNotify(); this._unsubNotify = null; }
    clearTimeout(this._testTimer);
    clearTimeout(this._notifyTestTimer);
  }

  _renderMessage(text) {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = "";
    this.shadowRoot.append(this._style(), el("ha-card", { header: "PowerEngine configuration" }, el("div", { class: "content" }, el("p", {}, text))));
  }

  _style() {
    return el("style", {}, `
      .content { padding: 0 16px 16px; }
      .banner { padding: 8px 12px; border-radius: 6px; margin: 8px 0; }
      .banner.info { background: var(--secondary-background-color); }
      .banner.ok { background: rgba(67,160,71,.15); }
      .banner.error { background: rgba(219,68,55,.15); }
      h3 { margin: 20px 0 4px; font-size: 1.05em; }
      .row { border-top: 1px solid var(--divider-color); padding: 10px 0; }
      .row.channel { border-left-color: var(--primary-color); background: var(--secondary-background-color, transparent); border-radius: 4px; margin: 8px 0; }
      .row.sub { border-top: 0; padding: 4px 0; }
      .row.channel .head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
      .scroll { overflow-x: auto; }
      table.matrix { border-collapse: collapse; margin-top: 6px; font-size: .92em; }
      table.matrix th, table.matrix td { padding: 4px 6px; text-align: center; border-bottom: 1px solid var(--divider-color); }
      table.matrix td:first-child, table.matrix thead th:first-child { text-align: left; }
      table.matrix thead th { max-width: 84px; overflow-wrap: break-word; font-size: .9em; }
      table.matrix td:first-child { max-width: 150px; }
      table.matrix tr.group th { text-align: left; font-size: .8em; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); padding-top: 10px; }
      .head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
      .label { font-weight: 500; }
      .badge { font-size: .75em; padding: 1px 6px; border-radius: 8px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.req { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .desc { color: var(--secondary-text-color); font-size: .9em; margin: 2px 0 6px; }
      .ctl { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
      .ctl ha-entity-picker, .ctl input[type=text] { flex: 1 1 260px; min-width: 200px; }
      input[type=text], input[type=number], select { padding: 6px; border-radius: 4px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
      .live { font-size: .9em; margin-top: 4px; }
      .reads { color: var(--primary-color); margin-left: 6px; }
      .sign { font-size: .85em; color: var(--secondary-text-color); }
      .problem { color: var(--error-color); font-size: .9em; }
      .warning { color: var(--warning-color, #b26a00); font-size: .9em; margin-top: 4px; }
      .status { font-size: .85em; color: var(--secondary-text-color); }
      .status.ok { color: var(--success-color, #43a047); }
      .plant { border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px; margin: 8px 0; }
      .actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; position: sticky; bottom: 0; background: var(--card-background-color); padding: 8px 0; }
      button { padding: 8px 14px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); cursor: pointer; }
      button.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border: none; }
      button:disabled { opacity: .5; cursor: default; }
      .muted { color: var(--secondary-text-color); font-size: .85em; }
      .suggest { font-size: .85em; color: var(--secondary-text-color); margin-top: 4px; }
      .tools { display: flex; gap: 12px; justify-content: flex-end; margin-bottom: 4px; }
      button.link { background: none; border: none; padding: 2px 0; color: var(--primary-color); cursor: pointer; font-size: .9em; }
      details.section { border: 1px solid var(--divider-color); border-radius: 8px; margin: 8px 0; }
      details.section > summary { cursor: pointer; padding: 10px 12px; display: flex; gap: 8px; align-items: baseline; list-style: none; }
      details.section > summary::-webkit-details-marker { display: none; }
      details.section > summary::before { content: "▸"; color: var(--secondary-text-color); transition: transform .15s; display: inline-block; }
      details.section[open] > summary::before { transform: rotate(90deg); }
      details.section > summary .title { font-weight: 500; font-size: 1.05em; }
      details.section .count { margin-left: auto; font-size: .85em; color: var(--secondary-text-color); }
      details.section .count.bad { color: var(--error-color); }
      details.section > .body { padding: 0 12px 8px; }
      h4 { margin: 8px 0 4px; }
      h4.sub { margin: 14px 0 2px; font-size: .8em; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
      .toolbar { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 8px; }
      input.search { width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 18px; }
      .chips { display: flex; gap: 6px; flex-wrap: wrap; }
      .chip { padding: 3px 10px; border-radius: 14px; font-size: .85em; }
      .chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
      .toolrow { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
      .toolrow .tools { margin: 0; }
      button.summary { background: none; border: none; padding: 2px 0; font-weight: 500; text-align: left; }
      button.summary.good { color: var(--success-color, #43a047); cursor: default; }
      button.summary.bad { color: var(--error-color, #db4437); }
      .nomatch { padding: 8px 0; }
      .row { border-left: 4px solid transparent; padding-left: 10px; }
      .row.plain { border-left: none; padding-left: 0; }
      .row.req-ok { border-left-color: var(--success-color, #43a047); }
      .row.req-bad { border-left-color: var(--error-color, #db4437); background: rgba(219, 68, 55, .06); }
      .row.cond { border-left-color: var(--warning-color, #ffa000); }
      .row.opt, .row.unused { border-left-color: var(--divider-color); }
      .row.unused { opacity: .6; }
      .row.feature { border-left-color: var(--primary-color); }
      .badge.req { background: var(--error-color, #db4437); color: #fff; }
      .row.req-ok .badge.req { background: var(--success-color, #43a047); }
      .badge.cond { background: rgba(255, 160, 0, .18); color: var(--warning-color, #b26a00); }
      .badge.opt { background: none; border: 1px solid var(--divider-color); }
      .badge.sw { background: none; border: 1px solid var(--primary-color); color: var(--primary-color); }
      details.section .count.good { color: var(--success-color, #43a047); }
      details.section.off > summary .title { color: var(--secondary-text-color); }
      .offnote { color: var(--secondary-text-color); font-style: italic; font-size: .9em; margin: 6px 0; }
      details.section.off .row:not(.feature) { opacity: .55; }
      .yoursystem { border: 1px solid var(--divider-color); border-radius: 8px; padding: 4px 12px 10px; margin: 8px 0; }
      .yoursystem h3 { margin: 8px 0 4px; }
      .yoursystem .alsolist { margin: 2px 0; padding-left: 20px; }
      .sysgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 16px; }
      .sysgrid .field { padding: 6px 0; }
      .sysgrid select { width: 100%; box-sizing: border-box; min-height: 40px; }
      .enginebox { border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 12px 12px; margin: 8px 0; }
      .enginepick { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-top: 6px; }
      .engineopt { border: 1px solid var(--divider-color); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 2px;
        text-align: left; background: var(--card-background-color); color: var(--primary-text-color); }
      .engineopt[aria-pressed="true"] { border-color: var(--primary-color); box-shadow: 0 0 0 1px var(--primary-color); }
      .engineopt small { color: var(--secondary-text-color); }
      .engineconfirm { border: 1px solid var(--primary-color); background: rgba(3, 169, 244, .1); border-radius: 10px; padding: 10px 12px; margin-top: 10px; display: flex; flex-direction: column; gap: 10px; }
      .engineconfirm .btns, .profilebox .btns { display: flex; gap: 8px; flex-wrap: wrap; }
      .profilebox { margin-top: 12px; border-top: 1px solid var(--divider-color); padding-top: 8px; }
      .profilebox .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0; }
      .groups { display: flex; flex-direction: column; }
      .group.dim { opacity: .6; }
      .groupchip:empty { display: none; }
      .grouphead { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin: 14px 0 2px; }
      details.group > summary.grouphead { cursor: pointer; list-style: none; justify-content: flex-start; }
      details.group > summary.grouphead::-webkit-details-marker { display: none; }
      details.group > summary.grouphead::before { content: "▸"; color: var(--secondary-text-color); transition: transform .15s; display: inline-block; }
      details.group[open] > summary.grouphead::before { transform: rotate(90deg); }
      .grouphead h3 { margin: 0; }
      .groupchip { font-size: .8em; padding: 1px 10px; border-radius: 999px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .groupchip.on { background: rgba(3, 169, 244, .14); color: var(--primary-color); }
      .readline { background: var(--secondary-background-color); border-radius: 8px; padding: 8px 10px; font-size: .9em; margin: 6px 0; }
      .badge.status-verified { border: 1px solid var(--success-color, #43a047); color: var(--success-color, #43a047); background: none; }
      .badge.status-community, .badge.status-draft { background: rgba(255, 160, 0, .18); color: var(--warning-color, #b26a00); }
    `);
  }

  /** The switches PowerEngine turns on while free power is on offer: a row each (switch, optional name, remove) and an add button. */
  _freeLoadsEditor() {
    const box = el("div", { class: "row freeloads" });
    const draw = () => {
      box.innerHTML = "";
      this._pickers = (this._pickers || []).filter((p) => p.isConnected !== false);
      box.append(el("div", { class: "desc" }, "Each switch is turned on when a free-electricity session starts and off when it ends. A switch that is already on is left alone, and one you turn off yourself stays off for that session. Applies in Active mode only, and needs the free-session inputs above (it works even with the switch above off)."));
      this._draft.free_power_loads.forEach((load, i) => {
        const name = el("input", { type: "text", value: load.name || "", placeholder: "Name (optional)", disabled: this._readOnly,
          onchange: (ev) => { load.name = ev.target.value; this._refresh(); } });
        const pick = this._entityInput(load.entity, FREE_LOAD_DOMAINS, (v) => { load.entity = v; this._refresh(); }, "Switch");
        if (pick.disabled !== undefined) pick.disabled = this._readOnly;
        box.append(el("div", { class: "ctl" }, pick, name,
          el("button", { disabled: this._readOnly, onclick: () => { this._draft.free_power_loads.splice(i, 1); draw(); this._refresh(); } }, "Remove")));
      });
      box.append(el("div", { class: "ctl" }, el("button", { disabled: this._readOnly,
        onclick: () => { this._draft.free_power_loads.push({ entity: "", name: "" }); draw(); } }, "Add a switch")));
    };
    draw();
    return box;
  }

  _entityInput(value, domains, onChange, label) {
    if (this._usePicker) {
      const p = document.createElement("ha-entity-picker");
      p.hass = this._hass;
      p.value = value || "";
      p.includeDomains = domains;
      p.allowCustomEntity = true;
      if (label) p.label = label;
      p.addEventListener("value-changed", (ev) => onChange(ev.detail.value || ""));
      this._pickers.push(p);
      return p;
    }
    const listId = `pe-list-${Math.random().toString(36).slice(2)}`;
    const ids = Object.keys(this._hass.states).filter((id) => domains.includes(id.split(".")[0])).sort();
    const input = el("input", { type: "text", value: value || "", list: listId, placeholder: label || "entity id", onchange: (ev) => onChange(ev.target.value.trim()) });
    return [input, el("datalist", { id: listId }, ids.map((id) => el("option", { value: id })))];
  }

  _build() {
    const root = this.shadowRoot;
    root.innerHTML = "";
    this._rows = [];
    this._pickers = [];
    const content = el("div", { class: "content" });
    this._banner = el("div", { class: "banner info" });
    this._pkgBox = makePackageBox();
    content.append(this._pkgBox);
    content.append(this._banner);
    this._pkgBox.update(this._hass, this._pkgForced);
    const pkgProblem = packageProblemLine(packageInfo(this._hass && this._hass.states));
    if (pkgProblem) content.append(el("div", { class: "warning" }, pkgProblem.text + " ",
      el("a", { href: pkgProblem.href, target: "_blank", rel: "noopener noreferrer" }, pkgProblem.label)));
    this._setBanner("info", this._readOnly
      ? "View only: log in as an admin to change PowerEngine's configuration."
      : this._prefilled
        ? "Suggested entities have been pre-filled from your system. Check each live value below, then Save."
        : "Change any input, check its live value, then Save.");
    this._v2Rows = [];
    this._v2Readouts = [];
    this._groups = {};
    if (this._v2) { this._engineBox = el("div", { class: "enginebox" }); content.append(this._engineBox); this._renderEngineBox(); }
    else this._engineBox = null;

    // search, filters and the overall status
    this._items = [];                                      // every row, for search and filters
    this._query = this._query || "";
    this._filter = this._filter || "all";
    const search = el("input", { type: "search", class: "search", placeholder: "Search settings and inputs (name, description or entity)",
      value: this._query, oninput: (ev) => { this._query = ev.target.value; this._applyFilter(); } });
    const chips = el("div", { class: "chips" });
    [["all", "All"], ["attention", "Needs attention"], ["req", "Required"], ["opt", "Optional"]].forEach(([k, label]) => {
      chips.append(el("button", { class: `chip${this._filter === k ? " on" : ""}`, "data-k": k, onclick: () => {
        this._filter = k;
        chips.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.k === k));
        this._applyFilter();
      } }, label));
    });
    this._summary = el("button", { class: "summary", onclick: () => this._jumpToProblem() });
    this._noMatch = el("div", { class: "muted nomatch" }, "Nothing matches.");
    const tools = el("div", { class: "tools" },
      el("button", { class: "link", onclick: () => this._toggleAll(true) }, "Expand all"),
      el("button", { class: "link", onclick: () => this._toggleAll(false) }, "Collapse all"));
    content.append(el("div", { class: "toolbar" }, search, chips, el("div", { class: "toolrow" }, this._summary, tools)), this._noMatch);

    // collapsible sections (open state kept across rebuilds and page loads)
    this._sections = {};
    let host = content;                                    // sections go here (a group's box once the groups start)
    const section = (key, title, note) => {
      const count = el("span", { class: "count" });
      const body = el("div", { class: "body" });
      const d = el("details", { class: "section" }, el("summary", {}, el("span", { class: "title" }, title), count), body);
      d.open = this._openSections().has(key);
      d.addEventListener("toggle", () => { if (!this._filtering) this._rememberOpen(key, d.open); });
      if (note) body.append(el("div", { class: "desc" }, note));
      this._sections[key] = { details: d, count, problems: 0, unsaved: 0, req: 0, reqOk: 0, opt: 0, body };
      this._currentSection = key;
      host.append(d);
      return body;
    };
    const track = (node, kind, text) => {
      node.dataset.kind = kind;
      this._items.push({ node, kind, text: text.toLowerCase(), section: this._currentSection });
      return node;
    };
    const featureRow = (key) => {
      const f = FEATURES.find((x) => x[0] === key);
      if (!f) return null;
      const [, rawLabel, rawDesc, rawWarning] = f;
      const [label, desc, warning] = [T(rawLabel), T(rawDesc), rawWarning && T(rawWarning)];
      const cb = el("input", { type: "checkbox", onchange: (ev) => { this._draft.features[key] = ev.target.checked; this._refresh(); } });
      cb.checked = !!this._draft.features[key];
      return track(el("div", { class: "row feature" }, el("label", { class: "head" }, cb, el("span", { class: "label" }, label),
        el("span", { class: "badge sw" }, "Switch")), el("div", { class: "desc" }, desc),
        warning ? el("div", { class: "warning" }, `⚠ ${warning}`) : null), "feature", `${label} ${desc} ${key}`);
    };
    const systemRow = (key) => {
      const st = (this._settings.system || []).find((x) => x.key === key);
      if (!st) return null;
      if (st.options) return track(this._choiceRow(st), "setting", `${st.label} ${st.help} ${key}`);
      const cb = el("input", { type: "checkbox", onchange: (ev) => { this._draft.system[st.key] = ev.target.checked; this._refresh(); } });
      cb.checked = !!this._draft.system[st.key];
      return track(el("div", { class: "row" }, el("label", { class: "head" }, cb, el("span", { class: "label" }, st.label)),
        el("div", { class: "desc" }, st.help)), "setting", `${st.label} ${st.help} ${key}`);
    };
    const sub = (body, title) => { const h = el("h4", { class: "sub" }, title); body.append(h); return h; };

    // operation
    let body = section("operation", "Operation");
    const activeWarn = el("div", { class: "warning" }, T("⚠ Live: PowerEngine writes the inverter's timed charge and discharge settings (and, with smart-charge optimisation on, asks <<supplier>> for slots). It only goes live when every handover guard is safe. Normally set by the Battery controller panel above; pause any time from the Monitoring tab."));
    const modeSel = el("select", { onchange: (ev) => {
      this._draft.operation.mode = ev.target.value;
      activeWarn.style.display = ev.target.value === "active" ? "" : "none";
      this._refresh();
    } },
      el("option", { value: "passive" }, "Passive: monitor and simulate, never control"),
      el("option", { value: "active" }, "Active: PowerEngine controls the inverter"));
    modeSel.value = this._draft.operation.mode === "active" ? "active" : "passive";
    activeWarn.style.display = modeSel.value === "active" ? "" : "none";
    body.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Operation mode")),
      el("div", { class: "ctl" }, modeSel), activeWarn), "setting", "operation mode passive active live"));
    const publisher = systemRow("publisher");
    if (publisher) body.append(publisher);

    // one section per topic
    const roles = this._catalogue.roles;
    const byKey = Object.fromEntries(roles.map((r) => [r.key, r]));
    const allSettings = this._settings.safety || [];
    const settingByKey = Object.fromEntries(allSettings.map((x) => [x.key, x]));
    this._settingRows = [];
    this._useRows = [];
    const grouping = engineGrouping(roles.map((r) => r.key), allSettings.map((x) => x.key), FEATURES.map((f) => f[0]),
      (this._settings.system || []).map((x) => x.key), !!this._v2);
    const renderTopic = (t) => {
      const body = section(`topic_${t.key}`, T(t.title), t.note && T(t.note));
      this._sections[`topic_${t.key}`].main = t.main;
      t.features.forEach((k) => { const r = featureRow(k); if (r) body.append(r); });
      if (t.main) body.append(this._sections[`topic_${t.key}`].offNote = el("div", { class: "offnote" },
        "Switched off: the inputs and settings below aren't used."));
      const guardsOk = guardRolesShown(this._otherEffective());
      const inTopic = t.roles.map((k) => byKey[k]).filter(Boolean).filter((r) => !GUARD_ROLES.includes(r.key) || guardsOk.includes(r.key));
      const pair = BATTERY_PAIR.every((k) => (this._draft.inputs[k] || {}).entity);
      const need = (r) => roleNeed(r, this._draft, pair, this._otherEffective()).level;
      const firstly = inTopic.filter((r) => need(r) !== "opt");
      const optional = inTopic.filter((r) => need(r) === "opt");
      const suggestable = inTopic.filter((r) => this._suggestion(r));
      if (suggestable.length > 1 && !this._readOnly) {
        body.append(el("div", { class: "row plain" }, el("button", { onclick: () => this._useSuggestions(suggestable) },
          `Use all ${suggestable.length} suggested entities`), el("span", { class: "muted" }, " (then check each and Save)")));
      }
      if (firstly.length) {
        sub(body, t.key === "control" ? "Needed to go live" : "Inputs");
        firstly.forEach((r) => body.append(this._roleRow(r)));
      }
      if (optional.length) {
        sub(body, "Optional inputs");
        optional.forEach((r) => body.append(this._roleRow(r)));
      }
      const sets = t.settings.map((k) => settingByKey[k]).filter(Boolean);
      if (sets.length || t.system.length) {
        sub(body, "Settings");
        t.system.forEach((k) => {
          const r = systemRow(k);
          if (r && k === "other_controller") {
            const prompt = otherControllerPrompt(this._otherEffective());
            if (prompt) body.append(el("div", { class: "warning" }, prompt));
          }
          if (r) body.append(r);
          if (r && k === "overnight_window") { const o = this._overnightRow(); if (o) body.append(o); }
        });
        sets.forEach((st) => body.append(track(this._settingRow(st), "setting", `${st.label} ${st.help} ${st.key}`)));
      }
      if (t.learning.length) {
        sub(body, "Learning");
        t.learning.forEach((k) => { const r = featureRow(k); if (r) body.append(r); });
      }
      if (t.damp && (t.damp.features.length || t.damp.settings.length)) {
        sub(body, "Dampening tuning");
        body.append(el("div", { class: "desc" }, T(t.damp.note)), el("div", { class: "note" }, dampingNote(this._hass && this._hass.states)));
        t.damp.features.forEach((k) => { const r = featureRow(k); if (r) body.append(r); });
        t.damp.settings.map((k) => settingByKey[k]).filter(Boolean)
          .forEach((st) => body.append(track(this._settingRow(st), "setting", `${st.label} ${st.help} ${st.key}`)));
      }
      if (t.key === "free" && this._freeLoads) {
        sub(body, "Switches to run during free power");
        body.append(this._freeLoadsEditor());
      }
    };
    // notifications: channels and rules (app 0.9.195+), or HA's notification area / a phone / off (older apps). They sit
    // in "Your house" when the engine groups are shown, else after the topics.
    const renderNotifications = () => {
      if (this._draft.notifications.channels) {
        const nb = section("notifications", "Notifications and announcements",
          "Where messages go, and which ones go where. A channel is a place a message can be sent: Home Assistant's notification area, a phone, the Alexa speakers or WhatsApp.");
        this._notifyRouterUi(nb, track);
      } else {
        const nb = section("notifications", "Notifications",
          "Shown in Home Assistant's notification area (the bell) by default, or sent to your phone through the companion app.");
        const services = Object.keys((this._hass.services || {}).notify || {}).sort();
        const svcSel = el("select", { onchange: (ev) => { this._draft.notifications.service = ev.target.value; this._refresh(); } },
          el("option", { value: "persistent_notification" }, "Home Assistant notification area"),
          services.map((s) => el("option", { value: `notify.${s}` }, `Phone: notify.${s}`)),
          el("option", { value: "off" }, "Off (no notifications)"));
        const curSvc = this._draft.notifications.service || "off";
        if (curSvc.startsWith("notify.") && !services.includes(curSvc.slice(7))) svcSel.append(el("option", { value: curSvc }, `${curSvc} (not found)`));
        svcSel.value = curSvc;
        nb.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Send to")),
          el("div", { class: "desc" }, "Notification area: nothing to set up; each one clears itself when the problem is over. Phone: your notify service, usually notify.mobile_app_<phone name>."), el("div", { class: "ctl" }, svcSel)),
          "setting", "notifications send to notify service phone notification area bell"));
        NOTIFY_EVENTS.filter(([key]) => key !== "alexa").forEach(([key, rawLabel, rawDesc]) => {   // Alexa has its own section below
          const [label, desc] = [T(rawLabel), T(rawDesc)];
          const cb = el("input", { type: "checkbox", onchange: (ev) => { this._draft.notifications.events[key] = ev.target.checked; this._refresh(); } });
          cb.checked = !!this._draft.notifications.events[key];
          nb.append(track(el("div", { class: "row" }, el("label", { class: "head" }, cb, el("span", { class: "label" }, label)), el("div", { class: "desc" }, desc)),
            "setting", `notification ${label} ${desc}`));
        });
        // Announcements: spoken reminders on Alexa speakers. Their own heading and section, apart from the engine settings
        // above (they work with either engine) and from Home Assistant's notification area.
        if (!grouping.grouped) {
          host = content;
          content.append(el("div", { class: "grouphead" }, el("h3", {}, "Announcements")));
        }
        const ab = section("announcements", "Alexa announcements",
          "Spoken reminders on your Alexa speakers. They work with either engine, and are separate from the notifications above.");
        const alexa = this._draft.notifications.alexa;
        const [, alexaLabel, alexaDesc] = NOTIFY_EVENTS.find(([k]) => k === "alexa");
        const onBox = el("input", { type: "checkbox", onchange: (ev) => { this._draft.notifications.events.alexa = ev.target.checked; this._refresh(); } });
        onBox.checked = !!this._draft.notifications.events.alexa;
        ab.append(track(el("div", { class: "row" }, el("label", { class: "head" }, onBox, el("span", { class: "label" }, T(alexaLabel))), el("div", { class: "desc" }, T(alexaDesc))),
          "setting", `alexa announcements switch on off ${alexaLabel} ${alexaDesc}`));
        const speakers = Object.keys((this._hass.services || {}).notify || {}).filter((s) => /^alexa_media_/.test(s) && s !== "alexa_media_last_called").sort();
        alexa.services.forEach((s) => { if (!speakers.includes(s.slice(7))) speakers.push(s.slice(7)); });
        const kinds = alexaSpeakerKinds(speakers, this._hass.devices);
        const pick = (s) => {
          const cb = el("input", { type: "checkbox", onchange: (ev) => {
            const set = new Set(alexa.services); ev.target.checked ? set.add(`notify.${s}`) : set.delete(`notify.${s}`);
            alexa.services = [...set]; this._refresh(); } });
          cb.checked = alexa.services.includes(`notify.${s}`);
          return el("label", { class: "head" }, cb, el("span", { class: "label" }, alexaSpeakerName(`notify.${s}`)));
        };
        const picks = el("div", { class: "ctl" });
        if (!speakers.length) picks.append(el("span", { class: "muted" }, "No Alexa speakers found. Install Alexa Media Player in Home Assistant."));
        if (kinds.speakers.length) picks.append(el("div", { class: "muted" }, "Single speakers"), ...kinds.speakers.map(pick));
        if (kinds.groups.length) picks.append(el("div", { class: "muted" }, "Speaker groups (each plays on all of its speakers)"), ...kinds.groups.map(pick));
        ab.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Speakers")),
          el("div", { class: "desc" }, "Announcements play on every speaker ticked. Don't tick a group as well as a speaker that is in it, or that speaker says it twice. \"Everywhere\" is all of them. A speaker added to Alexa Media Player later appears here after the page is reloaded."), picks),
          "setting", "alexa announcements speakers groups devices echo"));
        if (this._whatsapp) {
          const waBox = el("input", { type: "checkbox", onchange: (ev) => { alexa.whatsapp = ev.target.checked; this._refresh(); } });
          waBox.checked = alexa.whatsapp === true;
          const waInput = el("input", { type: "text", size: 34, placeholder: "120363012345678901@g.us", onchange: (ev) => {
            const list = parseWhatsappTargets(ev.target.value);
            if (list) { alexa.whatsapp_targets = list; ev.target.classList.remove("bad"); } else ev.target.classList.add("bad");
            this._refresh(); } });
          waInput.value = (alexa.whatsapp_targets || []).join(", ");
          ab.append(track(el("div", { class: "row" }, el("label", { class: "head" }, waBox, el("span", { class: "label" }, "Also send them to WhatsApp")),
            el("div", { class: "desc" }, "Sends the same words to WhatsApp at the same times, inside the same allowed hours. Needs the WhatsApp integration in Home Assistant. Give a group id (find it with the whatsapp.search_groups action) or a phone number with its country code; separate several with commas. Leave the speakers all unticked to use WhatsApp alone."),
            el("div", { class: "ctl" }, waInput)),
            "setting", "alexa announcements whatsapp group chat message send"));
        }
        this._testStatus = el("span", { class: "muted" });
        ab.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Test announcement")),
          el("div", { class: "desc" }, "Plays the opening words and \"Test message\" on the speakers ticked above, and sends it to WhatsApp if that is switched on, now, without saving first. It ignores the allowed hours."),
          el("div", { class: "ctl" }, el("button", { onclick: () => this._alexaTest() }, "Play a test announcement"), " ", this._testStatus)),
          "setting", "alexa announcements test announcement play button"));
        const timeRow = (label, desc, key) => {
          const input = el("input", { type: "time", onchange: (ev) => { if (TIME_OF_DAY.test(ev.target.value)) alexa[key] = ev.target.value; this._refresh(); } });
          input.value = alexa[key];
          return track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, label)), el("div", { class: "desc" }, desc), el("div", { class: "ctl" }, input)),
            "setting", `alexa announcements quiet hours ${label} ${desc}`);
        };
        ab.append(timeRow("Earliest on a weekday", "Nothing is announced before this on Monday to Friday.", "earliest_weekday"),
          timeRow("Earliest at the weekend", "Nothing is announced before this on Saturday or Sunday.", "earliest_weekend"),
          timeRow("Latest", "Nothing is announced from this time until the earliest time next morning.", "latest"));
        const introInput = el("input", { type: "text", size: 30, maxlength: 80, onchange: (ev) => { alexa.intro = ev.target.value.trim(); this._refresh(); } });
        introInput.value = alexa.intro;
        ab.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Opening words")),
          el("div", { class: "desc" }, "Said at the start of every announcement, so the household knows who it is from. Leave empty for none."), el("div", { class: "ctl" }, introInput)),
          "setting", "alexa announcements opening words intro name"));
        const hoursInput = el("input", { type: "text", size: 14, onchange: (ev) => { const h = parseHoursBefore(ev.target.value); if (h) alexa.hours_before = h; this._refresh(); } });
        hoursInput.value = alexa.hours_before.join(", ");
        ab.append(track(el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Hours before a grid event")),
          el("div", { class: "desc" }, "When each reminder is said, in hours before the event starts; 0 is as it starts. A reminder that falls outside the allowed hours is skipped."), el("div", { class: "ctl" }, hoursInput)),
          "setting", "alexa announcements hours before reminders times"));
        const finishBox = el("input", { type: "checkbox", onchange: (ev) => { alexa.finish = ev.target.checked; this._refresh(); } });
        finishBox.checked = alexa.finish;
        ab.append(track(el("div", { class: "row" }, el("label", { class: "head" }, finishBox, el("span", { class: "label" }, "Announce a summary when a grid event ends")),
          el("div", { class: "desc" }, "Says what the house used, what was exported, what the event should pay, and what was turned back (the heating set-back, the car charger) and to what. Only inside the allowed hours: after the latest time it is kept for the morning.")),
          "setting", "alexa announcements summary finish end of grid event"));
      }
    };
    if (!grouping.grouped) { grouping.house.forEach(renderTopic); renderNotifications(); }
    else {
      // Your equipment, Tariff and events, Tools and messages (both engines use them), then the settings of the engine in
      // use: Engine v1 or Engine v2 (the other one is hidden)
      const groupsBox = el("div", { class: "groups" });
      content.append(groupsBox);
      const startGroup = (key, title) => {
        const chip = el("span", { class: "groupchip" });
        const body = el("div", { class: "groupbody" });
        const wrap = el("details", { class: "group" }, el("summary", { class: "grouphead" }, el("h3", {}, title), chip), body);
        wrap.open = !this._openSections().has(`closed:${key}`);     // open unless closed before
        // remembered on a click of the heading only, so a search or a problem opening a group doesn't change what is kept
        wrap.firstChild.addEventListener("click", () => setTimeout(() => this._rememberOpen(`closed:${key}`, !wrap.open)));
        groupsBox.append(wrap);
        host = body;
        this._groups[key] = { wrap, chip, body };
      };
      HOUSE_GROUPS.forEach(([key, title]) => {
        startGroup(key, title);
        grouping.house.filter((t) => (TOPIC_GROUP[t.key] || "equipment") === key).forEach(renderTopic);
        if (key === "tools") renderNotifications();
      });
      startGroup("v1", "Engine v1 settings");
      grouping.v1.forEach((sec) => renderTopic(Object.assign({ roles: [], learning: [], system: [] }, sec)));
      startGroup("v2", "Engine v2 settings");
      this._renderV2Sections(section, track, sub);
      host = content;
    }

    this._currentSection = null;

    // actions
    this._saveBtn = el("button", { class: "primary", onclick: () => this._save() }, "Save");
    this._resetBtn = el("button", { onclick: () => { this._built = false; this._maybeBuild(); } }, "Discard changes");
    content.append(el("div", { class: "actions" }, el("span", { class: "muted" }, `Card v${CARD_VERSION}`), this._resetBtn, this._saveBtn));

    root.append(this._style(), el("ha-card", { header: "PowerEngine configuration" }, content));
    if (this._readOnly) {
      content.querySelectorAll("input:not(.search), select, button:not(.link):not(.chip):not(.summary)").forEach((n) => { n.disabled = true; });
      this._pickers.forEach((pk) => { pk.disabled = true; });
    }
    this._refresh();
    // open any section that needs attention
    Object.values(this._sections).forEach((s) => { if (s.problems) this._openWithGroup(s.details); });
    this._applyFilter();
  }

  /** The engine choice at the top: two buttons, and the same inline confirmation whichever way you switch. */
  _renderEngineBox() {
    const box = this._engineBox;
    if (!box) return;
    const cur = this._engine || engineInUse(this._hass.states, this._saved);
    const opt = (id, name, text) => el("button", { class: "engineopt", "aria-pressed": String(cur === id), disabled: this._readOnly || this._saving,
      onclick: () => { this._enginePending = cur === id ? null : id; this._renderEngineBox(); } }, el("b", {}, name), el("small", {}, text));
    box.replaceChildren(el("div", { class: "head" }, el("span", { class: "label" }, "Planning engine")),
      el("div", { class: "enginepick", role: "group", "aria-label": "Planning engine" },
        opt("v1", "Engine v1", "Half-hour plan, remade every 5 minutes and on changes."),
        opt("v2", "Engine v2", "Acts on conditions: a level reached, a price change, the sun falling short. Its settings come as profiles, below."),
        ...(!(this._v2 && this._v2.profiles) && ((this._v2 && Array.isArray(this._v2.engines) && this._v2.engines.includes("v2_1")) || cur === "v2_1")
          ? [opt("v2_1", "Engine 2.1 beta", "Engine v2 following its own plan, with a smooth cost keeping the battery off the very top and bottom. Beta.")] : [])));
    if (this._enginePending && this._enginePending !== cur) {
      const c = engineConfirm(cur, this._enginePending);
      box.append(el("div", { class: "engineconfirm" }, el("p", {}, el("b", {}, c.title), " ", c.text),
        el("div", { class: "btns" },
          el("button", { class: "primary", disabled: this._readOnly || this._saving, onclick: () => this._switchEngine(this._enginePending) }, c.confirm),
          el("button", { onclick: () => { this._enginePending = null; this._renderEngineBox(); } }, c.cancel))));
    }
    if (cur === "v2" && this._v2 && this._v2.profiles) box.append(this._profileView());
  }

  _profileSig() { return JSON.stringify([this._draft && this._draft.v2_profile, this._draft && this._draft.v2_profiles, this._profilePending, profileChanges(this._v2, (this._draft || {}).engine_v2)]); }

  /** The settings profile: which one is in use, whether you have changed it, and loading, saving and deleting. */
  _profileView() {
    const cat = this._v2, d = this._draft, saved = d.v2_profiles || {}, active = d.v2_profile;
    const names = profileList(cat, saved);
    const changed = profileChanges(cat, d.engine_v2);
    const busy = this._readOnly || this._saving;
    const pick = el("select", { disabled: busy, onchange: (ev) => { this._profilePending = ev.target.value === active ? null : { kind: "load", name: ev.target.value }; this._renderEngineBox(); } });
    names.forEach((n) => pick.append(el("option", { value: n.name }, n.builtin ? `${n.name} (built in)` : n.name)));
    pick.value = this._profilePending && this._profilePending.kind === "load" ? this._profilePending.name : active;
    const note = (names.find((n) => n.name === active) || {}).note;
    const isSaved = !!saved[active];
    const btn = (label, kind, extra) => el("button", Object.assign({ disabled: busy, onclick: () => { this._profilePending = { kind }; this._renderEngineBox(); } }, extra || {}), label);
    const row = el("div", { class: "btns" },
      isSaved && changed.length ? btn(`Save changes to “${active}”`, "update") : null,
      changed.length ? btn("Save as a new profile…", "new") : null,
      changed.length ? btn(`Revert to “${active}”`, "load", { onclick: () => { this._profilePending = { kind: "load", name: active }; this._renderEngineBox(); } }) : null,
      isSaved ? btn(`Delete “${active}”`, "delete") : null);
    const out = el("div", { class: "profilebox" },
      el("div", { class: "head" }, el("span", { class: "label" }, "Settings profile")),
      el("div", { class: "row" }, pick, el("span", { class: "desc" }, changed.length ? `Changed from “${active}”: ${changed.length} setting${changed.length === 1 ? "" : "s"}.` : (note || "Your saved profile."))),
      row);
    const p = this._profilePending;
    if (p) {
      let text, confirm, name = null, input = null;
      if (p.kind === "load") {
        text = `Loading “${p.name}” replaces the engine's tuning settings with the profile's. ${changed.length ? "Your changes since you loaded this profile are dropped (save them as a profile first to keep them). " : ""}Your reserve, the smart slot odds and what the engine may do stay as they are. Other unsaved edits on this page stay unsaved.`;
        confirm = `Load “${p.name}”`;
      } else if (p.kind === "delete") {
        text = `Delete the saved profile “${active}”? The engine goes back to the built-in defaults profile and keeps the changes you made on top.`;
        confirm = `Delete “${active}”`;
      } else if (p.kind === "update") {
        text = `Replace the saved profile “${active}” with its values plus your changes.`;
        confirm = `Save changes to “${active}”`;
      } else {
        input = el("input", { type: "text", placeholder: "Profile name", maxlength: String(cat.profiles.max_name || 30), value: p.text || "", oninput: (ev) => { p.text = ev.target.value; } });
        text = `Saves the values in use (“${active}” plus your changes) as a new profile and switches to it. Up to ${cat.profiles.max_saved || 10} profiles can be saved.`;
        confirm = "Save profile";
      }
      const go = () => {
        let fields;
        if (p.kind === "load") fields = profileLoadFields(cat, saved, d.engine_v2, p.name);
        else if (p.kind === "delete") fields = profileDeleteFields(cat, saved, active, d.engine_v2);
        else fields = profileSaveFields(cat, saved, active, d.engine_v2, p.kind === "update" ? active : (p.text || ""));
        if (fields.error) { this._setBanner("error", fields.error); return; }
        this._applyProfile(fields, p.kind === "load" ? `Loading “${p.name}”` : p.kind === "delete" ? "Deleting the profile" : "Saving the profile");
      };
      out.append(el("div", { class: "engineconfirm" }, el("p", {}, text), input,
        el("div", { class: "btns" }, el("button", { class: "primary", disabled: busy, onclick: go }, confirm),
          el("button", { onclick: () => { this._profilePending = null; this._renderEngineBox(); } }, "Cancel"))));
    }
    return out;
  }

  /** Sends the saved configuration with only the profile fields changed (other edits on this page stay unsaved drafts). */
  async _applyProfile(fields, what) {
    if (this._readOnly || this._saving) return;
    const base = this._baseDraft(this._saved || {});
    base.v2_profile = fields.v2_profile;
    base.v2_profiles = fields.v2_profiles;
    base.engine_v2 = fields.engine_v2;
    this._profilePending = null;
    this._saving = true;
    this._renderEngineBox();
    try {
      await this._hass.callWS({ type: "fire_event", event_type: SAVE_EVENT, event_data: { config: buildConfig(base) } });
      this._setBanner("info", `${what}: sent to PowerEngine; waiting for it to check and save…`);
      setTimeout(() => { if (this._saving) { this._saving = false; this._setBanner("info", SAVE_NO_REPLY); this._renderEngineBox(); this._refresh(); } }, SAVE_WAIT_MS);
    } catch (e) {
      this._saving = false;
      this._setBanner("error", `Could not send: ${e.message || e}. Saving needs an admin user.`);
      this._renderEngineBox();
    }
    this._refresh();
  }

  /** Sends the saved configuration with only the engine changed (other edits on this page stay unsaved drafts). */
  async _switchEngine(to) {
    if (this._readOnly || this._saving) return;
    const saved = this._saved || {};
    const base = this._baseDraft(saved);
    base.system.engine = to;
    this._enginePending = null;
    this._draft.system.engine = to;
    this._saving = true;
    this._renderEngineBox();
    try {
      await this._hass.callWS({ type: "fire_event", event_type: SAVE_EVENT, event_data: { config: buildConfig(base) } });
      this._setBanner("info", `Switching to ${engineName(to)}: sent to PowerEngine; waiting for it to check and save…`);
      setTimeout(() => { if (this._saving) { this._saving = false; this._setBanner("info", SAVE_NO_REPLY); this._renderEngineBox(); this._refresh(); } }, SAVE_WAIT_MS);
    } catch (e) {
      this._saving = false;
      this._draft.system.engine = engineInUse(this._hass.states, saved);
      this._setBanner("error", `Could not send: ${e.message || e}. Saving needs an admin user.`);
      this._renderEngineBox();
    }
    this._refresh();
  }

  /** The saved configuration as a draft (what the page is compared with, and what a switch sends). */
  _baseDraft(saved) {
    const base = initialDraft(saved, [], [], this._settings).draft;
    if (this._siteSaved) base.site = this._siteSaved; else delete base.site;
    if (this._devices) base.devices = deviceDraft(base.devices); else delete base.devices;
    if (this._freeLoads) base.free_power_loads = freeLoadDraft(base.free_power_loads); else delete base.free_power_loads;
    withWhatsapp(base, this._whatsapp);
    withRouter(base, this._router);
    if (this._v2) { base.engine_v2 = Object.assign({}, saved.engine_v2 || {}); base.system.engine = uiEngine(this._v2, engineInUse(this._hass.states, saved)); Object.assign(base, profileDraft(this._v2, saved)); }
    else { delete base.engine_v2; delete base.v2_profile; delete base.v2_profiles; }
    return base;
  }

  /** The engine v2 settings, from the app's catalogue (sensor.pe_diag_v2_settings): its sections, then each setting. */
  _renderV2Sections(section, track, sub) {
    const cat = this._v2 || {};
    const byKey = Object.fromEntries((cat.settings || []).map((x) => [x.key, x]));
    const listed = new Set();
    const sections = (cat.sections || []).map((sec) => ({ key: sec.key, label: sec.label, keys: (sec.keys || []).filter((k) => byKey[k] && !listed.has(k) && listed.add(k)) }));
    const rest = (cat.settings || []).map((x) => x.key).filter((k) => !listed.has(k));
    if (rest.length) sections.push({ key: "more", label: "More", keys: rest });
    sections.forEach((sec) => {
      if (!sec.keys.length) return;
      const body = section(`v2_${sec.key}`, T(sec.label));
      sec.keys.forEach((k) => body.append(track(this._v2Row(byKey[k], `v2_${sec.key}`), "setting", `${byKey[k].label} ${byKey[k].help} ${k} engine v2`)));
      const readout = (fn) => { const box = el("div", { class: "readline" }); body.append(box); this._v2Readouts.push({ box, fn }); };
      if (sec.key === "range") readout(() => comfortReadout(this._v2Diag(), v2Values(this._v2, this._draft.engine_v2)));
      if (sec.key === "range") readout(() => floorsReadout(this._draft.safety, v2Values(this._v2, this._draft.engine_v2), inUseFor(this._hass, "battery_floor_soc")));
      if (sec.key === "forecast") readout(() => weightsReadout(this._v2Diag()));
    });
  }

  _v2Diag() { return v2Attrs(this._hass.states, V2_DIAG); }

  _v2Row(st, section) {
    const cur = () => (this._draft.engine_v2[st.key] !== undefined ? this._draft.engine_v2[st.key] : (this._v2.values || {})[st.key]);
    const set = (v) => {
      const inSaved = (this._saved.engine_v2 || {})[st.key] !== undefined;
      if (!inSaved && v2Same(v, (this._v2.values || {})[st.key])) delete this._draft.engine_v2[st.key];   // back to what is in use: leave it unset
      else this._draft.engine_v2[st.key] = v;
      this._refresh();
    };
    const label = T(st.label), help = T(st.help || "");
    const problem = el("div", { class: "problem" });
    this._v2Rows.push({ st, problem, section });
    if (st.kind === "bool") {
      const cb = el("input", { type: "checkbox", onchange: (ev) => set(ev.target.checked) });
      cb.checked = !!asBool(cur());
      return el("div", { class: "row" }, el("label", { class: "head" }, cb, el("span", { class: "label" }, label)), el("div", { class: "desc" }, help), problem);
    }
    let input;
    if (st.kind === "choice") {
      input = el("select", { onchange: (ev) => set(ev.target.value) });
      (st.options || []).forEach((o) => input.append(el("option", { value: o }, capFirst(String(o)))));
      input.value = String(cur());
    } else {
      input = el("input", { type: "number", step: st.kind === "int" ? "1" : "any", min: st.min, max: st.max, value: cur(), onchange: (ev) => set(ev.target.value) });
    }
    return el("div", { class: "row" },
      el("div", { class: "head" }, el("span", { class: "label" }, label), el("span", { class: "badge" }, `default ${st.default}${st.unit ? " " + st.unit : ""}`)),
      el("div", { class: "desc" }, help), el("div", { class: "ctl" }, input, el("span", { class: "muted" }, st.kind === "choice" ? "" : (st.unit || ""))), problem);
  }

  /** Search and filter chips: show matching rows only, opening their sections; restore when cleared. */
  _applyFilter() {
    const q = (this._query || "").trim();
    const f = this._filter || "all";
    const active = !!q || f !== "all";
    const shown = {};
    (this._items || []).forEach((it) => {
      const n = it.node;
      const kindOk = f === "all" || (f === "attention" && n.classList.contains("req-bad"))
        || (f === "req" && (it.kind === "req" || it.kind === "cond")) || (f === "opt" && it.kind === "opt");
      const ok = kindOk && (!q || matchesSearch(`${it.text} ${n.dataset.entity || ""}`, q));
      n.style.display = ok ? "" : "none";
      if (ok) shown[it.section] = (shown[it.section] || 0) + 1;
    });
    this._filtering = true;
    Object.entries(this._sections || {}).forEach(([key, s]) => {
      s.details.style.display = !active || shown[key] ? "" : "none";
      s.body.querySelectorAll("h4.sub").forEach((h) => { h.style.display = active ? "none" : ""; });
      if (active) s.details.open = !!shown[key];
      else s.details.open = this._openSections().has(key) || !!s.problems;
    });
    Object.entries(this._groups || {}).forEach(([key, g]) => {
      const any = [...g.body.querySelectorAll("details.section")].some((d) => d.style.display !== "none");
      g.wrap.style.display = !active || any ? "" : "none";
      if (active) g.wrap.open = any;
      else g.wrap.open = (!g.wrap.classList.contains("dim") && !this._openSections().has(`closed:${key}`)) || Object.values(this._sections).some((x) => x.problems && g.body.contains(x.details));
    });
    this._filtering = false;
    if (this._noMatch) this._noMatch.style.display = active && !Object.keys(shown).length ? "" : "none";
  }

  _jumpToProblem() {
    const first = (this._items || []).find((it) => it.node.classList.contains("req-bad"));
    if (!first) return;
    const s = this._sections[first.section];
    if (s) this._openWithGroup(s.details);
    first.node.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  _overnightRow() {
    const view = overnightReadout(this._hass.states["sensor.pe_diag_overnight"]);
    if (!view) return null;
    return el("div", { class: "row plain" },
      view.lines.map((l) => el("div", { class: "desc" }, el("strong", {}, `${l.label}: `), l.text)),
      view.notes.map((n) => el("div", { class: "muted" }, n)));
  }

  _otherEffective() {
    const sys = (this._draft && this._draft.system) || {};
    return effectiveOtherController(otherControllerValue(this._hass && this._hass.states), sys.other_controller);
  }

  _choiceRow(st) {
    const sel = el("select", { onchange: (ev) => {
      this._draft.system[st.key] = ev.target.value;
      if (st.key === "other_controller") this._build(); else this._refresh();
    } });
    const undecided = st.key === "other_controller" && this._draft.system[st.key] === undefined;
    if (undecided) sel.append(el("option", { value: "" }, "Choose…"));
    st.options.forEach(([value, label]) => sel.append(el("option", { value }, label)));
    sel.value = undecided ? "" : this._draft.system[st.key] !== undefined ? this._draft.system[st.key] : st.default;
    return el("div", { class: "row" },
      el("div", { class: "head" }, el("span", { class: "label" }, st.label)),
      el("div", { class: "desc" }, st.help), el("div", { class: "ctl" }, sel));
  }

  _settingRow(st) {
    const input = el("input", { type: "number", step: "any", min: st.min, max: st.max, value: this._draft.safety[st.key],
      onchange: (ev) => { this._draft.safety[st.key] = ev.target.value; this._refresh(); } });
    const problem = el("div", { class: "problem" });
    this._settingRows.push({ st, problem, section: this._currentSection });
    const use = el("div", { class: "readline" });                 // what the plan really uses, where it can differ from this
    (this._useRows = this._useRows || []).push({ box: use, key: st.key });
    return el("div", { class: "row" },
      el("div", { class: "head" }, el("span", { class: "label" }, st.label), el("span", { class: "badge" }, `default ${st.default}${st.unit ? " " + st.unit : ""}`)),
      el("div", { class: "desc" }, st.help), use,
      el("div", { class: "ctl" }, input, el("span", { class: "muted" }, st.unit || "")), problem);
  }

  _openSections() {
    if (!this._open) {
      try { this._open = new Set(JSON.parse(window.localStorage.getItem("powerengine-config-open") || "[]")); } catch (e) { this._open = new Set(); }
    }
    return this._open;
  }

  _rememberOpen(key, open) {
    const s = this._openSections();
    if (open) s.add(key); else s.delete(key);
    try { window.localStorage.setItem("powerengine-config-open", JSON.stringify([...s])); } catch (e) { /* private mode */ }
  }

  _toggleAll(open) {
    Object.entries(this._sections || {}).forEach(([key, s]) => { s.details.open = open; this._rememberOpen(key, open); });
    Object.entries(this._groups || {}).forEach(([key, g]) => { g.wrap.open = open; this._rememberOpen(`closed:${key}`, !open); });
  }

  /** Open a section and the group (Your house, Engine v1 or v2 settings) it sits in. */
  _openWithGroup(details) {
    details.open = true;
    const g = details.closest("details.group");
    if (g) g.open = true;
  }

  _badge(role) {
    if (role.required === "yes") return el("span", { class: "badge req" }, "Required");
    if (role.required === "no") return el("span", { class: "badge" }, "Optional");
    const f = { axle: "axle", free_power: "free_power_days" }[role.required];
    const label = T((FEATURES.find((x) => x[0] === f) || [0, role.required])[1]);
    return el("span", { class: "badge" }, `Needed for ${label}`);
  }

  /** The pre-filled suggestion for an unmapped role (entity id), or "". */
  _suggestion(role) {
    const cur = this._draft.inputs[role.key];
    if (cur && (cur.entity || cur.value !== undefined) || role.kind === "static") return "";
    return suggestEntity(role, Object.keys(this._hass.states));
  }

  _useSuggestions(roles) {
    roles.forEach((r) => { const id = this._suggestion(r); if (id) this._draft.inputs[r.key] = { entity: id }; });
    this._build();                                        // redraw with the new values (open sections are kept)
  }

  _roleRow(role) {
    const spec = () => this._draft.inputs[role.key];
    const set = (v) => { if (v) this._draft.inputs[role.key] = v; else delete this._draft.inputs[role.key]; this._refresh(); };
    const row = el("div", { class: "row" });
    const badgeBox = el("span", {});
    const item = { node: row, kind: "opt", text: `${role.label} ${role.description} ${role.key}`.toLowerCase(),
      section: this._currentSection };
    (this._items = this._items || []).push(item);
    row.append(el("div", { class: "head" }, el("span", { class: "label" }, role.label), badgeBox));
    row.append(el("div", { class: "desc" }, role.description));
    const ctl = el("div", { class: "ctl" });
    const cur = spec() || {};
    const isStatic = role.kind === "static" || (role.static_ok && cur.value !== undefined);

    const staticInput = el("input", { type: "number", step: "any", value: cur.value !== undefined ? cur.value : "", onchange: (ev) => {
      const prev = spec() || {};
      set(ev.target.value === "" ? null : Object.assign({ value: ev.target.value }, typeof prev.use_measured === "boolean" ? { use_measured: prev.use_measured } : {}));
    } });
    const unit = el("span", { class: "muted" }, role.static_unit || "");
    const entityBox = el("span", { style: "display:contents" }, this._entityInput(cur.entity, role.domains || ["sensor"], (v) => {
      const prev = spec() || {};
      set(v ? Object.assign({ entity: v }, prev.invert ? { invert: true } : {}) : null);
    }, role.label));

    if (role.kind === "static") {
      ctl.append(staticInput, unit);
    } else if (role.static_ok) {
      const modeSel = el("select", { onchange: (ev) => {
        if (ev.target.value === "fixed") { set(null); staticInput.value = ""; } else set(null);
        showMode(ev.target.value);
      } }, el("option", { value: "entity" }, "Entity"), el("option", { value: "fixed" }, "Fixed value"));
      modeSel.value = isStatic ? "fixed" : "entity";
      const showMode = (m) => { entityBox.style.display = m === "entity" ? "contents" : "none"; staticInput.style.display = unit.style.display = m === "fixed" ? "" : "none"; };
      ctl.append(modeSel, entityBox, staticInput, unit);
      showMode(modeSel.value);
    } else {
      ctl.append(entityBox);
    }

    if (role.measurable) {
      // PowerEngine measures this (e.g. usable capacity); ticked = use the measured figure once there is one
      const cb = el("input", { type: "checkbox", onchange: (ev) => {
        const s = spec();
        if (s && s.value !== undefined) s.use_measured = ev.target.checked;
        else {                                   // no figure entered yet: keep the default alongside the choice
          set({ value: role.suggest_static, use_measured: ev.target.checked });
          staticInput.value = role.suggest_static;
        }
        this._refresh();
      } });
      cb.checked = cur.use_measured !== false;
      ctl.append(el("label", { title: "Once PowerEngine has measured it (Health tab), use the measured figure instead of this one" }, cb, " Use measured ",
        el("span", { class: "muted" }, measuredText(this._hass, role.key))));
    }

    let invertCb = null;
    if (role.signed) {
      invertCb = el("input", { type: "checkbox", onchange: (ev) => { const s = spec(); if (s && s.entity) { s.invert = ev.target.checked; if (!s.invert) delete s.invert; this._refresh(); } } });
      invertCb.checked = !!cur.invert;
      ctl.append(el("label", {}, invertCb, " Invert"));
    }
    row.append(ctl);
    const hint = this._suggestion(role);
    if (hint && !this._readOnly) {
      row.append(el("div", { class: "suggest" }, "Suggested: ",
        el("button", { class: "link", onclick: () => this._useSuggestions([role]) }, hint)));
    }
    let signNote = null;
    if (role.signed) {
      const w = parseSignNote(role.sign_note);
      signNote = el("div", { class: "sign" }, `PowerEngine expects: + = ${w.pos}, − = ${w.neg}. Tick Invert if your entity reports it the other way round.`);
      row.append(signNote);
    }
    const live = el("div", { class: "live" });
    const problem = el("div", { class: "problem" });
    const status = el("div", { class: "status" });
    row.append(live, problem, status);
    this._rows.push({ role, spec, live, problem, status, section: this._currentSection, badgeBox, signNote, invertCb, row, item });
    return row;
  }

  _refresh() {
    if (!this._built && !this._rows.length) return;
    const states = this._hass.states;
    if (this._pkgBox) this._pkgBox.update(this._hass, this._pkgForced);
    this._updateNotifyLog();
    const mapping = states[MAPPING_SENSOR];
    const checks = ((mapping && mapping.attributes) || {}).checks || {};
    const saved = ((mapping && mapping.attributes) || {}).config || {};
    const savedInputs = saved.inputs || {};
    if (this._site) {                                       // the app's published site moves after Apply to System: follow it
      const info = siteInfo(((states[VERSION_SENSOR] || {}).attributes));
      if (info) { this._site = info; this._siteSaved = siteFromSelection(info.site); this._draft.site = Object.assign({}, this._siteSaved); }
    }
    // equipment is changed in Your system, not here: when the saved plants, devices or inputs move, the draft follows
    if (mapping && mapping.attributes && mapping.attributes.config && JSON.stringify(saved) !== JSON.stringify(this._saved)) {
      const next = overlayEquipment(this._draft, this._saved, saved, !!this._devices);
      this._saved = saved;
      if (JSON.stringify(next) !== JSON.stringify(this._draft)) { this._draft = next; this._build(); return; }
    }
    let blocking = 0;
    const secs = this._sections || {};
    Object.values(secs).forEach((s) => { s.problems = 0; s.unsaved = 0; s.req = 0; s.reqOk = 0; s.opt = 0; s.cond = 0; s.condOk = 0; });
    let reqBad = 0;
    const tally = (key, field) => { if (key && secs[key]) secs[key][field]++; };
    // separate charging/discharging sensors replace an unsigned battery power sensor (and become required)
    const pair = BATTERY_PAIR.every((k) => (this._draft.inputs[k] || {}).entity);
    this._rows.forEach(({ role: baseRole, spec, live, problem, status, section, badgeBox, signNote, invertCb, row, item }) => {
      const s = spec();
      const need = roleNeed(baseRole, this._draft, pair, this._otherEffective());
      const role = Object.assign({}, effectiveRole(baseRole, pair), need.level === "req" ? { required: "yes" } : {});
      const unused = need.level === "unused";
      badgeBox.replaceChildren(el("span", { class: `badge ${need.level}` }, need.badge));
      if (item) item.kind = need.level === "unused" ? "opt" : need.level;
      if (row) row.dataset.entity = (s && s.entity) || "";
      if (signNote) signNote.style.display = unused ? "none" : "";
      if (invertCb) invertCb.parentElement.style.display = unused ? "none" : "";
      if (unused) {
        live.textContent = "Not used: Battery charging power and Battery discharging power are mapped, so PowerEngine uses those.";
        problem.textContent = "";
        status.textContent = "";
        if (row) row.className = "row unused";
        return;
      }
      const st = s && s.entity ? states[s.entity] : null;
      const r = readout(role, s, st, undefined, inUseFor(this._hass, role.key));
      live.textContent = "";
      if (r.text) live.append(nowText(r));
      if (r.readsAs) live.append(el("span", { class: "reads" }, `(${r.readsAs})`));
      const p = role.required === "yes" || s ? instantProblem(role, s, st) : "";
      problem.textContent = p;
      if (p && (s && (s.value !== undefined || /bump|boost/.test(s.entity || "")))) blocking++;
      const same = JSON.stringify(savedInputs[role.key] || null) === JSON.stringify(s ? JSON.parse(JSON.stringify(buildConfig({ inputs: { [role.key]: s }, features: {}, operation: {} }).inputs[role.key] || null)) : null);
      const c = checks[role.key];
      status.className = "status";
      if (!same) status.textContent = "Unsaved change";
      else if (c) { status.textContent = `PowerEngine check: ${c.message}`; if (c.status === "ok") status.className = "status ok"; }
      else status.textContent = "";
      const bad = !!p || (c && same && c.status !== "ok" && c.status !== "unmapped");
      if (bad) tally(section, "problems");
      if (!same) tally(section, "unsaved");
      const mapped = !!(s && (s.entity || s.value !== undefined));
      if (need.level === "req") { tally(section, "req"); if (mapped && !bad) tally(section, "reqOk"); else reqBad++; }
      else if (need.level === "cond") { tally(section, "cond"); if (mapped && !bad) tally(section, "condOk"); }
      else tally(section, "opt");
      if (row) row.dataset.kind = item ? item.kind : "";
      if (row) row.className = `row ${need.level === "req" ? (mapped && !bad ? "req-ok" : "req-bad")
        : need.level === "cond" ? (bad ? "req-bad" : "cond") : (bad ? "req-bad" : "opt")}`;
    });
    const savedSafety = saved.safety || {};
    (this._settingRows || []).forEach(({ st, problem, section }) => {
      const p = settingProblem(st, this._draft.safety[st.key]);
      problem.textContent = p;
      if (p) { blocking++; tally(section, "problems"); }
      const was = savedSafety[st.key] !== undefined ? savedSafety[st.key] : st.default;
      if (Number(this._draft.safety[st.key]) !== Number(was)) tally(section, "unsaved");
    });
    (this._useRows || []).forEach(({ box, key }) => { const u = inUseFor(this._hass, key); box.textContent = u ? u.text : ""; box.style.display = u ? "" : "none"; });
    if (this._v2) {                                         // engine v2 settings and the engine choice
      const savedV2 = saved.engine_v2 || {};
      const vals = v2Values(this._v2, this._draft.engine_v2);
      const contra = v2Contradictions(vals);
      (this._v2Rows || []).forEach(({ st, problem, section }) => {
        const p = v2Problem(st, vals[st.key]) || contra[st.key] || "";
        problem.textContent = p;
        if (p) { blocking++; tally(section, "problems"); }
        const was = savedV2[st.key] !== undefined ? savedV2[st.key] : (this._v2.values || {})[st.key];
        if (!v2Same(vals[st.key], was)) tally(section, "unsaved");
      });
      (this._v2Readouts || []).forEach(({ box, fn }) => { const t = fn(); box.textContent = t || ""; box.style.display = t ? "" : "none"; });
      const eng = uiEngine(this._v2, engineInUse(states, saved));
      if (eng !== this._engine) { this._engine = eng; this._draft.system.engine = eng; }
      const g = this._groups || {};
      if (g.v1 && g.v2) {
        // the engine in use shows its settings as normal; the other is greyed out and folded away, still there to
        // change (v1's planning settings still shape its comparison plan and the simulators)
        ["v1", "v2"].forEach((k) => {
          const on = this._engine === k;
          g[k].wrap.classList.toggle("dim", !on);
          g[k].chip.textContent = on ? "in use" : "not in use: can be set now";
          g[k].chip.className = `groupchip${on ? " on" : ""}`;
          if (g[k].was !== on) {                           // the engine changed (or the page was just built)
            g[k].was = on;
            g[k].wrap.open = on && !this._openSections().has(`closed:${k}`);
          }
        });
      }
      const sig = [this._engine, this._enginePending, this._saving, this._readOnly, this._profileSig()].join("|");
      if (sig !== this._engineSig) { this._engineSig = sig; this._renderEngineBox(); }
    }
    Object.values(secs).forEach((s) => {
      const parts = [];
      if (s.problems) parts.push(`${s.problems} to check`);
      if (s.req) parts.push(s.reqOk === s.req ? `${s.req} required ✓` : `${s.reqOk} of ${s.req} required set`);
      if (s.cond) parts.push(s.condOk === s.cond ? `${s.cond} for going live ✓` : `${s.cond - s.condOk} of ${s.cond} for going live not set`);
      if (s.opt) parts.push(`${s.opt} optional`);
      if (s.unsaved) parts.push(`${s.unsaved} unsaved`);
      const off = s.main && !this._draft.features[s.main];
      if (off) parts.unshift("off");
      s.count.textContent = parts.join(" · ");
      s.count.className = s.problems || s.reqOk < s.req ? "count bad" : (s.req && !off ? "count good" : "count");
      s.details.classList.toggle("off", !!off);
      if (s.offNote) s.offNote.style.display = off ? "" : "none";
    });
    if (this._summary) {
      this._summary.textContent = reqBad ? `⚠ ${reqBad} required input${reqBad === 1 ? " needs" : "s need"} attention: show` : "✓ All required inputs are set";
      this._summary.className = reqBad ? "summary bad" : "summary good";
    }
    if (this._filter === "attention" || this._query) this._applyFilter();
    const base = this._baseDraft(saved);
    const dirty = JSON.stringify(buildConfig(this._draft)) !== JSON.stringify(buildConfig(base));
    if (this._saveBtn) {
      this._saveBtn.disabled = this._readOnly || blocking > 0 || this._saving || !dirty;
      this._saveBtn.textContent = this._saving ? "Saving…" : "Save";
    }
    if (this._resetBtn) this._resetBtn.disabled = this._readOnly || !dirty || this._saving;
  }

  _setBanner(kind, text) {
    if (!this._banner) return;
    this._banner.className = `banner ${kind}`;
    this._banner.textContent = text;
  }

  async _save() {
    const problem = this._draft.notifications && this._draft.notifications.channels ? routerProblem(this._draft.notifications) : "";
    if (problem) { this._setBanner("error", `Not saved: ${problem}`); return; }
    this._saving = true;
    this._pkgPending = packagePromptAfterSave(packageInfo(this._hass.states), (this._draft.system || {}).other_controller,
      otherControllerValue(this._hass.states) || ((this._saved.system || {}).other_controller));
    this._refresh();
    try {
      await this._hass.callWS({ type: "fire_event", event_type: SAVE_EVENT, event_data: { config: buildConfig(this._draft) } });
      this._setBanner("info", "Sent to PowerEngine; waiting for it to check and save…");
      setTimeout(() => { if (this._saving) { this._saving = false; this._setBanner("info", SAVE_NO_REPLY); this._refresh(); } }, SAVE_WAIT_MS);
    } catch (e) {
      this._saving = false;
      this._setBanner("error", `Could not send: ${e.message || e}. Saving needs an admin user.`);
      this._refresh();
    }
  }
}

/* ------------------------------------------------------------ toggle card
 * A small, discreet header row for PowerEngine dashboards: an optional title on the left and a compact switch
 * on the right. No card box. Used for the Costs tab's number alignment.
 *   type: custom:powerengine-toggle-card
 *   title: Costs
 *   entity: switch.pe_ui_right_align
 *   name: Alignment
 *   icon_on: mdi:format-align-right     (optional)
 *   icon_off: mdi:format-align-left     (optional)
 */
class PowerEngineToggleCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("entity is required");
    this._config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 1; }

  getGridOptions() { return { columns: "full", rows: 1, min_rows: 1 }; }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const c = this._config;
    const st = this._hass && this._hass.states[c.entity];
    const on = !!st && st.state === "on";
    const icon = on ? (c.icon_on || "mdi:format-align-right") : (c.icon_off || "mdi:format-align-left");
    if (!this._built) {
      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          .bar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; }
          .title { font-size: var(--ha-font-size-xl, 1.3em); color: var(--primary-text-color); }
          .ctl { display: inline-flex; align-items: center; gap: 6px; padding: 2px 4px 2px 8px; border-radius: 16px;
                 color: var(--secondary-text-color); font-size: .9em; cursor: pointer; user-select: none; }
          .ctl:hover { background: var(--secondary-background-color); }
          ha-icon { --mdc-icon-size: 18px; }
          .tools { display: inline-flex; gap: 14px; font-size: .85em; margin-right: auto; }
          .tools button { font: inherit; background: none; border: 0; padding: 0; cursor: pointer; color: var(--primary-color); }
          .sw { position: relative; width: 30px; height: 16px; border-radius: 8px; background: var(--disabled-color, #bdbdbd);
                transition: background .15s; flex: none; }
          .sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%;
                       background: #fff; transition: left .15s; }
          .sw.on { background: var(--primary-color); }
          .sw.on::after { left: 16px; }
        </style>
        <div class="bar"><span class="title"></span>${c.tools ? '<span class="tools"><button data-open="1">Expand all</button><button data-open="0">Collapse all</button></span>' : ""}
          <span class="ctl" role="switch" tabindex="0"><ha-icon></ha-icon><span class="name"></span><span class="sw"></span></span>
        </div>`;
      this.shadowRoot.querySelectorAll(".tools button").forEach((b) => b.addEventListener("click", () =>
        window.dispatchEvent(new CustomEvent(SECTIONS_EVENT, { detail: { open: b.dataset.open === "1" } }))));
      const ctl = this.shadowRoot.querySelector(".ctl");
      const toggle = () => {
        if (!this._hass) return;
        const cur = this._hass.states[this._config.entity];
        const svc = cur && cur.state === "on" ? "turn_off" : "turn_on";
        this._hass.callService(this._config.entity.split(".")[0], svc, { entity_id: this._config.entity });
      };
      ctl.addEventListener("click", toggle);
      ctl.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
      this._built = true;
    }
    this.shadowRoot.querySelector(".title").textContent = c.title || "";
    this.shadowRoot.querySelector(".name").textContent = c.name || "";
    this.shadowRoot.querySelector("ha-icon").setAttribute("icon", icon);
    const sw = this.shadowRoot.querySelector(".sw");
    sw.className = on ? "sw on" : "sw";
    const ctl = this.shadowRoot.querySelector(".ctl");
    ctl.setAttribute("aria-checked", on ? "true" : "false");
    ctl.title = st ? `${c.name || c.entity}: ${on ? "on" : "off"}` : `${c.entity} not found`;
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-toggle-card")) {
  customElements.define("powerengine-toggle-card", PowerEngineToggleCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-toggle-card", name: "PowerEngine toggle", description: "A discreet title + switch row." });
}

// --- Other battery controller (Predbat optional) --------------------------------------------------------------
// The app publishes `other_controller` on sensor.pe_diag_version: none | predbat | other | unset (app 0.9.108+).
// Older apps publish nothing, so the card falls back to looking for Predbat's own entities.
const OTHER_CONTROLLERS = ["none", "predbat", "other"];
const GUARD_ROLES = ["guard_read_only", "guard_off_1", "guard_off_2"];
const OTHER_UNSET_PROMPT = "Choose whether another battery controller is installed. PowerEngine won't go Active until you do.";
const PREDBAT_ENTITIES = ["switch.predbat_set_read_only", "input_select.battery_controller"];

/** The published value (null for an older app that doesn't publish it). */
function otherControllerValue(states) {
  const a = (((states || {})[VERSION_SENSOR] || {}).attributes) || {};
  return typeof a.other_controller === "string" ? a.other_controller : null;
}

/** Show the Predbat <-> PowerEngine handover card? Only when Predbat is the other controller; an older app (no
 *  attribute) shows it when Predbat's entities exist. */
function handoverVisible(states) {
  const v = otherControllerValue(states);
  if (v !== null) return v === "predbat";
  return PREDBAT_ENTITIES.some((id) => !!(states || {})[id]);
}

/** The value in force for the config page: what the owner has picked in the form (if different from what's saved),
 *  else what the app reports (the draft holds no value while it is unset). null = older app (behave as before). */
function effectiveOtherController(published, draftValue) {
  if (published === null || published === undefined) return null;
  if (OTHER_CONTROLLERS.includes(draftValue)) return draftValue;
  return published;
}

/** Which guard roles the Inverter control topic shows for a value. */
function guardRolesShown(value) {
  return value === null || value === undefined || value === "predbat" || value === "other" ? GUARD_ROLES.slice() : [];
}

/** The prompt above the choice (empty unless the owner hasn't chosen yet). */
function otherControllerPrompt(value) { return value === "unset" ? OTHER_UNSET_PROMPT : ""; }

/** Is Predbat the other controller (for hints that name it)? An older app: Predbat's entities exist. */
function predbatInUse(states) { return handoverVisible(states); }

/** The first step of the Tests card's checklist. */
function testsPauseHint(states) {
  return predbatInUse(states)
    ? "Keep PowerEngine selected in the battery controller switch (Predbat stays read-only), and turn on <b>Pause control</b> on the Monitoring page. Tests are refused while PowerEngine is in control."
    : "Turn on <b>Pause control</b> on the Monitoring page. Tests are refused while PowerEngine is in control.";
}

// --- Battery controller handover ---------------------------------------------------------------------------
// Switches between Predbat and PowerEngine through input_select.battery_controller (docs/ha/
// powerengine_handover.yaml runs the handover scripts). Shows what each related entity should be for the chosen
// controller, so a half-finished or manually undone handover is obvious and can be re-applied.
const HANDOVER_DEFAULTS = {
  selector: "input_select.battery_controller",
  read_only: "switch.predbat_set_read_only",
  pause: "switch.pe_ctl_pause",
  mode: "sensor.pe_state_operation_mode",
  scripts: { PowerEngine: "script.battery_handover_to_powerengine", Predbat: "script.battery_handover_to_predbat" },
};
const CONTROLLERS = ["Predbat", "PowerEngine"];
const HANDOVER_STEPS = {
  PowerEngine: "Predbat goes read-only, and PowerEngine is set to Active and " +
    "un-paused. About 20 seconds. PowerEngine is then live; you'll get a notification saying so, or why not.",
  Predbat: "PowerEngine is set to Passive and closes its inverter windows (Self-Use), then Predbat leaves read-only " +
    "and takes over at its next update. About 30 seconds. Predbat is then live.",
};

// Rows of {label, want, have, ok, note} for the controller currently selected (ok null = not a fault / unknown),
// plus a one-line status: live, paused (for testing) or not live.
function handoverRows(states, cfg) {
  const c = Object.assign({}, HANDOVER_DEFAULTS, cfg || {});
  const st = (id) => (states && states[id]) || null;
  const val = (id) => (st(id) ? st(id).state : "missing");
  const sel = val(c.selector);
  const toPE = sel === "PowerEngine";
  const rows = [];
  const add = (label, want, have, ok, note) => rows.push({ label, want, have, ok, note: note || "" });
  const known = (v) => !["missing", "unknown", "unavailable"].includes(v);

  const ro = val(c.read_only);
  if (!known(ro) && toPE) {
    // Predbat isn't connected to HA, so it can't be controlling the inverter: PowerEngine counts this as safe
    add("Predbat read-only", "on", ro, true, "Predbat isn't connected to Home Assistant right now, so it can't be " +
      "controlling the inverter; PowerEngine carries on. Restart the Predbat add-on to bring it back.");
  } else {
    add("Predbat read-only", toPE ? "on" : "off", ro, known(ro) ? ro === (toPE ? "on" : "off") : null,
      known(ro) ? "" : "Home Assistant doesn't have this entity right now (Predbat not running, or still starting).");
  }

  const pz = val(c.pause);
  const paused = toPE && pz === "on";
  add("PowerEngine paused", "off", pz, paused ? null : (known(pz) ? pz === "off" : null),
      paused ? "Paused for testing: nothing is driving the battery (Self-Use). Resume to go live again." : "");

  const m = val(c.mode);
  const reason = (st(c.mode) && st(c.mode).attributes && st(c.mode).attributes.reason) || "";
  if (toPE) {
    add("PowerEngine mode", "active", m, paused && m === "paused" ? null : (known(m) ? m === "active" : null),
        m === "active" || m === "paused" ? "" : reason);
  } else {
    add("PowerEngine mode", "passive", m, known(m) ? !["active", "paused"].includes(m) : null,
        m === "active" ? reason : "");
  }
  // a row we can't read (entity missing or unavailable) means we can't vouch for the handover: not live
  const bad = rows.some((r) => r.ok === false || (r.ok === null && !(paused && r.label.startsWith("PowerEngine"))));
  const status = bad ? "not_live" : paused ? "paused" : "live";
  return { selected: sel, rows, allOk: !bad, paused, status };
}

class PowerEngineHandoverCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    this._c = Object.assign({}, HANDOVER_DEFAULTS, this._config);
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 5; }

  _busy() {
    const s = this._hass && this._hass.states;
    return !!s && Object.values(this._c.scripts).some((id) => s[id] && s[id].state === "on");
  }

  async _switch(target) {
    this._confirm = null;
    try {
      await this._hass.callService("input_select", "select_option", { entity_id: this._c.selector, option: target });
      this._msg = "";
    } catch (err) {
      this._msg = "Could not switch: " + ((err && err.message) || err);
    }
    this._render();
  }

  async _pause(on) {
    try {
      await this._hass.callService("switch", on ? "turn_on" : "turn_off", { entity_id: this._c.pause });
      this._msg = "";
    } catch (err) {
      this._msg = "Could not change pause: " + ((err && err.message) || err);
    }
    this._render();
  }

  async _reapply(sel) {
    try {
      await this._hass.callService("script", "turn_on", { entity_id: this._c.scripts[sel] });
      this._msg = "";
    } catch (err) {
      this._msg = "Could not re-apply: " + ((err && err.message) || err);
    }
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._c) return;
    if (!this._built) {
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { padding: 16px; }
          h2 { margin: 0 0 8px; font-size: 1.2em; font-weight: 500; }
          p { margin: 4px 0 10px; color: var(--secondary-text-color); }
          .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 18px; overflow: hidden; }
          .seg button { font: inherit; padding: 6px 16px; border: 0; background: none; color: var(--primary-text-color);
                        cursor: pointer; }
          .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: default; }
          .seg button:disabled:not(.on) { opacity: .5; cursor: default; }
          .confirm { margin: 10px 0; padding: 10px; border-radius: 8px; background: var(--secondary-background-color); }
          .btn { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--divider-color);
                 background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; margin-right: 6px; }
          .btn.plain { background: none; color: var(--primary-text-color); }
          table { border-collapse: collapse; width: 100%; margin-top: 10px; }
          td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
          th { font-weight: 500; color: var(--secondary-text-color); }
          .ok { color: var(--success-color, #43a047); } .bad { color: var(--error-color, #db4437); }
          .note { color: var(--secondary-text-color); font-size: .9em; }
          .msg { color: var(--error-color, #db4437); margin-top: 8px; }
          .status { margin: 10px 0 0; font-weight: 500; }
          .status.live { color: var(--success-color, #43a047); } .status.not_live { color: var(--error-color, #db4437); }
          .status.paused, .status.busy, .warn { color: var(--warning-color, #ffa000); }
        </style>
        <ha-card><div class="body"></div></ha-card>`;
      this.shadowRoot.addEventListener("click", (ev) => {
        const b = ev.target.closest("button");
        if (!b || b.disabled) return;
        if (b.dataset.pick) { this._confirm = b.dataset.pick; this._render(); }
        else if (b.dataset.go) this._switch(b.dataset.go);
        else if (b.dataset.cancel !== undefined) { this._confirm = null; this._render(); }
        else if (b.dataset.reapply) this._reapply(b.dataset.reapply);
        else if (b.dataset.pause) this._pause(b.dataset.pause === "on");
      });
      this._built = true;
    }
    const esc = (t) => String(t).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
    const body = this.shadowRoot.querySelector(".body");
    const states = this._hass ? this._hass.states : {};
    const show = !this._hass || handoverVisible(states);
    this.style.display = show ? "" : "none";
    if (!show) { body.innerHTML = ""; return; }
    const title = `<h2>${esc(this._config.title || "Battery controller")}</h2>`;
    if (!states[this._c.selector]) {
      body.innerHTML = title + `<p>${esc(this._c.selector)} not found. Install docs/ha/powerengine_handover.yaml as a ` +
        `package (see INSTALL.md), check the config and restart Home Assistant.</p>`;
      return;
    }
    const h = handoverRows(states, this._config);
    const busy = this._busy();
    const seg = CONTROLLERS.map((c) => `<button class="${c === h.selected ? "on" : ""}" ${busy || c === h.selected ? "disabled" : ""}
        data-pick="${c}">${c}</button>`).join("");
    const line = busy ? "Switching…" : h.status === "live" ? `${h.selected} is live.`
      : h.status === "paused" ? "PowerEngine is paused for testing: nothing is driving the battery (Self-Use)."
      : `${h.selected} is selected but not fully live: see ✗ below.`;
    let html = title + `<p>Which system drives the battery. Switching hands over and leaves the chosen one fully
      live.</p><div><span class="seg">${seg}</span></div>
      <div class="status ${busy ? "busy" : h.status}">${esc(line)}</div>`;
    if (this._confirm && this._confirm !== h.selected && !busy) {
      html += `<div class="confirm"><b>Hand control to ${esc(this._confirm)}?</b><p>${esc(HANDOVER_STEPS[this._confirm])}</p>
        <button class="btn" data-go="${esc(this._confirm)}">Switch to ${esc(this._confirm)}</button>
        <button class="btn plain" data-cancel>Cancel</button></div>`;
    }
    html += `<table><tr><th></th><th>Should be</th><th>Is</th></tr>` + h.rows.map((r) => {
      const mark = r.ok === true ? '<span class="ok">✓</span>' : r.ok === false ? '<span class="bad">✗</span>'
        : (h.paused ? '<span class="warn">‖</span>' : "?");
      return `<tr><td>${mark} ${esc(r.label)}${r.note ? `<div class="note">${esc(r.note)}</div>` : ""}</td>` +
        `<td>${esc(r.want)}</td><td>${esc(r.have)}</td></tr>`;
    }).join("") + `</table>`;
    if (h.status === "not_live" && !busy && this._c.scripts[h.selected]) {
      html += `<p>Something doesn't match ${esc(h.selected)} (changed by hand, or a handover didn't finish).</p>
        <button class="btn" data-reapply="${esc(h.selected)}">Re-apply ${esc(h.selected)} handover</button>`;
    }
    if (h.selected === "PowerEngine" && !busy) {
      html += h.paused
        ? `<p><button class="btn" data-pause="off">Resume PowerEngine (go live)</button></p>`
        : `<p><button class="btn plain" data-pause="on">Pause for testing</button>
           <span class="note">Stops PowerEngine and returns the inverter to Self-Use; Predbat stays read-only.
           Needed for the supervised tests.</span></p>`;
    }
    if (this._msg) html += `<div class="msg">${esc(this._msg)}</div>`;
    body.innerHTML = html;
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-handover-card")) {
  customElements.define("powerengine-handover-card", PowerEngineHandoverCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-handover-card", name: "PowerEngine handover",
    description: "Switch battery control between Predbat and PowerEngine." });
}

// --- Supervised tests (the Tests page) ---------------------------------------------------------------------
// Admin only (HA only lets admins fire events). Each test writes to the inverter for a few minutes while someone
// watches, records what the battery did, then hands the inverter back to Self-Use. The app refuses unless the
// handover guards are safe and PowerEngine isn't in control.
const TEST_EVENT = "pe_test_write";
const TEST_ENTITY = "sensor.pe_diag_test_write";
const RC_TAILS = {
  rc_mode: ["select.", "battery_control_override"],
  rc_charge_power: ["number.", "battery_control_override_charge_power"],
  rc_discharge_power: ["number.", "battery_control_override_discharge_power"],
};
const SCREEN = "On the inverter screen watch the battery reading (power or current, and whether it is charging or " +
  "discharging) and the grid reading. If the screen is slow to update, the <<inverter>> app's live view or the readings " +
  "below show the same thing.";
const TESTS = [
  { key: "hold", group: "timed", label: "Hold (0 A charge window)", minutes: 5, power: false,
    what: "Opens a timed charge window with 0 A, which should stop the battery charging or discharging.",
    watch: "Battery near 0 W (neither charging nor discharging); the house runs from the grid (and any solar).",
    checks: "The window settings read back from the inverter; battery power is logged every minute." },
  { key: "charge", group: "timed", label: "Grid charge (timed window)", minutes: 5, power: true,
    what: "Opens a timed charge window: the battery charges from the grid at the chosen power (or the maximum).",
    watch: "Battery charging at about the chosen power; grid import goes up by the same amount.",
    checks: "The window settings read back; battery power is logged every minute." },
  { key: "discharge", group: "timed", label: "Force discharge (timed window)", minutes: 5, power: true,
    what: "Opens a timed discharge window: the battery discharges at the chosen power, exporting what the house " +
      "doesn't use.",
    watch: "Battery discharging at about the chosen power; the grid shows export.",
    checks: "The window settings read back; battery power is logged every minute." },
  { key: "self_use", group: "timed", label: "Self-Use (windows closed)", minutes: 2, power: false,
    what: "Closes both timed windows: plain Self-Use.",
    watch: "Battery covers the house load (discharging about what the house uses); little or no grid import.",
    checks: "The settings read back as closed." },
  { key: "rc_charge", group: "rc", label: "RC force charge", minutes: 5, power: true, defPower: 2000,
    what: "Closes the timed windows, then sets remote control to 'Force charge' at the chosen power (register " +
      "43135 with 43136). No timed-window (EEPROM) settings are used for the charge itself.",
    watch: "Battery charging at about the chosen power within a minute; grid import goes up by the same amount. " +
      "At the end the battery goes back to normal Self-Use.",
    checks: "Battery power every 30 s: 'worked' if it charged at 60% or more of the asked power. Also checks the " +
      "timed-window settings didn't change and that remote control is Off at the end." },
  { key: "rc_discharge", group: "rc", label: "RC force discharge", minutes: 5, power: true, defPower: 2000,
    what: "Closes the timed windows, then sets remote control to 'Force discharge' at the chosen power (43135 " +
      "with 43129).",
    watch: "Battery discharging at about the chosen power; the grid shows export of whatever the house doesn't " +
      "use.",
    checks: "'Worked' if the battery discharged at 60% or more of the asked power; windows unchanged; Off at the " +
      "end." },
  { key: "rc_hold", group: "rc", label: "RC hold (force charge at 0 W)", minutes: 5, power: false,
    what: "Remote control 'Force charge' at 0 W. The RC registers have no hold option, so this checks whether a " +
      "0 W force charge holds the battery. Run it with some house load (a kettle helps) so a hold is visible.",
    watch: "Battery near 0 W while the house draws from the grid. If the battery discharges to cover the house, " +
      "0 W doesn't mean hold on this firmware.",
    checks: "'Worked' if the battery stayed within 300 W of zero throughout." },
  { key: "rc_failsafe", group: "rc", label: "RC failsafe (stop re-sending)", minutes: 12, power: true,
    defPower: 2000,
    what: "Force charges for 2 minutes, then stops the command being re-sent by reloading the inverter's Modbus " +
      "integration, without writing Off. This is what would happen if HA or AppDaemon died: the inverter should " +
      "drop the command by itself after its RC timeout (reported as 5 to 30 minutes). Its entities show as " +
      "unavailable for a few seconds during the reload; that's expected.",
    watch: "Charging starts, then keeps going for a while after the reload. Note the time it stops by itself " +
      "and the battery goes back to Self-Use. If it's still charging when the test ends, the test switches it " +
      "Off.",
    checks: "'Reverted' (with the time) if charging stopped by itself after the reload; 'did not revert' if it " +
      "was still charging at the end (then run it again with more minutes, up to 35). This is the key test for a " +
      "safe fallback." },
];
const TEST_BY_KEY = Object.fromEntries(TESTS.map((t) => [t.key, t]));

function findRcEntities(states) {
  const ids = Object.keys(states || {}).sort();
  const out = {};
  for (const [role, [domain, tail]] of Object.entries(RC_TAILS)) {
    const hits = ids.filter((e) => e.startsWith(domain) && e.endsWith(tail));
    hits.sort((a, b) => (a.includes("solis") ? 0 : 1) - (b.includes("solis") ? 0 : 1) || a.localeCompare(b));
    if (hits.length) out[role] = hits[0];
  }
  return out;
}

function dampingNote(states) {
  const st = states && states["sensor.pe_diag_writes_today"];
  const w = st && st.attributes && st.attributes.damping_week;
  if (!w || !(w.none || w.restart || w.both)) return "Measured savings appear here (and on the Health tab) once PowerEngine has been live for a while.";
  const on = (st.attributes.damping || {});
  const n = (x) => `${x} write${x === 1 ? "" : "s"}`;
  return `Last ${w.days} days (modelled): no dampening ${n(w.none)}; restart hold-off saved ${n(w.saved_restart)}` +
    `${on.restart === false ? " (would have)" : ""}; burst damping ${on.bursts ? "saved" : "would have saved"} ${n(w.saved_bursts)} more.`;
}

function testSummary(st) {
  if (!st || ["unknown", "unavailable"].includes(st.state)) return { status: "idle", problems: [], lines: [] };
  const a = st.attributes || {};
  const w = (v) => (v === undefined || v === null ? null : Math.round(v));
  const lines = (a.steps || []).map((s) => {
    const t = (s.time || "").slice(11, 19);
    const bits = [];
    if (s.ok === true) bits.push("OK");
    if (s.ok === false) bits.push("MISMATCH: " + (s.mismatched || []).join(", "));
    if (s.writes) bits.push(`${s.writes.length} write${s.writes.length === 1 ? "" : "s"}`);
    if (w(s.soc) !== null) bits.push(`SoC ${w(s.soc)}%`);
    if (w(s.battery_w) !== null) {
      const b = w(s.battery_w);
      bits.push(`battery ${b < 0 ? "charging " + -b : b > 0 ? "discharging " + b : "0"} W`);
    }
    if (w(s.grid_w) !== null) {
      const g = w(s.grid_w);
      bits.push(`grid ${g < 0 ? "export " + -g : "import " + g} W`);
    }
    if (s.rc) bits.push(`RC ${s.rc}`);
    if (s.note) bits.push(s.note);
    return `${t} ${s.what}${bits.length ? ": " + bits.join(", ") : ""}`;
  });
  return { status: st.state, action: a.action, minutes: a.minutes, problems: a.problems || [], lines,
    verdict: a.verdict || null, explanation: a.explanation || "" };
}

function liveLine(states) {
  const v = (id) => { const s = states && states[id]; return s && !isNaN(Number(s.state)) ? Number(s.state) : null; };
  const b = v("sensor.pe_state_battery_power"), g = v("sensor.pe_state_grid_power"), c = v("sensor.pe_state_battery_soc");
  const parts = [];
  if (c !== null) parts.push(`Battery ${Math.round(c)}%`);
  if (b !== null) parts.push(b < 0 ? `charging ${Math.round(-b)} W` : b > 0 ? `discharging ${Math.round(b)} W` : "idle");
  if (g !== null) parts.push(g < 0 ? `grid export ${Math.round(-g)} W` : `grid import ${Math.round(g)} W`);
  return parts.join(" · ") || "No live readings";
}

class PowerEngineTestCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    setNames(hass);
    this._render();
  }

  getCardSize() { return 8; }

  async _fire(data) {
    try {
      await this._hass.callWS({ type: "fire_event", event_type: TEST_EVENT, event_data: data });
      this._msg = "";
    } catch (err) {
      this._msg = "Could not start: " + ((err && err.message) || err) + " (admin users only)";
    }
    this._render();
  }

  _describe() {
    const q = (sel) => this.shadowRoot.querySelector(sel);
    const t = TEST_BY_KEY[q(".action").value];
    q(".what").textContent = t.what;
    q(".watch").textContent = t.watch;
    q(".checks").textContent = t.checks;
    q(".minutes").value = t.minutes;
    q(".minutes").max = t.key === "rc_failsafe" ? 35 : t.group === "rc" ? 15 : 10;
    q(".minutes").min = t.key === "rc_failsafe" ? 4 : 1;
    q(".powerrow").style.display = t.power ? "" : "none";
    q(".power").value = t.defPower || "";
    q(".power").placeholder = t.defPower ? String(t.defPower) : "max";
  }

  _render() {
    if (!this.shadowRoot) return;
    if (!this._built) {
      const opts = (g) => TESTS.filter((t) => t.group === g).map((t) => `<option value="${t.key}">${t.label}</option>`).join("");
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { padding: 16px; }
          h2 { margin: 0 0 8px; font-size: 1.2em; font-weight: 500; }
          h3 { margin: 14px 0 4px; font-size: 1em; font-weight: 500; }
          p, li { color: var(--secondary-text-color); }
          p { margin: 4px 0 8px; }
          ol { margin: 4px 0 8px; padding-left: 1.3em; }
          .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
          select, input { font: inherit; padding: 4px 6px; max-width: 100%; }
          input[type=number] { width: 6em; }
          button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--divider-color);
                   background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; }
          button.stop { background: var(--error-color, #db4437); }
          button:disabled { opacity: .5; cursor: default; }
          .box { border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 12px; margin: 8px 0; }
          .box b { color: var(--primary-text-color); }
          .live { font-weight: 500; }
          .status { font-weight: 500; }
          .passed, .worked, .reverted { color: var(--success-color, #43a047); }
          .failed, .refused, .no-effect, .did-not-revert { color: var(--error-color, #db4437); }
          .inconclusive { color: var(--warning-color, #f9a825); }
          pre { background: var(--secondary-background-color); padding: 8px; border-radius: 6px; overflow-x: auto;
                font-size: .85em; margin: 8px 0 0; white-space: pre-wrap; }
          .msg { color: var(--error-color, #db4437); }
          .rc { font-size: .9em; }
        </style>
        <ha-card>
          <h2>Supervised inverter tests</h2>
          <p>Each test writes to the inverter for a few minutes while you watch, logs what the battery did, then
             hands the inverter back to Self-Use. Run one at a time.</p>
          <h3>Before you start</h3>
          <ol>
            <li class="pausehint"></li>
            <li>Best in the evening or at night: no solar, the car not charging, battery between about 30% and 90%
                so it can both charge and discharge.</li>
            <li>Stand where you can see the inverter screen, or have the ${escHtml(T("<<inverter>>"))} app's live view open.</li>
            <li>When you've finished, turn Pause control off again.</li>
          </ol>
          <div class="row"><span>Now:</span><span class="live"></span></div>
          <h3>Choose a test</h3>
          <div class="row">
            <select class="action">
              <optgroup label="RAM remote control (43135): no EEPROM writes">${opts("rc")}</optgroup>
              <optgroup label="Timed windows (current method, EEPROM)">${opts("timed")}</optgroup>
            </select>
          </div>
          <div class="box">
            <p><b>What it does:</b> <span class="what"></span></p>
            <p><b>Watch for:</b> <span class="watch"></span></p>
            <p class="screen"></p>
            <p><b>PowerEngine checks:</b> <span class="checks"></span></p>
          </div>
          <div class="row">
            <label>Minutes <input class="minutes" type="number" min="1" max="15"></label>
            <label class="powerrow">Power (W) <input class="power" type="number" min="100" max="6000" step="100"></label>
          </div>
          <div class="row">
            <label><input class="confirm" type="checkbox"> I'm watching the inverter and PowerEngine is paused</label>
          </div>
          <div class="row">
            <button class="start">Start test</button>
            <button class="stop">Stop and revert</button>
            <span class="msg"></span>
          </div>
          <div class="row"><span>Status:</span><span class="status"></span><span class="verdict"></span></div>
          <pre class="log"></pre>
          <p class="rc"></p>
        </ha-card>`;
      const q = (sel) => this.shadowRoot.querySelector(sel);
      q(".screen").textContent = T(SCREEN);
      q(".action").addEventListener("change", () => { this._describe(); this._render(); });
      q(".start").addEventListener("click", () => {
        const t = TEST_BY_KEY[q(".action").value];
        const data = { action: t.key, minutes: Number(q(".minutes").value), confirm: q(".confirm").checked };
        const power = q(".power").value;
        if (t.power && power) data.power_w = Number(power);
        this._fire(data);
        q(".confirm").checked = false;
      });
      q(".stop").addEventListener("click", () => this._fire({ action: "stop" }));
      q(".confirm").addEventListener("change", () => this._render());
      this._built = true;
      this._describe();
    }
    const q = (sel) => this.shadowRoot.querySelector(sel);
    const states = this._hass ? this._hass.states : {};
    const sum = testSummary(states[TEST_ENTITY]);
    const running = sum.status === "running" || sum.status === "reverting";
    q(".pausehint").innerHTML = testsPauseHint(states);
    q(".live").textContent = liveLine(states);
    q(".status").textContent = sum.status + (sum.action && sum.status !== "idle" ? ` (${sum.action}${sum.minutes ? ", " + sum.minutes + " min" : ""})` : "");
    q(".status").className = "status " + sum.status;
    q(".verdict").textContent = sum.verdict ? `: ${sum.verdict}${sum.explanation ? " (" + sum.explanation + ")" : ""}` : "";
    q(".verdict").className = "verdict " + (sum.verdict || "").replace(/ /g, "-");
    q(".log").textContent = [...sum.problems.map((p) => "Problem: " + p), ...sum.lines].join("\n") || "No test run yet.";
    const t = TEST_BY_KEY[q(".action").value];
    const rc = findRcEntities(states);
    const need = t.group !== "rc" ? [] : ["rc_mode", t.key === "rc_discharge" ? "rc_discharge_power" : "rc_charge_power"];
    const gone = need.filter((r) => !rc[r]);
    q(".rc").textContent = t.group !== "rc" ? "" : gone.length
      ? "Remote-control entities not found in HA: " + gone.join(", ") + ". The inverter integration needs to expose the 'Battery control override' entities."
      : "Remote-control entities: " + need.map((r) => `${rc[r]} (${(states[rc[r]] || {}).state})`).join(", ");
    q(".start").disabled = running || !q(".confirm").checked || gone.length > 0;
    q(".stop").disabled = !running;
    q(".msg").textContent = this._msg || versionWarnings(states).join(" ");
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-test-card")) {
  customElements.define("powerengine-test-card", PowerEngineTestCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-test-card", name: "PowerEngine supervised tests", description: "Run short, supervised inverter tests (timed windows and RAM remote control)." });
}

// --- Update (Configuration page) ---------------------------------------------------------------------------
// One button: ask HACS to check GitHub now for both PowerEngine repositories (otherwise HACS may not notice a new
// release for hours), then run the handover package's script.powerengine_update, which installs what's new and
// restarts AppDaemon. Admins only (HACS's refresh and the add-on restart both need admin).
const UPDATE_SCRIPT = "script.powerengine_update";

function peRepos(list) {
  return (list || []).filter((r) => r && r.installed && /powerengine/i.test(String(r.full_name || r.name || "")));
}

function updateEntities(states) {
  return Object.keys(states || {}).filter((id) => /^update\..*powerengine/i.test(id)).sort();
}

function versionLine(states) {
  const running = ((states || {})["sensor.pe_diag_version"] || {}).state || "?";
  const parts = updateEntities(states).map((id) => {
    const a = (states[id] || {}).attributes || {};
    const what = /card/i.test(id) ? "card" : "app";
    const more = states[id].state === "on" && a.latest_version ? ` → ${a.latest_version} available` : "";
    return `${what} ${a.installed_version || "?"}${more}`;
  });
  const rel = (states || {})["sensor.pe_diag_update"];
  const released = rel && rel.state === "available" ? (rel.attributes || {}).latest : null;
  const entities = updateEntities(states);
  const hacsOn = entities.some((id) => (states[id] || {}).state === "on");
  // Nothing to install when HACS and the release check both say we're current. With no HACS entities at all we can't
  // tell, so the button stays usable.
  const known = hacsOn || !!released || !entities.length;
  return { running, parts, released, known, notes: released ? (rel.attributes || {}).notes || "" : "" };
}

class PowerEngineUpdateCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    if (this._pkgBox) this._pkgBox.update(hass);
    const running = ((hass.states || {})["sensor.pe_diag_version"] || {}).state;
    if (this._busy && this._from && running && running !== this._from && running !== "unavailable") {
      this._busy = false;
      this._msg = `PowerEngine ${running} is running. Reload the page to load the matching card.`;
      this._reload = true;
    }
    this._render();
  }

  getCardSize() { return 2; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  async _refresh() {
    const hass = this._hass;
    const repos = peRepos(await hass.callWS({ type: "hacs/repositories/list" }));
    for (const r of repos) {
      await hass.callWS({ type: "hacs/repository/refresh", repository: String(r.id) });
    }
    await new Promise((res) => setTimeout(res, 3000));      // let the update entities catch up
  }

  async _checkNow() {
    const hass = this._hass;
    if (!hass || this._busy || this._checkingNow) return;
    this._checkingNow = true;
    this._status("Asking HACS to check GitHub for new releases…");
    try {
      await this._refresh();
      this._checkingNow = false;
      const v = versionLine(this._hass.states);
      this._status(v.known && v.parts.some((p) => p.includes("available")) || v.released
        ? "A new version is available." : "You're up to date.");
    } catch (err) {
      this._checkingNow = false;
      this._status("Couldn't check: " + ((err && err.message) || err));
    }
  }

  async _update() {
    const hass = this._hass;
    if (!hass || this._busy) return;
    this._busy = true;
    this._reload = false;
    this._from = ((hass.states || {})["sensor.pe_diag_version"] || {}).state;
    try {
      if (hass.user && hass.user.is_admin) {
        this._status("Asking HACS to check GitHub for new releases…");
        await this._refresh();
      }
      if (!hass.states[UPDATE_SCRIPT]) {
        this._busy = false;
        this._status("The update script isn't installed: copy docs/ha/powerengine_handover.yaml (0.9.42 or later) " +
          "into /config/packages/ and reload scripts.");
        return;
      }
      this._status("Installing what's new and restarting AppDaemon if the app changed (about a minute)…");
      await hass.callService("script", "turn_on", { entity_id: UPDATE_SCRIPT });
      setTimeout(() => {
        if (this._busy) {
          this._busy = false;
          const now = ((this._hass.states || {})["sensor.pe_diag_version"] || {}).state;
          this._status(now === this._from
            ? `Still ${now}: nothing new to install, or it's still going (see the notification).`
            : `PowerEngine ${now} is running. Reload the page to load the matching card.`);
          this._reload = now !== this._from;
          this._render();
        }
      }, 180000);
    } catch (err) {
      this._busy = false;
      this._status("Update failed: " + ((err && err.message) || err));
    }
  }

  _status(text) {
    this._msg = text;
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    if (!this._built) {
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { padding: 12px 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
          .text { flex: 1 1 260px; }
          .msg { color: var(--secondary-text-color); font-size: 0.9em; margin-top: 4px; }
          .notes { flex: 1 1 100%; border-top: 1px solid var(--divider-color); padding-top: 4px; }
          .notes:empty { display: none; }
          button { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--divider-color);
                   background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; }
          button.second { background: none; color: var(--primary-text-color); }
          button:disabled { opacity: .5; cursor: default; }
          button.go:disabled { background: none; color: var(--disabled-text-color, var(--secondary-text-color)); }
        </style>
        <ha-card>
          <div class="pkg" style="flex:1 1 100%"></div>
          <div class="text"><div class="line"></div><div class="msg"></div></div>
          <button class="reload second">Reload page</button>
          <button class="chk second">Check for updates</button>
          <button class="go">Update</button>
          <div class="notes"></div>
        </ha-card>`;
      this.shadowRoot.querySelector(".go").addEventListener("click", () => {
        if (confirm("Install the latest PowerEngine app and card, then restart AppDaemon? Control pauses for " +
            "about a minute while it restarts.")) this._update();
      });
      this._pkgBox = makePackageBox();
      this.shadowRoot.querySelector(".pkg").append(this._pkgBox);
      this.shadowRoot.querySelector(".chk").addEventListener("click", () => this._checkNow());
      this.shadowRoot.querySelector(".reload").addEventListener("click", () => location.reload());
      this._built = true;
    }
    this._pkgBox.update(this._hass);
    const { running, parts, released, known, notes } = versionLine(this._hass.states);
    const q = (s) => this.shadowRoot.querySelector(s);
    q(".line").innerHTML = `<b>PowerEngine ${running}</b> running` + (parts.length
      ? " · " + parts.join(" · ") : " · HACS update entities not found")
      + (released ? ` · <b>${released} released</b>` : "");
    if (notes !== this._notes) {
      this._notes = notes;
      const box = q(".notes");
      box.innerHTML = "";
      if (notes) {
        const md = document.createElement("ha-markdown");
        md.breaks = true;
        md.content = "**What's new**\n\n" + notes;
        box.appendChild(md);
      }
    }
    q(".msg").textContent = this._msg || "";
    const admin = !!(this._hass.user && this._hass.user.is_admin);
    q(".go").disabled = !!this._busy || !known;
    q(".go").title = known ? "" : "No update is known. Press Check for updates.";
    q(".chk").style.display = admin ? "" : "none";
    q(".chk").disabled = !!this._busy || !!this._checkingNow;
    q(".chk").textContent = this._checkingNow ? "Checking…" : "Check for updates";
    q(".go").textContent = this._busy ? "Updating…" : "Update";
    q(".reload").style.display = this._reload ? "" : "none";
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-update-card")) {
  customElements.define("powerengine-update-card", PowerEngineUpdateCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-update-card", name: "PowerEngine update",
    description: "Check for and install the latest PowerEngine release." });
}

// --- Setup checklist ---------------------------------------------------------------------------------------
// Checks each prerequisite once (on load and after an install) and installs what HACS can
// install with one button. Admins only: every HACS and Supervisor call below is admin-only on HA's side.
//
// Commands (verified against the sources, see the notes in tests/helpers.test.cjs):
//   hacs/info                    (hacs/websocket/__init__.py)   -> {categories: [...], disabled_reason, version, ...}
//   hacs/repositories/list       (hacs/websocket/repositories.py), optional "categories" -> [{id, full_name, category,
//                                 installed, installed_version, ...}]
//   hacs/repositories/add        (same file) {repository, category}; answers {} even when it refuses (the reason goes to
//                                 HACS's error signal), so the card lists again to see whether it worked
//   hacs/repository/download     (hacs/websocket/repository.py) {repository: <id as text>}; a failure is a WS error
//   supervisor/api               (HA core hassio/websocket_api.py) {endpoint: "/addons", method: "get"} -> {addons: [...]}
const SETUP_REPOS = {
  app: { key: "app", full_name: "durkimat/ha-powerengine-controller", category: "appdaemon", label: "PowerEngine app" },
  apex: { key: "apex", full_name: "RomRider/apexcharts-card", category: "plugin", label: "Chart card", element: "apexcharts-card" },
  flow: { key: "flow", full_name: "slipx06/sunsynk-power-flow-card", category: "plugin", label: "Energy-flow card", element: "sunsynk-power-flow-card" },
};
const SETUP_LINKS = {
  hacs: "https://hacs.xyz/docs/use/download/download/",
  hacsOptions: "https://my.home-assistant.io/redirect/integration/?domain=hacs",
  addon: "https://my.home-assistant.io/redirect/supervisor_addon/?addon=a0d7b954_appdaemon",
  appdaemonDocs: "https://appdaemon.readthedocs.io/en/latest/INSTALL.html",
  mqtt: "https://my.home-assistant.io/redirect/config_flow_start/?domain=mqtt",
};

/** The HACS repository (from hacs/repositories/list) for "owner/name", or null. Case doesn't matter. */
function findHacsRepo(list, fullName) {
  const want = String(fullName || "").toLowerCase();
  return (list || []).find((r) => r && String(r.full_name || "").toLowerCase() === want) || null;
}

function hacsInfoPayload() { return { type: "hacs/info" }; }
function hacsListPayload(categories) { return { type: "hacs/repositories/list", categories: categories || ["appdaemon", "plugin"] }; }
function hacsAddPayload(fullName, category) { return { type: "hacs/repositories/add", repository: fullName, category }; }
/** HACS wants the repository id as text. */
function hacsDownloadPayload(id) { return { type: "hacs/repository/download", repository: String(id) }; }
function addonsPayload() { return { type: "supervisor/api", endpoint: "/addons", method: "get" }; }

/** The next HACS call to install one of SETUP_REPOS: add it to HACS if HACS doesn't know it yet, else download it. */
function installStep(key, repos) {
  const def = SETUP_REPOS[key];
  if (!def) return null;
  const repo = findHacsRepo(repos, def.full_name);
  return repo ? hacsDownloadPayload(repo.id) : hacsAddPayload(def.full_name, def.category);
}

/** The AppDaemon add-on from a supervisor /addons answer: {state: "started" | "stopped" | "missing", slug, name, version}. */
function addonFrom(result) {
  const list = ((result || {}).addons || []).filter((a) => a && /appdaemon/i.test(String(a.slug || "")));
  const a = list.find((x) => /^a0d7b954_appdaemon$/.test(x.slug)) || list[0];
  if (!a) return { state: "missing" };
  return { state: a.state === "started" ? "started" : "stopped", slug: a.slug, name: a.name, version: a.version };
}

/** A version sensor that exists and has a value. */
function peRunning(states) {
  const s = (states || {})[VERSION_SENSOR];
  return s && s.state !== "unavailable" && s.state !== "unknown" && s.state !== "" ? String(s.state) : null;
}

const ADMIN_NOTE = "Only an admin can check or install this.";

/**
 * The checklist. facts: {isAdmin, hacs (true/false/null=unknown), hacsReason, categories (array or null), addon
 * ({state: "started"|"stopped"|"missing"|"unsupervised"|"unknown"}), repos (HACS list, or null), cardsLoaded
 * ({element: bool}), components (array), peVersion (string or null)}.
 * Row: {key, title, why, status: "ok"|"missing"|"unknown", needed, detail, action}. action is null, or
 * {kind: "install", target, label}, {kind: "link", href, label} or {kind: "reload", label}.
 */
function setupRows(facts) {
  const f = facts || {};
  const admin = !!f.isAdmin;
  const cats = Array.isArray(f.categories) ? f.categories : null;
  const repos = f.repos || null;
  const comps = f.components || [];
  const rows = [];
  const hacsOk = f.hacs === true;
  const install = (target, label) => (admin && hacsOk ? { kind: "install", target, label } : null);
  const link = (href, label) => ({ kind: "link", href, label });
  const unknownFor = (r) => (!admin ? { ...r, status: "unknown", detail: ADMIN_NOTE, action: null } : r);

  // 1. HACS
  const hacsRow = { key: "hacs", title: "HACS", why: "Installs PowerEngine and its cards.", needed: true };
  rows.push(unknownFor(f.hacs === true
    ? { ...hacsRow, status: "ok", detail: "Installed.", action: null }
    : f.hacs === false
      ? { ...hacsRow, status: "missing",
          detail: f.hacsReason ? "HACS is switched off: " + f.hacsReason : "HACS isn't installed. Install it first; it takes a few minutes.",
          action: link(SETUP_LINKS.hacs, "How to install HACS") }
      : { ...hacsRow, status: "unknown", detail: "Couldn't check.", action: null }));

  // 2. HACS AppDaemon discovery
  const disc = { key: "discovery", title: "HACS shows AppDaemon apps", why: "Lets HACS install PowerEngine (an AppDaemon app).", needed: true };
  if (f.hacs !== true || !cats) rows.push(unknownFor({ ...disc, status: "unknown", detail: "Check HACS first.", action: null }));
  else if (cats.includes("appdaemon")) rows.push(unknownFor({ ...disc, status: "ok", detail: "On.", action: null }));
  else rows.push(unknownFor({ ...disc, status: "missing",
    detail: "Switch it on: Settings, Devices & services, HACS, Configure, then tick \"Enable AppDaemon apps discovery & tracking\" and submit. Then reload the page.",
    action: link(SETUP_LINKS.hacsOptions, "Open HACS") }));

  // 3. AppDaemon add-on
  const ad = f.addon || { state: "unknown" };
  const addon = { key: "addon", title: "AppDaemon add-on", why: "Runs PowerEngine.", needed: true };
  if (ad.state === "started") rows.push(unknownFor({ ...addon, status: "ok", detail: "Installed and running" + (ad.version ? ` (${ad.version}).` : "."), action: null }));
  else if (ad.state === "stopped") rows.push(unknownFor({ ...addon, status: "missing", detail: "Installed, but not running. Start it from its page.", action: link(SETUP_LINKS.addon, "Open add-on") }));
  else if (ad.state === "missing") rows.push(unknownFor({ ...addon, status: "missing", detail: "Not installed. Install it from the add-on store.", action: link(SETUP_LINKS.addon, "Open add-on store page") }));
  else rows.push(unknownFor({ ...addon, status: "unknown", detail: "Can't check this here. Make sure AppDaemon is running.", action: link(SETUP_LINKS.appdaemonDocs, "AppDaemon guide") }));

  // 4. The PowerEngine app in HACS
  const appRepo = findHacsRepo(repos, SETUP_REPOS.app.full_name);
  const appRow = { key: "app", title: "PowerEngine app", why: "The energy manager itself, installed through HACS.", needed: true };
  if (appRepo && appRepo.installed) rows.push(unknownFor({ ...appRow, status: "ok", detail: `Installed in HACS${appRepo.installed_version ? " (" + appRepo.installed_version + ")" : ""}.`, action: null }));
  else if (!hacsOk || !repos) rows.push(unknownFor({ ...appRow, status: f.hacs === false ? "missing" : "unknown", detail: f.hacs === false ? "Needs HACS first." : "Check HACS first.", action: null }));
  else if (!appRepo && !(cats || []).includes("appdaemon")) rows.push(unknownFor({ ...appRow, status: "missing", detail: "Switch on AppDaemon discovery in HACS first (above).", action: null }));
  else rows.push(unknownFor({ ...appRow, status: "missing", detail: "Not installed.", action: install("app", "Install") }));

  // 5. PowerEngine running
  const ver = f.peVersion || null;
  rows.push({ key: "running", title: "PowerEngine running", why: "It publishes its version once it has started.", status: ver ? "ok" : "missing", needed: true,
    detail: ver ? `Version ${ver}.` : "Not running yet. After installing the app, restart the AppDaemon add-on.", action: null });

  // 6. Chart cards
  ["apex", "flow"].forEach((k) => {
    const def = SETUP_REPOS[k];
    const loaded = !!(f.cardsLoaded || {})[def.element];
    const repo = findHacsRepo(repos, def.full_name);
    const base = { key: k, title: def.label, why: k === "apex" ? "Draws the dashboard's charts." : "Draws the live energy-flow picture.", needed: true };
    if (loaded) rows.push({ ...base, status: "ok", detail: `Loaded (${def.full_name}).`, action: null });
    else if (repo && repo.installed) rows.push({ ...base, status: "missing", detail: "Installed. Reload this page to load it.", action: { kind: "reload", label: "Reload page" } });
    else rows.push(unknownFor({ ...base, status: hacsOk || f.hacs === false ? "missing" : "unknown",
      detail: hacsOk ? `Not installed (${def.full_name}).` : f.hacs === false ? "Needs HACS first." : `Check HACS first (${def.full_name}).`, action: install(k, "Install") }));
  });

  // 7. MQTT
  const mq = { key: "mqtt", title: "MQTT", why: "Needed for full use, not for the demo. PowerEngine shares its data through it.", needed: false };
  rows.push(comps.includes("mqtt") ? { ...mq, status: "ok", detail: "Set up.", action: null }
    : { ...mq, status: "missing", detail: "Not set up.", action: link(SETUP_LINKS.mqtt, "Set up MQTT") });

  // 8. Another battery controller: the app won't go Active until the owner has said whether there is one
  if (f.otherController === "unset") rows.push({ key: "controller", title: "Other battery controller", needed: true, status: "missing",
    why: "PowerEngine won't go Active until you choose.", detail: OTHER_UNSET_PROMPT, action: null });

  return rows;
}

/** "All set" when PowerEngine is running and the chart cards are loaded (MQTT is optional). */
function setupSummary(facts) {
  const rows = setupRows(facts);
  const ok = (k) => (rows.find((r) => r.key === k) || {}).status === "ok";
  const allSet = ok("running") && ok("apex") && ok("flow") && !rows.some((r) => r.key === "controller");
  return { allSet, line: allSet ? `All set. PowerEngine is running ${(facts || {}).peVersion}` : null,
    todo: rows.filter((r) => r.needed && r.status !== "ok").length };
}

class PowerEngineSetupCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._notes = this._notes || {};
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._check();          // once on load; never on a timer
    else this._render();
  }

  getCardSize() { return 4; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  _isAdmin() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }

  /** One round of checks. Together with _install, the only place that calls HACS or the Supervisor. */
  async _check() {
    const hass = this._hass;
    if (!hass || this._checking) return;
    this._checking = true;
    this._render();
    const comps = (hass.config && hass.config.components) || [];
    const s = { hacs: null, hacsReason: null, categories: null, addon: { state: "unknown" }, repos: null };
    if (this._isAdmin()) {
      if (!comps.includes("hacs")) s.hacs = false;
      else {
        try {
          const info = await hass.callWS(hacsInfoPayload());
          s.categories = info.categories || [];
          s.hacsReason = info.disabled_reason || null;
          s.hacs = !s.hacsReason;
          if (s.hacs) s.repos = await hass.callWS(hacsListPayload());
        } catch (err) { s.hacs = null; this._notes.hacs = "Couldn't ask HACS: " + ((err && err.message) || err); }
      }
      if (!comps.includes("hassio")) s.addon = { state: "unsupervised" };
      else {
        try { s.addon = addonFrom(await hass.callWS(addonsPayload())); }
        catch (err) { s.addon = { state: "unknown" }; }
      }
    }
    this._scan = s;
    this._checking = false;
    this._render();
  }

  async _install(key) {
    const hass = this._hass;
    const def = SETUP_REPOS[key];
    if (!hass || !def || this._busy) return;
    this._busy = key;
    delete this._notes[key];
    try {
      let repos = (this._scan || {}).repos || [];
      if (!findHacsRepo(repos, def.full_name)) {
        this._progress(key, "Adding it to HACS…");
        await hass.callWS(installStep(key, repos));
        for (let i = 0; i < 3 && !findHacsRepo(repos, def.full_name); i++) {
          if (i) await new Promise((res) => setTimeout(res, 1500));
          repos = await hass.callWS(hacsListPayload());
        }
        if (!findHacsRepo(repos, def.full_name)) throw new Error("HACS didn't add it. Check HACS's notifications and log.");
      }
      this._progress(key, "Downloading with HACS…");
      await hass.callWS(installStep(key, repos));
      this._notes[key] = key === "app"
        ? "Installed. Restart the AppDaemon add-on so it starts PowerEngine."
        : "Installed. Reload this page for the card to load.";
    } catch (err) {
      this._notes[key] = "Failed: " + ((err && err.message) || err);
    }
    this._busy = null;
    this._progressNote = null;
    await this._check();               // once, after the install finishes
  }

  _progress(key, text) { this._progressNote = { key, text }; this._render(); }

  _facts() {
    const hass = this._hass;
    const s = this._scan || {};
    const cardsLoaded = {};
    Object.values(SETUP_REPOS).forEach((d) => {
      if (d.element) cardsLoaded[d.element] = typeof customElements !== "undefined" && !!customElements.get(d.element);
    });
    return { isAdmin: this._isAdmin(), hacs: s.hacs === undefined ? null : s.hacs, hacsReason: s.hacsReason, categories: s.categories,
      addon: s.addon, repos: s.repos, cardsLoaded, components: (hass.config && hass.config.components) || [],
      peVersion: peRunning(hass.states),
      otherController: otherControllerValue(hass.states),
      setup: (((hass.states || {})[VERSION_SENSOR] || {}).attributes || {}).setup || null };
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const facts = this._facts();
    const sig = JSON.stringify([facts, (this._hass.states || {})[PACKAGE_SENSOR] || null, this._busy, this._checking, this._notes, this._progressNote]);
    if (sig === this._sig) return;      // hass updates come often; only redraw when something we show changed
    this._sig = sig;
    const rows = setupRows(facts);
    const sum = setupSummary(facts);
    const root = this.shadowRoot;
    // all set: nothing to do here, so the card takes no room (the update card shows the version)
    this.style.display = sum.allSet && !this._busy && !this._checking ? "none" : "";
    if (this.style.display === "none") { root.innerHTML = ""; return; }
    root.innerHTML = `
      <style>
        ha-card { padding: 12px 16px; display: block; }
        .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .head h2 { flex: 1 1 auto; margin: 0; font-size: 1.1em; font-weight: 500; }
        .btns { display: flex; gap: 8px; flex: 0 0 auto; flex-wrap: wrap; }
        .sub { color: var(--secondary-text-color); font-size: 0.9em; margin: 4px 0 8px; }
        .row { display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap; padding: 8px 0; border-top: 1px solid var(--divider-color); }
        .dot { flex: 0 0 22px; height: 22px; border-radius: 50%; color: #fff; text-align: center; line-height: 22px; font-size: 13px; font-weight: 600; }
        .ok .dot { background: var(--success-color, #43a047); }
        .missing .dot { background: var(--error-color, #db4437); }
        .missing.optional .dot { background: var(--warning-color, #f9a825); }
        .unknown .dot { background: var(--disabled-text-color, #9e9e9e); }
        .text { flex: 1 1 200px; min-width: 0; }
        .title { font-weight: 500; }
        .why, .detail { color: var(--secondary-text-color); font-size: 0.9em; overflow-wrap: anywhere; }
        .detail.note { color: var(--primary-text-color); }
        .act { flex: 0 0 auto; margin-left: 32px; }
        @media (min-width: 600px) { .act { margin-left: 0; } }
        button, a.btn { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--divider-color); text-decoration: none;
                        background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; display: inline-block; box-sizing: border-box; }
        button.second, a.btn.second { background: none; color: var(--primary-text-color); }
        button:disabled { opacity: .5; cursor: default; }
      </style>
      <ha-card><div class="head"><h2>PowerEngine setup</h2><div class="btns"></div></div><div class="sub"></div><div class="list"></div></ha-card>`;
    const q = (s) => root.querySelector(s);
    const head = q(".btns");
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
    const busy = !!this._busy || this._checking;
    const sub = q(".sub");
    if (!facts.isAdmin) {
      sub.textContent = "You're not an admin. An admin needs to run the installs; you can see what's missing here.";
    } else {
      sub.textContent = sum.todo ? `${sum.todo} thing${sum.todo === 1 ? "" : "s"} still to do.` : "";
    }
    const pkgLine = packageProblemLine(packageInfo(this._hass.states));
    if (pkgLine) {
      const note = mk("div", "detail note", pkgLine.text + " ");
      const l = mk("a", "", pkgLine.label);
      l.href = pkgLine.href; l.target = "_blank"; l.rel = "noopener noreferrer";
      note.append(l);
      sub.after(note);
    }
    if (showDemoLink(facts)) {
      const tryIt = mk("button", "second", "Try the demo");
      tryIt.title = "The demo is at the top of this page";
      tryIt.addEventListener("click", () => { if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" }); });
      head.append(tryIt);
    }
    const list = q(".list");
    rows.forEach((r) => {
      const row = mk("div", `row ${r.status}${r.needed ? "" : " optional"}`);
      row.append(mk("div", "dot", r.status === "ok" ? "✓" : r.status === "missing" ? (r.needed ? "✕" : "!") : "?"));
      const text = mk("div", "text");
      text.append(mk("div", "title", r.title), mk("div", "why", r.why));
      const prog = this._progressNote && this._progressNote.key === r.key ? this._progressNote.text : null;
      const note = prog || this._notes[r.key];
      if (r.detail) text.append(mk("div", "detail", r.detail));
      if (note) text.append(mk("div", "detail note", note));
      row.append(text);
      const a = r.action;
      if (a) {
        const box = mk("div", "act");
        if (a.kind === "link") {
          const l = mk("a", "btn second", a.label);
          l.href = a.href; l.target = "_blank"; l.rel = "noopener noreferrer";
          box.append(l);
        } else if (a.kind === "reload") {
          const b = mk("button", "", a.label);
          b.addEventListener("click", () => location.reload());
          box.append(b);
        } else {
          const b = mk("button", "", this._busy === a.target ? "Installing…" : a.label);
          b.disabled = busy;
          b.addEventListener("click", () => this._install(a.target));
          box.append(b);
        }
        row.append(box);
      }
      list.append(row);
    });
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-setup-card")) {
  customElements.define("powerengine-setup-card", PowerEngineSetupCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-setup-card", name: "PowerEngine setup",
    description: "Checks what PowerEngine needs and installs what HACS can install." });
}

// --- Demo card: welcome, banner, day picker and exit --------------------------------------------------------
// The controller puts this card first on every dashboard view. What it shows comes from sensor.pe_diag_version:
//   attributes.setup  "unconfigured" | "configured"
//   attributes.demo   null, or {day, title, days: [{key, title}], note}
// It asks for changes by firing the event pe_demo {action: "start" | "day" | "exit", day}, and hears the answer as the
// event pe_demo_result {ok, message}. Only admins can fire (HA's fire_event needs admin).
const DEMO_EVENT = "pe_demo";
const DEMO_RESULT_EVENT = "pe_demo_result";
const DEMO_NOTE = "Recorded data from a real home. Nothing is controlled.";
// Until the app says which days exist (the welcome only knows "unconfigured"), these match the demo pack's keys.
const DEMO_DAYS = [
  { key: "sunny", title: "Sunny day" }, { key: "dull", title: "Dull day" },
  { key: "axle", title: "<<event>> event day" }, { key: "car", title: "Car charging day" },
];
const DEMO_WAIT = "Working on it… (about a minute; the page reloads when it's done)";
// The demo switches the dashboard file (the demo view, the flow card's plants), which HA only reads when the page loads.
const DEMO_RELOAD_DELAY_MS = 3000;    // the app writes the dashboard just after it publishes the change

/** Should the page reload? Only when this card sent a start, day change or exit (`pending`) and the app's answer has
 *  since changed the demo state. A page that was just reloaded has no pending, so it can't loop. */
function demoNeedsReload(pending, stateBefore, stateNow) {
  return !!pending && pending.from !== undefined && pending.from !== stateNow && stateBefore !== undefined;
}

function capFirst(text) { const s = String(text); return s.charAt(0).toUpperCase() + s.slice(1); }

/** Which of the card's three modes to show, and what goes in it. attrs: the version sensor's attributes. */
function demoView(attrs, isAdmin) {
  const a = attrs || {};
  const names = a.names && typeof a.names === "object" ? a.names : null;
  const title = (t) => capFirst(fillNames(t, names));
  const canAct = !!isAdmin;
  const demo = a.demo && typeof a.demo === "object" ? a.demo : null;
  if (demo) {
    const list = Array.isArray(demo.days) && demo.days.length ? demo.days : [];
    return { mode: "banner", canAct, title: title(demo.title || demo.day || "demo"), day: demo.day || null,
      note: demo.note || DEMO_NOTE,
      days: list.filter((d) => d && d.key).map((d) => ({ key: d.key, title: title(d.title || d.key), current: d.key === demo.day })) };
  }
  if (a.setup === "unconfigured") {
    // the app lists the days titled as the demo will show them (its own names), so the welcome and the banner agree;
    // an older app doesn't, and the fixed list is filled from the names map it does publish
    const offered = Array.isArray(a.demo_days) ? a.demo_days.filter((d) => d && d.key) : [];
    const days = offered.length ? offered : DEMO_DAYS;
    return { mode: "welcome", canAct, days: days.map((d, i) => ({ key: d.key, title: title(d.title || d.key), current: i === 0 })) };
  }
  return { mode: "hidden", canAct, days: [] };
}

/** The fire_event call for a demo action, or null if the action or day isn't valid. */
function demoEventPayload(action, day) {
  if (!["start", "day", "exit"].includes(action)) return null;
  const data = { action };
  if (action !== "exit") {
    if (!day || typeof day !== "string") return null;
    data.day = day;
  }
  return { type: "fire_event", event_type: DEMO_EVENT, event_data: data };
}

/** Where the Configuration view is, on the dashboard the visitor is on: /<dashboard>/config. */
function configPath(pathname) {
  const seg = String(pathname || "").split("/").filter(Boolean)[0];
  return seg ? `/${seg}/config` : "/powerengine/config";
}

/** The setup card offers the demo when PowerEngine runs but nothing is set up yet. */
function showDemoLink(facts) {
  const f = facts || {};
  return !!f.peVersion && f.setup === "unconfigured";
}

class PowerEngineDemoCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._selected = this._selected || null;
  }

  // HA removes a hidden card's element from the page unless it says it wants to stay connected. We must stay, or the
  // card could never come back when a demo starts.
  get connectedWhileHidden() { return true; }

  getCardSize() { return this._mode === "hidden" ? 0 : 2; }

  getGridOptions() {
    return this._mode === "hidden" ? { columns: "full", rows: 1, min_rows: 0 } : { columns: "full", rows: "auto" };
  }

  connectedCallback() { if (this._hass) this._subscribe(); }

  disconnectedCallback() { this._unsubscribe(); }

  set hass(hass) {
    this._hass = hass;
    this._subscribe();
    this._render();
  }

  _subscribe() {
    const conn = this._hass && this._hass.connection;
    if (this._unsub || this._subscribing || !conn || !conn.subscribeEvents) return;
    this._subscribing = true;
    conn.subscribeEvents((ev) => this._result(ev && ev.data), DEMO_RESULT_EVENT).then((unsub) => {
      this._subscribing = false;
      if (!this.isConnected) unsub();          // removed while we were subscribing
      else this._unsub = unsub;
    }, () => { this._subscribing = false; });
  }

  _unsubscribe() {
    if (this._unsub) { try { this._unsub(); } catch (e) { /* connection already gone */ } }
    this._unsub = null;
  }

  _result(data) {
    const d = data || {};
    this._msg = { ok: d.ok !== false, text: String(d.message || (d.ok === false ? "That didn't work." : "Done.")) };
    if (d.ok === false) this._pending = null;
    this._render();
  }

  async _fire(action, day) {
    const payload = demoEventPayload(action, day);
    if (!payload || !this._hass) return;
    const view = demoView(this._attrs(), this._isAdmin());
    if (!view.canAct) return;
    this._msg = null;
    this._confirm = false;
    this._pending = { text: DEMO_WAIT, from: this._sig0 };
    this._render();
    try {
      await this._hass.callWS(payload);
    } catch (err) {
      this._pending = null;
      this._msg = { ok: false, text: "Couldn't send that: " + ((err && err.message) || err) };
      this._render();
    }
  }

  _isAdmin() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }

  _reloadOnce() {
    if (this._reloading) return;
    this._reloading = true;
    this._msg = { ok: true, text: "Done. Reloading the page…" };
    setTimeout(() => { try { location.reload(); } catch (e) { /* not in a page */ } }, DEMO_RELOAD_DELAY_MS);
  }

  _attrs() {
    const s = ((this._hass || {}).states || {})[VERSION_SENSOR];
    return (s && s.attributes) || {};
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const attrs = this._attrs();
    const view = demoView(attrs, this._isAdmin());
    this._mode = view.mode;
    const state0 = JSON.stringify([attrs.setup, attrs.demo && attrs.demo.day, !!attrs.demo]);
    if (this._pending && this._pending.from !== undefined && this._pending.from !== state0) {
      const reload = demoNeedsReload(this._pending, this._pending.from, state0);
      this._pending = null;                        // the sensor moved on: the start, day change or exit happened
      this._msg = null;
      if (reload) this._reloadOnce();
    }
    this._sig0 = state0;
    const editing = !!this.editMode;
    const hide = view.mode === "hidden" && !editing;
    this.hidden = hide;                            // HA's card wrapper hides its tile when the card says so
    if (hide !== this._wasHidden) {
      this._wasHidden = hide;
      this.dispatchEvent(new CustomEvent("card-visibility-changed", { detail: { value: !hide }, bubbles: true, composed: true }));
    }
    const sig = JSON.stringify([view, this._selected, this._pending, this._msg, this._confirm, editing]);
    if (sig === this._sig) return;
    this._sig = sig;
    const root = this.shadowRoot;
    if (hide) { root.innerHTML = ""; return; }
    root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { display: block; padding: 10px 16px; }
        .banner { background: color-mix(in srgb, var(--primary-color, #03a9f4) 16%, var(--card-background-color, #fff));
                  border-left: 5px solid var(--primary-color, #03a9f4); }
        .row { display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap; }
        .text { flex: 1 1 260px; min-width: 0; }
        .text b { font-weight: 600; }
        h2 { margin: 0 0 4px; font-size: 1.1em; font-weight: 500; }
        .sub, .msg { color: var(--secondary-text-color); font-size: 0.9em; margin-top: 4px; overflow-wrap: anywhere; }
        .msg.bad { color: var(--error-color, #db4437); }
        .chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
        .section { padding: 8px 0; border-top: 1px solid var(--divider-color); }
        .section:first-of-type { border-top: 0; }
        button, a.btn { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--divider-color); cursor: pointer;
                        background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); text-decoration: none;
                        display: inline-block; box-sizing: border-box; }
        button.second, a.btn.second, button.chip { background: none; color: var(--primary-text-color); }
        button.chip { border-radius: 16px; padding: 4px 12px; }
        button.chip.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: var(--primary-color, #03a9f4); }
        button:disabled { opacity: .5; cursor: default; }
      </style>
      <ha-card class="${view.mode}"></ha-card>`;
    const card = root.querySelector("ha-card");
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
    const busy = !!this._pending;
    const chips = (list, currentKey, onPick) => {
      const box = mk("div", "chips");
      list.forEach((d) => {
        const on = d.key === currentKey;
        const b = mk("button", "chip" + (on ? " on" : ""), d.title);
        b.setAttribute("aria-pressed", on ? "true" : "false");
        if (!view.canAct || busy) b.disabled = true;
        b.addEventListener("click", () => onPick(d.key));
        box.append(b);
      });
      return box;
    };
    const status = (parent) => {
      const note = this._pending ? this._pending.text : this._msg ? this._msg.text : "";
      if (note) parent.append(mk("div", "msg" + (this._msg && !this._msg.ok && !this._pending ? " bad" : ""), note));
    };
    if (view.mode === "hidden") {
      card.append(mk("div", "sub", "PowerEngine demo card: shows a welcome when PowerEngine isn't set up yet, and a banner during the demo. Nothing to show now."));
    } else if (view.mode === "banner") {
      const row = mk("div", "row");
      const text = mk("div", "text");
      const line = mk("div");
      line.append(mk("b", "", `Demo: ${view.title}.`), document.createTextNode(" " + view.note));
      text.append(line);
      status(text);
      row.append(text);
      if (view.canAct) {
        const act = mk("div", "row");
        if (this._confirm) {
          act.append(mk("span", "", "Leave the demo? Settings you changed are reset."));
          const yes = mk("button", "", "Leave demo");
          yes.disabled = busy;
          yes.addEventListener("click", () => this._fire("exit"));
          const no = mk("button", "second", "Stay");
          no.addEventListener("click", () => { this._confirm = false; this._render(); });
          act.append(yes, no);
        } else {
          const exit = mk("button", "second", "Exit demo");
          exit.disabled = busy;
          exit.addEventListener("click", () => { this._confirm = true; this._render(); });
          act.append(exit);
        }
        row.append(act);
      }
      card.append(row);
      if (view.days.length > 1 && view.canAct) card.append(chips(view.days, view.day, (k) => this._fire("day", k)));
    } else {
      const pick = this._selected || view.days[0].key;
      const a = mk("div", "section");
      a.append(mk("h2", "", "PowerEngine is installed but not set up yet."));
      a.append(mk("div", "sub", "Have a look around first, or set up your own system."));
      const demo = mk("div", "section");
      demo.append(mk("b", "", "Try the demo"));
      demo.append(mk("div", "sub", "Uses recorded data from a real home and controls nothing. Pick a day to start with:"));
      demo.append(chips(view.days, pick, (k) => { this._selected = k; this._render(); }));
      if (view.canAct) {
        const go = mk("button", "", busy ? "Starting…" : "Start the demo");
        go.disabled = busy;
        go.addEventListener("click", () => this._fire("start", pick));
        demo.append(go);
      } else demo.append(mk("div", "sub", "An admin can start the demo."));
      status(demo);
      const setup = mk("div", "section");
      setup.append(mk("b", "", "Set up your system"));
      setup.append(mk("div", "sub", "Open the Configuration page to connect your inverter and meters. The PowerEngine setup card there checks what is installed."));
      const path = configPath(typeof location !== "undefined" ? location.pathname : "");
      const link = mk("a", "btn second", "Open Configuration");
      link.href = path;
      link.addEventListener("click", (ev) => {
        ev.preventDefault();
        history.pushState(null, "", path);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      });
      setup.append(link);
      card.append(a, demo, setup);
    }
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-demo-card")) {
  customElements.define("powerengine-demo-card", PowerEngineDemoCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-demo-card", name: "PowerEngine demo",
    description: "Welcome and demo banner: try PowerEngine on recorded data before setting it up." });
}

// --- Manual override (Monitoring page) --------------------------------------------------------------------------
// Switches the inverter to Self-use, Hold, Charge or Export for a while, or until cancelled. Fires the event
// pe_override {action: "set", mode, window | slots | until | permanent} or {action: "clear"} (admin only, like the demo)
// and hears pe_override_result {ok, message}. The app (pe_core/override.py) does the checks; this only offers valid
// choices. Hidden when the app publishes no sensor.pe_state_override (an older app). Plan: the app repo's
// docs/plans/mode-override.md.
const OVERRIDE_EVENT = "pe_override";
const OVERRIDE_RESULT_EVENT = "pe_override_result";
const OVERRIDE_SENSOR = "sensor.pe_state_override";
const OVERRIDE_MODES = [
  { key: "self_use", label: "Self-use", help: "The battery covers the house." },
  { key: "hold", label: "Hold", help: "The grid runs the house; the battery is kept." },
  { key: "grid_charge", label: "Charge", help: "Charge the battery from the grid to the charge target." },
  { key: "export", label: "Export", help: "Sell from the battery to the grid." },
];
const OVERRIDE_PERIODS = [
  { key: "window", label: "This plan window" },
  { key: "slots", label: "Half-hours" },
  { key: "until", label: "Until a time" },
  { key: "permanent", label: "Permanent" },
];
const OVERRIDE_MAX_SLOTS = 24;      // 12 hours
const INVERTER_WORDS = {
  self_use: "Self-use", grid_charge: "Charging from the grid", hold: "Holding (grid runs the house)",
  force_discharge: "Force discharging", export: "Exporting", none: "No decision",
};

/** What the inverter is doing, in words, from the decision sensor's state. */
function inverterWords(decision) {
  return INVERTER_WORDS[decision] || (decision && decision !== "unknown" && decision !== "unavailable" ? String(decision) : "Unknown");
}

/** The half-hour boundaries from the end of the half-hour now running, up to 12 hours on: [{iso, label}]. `tz` is
 *  HA's time zone (hass.config.time_zone); a bad or missing one falls back to the browser's. */
function overrideEndOptions(now, tz) {
  const t = new Date(now instanceof Date ? now.getTime() : now);
  t.setUTCSeconds(0, 0);
  t.setUTCMinutes(t.getUTCMinutes() < 30 ? 30 : 60);       // next boundary (half-hours are UTC half-hours everywhere)
  let fmt;
  try { fmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz || undefined }); }
  catch (e) { fmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false }); }
  const out = [];
  for (let i = 0; i < OVERRIDE_MAX_SLOTS; i += 1) {
    out.push({ iso: t.toISOString(), label: fmt.format(t) });
    t.setUTCMinutes(t.getUTCMinutes() + 30);
  }
  return out;
}

/** The fire_event call for a choice {mode, period, slots, until}, or null when it isn't valid. */
function overridePayload(choice) {
  const c = choice || {};
  if (c.clear) return { type: "fire_event", event_type: OVERRIDE_EVENT, event_data: { action: "clear" } };
  if (!OVERRIDE_MODES.some((m) => m.key === c.mode)) return null;
  const data = { action: "set", mode: c.mode };
  if (c.period === "window") data.window = true;
  else if (c.period === "permanent") data.permanent = true;
  else if (c.period === "slots") {
    const n = Number(c.slots);
    if (!Number.isInteger(n) || n < 1 || n > OVERRIDE_MAX_SLOTS) return null;
    data.slots = n;
  } else if (c.period === "until") {
    if (!c.until || Number.isNaN(Date.parse(c.until))) return null;
    data.until = c.until;
  } else return null;
  return { type: "fire_event", event_type: OVERRIDE_EVENT, event_data: data };
}

/** What the card shows. states: hass.states. */
function overrideView(states, isAdmin) {
  const st = states || {};
  const ov = st[OVERRIDE_SENSOR];
  if (!ov) return { shown: false };
  const a = ov.attributes || {};
  const active = ov.state && ov.state !== "none" && ov.state !== "unknown" && ov.state !== "unavailable";
  const op = st["sensor.pe_state_operation_mode"];
  const isActive = !!op && op.state === "active";
  const dec = st["sensor.pe_state_decision"];
  const rate = st["sensor.pe_state_import_rate"];
  const p = rate && rate.attributes && rate.attributes.pence != null ? Number(rate.attributes.pence) : null;
  let blocked = "";
  if (!isAdmin) blocked = "Only an admin user can change the override.";
  else if (!isActive && !active) blocked = "An override works only while PowerEngine is Active.";
  return {
    shown: true, active: !!active, text: active ? String(a.text || ov.state) : "", mode: active ? ov.state : null,
    inverter: inverterWords(dec && dec.state), canAct: !blocked, blocked, canCancel: !!isAdmin && !!active,
    priceNow: p != null && Number.isFinite(p) ? `${Number(p.toFixed(2))}p` : null,
  };
}

/** The line shown before applying: what the choice means and, for Charge, the price being paid now. */
function overrideSummary(choice, priceNow) {
  const m = OVERRIDE_MODES.find((x) => x.key === (choice || {}).mode);
  if (!m) return "";
  let line = m.help;
  if (m.key === "grid_charge" && priceNow) line += ` The grid rate now is ${priceNow}.`;
  if (m.key === "export") line += " It stops at the minimum reserve.";
  if ((choice || {}).period === "permanent") line += " It stays until you cancel it.";
  line += " A grid event in progress still takes over.";
  return line;
}

class PowerEngineOverrideCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._choice = this._choice || { mode: "hold", period: "window", slots: 2 };
    this._open = !!this._open;
  }

  get connectedWhileHidden() { return true; }

  getCardSize() { return 2; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  connectedCallback() { if (this._hass) this._subscribe(); }

  disconnectedCallback() {
    if (this._unsub) { try { this._unsub(); } catch (e) { /* connection already gone */ } }
    this._unsub = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._subscribe();
    this._render();
  }

  _subscribe() {
    const conn = this._hass && this._hass.connection;
    if (this._unsub || this._subscribing || !conn || !conn.subscribeEvents) return;
    this._subscribing = true;
    conn.subscribeEvents((ev) => this._result(ev && ev.data), OVERRIDE_RESULT_EVENT).then((unsub) => {
      this._subscribing = false;
      if (!this.isConnected) unsub();
      else this._unsub = unsub;
    }, () => { this._subscribing = false; });
  }

  _result(data) {
    const d = data || {};
    this._busy = false;
    this._msg = { ok: d.ok !== false, text: String(d.message || (d.ok === false ? "That didn't work." : "Done.")) };
    if (d.ok !== false) this._open = false;
    this._render();
  }

  async _fire(choice) {
    const payload = overridePayload(choice);
    if (!payload || !this._hass) return;
    this._busy = true;
    this._msg = null;
    this._render();
    try {
      await this._hass.callWS(payload);
    } catch (err) {
      this._busy = false;
      this._msg = { ok: false, text: "Couldn't send that: " + ((err && err.message) || err) };
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const isAdmin = !!(this._hass.user && this._hass.user.is_admin);
    const view = overrideView(this._hass.states, isAdmin);
    const hide = !view.shown && !this.editMode;
    this.hidden = hide;
    if (hide !== this._wasHidden) {
      this._wasHidden = hide;
      this.dispatchEvent(new CustomEvent("card-visibility-changed", { detail: { value: !hide }, bubbles: true, composed: true }));
    }
    const tz = this._hass.config && this._hass.config.time_zone;
    const sig = JSON.stringify([view, this._choice, this._open, this._busy, this._msg, hide]);
    if (sig === this._sig) return;
    this._sig = sig;
    const root = this.shadowRoot;
    if (hide) { root.innerHTML = ""; return; }
    root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { display: block; padding: 10px 16px; }
        .row { display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap; }
        .text { flex: 1 1 220px; min-width: 0; overflow-wrap: anywhere; }
        .label { color: var(--secondary-text-color); font-size: 0.85em; }
        .big { font-size: 1.1em; font-weight: 500; }
        .on-banner { margin-top: 8px; padding: 6px 10px; border-left: 4px solid var(--warning-color, #ff9800);
                     background: color-mix(in srgb, var(--warning-color, #ff9800) 14%, var(--card-background-color, #fff)); }
        .panel { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
        .sub, .msg { color: var(--secondary-text-color); font-size: 0.9em; margin-top: 6px; overflow-wrap: anywhere; }
        .msg.bad { color: var(--error-color, #db4437); }
        .chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
        h3 { margin: 8px 0 0; font-size: 0.95em; font-weight: 500; }
        button, select { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--divider-color); cursor: pointer;
                         background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
        select { background: var(--card-background-color, #fff); color: var(--primary-text-color); padding: 5px 8px; }
        button.second, button.chip { background: none; color: var(--primary-text-color); }
        button.chip { border-radius: 16px; padding: 4px 12px; }
        button.chip.on { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: var(--primary-color, #03a9f4); }
        button:disabled, select:disabled { opacity: .5; cursor: default; }
      </style>
      <ha-card></ha-card>`;
    const card = root.querySelector("ha-card");
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
    const head = mk("div", "row");
    const text = mk("div", "text");
    text.append(mk("div", "label", "Inverter"), mk("div", "big", view.inverter || "Unknown"));
    head.append(text);
    const btn = mk("button", this._open ? "second" : "", this._open ? "Close" : "Override");
    btn.disabled = !view.canAct || this._busy;
    if (view.blocked) btn.title = view.blocked;
    btn.addEventListener("click", () => { this._open = !this._open; this._msg = null; this._render(); });
    head.append(btn);
    card.append(head);
    if (view.active) {
      const b = mk("div", "on-banner row");
      b.append(mk("div", "text", `Manual override: ${view.text}`));
      const cancel = mk("button", "second", "Cancel override");
      cancel.disabled = !view.canCancel || this._busy;
      cancel.addEventListener("click", () => this._fire({ clear: true }));
      b.append(cancel);
      card.append(b);
    }
    if (this._open) {
      const c = this._choice;
      const panel = mk("div", "panel");
      const chips = (list, current, set) => {
        const box = mk("div", "chips");
        list.forEach((o) => {
          const on = o.key === current;
          const ch = mk("button", "chip" + (on ? " on" : ""), o.label);
          ch.setAttribute("aria-pressed", on ? "true" : "false");
          ch.addEventListener("click", () => { set(o.key); this._render(); });
          box.append(ch);
        });
        return box;
      };
      panel.append(mk("h3", "", "Switch the inverter to"), chips(OVERRIDE_MODES, c.mode, (k) => { c.mode = k; }));
      panel.append(mk("h3", "", "For"), chips(OVERRIDE_PERIODS, c.period, (k) => { c.period = k; }));
      if (c.period === "slots") {
        const sel = mk("select");
        for (let n = 1; n <= OVERRIDE_MAX_SLOTS; n += 1) {
          const o = mk("option", "", n === 1 ? "1 half-hour (to the end of this one)" : `${n} half-hours (${n / 2} h)`);
          o.value = String(n);
          if (n === Number(c.slots)) o.selected = true;
          sel.append(o);
        }
        sel.addEventListener("change", () => { c.slots = Number(sel.value); this._render(); });
        panel.append(sel);
      } else if (c.period === "until") {
        const opts = overrideEndOptions(new Date(), tz);
        if (!c.until || !opts.some((o) => o.iso === c.until)) c.until = opts[0].iso;
        const sel = mk("select");
        opts.forEach((o) => { const op = mk("option", "", o.label); op.value = o.iso; if (o.iso === c.until) op.selected = true; sel.append(op); });
        sel.addEventListener("change", () => { c.until = sel.value; this._render(); });
        panel.append(sel);
      }
      panel.append(mk("div", "sub", overrideSummary(c, view.priceNow)));
      const go = mk("button", "", this._busy ? "Applying…" : "Apply");
      go.disabled = this._busy || !overridePayload(c);
      go.addEventListener("click", () => this._fire(c));
      const act = mk("div", "row");
      act.style.marginTop = "8px";
      act.append(go);
      panel.append(act);
      card.append(panel);
    }
    if (this._msg) card.append(mk("div", "msg" + (this._msg.ok ? "" : " bad"), this._msg.text));
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-override-card")) {
  customElements.define("powerengine-override-card", PowerEngineOverrideCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-override-card", name: "PowerEngine override",
    description: "What the inverter is doing, with a button to override it for a while." });
}

// --- Health findings with Dismiss, and PowerEngine's log (Health tab) -------------------------------------------
function escHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function logWhen(iso, now) {
  const d = new Date(iso);
  if (isNaN(d)) return iso || "";
  const p = (n) => String(n).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  const today = (now || new Date()).toDateString() === d.toDateString();
  return today ? hm : `${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
}

function logRows(attrs, warningsOnly) {
  const a = attrs || {};
  return (warningsOnly ? a.warnings : a.recent) || [];
}

class PowerEngineHealthCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states["sensor.pe_diag_health"];
    const vs = hass.states[VERSION_SENSOR];
    const sig = (st ? st.last_updated + st.state : "") + (vs ? vs.state + ((vs.attributes || {}).min_card_version || "") : "");
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  getCardSize() { return 3; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  async _dismiss(key) {
    try {
      await this._hass.callWS({ type: "fire_event", event_type: "pe_health_dismiss", event_data: { key } });
    } catch (err) {
      this._err = "Couldn't dismiss (admin users only): " + ((err && err.message) || err);
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const st = this._hass.states["sensor.pe_diag_health"];
    const a = (st && st.attributes) || {};
    const f = a.findings || [];
    const admin = !!(this._hass.user && this._hass.user.is_admin);
    let body;
    if (st && st.state === NOT_SET_UP) {
      body = `<p><b>${NOT_SET_UP}.</b> Health checks start once PowerEngine is set up.</p>`;
    } else if (!st || !["ok", "warnings", "problems"].includes(st.state)) {
      body = "<p>Health checks run after start-up and each night.</p>";
    } else if (!f.length) {
      body = "<p><b>All clear.</b> Inputs look healthy and yesterday's data adds up.</p>";
    } else {
      body = `<p><b>${f.length} thing${f.length === 1 ? "" : "s"} to look at${st.state === "problems" ? " (including problems)" : ""}:</b></p><ul>` +
        f.map((x) => `<li><span class="lvl ${x.level === "problem" ? "p" : "w"}">${x.level === "problem" ? "Problem" : "Check"}</span>
          <b>${escHtml(x.title)}.</b> ${escHtml(x.detail)}
          ${admin && x.key ? `<button data-key="${escHtml(x.key)}">Dismiss</button>` : ""}</li>`).join("") + "</ul>";
    }
    const gone = (a.dismissed || []).length
      ? `<p class="small">Dismissed recently: ${a.dismissed.map((d) => escHtml(d.title)).join("; ")}. A new or changed finding shows again.</p>` : "";
    const vw = versionWarnings(this._hass.states).map((w) => `<p><span class="lvl p">Problem</span> ${escHtml(w)}</p>`).join("");
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 16px; }
        ul { padding-left: 18px; margin: 4px 0; }
        li { margin: 6px 0; }
        .lvl { font-size: 0.8em; padding: 1px 6px; border-radius: 8px; margin-right: 4px; color: #fff; }
        .lvl.p { background: var(--error-color, #db4437); }
        .lvl.w { background: var(--warning-color, #ffa600); }
        button { font: inherit; font-size: 0.85em; margin-left: 6px; padding: 2px 10px; border-radius: 6px;
                 border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); cursor: pointer; }
        .small, .err { color: var(--secondary-text-color); font-size: 0.85em; }
        .err { color: var(--error-color, #db4437); }
      </style>
      <ha-card>${vw}${body}${gone}${this._err ? `<p class="err">${escHtml(this._err)}</p>` : ""}</ha-card>`;
    this.shadowRoot.querySelectorAll("button[data-key]").forEach((b) =>
      b.addEventListener("click", () => this._dismiss(b.dataset.key)));
  }
}

class PowerEngineLogCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    this._warn = this._config.warnings_only !== false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states["sensor.pe_diag_log"];
    const sig = st ? st.last_updated : "";
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  getCardSize() { return 6; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const st = this._hass.states["sensor.pe_diag_log"];
    const rows = logRows(st && st.attributes, this._warn);
    const n = (st && st.attributes && st.attributes.warning_count) || 0;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 16px; }
        .top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
        .top b { flex: 1; }
        button { font: inherit; font-size: 0.85em; padding: 3px 10px; border-radius: 6px; cursor: pointer;
                 border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); }
        button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        table { width: 100%; border-collapse: collapse; font-size: 0.88em; }
        td { padding: 3px 6px 3px 0; vertical-align: top; border-top: 1px solid var(--divider-color); }
        td.t { white-space: nowrap; color: var(--secondary-text-color); width: 1%; }
        tr.W td.m, tr.E td.m { color: var(--warning-color, #ffa600); }
        tr.E td.m { color: var(--error-color, #db4437); }
        .none { color: var(--secondary-text-color); }
      </style>
      <ha-card>
        <div class="top"><b>PowerEngine log</b>
          <button class="w ${this._warn ? "on" : ""}">Warnings (${n})</button>
          <button class="a ${this._warn ? "" : "on"}">All recent</button></div>
        ${rows.length ? `<table>${rows.map((r) => `<tr class="${escHtml(r.l)}"><td class="t">${escHtml(logWhen(r.t))}</td>
          <td class="m">${escHtml(r.m)}</td></tr>`).join("")}</table>`
          : `<p class="none">${st ? "Nothing to show." : "The log appears once PowerEngine 0.9.49 or later is running."}</p>`}
        <p class="none">Newest first; the full log is in the diagnostics export.</p>
      </ha-card>`;
    this.shadowRoot.querySelector("button.w").addEventListener("click", () => { this._warn = true; this._render(); });
    this.shadowRoot.querySelector("button.a").addEventListener("click", () => { this._warn = false; this._render(); });
  }
}

if (typeof customElements !== "undefined") {
  if (!customElements.get("powerengine-health-card")) customElements.define("powerengine-health-card", PowerEngineHealthCard);
  if (!customElements.get("powerengine-log-card")) customElements.define("powerengine-log-card", PowerEngineLogCard);
}

/* ------------------------------------------------------------ waterfall card
 * The Costs page's savings waterfall: what each feature saved, period by period (from sensor.pe_cost_waterfall's
 * `periods` attribute, built by pe_core.costs.waterfall).
 *   type: custom:powerengine-waterfall-card
 *   entity: sensor.pe_cost_waterfall
 *   period: week            (today | yesterday | week | month | days30 | custom; a button row also switches it)
 *
 * "Custom" totals any range of dates inside the last 31 days from the sensor's `daily` rows, with the same
 * steps the app builds for the fixed periods (pe_core.costs._steps_for).
 */

function waterfallRows(steps) {
  // steps: [{label, kind, value}] (kind: total | subtotal | step). Returns [{label, kind, from, to, value}]:
  // a total/subtotal bar runs from 0 to its value and resets the running total; a step bar floats between the
  // running total before and after it.
  let running = 0;
  return (steps || []).map((s) => {
    if (s.kind === "total" || s.kind === "subtotal") {
      running = s.value;
      return { label: s.label, kind: s.kind, from: 0, to: s.value, value: s.value };
    }
    const from = running;
    running += s.value;
    return { label: s.label, kind: s.kind, from, to: running, value: s.value };
  });
}

function waterfallScale(rows) {
  // The x-axis range for a set of rows: always includes 0 (so a day you earned money still shows the zero line).
  let min = 0;
  let max = 0;
  for (const r of rows) {
    min = Math.min(min, r.from, r.to);
    max = Math.max(max, r.from, r.to);
  }
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.08 || 1;
  return { min: min - pad, max: max + pad };
}

const WATERFALL_PERIODS = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "week", label: "7 days" },
  { key: "month", label: "This month" },
  { key: "days30", label: "30 days" },
  { key: "custom", label: "Custom" },
];

const WATERFALL_FIELDS = ["none", "solar", "tariff", "actual_adj", "carry", "events_metered", "axle_income"];

function waterfallCustom(daily, from, to, names) {
  // The steps for the days from..to (YYYY-MM-DD, inclusive) out of the app's per-day rows.
  return waterfallSteps((daily || []).filter((d) => d.date >= from && d.date <= to), names);
}

function waterfallSteps(chosen, names) {
  // The steps for some days' rows. Mirrors pe_core.costs._steps_for: each running total is rounded once, so steps
  // and totals chain to the penny.
  const sums = {};
  for (const k of WATERFALL_FIELDS) sums[k] = chosen.reduce((a, d) => a + (d[k] || 0), 0);
  const r2 = (x) => Math.round(x * 100) / 100 + 0;
  const nm = Object.assign({ tariff: "tariff", event: "Event" }, names);
  const r0 = r2(sums.none);
  const r1 = r2(sums.solar);
  const rTariff = r2(sums.tariff);
  const r5 = r2(sums.actual_adj + sums.carry);
  const r6 = r2(sums.actual_adj + sums.carry + sums.events_metered - sums.axle_income);
  const steps = [
    { label: "No solar or battery", kind: "total", value: r0 },
    { label: "Solar", kind: "step", value: r2(r1 - r0) },
    { label: nm.tariff, kind: "step", value: r2(rTariff - r1) },
    { label: "PowerEngine", kind: "step", value: r2(r5 - rTariff) },
  ];
  if (r2(sums.events_metered - sums.axle_income) !== 0) {
    steps.push({ label: `${nm.event} & free power`, kind: "step", value: r2(r6 - r5) });
  }
  const verb = r6 < -0.005 ? "You were paid" : "You paid";
  steps.push({ label: r2(sums.axle_income) > 0 ? `${verb} (after ${nm.event} payments)` : verb, kind: "total", value: r6 });
  return { days: chosen.length, from: chosen.length ? chosen[0].date : null,
           to: chosen.length ? chosen[chosen.length - 1].date : null, steps };
}


/* ------------------------------------------------------------ the Costs page's shared period
 * One choice of period (Today, Yesterday, 7 days, This month, 30 days, or two dates) for every card on the Costs page
 * that shows a period: the picker card sets it, the others follow. Kept in this browser (localStorage, tried and
 * caught) and announced on `window` so cards on the page redraw together. All of it is worked out from the per-day rows
 * the app publishes (sensor.pe_cost_waterfall `daily`).
 */
const COSTS_PERIOD_KEY = "powerengine-costs-period";
const COSTS_PERIOD_EVENT = "powerengine-costs-period";
const COSTS_PERIOD_LABELS = { today: "Today so far", yesterday: "Yesterday", week: "Last 7 days", month: "This month so far", days30: "Last 30 days" };

function costsPeriodLoad() {
  try {
    const raw = JSON.parse(localStorage.getItem(COSTS_PERIOD_KEY) || "null");
    if (raw && WATERFALL_PERIODS.some((p) => p.key === raw.key)) return raw;
  } catch (e) { /* no storage */ }
  return { key: "week" };
}
function costsPeriodSet(sel) {
  try { localStorage.setItem(COSTS_PERIOD_KEY, JSON.stringify(sel)); } catch (e) { /* private mode: it still works for now */ }
  window.dispatchEvent(new CustomEvent(COSTS_PERIOD_EVENT, { detail: sel }));
}

function costsDayLabel(date) {
  const d = new Date(`${date}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? String(date) : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}

function costsRange(sel, daily) {
  // The days a period covers: {key, label, rows, from, to}. As the app's own periods: Yesterday, 7 days, 30 days and
  // This month count complete days only; Today is the unfinished day; two dates take every day between them.
  const all = Array.isArray(daily) ? daily : [];
  const done = all.filter((d) => d.done);
  const key = (sel && sel.key) || "week";
  let rows;
  if (key === "today") rows = all.slice(-1).filter((d) => !d.done);
  else if (key === "yesterday") rows = done.slice(-1);
  else if (key === "days30") rows = done.slice(-30);
  else if (key === "month") {
    const month = all.length ? all[all.length - 1].date.slice(0, 7) : "";
    rows = done.filter((d) => d.date.startsWith(month));
  } else if (key === "custom") {
    const from = (sel && sel.from) || (all.length ? all[Math.max(0, all.length - 7)].date : "");
    const to = (sel && sel.to) || (all.length ? all[all.length - 1].date : "");
    rows = all.filter((d) => d.date >= from && d.date <= to);
  } else rows = done.slice(-7);
  const from = rows.length ? rows[0].date : null;
  const to = rows.length ? rows[rows.length - 1].date : null;
  let label = COSTS_PERIOD_LABELS[key];
  if (key === "custom") label = from ? (from === to ? costsDayLabel(from) : `${costsDayLabel(from)} to ${costsDayLabel(to)}`) : "Those dates";
  return { key: key === "custom" || COSTS_PERIOD_LABELS[key] ? key : "week", label: label || COSTS_PERIOD_LABELS.week, rows, from, to };
}

function costsSteps(range, names) {
  return waterfallSteps(range.rows, names);
}

function stepValue(steps, label) {
  const s = steps.find((x) => x.label === label);
  return s ? s.value : 0;
}

/** The sentences under the picker for the chosen period. `today` is {halfHours, axle:{kwh,gross,net}, free:{kwh,net},
 *  lastEvent:{time,end}} from the day-so-far sensors, used when the period includes today. Pure. */
function costsSummary(range, names, today) {
  const nm = Object.assign({ tariff: "tariff", event: "Event" }, names);
  if (!range.rows.length) {
    return [range.key === "today" ? "Nothing recorded yet today." : "No complete days yet. The savings appear after the first full day."];
  }
  const steps = costsSteps(range, nm).steps;
  const paid = steps[steps.length - 1].value;
  const hasEvent = /payments\)$/.test(steps[steps.length - 1].label);
  const none = stepValue(steps, "No solar or battery");
  const withSolarTariff = none + stepValue(steps, "Solar") + stepValue(steps, nm.tariff);
  const pe = stepValue(steps, "PowerEngine");
  const money = (v) => gbp(Math.abs(v));
  const lead = paid < -0.005 ? `${range.label}: you were paid ${money(paid)}` : `${range.label}: you paid ${money(paid)}`;
  const out = [`**${lead}**${hasEvent ? ` after ${nm.event}'s payments` : ""}. With no solar or battery it would have been ${gbp(none)}, with solar and your ${nm.tariff} alone ${gbp(withSolarTariff)}. **The battery and PowerEngine saved ${gbp(-pe)}** on top of that.`];
  const hasToday = range.rows.some((d) => !d.done);
  if (hasToday && today) {
    if (today.halfHours) out.push(`Today covers ${today.halfHours} of the day's 48 half-hours, with the standing charge for the whole day.`);
    if (today.axle) {
      const pays = toNumber(today.axle.pay) === null
        ? `paid you ${gbp(today.axle.gross)}`                                            // an app from before the two were kept apart
        : `paid ${gbp(today.axle.pay)}, and the export rate adds ${gbp(toNumber(today.axle.gross) - toNumber(today.axle.pay))}`;
      out.push(`${nm.event} exported ${today.axle.kwh} kWh today and ${pays} (${gbp(today.axle.net)} net of the battery energy it used).${today.lastEvent ? ` The last event ran from ${today.lastEvent.time} to ${today.lastEvent.end}.` : ""}`);
    }
    if (today.free) out.push(`Free power today: ${today.free.kwh} kWh, a net ${gbp(today.free.net)}.`);
  }
  return out;
}

/** What the day-so-far sensors say, for costsSummary. */
function costsTodayInfo(states) {
  const t = states && states["sensor.pe_cost_today"];
  const a = (t && t.attributes) || {};
  const ev = a.events || {};
  const e = states && states["sensor.pe_event_last"];
  const ea = (e && e.attributes) || {};
  const todayIso = new Date().toLocaleDateString("en-CA");
  let last = null;
  if (ea.type === "axle" && ea.date === todayIso && ea.time && ea.end) {
    const end = new Date(ea.end);
    last = { time: ea.time, end: Number.isNaN(end.getTime()) ? "" : end.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) };
    if (!last.end) last = null;
  }
  return { halfHours: toNumber(a.half_hours), axle: ev.axle || null, free: ev.free_power || null, lastEvent: last };
}

/** Per day, how the PowerEngine step splits (the app's cost book): energy carried in, the battery itself, timing. */
function peParts(row) {
  const num = (v) => toNumber(v) || 0;
  const carried = -num(row.carry);
  const plain = num(row.tariff) - num(row.self_use_adj);
  const timing = num(row.self_use_adj) - num(row.actual_adj);
  return { carried, plain, timing, total: carried + plain + timing };
}

/** The chart's five bars for a day: what it would have cost with no solar or battery, with solar, with the tariff, as
 *  PowerEngine ran it, and the net cost after events. Pure. */
function costsBars(row) {
  const num = (v) => toNumber(v) || 0;
  const pe = num(row.actual_adj) + num(row.carry);
  return [
    { key: "none", label: "No solar or battery", value: num(row.none) },
    { key: "solar", label: "Solar", value: num(row.solar) },
    { key: "tariff", label: "Tariff", value: num(row.tariff) },
    { key: "pe", label: "PowerEngine", value: pe },
    { key: "net", label: "Net cost", value: pe + num(row.events_metered) - num(row.axle_income) },
  ];
}

function inRange(date, range) {
  return !!range.from && date >= range.from && date <= range.to;
}

/** A card that redraws when the shared period changes. */
function followPeriod(proto) {
  const connected = proto.connectedCallback;
  const disconnected = proto.disconnectedCallback;
  proto.connectedCallback = function () {
    if (connected) connected.call(this);
    if (!this._config || !this._config.shared) return;
    this._onPeriod = () => { this._sel = null; this._sig = null; if (this._hass) this._render(); };
    window.addEventListener(COSTS_PERIOD_EVENT, this._onPeriod);
  };
  proto.disconnectedCallback = function () {
    if (disconnected) disconnected.call(this);
    if (this._onPeriod) window.removeEventListener(COSTS_PERIOD_EVENT, this._onPeriod);
  };
}

function pct(v, scale) {
  // Maps a value to a 0-100 position given a {min, max} scale (from waterfallScale). Pure; used to place bars,
  // the zero line and connectors as percentages inside a relatively-positioned track.
  const span = scale.max - scale.min;
  if (!span) return 50;
  return ((v - scale.min) / span) * 100;
}

const WATERFALL_SHORT = {
  "No solar or battery": "No solar/ battery",
  "Battery on self-use": "Self-use battery",
  "Day-to-day cost": "Day-to-day",
  "Battery carry-over": "Carry-over",
};

function shownValue(row) {
  // The number printed for a bar. A period you were paid for says "You were paid" and shows the amount without a minus.
  return row.kind === "total" && /^You were paid/.test(row.label) ? -row.value : row.value;
}

function waterfallShortLabel(label) {
  // A label that fits a narrow column on a phone ("You paid (after <event> payments)" -> "You paid").
  const l = String(label || "");
  if (WATERFALL_SHORT[l]) return WATERFALL_SHORT[l];
  if (l.startsWith("You paid")) return "You paid";
  if (l.startsWith("You were paid")) return "You were paid";
  if (/(^| )tariff$/.test(l)) return "Tariff";                       // "EDF tariff", "Octopus tariff", plain "tariff"
  if (l.endsWith(" & free power")) return l.slice(0, -" & free power".length);   // the event source: "Axle"
  return l;
}

function compactGbp(v) {
  // £ for a narrow column: whole pounds from £10, else one decimal ("£37", "−£7.7", "£0.4").
  const n = Math.abs(v) < 0.05 ? 0 : v;
  const a = Math.abs(n);
  return (n < 0 ? "−£" : "£") + (a >= 10 ? Math.round(a).toString() : a.toFixed(1));
}

class PowerEngineWaterfallCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("entity is required");
    this._config = config;
    this._period = config.period || "week";
    this._range = null;           // {from, to} for the custom period; filled in from the sensor's days
    if (config.shared) this._period = costsPeriodLoad().key;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config && this._config.entity];
    const sig = (st ? st.last_updated : "") + "|" + this._period;
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  getCardSize() { return 6; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  _setPeriod(key) {
    this._period = key;
    this._sel = null;
    if (key === "custom") this._range = this._range || this._defaultRange();
    const st = this._hass && this._hass.states[this._config.entity];
    this._sig = (st ? st.last_updated : "") + "|" + key;
    this._render();
  }

  _daily() {
    const st = this._hass && this._hass.states[this._config.entity];
    return (st && st.attributes && st.attributes.daily) || [];
  }

  _defaultRange() {
    // the last 7 days the app has figures for
    const d = this._daily();
    return d.length ? { from: d[Math.max(0, d.length - 7)].date, to: d[d.length - 1].date } : null;
  }

  _setRange(which, value) {
    if (!value) return;
    const r = Object.assign({}, this._range || this._defaultRange());
    r[which] = value;
    if (r.from > r.to) { if (which === "from") r.to = value; else r.from = value; }     // keep from <= to
    this._range = r;
    this._sel = null;
    this._render();
  }

  _gbp(v) {
    const s = Math.abs(v) < 0.005 ? 0 : v;
    return (s < 0 ? "−£" : "£") + Math.abs(s).toFixed(2);
  }

  _render() {
    if (!this.shadowRoot || !this._hass || !this._config) return;
    const st = this._hass.states[this._config.entity];
    const periods = (st && st.attributes && st.attributes.periods) || {};
    const daily = (st && st.attributes && st.attributes.daily) || [];
    if (this._config.shared) return this._renderShared(st, daily);
    const custom = this._period === "custom";
    if (custom && !this._range) this._range = this._defaultRange();
    const p = custom
      ? (this._range && daily.length ? waterfallCustom(daily, this._range.from, this._range.to, st.attributes.names) : null)
      : periods[this._period];
    const picker = custom && daily.length
      ? `<div class="range"><label>From <input type="date" data-range="from" value="${escHtml(this._range.from)}" min="${escHtml(daily[0].date)}" max="${escHtml(daily[daily.length - 1].date)}"></label>
           <label>To <input type="date" data-range="to" value="${escHtml(this._range.to)}" min="${escHtml(daily[0].date)}" max="${escHtml(daily[daily.length - 1].date)}"></label></div>`
      : "";
    const buttons = WATERFALL_PERIODS.map((w) =>
      `<button data-period="${w.key}" class="${w.key === this._period ? "on" : ""}">${escHtml(w.label)}</button>`).join("");

    if (!st || !p || !p.days) {
      this.shadowRoot.innerHTML = `
        <style>${this._css()}</style>
        <ha-card class="wf"><div class="top">${buttons}</div>${picker}<p class="none">${this._noneText(custom, daily)}</p></ha-card>`;
      this._wire();
      return;
    }

    const rows = waterfallRows(p.steps);
    const scale = waterfallScale(rows);
    const zeroPct = pct(0, scale);
    // columns left to right: the starting total, the steps cascading down, the final total on the right
    const cols = rows.map((r, i) => {
      let cls = "neutral";
      if (r.kind === "step") cls = r.value <= 0 ? "good" : "bad";
      if (r.kind === "total" && i === rows.length - 1) cls = "final";
      const lo = pct(Math.min(r.from, r.to), scale);
      const hi = pct(Math.max(r.from, r.to), scale);
      const height = Math.max(0.8, hi - lo);
      // dashed line from the previous bar's end level across the gap to this bar
      const conn = i > 0 ? `<div class="conn" style="bottom:${pct(rows[i - 1].to, scale)}%"></div>` : "";
      const shown = shownValue(r);
      const full = this._gbp(shown);
      return `
        <div class="col${this._sel === i ? " sel" : ""}" data-i="${i}" title="${escHtml(r.label)}: ${full}">
          ${conn}
          <div class="bar ${cls}" style="bottom:${lo}%; height:${height}%"></div>
          <div class="v" style="bottom:${hi}%"><span class="full">${full}</span><span class="short">${compactGbp(shown)}</span></div>
        </div>`;
    }).join("");
    const labels = rows.map((r) =>
      `<div class="lbl" data-i="${rows.indexOf(r)}"><span class="full">${escHtml(r.label)}</span><span class="short">${escHtml(waterfallShortLabel(r.label))}</span></div>`).join("");
    const n = rows.length;

    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card class="wf">
        <div class="top">${buttons}</div>
        ${picker}
        <div class="selinfo">${this._sel != null && rows[this._sel]
          ? `<b>${escHtml(rows[this._sel].label)}:</b> ${this._gbp(shownValue(rows[this._sel]))}`
          : "Tap a column for its name and exact value."}</div>
        <div class="plot" style="grid-template-columns: repeat(${n}, 1fr)">
          <div class="zero" style="bottom:${zeroPct}%"></div>
          ${cols}
        </div>
        <div class="labels" style="grid-template-columns: repeat(${n}, 1fr)">${labels}</div>
        <p class="caption">${escHtml(p.from === p.to ? p.from : `${p.from}–${p.to}`)}, ${p.days} day${p.days === 1 ? "" : "s"}${this._period === "today" || (custom && p.to === daily[daily.length - 1].date && !daily[daily.length - 1].done) ? " (today so far, with the whole day's standing charge)" : ""}. Green steps saved money; orange ones cost money.</p>
      </ha-card>`;
    this._wire();
  }

  _renderShared(st, daily) {
    // The Costs page: the period is the page's (see costsRange), so there are no buttons here.
    const range = costsRange(costsPeriodLoad(), daily);
    if (!st || !range.rows.length) {
      this.shadowRoot.innerHTML = `<style>${this._css()}</style><ha-card class="wf"><p class="none">${escHtml(range.key === "today" ? "Nothing recorded yet today." : "No days to show for this period yet.")}</p></ha-card>`;
      return;
    }
    const p = costsSteps(range, st.attributes.names);
    const rows = waterfallRows(p.steps);
    const scale = waterfallScale(rows);
    const zeroPct = pct(0, scale);
    const cols = rows.map((r, i) => {
      let cls = "neutral";
      if (r.kind === "step") cls = r.value <= 0 ? "good" : "bad";
      if (r.kind === "total" && i === rows.length - 1) cls = "final";
      const lo = pct(Math.min(r.from, r.to), scale);
      const hi = pct(Math.max(r.from, r.to), scale);
      const conn = i > 0 ? `<div class="conn" style="bottom:${pct(rows[i - 1].to, scale)}%"></div>` : "";
      const shown = shownValue(r);
      return `<div class="col${this._sel === i ? " sel" : ""}" data-i="${i}" title="${escHtml(r.label)}: ${this._gbp(shown)}">${conn}<div class="bar ${cls}" style="bottom:${lo}%; height:${Math.max(0.8, hi - lo)}%"></div><div class="v" style="bottom:${hi}%"><span class="full">${this._gbp(shown)}</span><span class="short">${compactGbp(shown)}</span></div></div>`;
    }).join("");
    const labels = rows.map((r, i) => `<div class="lbl" data-i="${i}"><span class="full">${escHtml(r.label)}</span><span class="short">${escHtml(waterfallShortLabel(r.label))}</span></div>`).join("");
    const n = rows.length;
    const today = range.rows.some((d) => !d.done) ? " (today so far, with the whole day's standing charge)" : "";
    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card class="wf">
        <div class="selinfo">${this._sel != null && rows[this._sel] ? `<b>${escHtml(rows[this._sel].label)}:</b> ${this._gbp(shownValue(rows[this._sel]))}` : "Tap a column for its name and exact value."}</div>
        <div class="plot" style="grid-template-columns: repeat(${n}, 1fr)"><div class="zero" style="bottom:${zeroPct}%"></div>${cols}</div>
        <div class="labels" style="grid-template-columns: repeat(${n}, 1fr)">${labels}</div>
        <p class="caption">${escHtml(range.from === range.to ? range.from : `${range.from}–${range.to}`)}, ${range.rows.length} day${range.rows.length === 1 ? "" : "s"}${today}. Green steps saved money; orange ones cost money.</p>
      </ha-card>`;
    this._wire();
  }

  _noneText(custom, daily) {
    if (this._period === "today") return "Nothing recorded yet today.";
    if (custom && !daily.length) return "Choosing dates needs the newer PowerEngine app (0.9.183 or later).";
    if (custom) return "No days with costs in that range.";
    return "Costs appear after the first full day.";
  }

  _wire() {
    this.shadowRoot.querySelectorAll("input[data-range]").forEach((i) =>
      i.addEventListener("change", () => this._setRange(i.dataset.range, i.value)));
    this.shadowRoot.querySelectorAll("button[data-period]").forEach((b) =>
      b.addEventListener("click", () => this._setPeriod(b.dataset.period)));
    this.shadowRoot.querySelectorAll("[data-i]").forEach((el) =>
      el.addEventListener("click", () => {
        const i = Number(el.dataset.i);
        this._sel = this._sel === i ? null : i;       // tap again to clear
        this._render();
      }));
  }

  _css() {
    return `
      ha-card.wf { display: block; padding: 12px 16px; container-type: inline-size; }
      .top { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
      button { font: inherit; font-size: 0.85em; padding: 3px 10px; border-radius: 6px; cursor: pointer;
               border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); }
      button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .range { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; font-size: 0.85em; }
      .range input { font: inherit; color: var(--primary-text-color); background: none;
                     border: 1px solid var(--divider-color); border-radius: 6px; padding: 2px 6px; }
      .plot { position: relative; display: grid; column-gap: 6px; height: 220px; margin-top: 18px; }
      .col { position: relative; height: 100%; cursor: pointer; }
      .col.sel .bar { outline: 2px solid var(--primary-color); outline-offset: 1px; }
      .lbl { cursor: pointer; }
      .selinfo { min-height: 1.3em; font-size: 0.9em; color: var(--primary-text-color); margin: 2px 0 0; }
      .zero { position: absolute; left: 0; right: 0; height: 0; border-top: 1px solid var(--divider-color); }
      .bar { position: absolute; left: 15%; right: 15%; border-radius: 3px 3px 0 0; }
      .conn { position: absolute; left: calc(-15% - 6px); width: calc(30% + 6px); height: 0;
              border-top: 1px dashed var(--secondary-text-color); opacity: .6; }
      .v { position: absolute; left: -3px; right: -3px; margin-bottom: 2px; text-align: center; white-space: nowrap;
           font-size: 0.75em; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
      .bar.neutral { background: var(--secondary-text-color); opacity: .55; }
      .bar.final { background: var(--primary-text-color); opacity: .8; }
      .bar.good { background: var(--success-color, #43a047); }
      .bar.bad { background: var(--warning-color, #fb8c00); }
      .labels { display: grid; column-gap: 6px; margin-top: 6px; }
      .lbl { text-align: center; font-size: 0.75em; line-height: 1.2; color: var(--secondary-text-color);
             overflow-wrap: anywhere; }
      .short { display: none; }
      .none { color: var(--secondary-text-color); font-size: 0.9em; }
      .caption { color: var(--secondary-text-color); font-size: 0.85em; margin-top: 8px; }
      @container (max-width: 520px) {
        .full { display: none; }
        .short { display: inline; }
        .plot, .labels { column-gap: 3px; }
        .conn { left: calc(-15% - 3px); width: calc(30% + 3px); }
        .v, .lbl { font-size: 0.68em; }
      }
    `;
  }
}

followPeriod(PowerEngineWaterfallCard.prototype);

if (typeof customElements !== "undefined" && !customElements.get("powerengine-waterfall-card")) {
  customElements.define("powerengine-waterfall-card", PowerEngineWaterfallCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-waterfall-card", name: "PowerEngine savings waterfall", description: "Where the Costs page's savings came from, period by period." });
}


/* ------------------------------------------------------------ the Costs page's period picker and day-by-day card
 *   type: custom:powerengine-costs-period-card      (entity: sensor.pe_cost_waterfall)
 *   type: custom:powerengine-costs-days-card        (entity: sensor.pe_cost_waterfall)
 * The picker sets the page's period and says in words what it came to; the days card follows it.
 */
class PowerEngineCostsPeriodCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = Object.assign({ entity: "sensor.pe_cost_waterfall" }, config || {});
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const ids = [this._config.entity, "sensor.pe_cost_today", "sensor.pe_event_last"];
    const sig = ids.map((id) => (hass.states[id] ? hass.states[id].last_updated : "")).join("|");
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  getCardSize() { return 3; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  connectedCallback() {
    this._onPeriod = () => { this._sig = null; if (this._hass) this._render(); };
    window.addEventListener(COSTS_PERIOD_EVENT, this._onPeriod);
  }

  disconnectedCallback() { window.removeEventListener(COSTS_PERIOD_EVENT, this._onPeriod); }

  _pick(key) {
    const sel = { key };
    if (key === "custom") {
      const prev = costsPeriodLoad();
      const r = costsRange({ key: "week" }, this._daily());
      Object.assign(sel, { from: prev.from || r.from, to: prev.to || r.to });
    }
    costsPeriodSet(sel);
  }

  _daily() {
    const st = this._hass && this._hass.states[this._config.entity];
    return (st && st.attributes && st.attributes.daily) || [];
  }

  _setDate(which, value) {
    if (!value) return;
    const sel = Object.assign({}, costsPeriodLoad(), { key: "custom" });
    if (!sel.from || !sel.to) Object.assign(sel, costsRange({ key: "week" }, this._daily()));
    sel[which] = value;
    if (sel.from > sel.to) { if (which === "from") sel.to = value; else sel.from = value; }
    costsPeriodSet({ key: "custom", from: sel.from, to: sel.to });
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const st = this._hass.states[this._config.entity];
    const daily = this._daily();
    const sel = costsPeriodLoad();
    const range = costsRange(sel, daily);
    const names = st && st.attributes && st.attributes.names;
    const lines = costsSummary(range, names, costsTodayInfo(this._hass.states));
    const buttons = WATERFALL_PERIODS.map((w) => `<button data-period="${w.key}" class="${w.key === range.key ? "on" : ""}">${escHtml(w.label)}</button>`).join("");
    const picker = range.key === "custom" && daily.length
      ? `<div class="range"><label>From <input type="date" data-range="from" value="${escHtml(range.from || sel.from || "")}" min="${escHtml(daily[0].date)}" max="${escHtml(daily[daily.length - 1].date)}"></label>
           <label>To <input type="date" data-range="to" value="${escHtml(range.to || sel.to || "")}" min="${escHtml(daily[0].date)}" max="${escHtml(daily[daily.length - 1].date)}"></label></div>`
      : "";
    const md = (s) => escHtml(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .top { display: grid; grid-template-columns: repeat(auto-fit, minmax(5.5em, 1fr)); gap: 6px; margin-bottom: 8px; }
        button { font: inherit; font-size: 0.9em; padding: 4px 6px; white-space: nowrap; border-radius: 6px; cursor: pointer;
                 border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); }
        button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .range { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; font-size: 0.9em; }
        .range input { font: inherit; color: var(--primary-text-color); background: none; border: 1px solid var(--divider-color); border-radius: 6px; padding: 2px 6px; }
        p { margin: 6px 0; line-height: 1.4; }
        .hint { color: var(--secondary-text-color); font-size: 0.8em; }
      </style>
      <div class="top">${buttons}</div>${picker}
      ${lines.map((l) => `<p>${md(l)}</p>`).join("")}
      ${daily.length ? "" : '<p class="hint">Choosing a period needs the app to have published its daily figures (0.9.183 or later).</p>'}`;
    this.shadowRoot.querySelectorAll("button[data-period]").forEach((b) => b.addEventListener("click", () => this._pick(b.dataset.period)));
    this.shadowRoot.querySelectorAll("input[data-range]").forEach((i) => i.addEventListener("change", () => this._setDate(i.dataset.range, i.value)));
  }
}

const COSTS_BAR_COLOURS = { none: "#8d6e63", solar: "#fdd835", tariff: "#26a69a", pe: "#ec407a", net: "#7e57c2" };

class PowerEngineCostsDaysCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = Object.assign({ entity: "sensor.pe_cost_waterfall", shared: true }, config || {});
    this._split = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const sig = st ? st.last_updated : "";
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  getCardSize() { return 8; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const st = this._hass.states[this._config.entity];
    const daily = (st && st.attributes && st.attributes.daily) || [];
    const names = Object.assign({ tariff: "tariff", event: "Event" }, st && st.attributes && st.attributes.names);
    const range = costsRange(costsPeriodLoad(), daily);
    if (!range.rows.length) {
      this.shadowRoot.innerHTML = `<style>:host{display:block}.none{color:var(--secondary-text-color)}</style><p class="none">${escHtml(range.key === "today" ? "Nothing recorded yet today." : "No days to show for this period yet.")}</p>`;
      return;
    }
    const sel = this._selDay;
    const bars = range.rows.map((r) => costsBars(r));
    const vals = bars.flat().map((b) => b.value);
    const scale = waterfallScale([{ from: Math.min(0, ...vals), to: Math.max(0, ...vals) }]);
    const zero = pct(0, scale);
    const n = range.rows.length;
    const group = (r, i) => {
      const date = costsDayLabel(r.date);
      const inner = bars[i].map((b, k) => {
        const lo = pct(Math.min(0, b.value), scale);
        const hi = pct(Math.max(0, b.value), scale);
        return `<div class="b" style="left:${k * 20}%; bottom:${lo}%; height:${Math.max(0.6, hi - lo)}%; background:${COSTS_BAR_COLOURS[b.key]}" title="${escHtml(date)}, ${escHtml(b.label)}: ${gbp(b.value)}"></div>`;
      }).join("");
      return `<div class="g${sel === r.date ? " sel" : ""}" data-day="${r.date}">${inner}</div>`;
    };
    const dayLabel = (r) => new Date(`${r.date}T12:00:00Z`).toLocaleDateString("en-GB", n > 10 ? { day: "numeric", timeZone: "UTC" } : { weekday: "short", day: "numeric", timeZone: "UTC" });
    const info = sel && range.rows.find((r) => r.date === sel)
      ? `<b>${escHtml(costsDayLabel(sel))}:</b> ` + costsBars(range.rows.find((r) => r.date === sel)).map((b) => `${escHtml(b.label)} ${gbp(b.value)}`).join(" · ")
      : "Tap a day to see its five figures.";
    const legend = ["none", "solar", "tariff", "pe", "net"].map((k) => {
      const label = { none: "No solar or battery", solar: "Solar", tariff: `+ ${names.tariff}`, pe: "PowerEngine", net: "Net cost (below 0 = paid to you)" }[k];
      return `<span><i style="background:${COSTS_BAR_COLOURS[k]}"></i>${escHtml(label)}</span>`;
    }).join("");
    const cell = (v) => `<td class="num">${gbp(v)}</td>`;
    const paidCell = (v) => `<td class="num"><b>${v < -0.005 ? `paid you ${gbp(-v)}` : gbp(v)}</b></td>`;
    const stepsOf = (rows) => waterfallSteps(rows, names).steps;
    const rowCells = (steps) => {
      const ev = steps.length === 6 ? steps[4].value : 0;
      const parts = [steps[0].value, steps[1].value, steps[2].value, steps[3].value, ev];
      return parts.map(cell).join("") + paidCell(steps[steps.length - 1].value);
    };
    const split = this._split;
    const splitCells = (rows) => {
      const sum = rows.reduce((a, r) => { const p = peParts(r); return { carried: a.carried + p.carried, plain: a.plain + p.plain, timing: a.timing + p.timing, bin: a.bin + (r.bin || 0), bout: a.bout + (r.bout || 0) }; }, { carried: 0, plain: 0, timing: 0, bin: 0, bout: 0 });
      return `${cell(sum.carried)}${cell(sum.plain)}${cell(sum.timing)}<td class="num">${sum.bin.toFixed(1)} / ${sum.bout.toFixed(1)}</td>`;
    };
    const body = range.rows.slice().reverse().map((r) => `<tr><th scope="row">${escHtml(costsDayLabel(r.date))}${r.done ? "" : " (so far)"}</th>${rowCells(stepsOf([r]))}${split ? splitCells([r]) : ""}</tr>`).join("");
    const total = `<tr class="tot"><th scope="row">${n} day${n === 1 ? "" : "s"}</th>${rowCells(stepsOf(range.rows))}${split ? splitCells(range.rows) : ""}</tr>`;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.8em; color: var(--secondary-text-color); margin-bottom: 6px; }
        .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
        .info { min-height: 1.3em; font-size: 0.85em; margin: 2px 0 6px; }
        .scroll { overflow-x: auto; }
        .plot { position: relative; height: 200px; display: flex; gap: ${n > 14 ? 3 : 8}px; min-width: ${Math.max(0, n * (n > 14 ? 26 : 44))}px; margin-top: 8px; }
        .g { position: relative; flex: 1; cursor: pointer; }
        .g.sel { outline: 2px solid var(--primary-color); outline-offset: 1px; }
        .b { position: absolute; width: 18%; border-radius: 2px 2px 0 0; }
        .zero { position: absolute; left: 0; right: 0; height: 0; border-top: 1px solid var(--divider-color); bottom: ${zero}%; }
        .days { display: flex; gap: ${n > 14 ? 3 : 8}px; min-width: ${Math.max(0, n * (n > 14 ? 26 : 44))}px; font-size: 0.7em; color: var(--secondary-text-color); margin-top: 4px; }
        .days span { flex: 1; text-align: center; overflow: hidden; }
        label.split { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85em; margin: 10px 0 4px; cursor: pointer; }
        table { border-collapse: collapse; width: 100%; font-size: 0.85em; }
        th, td { padding: 5px 8px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--divider-color); }
        th:first-child { text-align: left; font-weight: 500; }
        thead th { font-size: 0.8em; font-weight: 500; color: var(--secondary-text-color); white-space: normal; vertical-align: bottom; }
        tr.tot th, tr.tot td { border-top: 2px solid var(--divider-color); font-weight: 600; }
        .small { color: var(--secondary-text-color); font-size: 0.8em; margin: 6px 0; }
        @media (max-width: 520px) { th, td { padding: 5px 4px; } table { font-size: 0.78em; } }
      </style>
      <div class="legend">${legend}</div>
      <div class="info">${info}</div>
      <div class="scroll"><div class="plot"><div class="zero"></div>${range.rows.map(group).join("")}</div><div class="days">${range.rows.map((r) => `<span>${escHtml(dayLabel(r))}</span>`).join("")}</div></div>
      <label class="split"><input type="checkbox" ${split ? "checked" : ""}> Show how the PowerEngine step splits</label>
      <div class="scroll"><table>
        <thead><tr><th>Day</th><th>No solar or battery</th><th>Solar</th><th>${escHtml(names.tariff)}</th><th>PowerEngine</th><th>${escHtml(names.event)} &amp; free power</th><th>You paid (or were paid)</th>${split ? "<th>Energy carried in</th><th>The battery itself</th><th>PowerEngine's timing</th><th>Battery in / out (kWh)</th>" : ""}</tr></thead>
        <tbody>${body}${total}</tbody></table></div>
      <p class="small">The same steps as the waterfall, one row per day (a negative step saved money). Each part is rounded, so a row can differ from its total by a penny.${split ? " The split: carried in is battery energy bought on an earlier day and used in this one; the battery itself is what a plain self-use battery would have saved; PowerEngine's timing is what its choices added on top (negative means it did worse than plain self-use that day)." : ""}</p>`;
    this.shadowRoot.querySelector("label.split input").addEventListener("change", (ev) => { this._split = ev.target.checked; this._render(); });
    this.shadowRoot.querySelectorAll(".g").forEach((g) => g.addEventListener("click", () => {
      this._selDay = this._selDay === g.dataset.day ? null : g.dataset.day;
      this._render();
    }));
  }

  connectedCallback() {
    this._onPeriod = () => { this._selDay = null; this._sig = null; if (this._hass) this._render(); };
    window.addEventListener(COSTS_PERIOD_EVENT, this._onPeriod);
  }

  disconnectedCallback() { window.removeEventListener(COSTS_PERIOD_EVENT, this._onPeriod); }
}

if (typeof customElements !== "undefined") {
  if (!customElements.get("powerengine-costs-period-card")) customElements.define("powerengine-costs-period-card", PowerEngineCostsPeriodCard);
  if (!customElements.get("powerengine-costs-days-card")) customElements.define("powerengine-costs-days-card", PowerEngineCostsDaysCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-costs-period-card", name: "PowerEngine costs period", description: "The Costs page's period picker and a summary of the chosen period." });
  window.customCards.push({ type: "powerengine-costs-days-card", name: "PowerEngine costs by day", description: "The chosen period day by day: a chart and a table." });
}

// --- Collapsible sections -------------------------------------------------------------------------------------
// A titled, foldable group of other cards, so a long page can be shortened. Open or closed is remembered per browser
// (localStorage, tried and caught) and the cards inside are only built when the section is first opened (a chart in a
// hidden section would draw at zero width).
//   type: custom:powerengine-section-card
//   title: Reference
//   key: costs-reference      (optional, defaults to the title; names the section in the remembered state)
//   open: false               (open or closed until the person chooses, default closed)
//   cards: [ ...any cards... ]
// custom:powerengine-section-tools is a pair of "Expand all" / "Collapse all" links for every section on the page.
const SECTIONS_KEY = "powerengine-sections-open";
const SECTIONS_EVENT = "powerengine-sections";

function sectionsLoad() {
  try {
    const raw = typeof localStorage !== "undefined" ? JSON.parse(localStorage.getItem(SECTIONS_KEY) || "{}") : {};
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  } catch (e) { return {}; }
}
function sectionsSave(state) {
  try { localStorage.setItem(SECTIONS_KEY, JSON.stringify(state)); } catch (e) { /* private mode: it still works for now */ }
}
/** Open or closed: what the person chose if they did, else the card's own default. Pure. */
function sectionOpen(state, key, dflt) {
  const v = state && state[key];
  return typeof v === "boolean" ? v : !!dflt;
}

class PowerEngineSectionCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    if (!config || !Array.isArray(config.cards) || !config.cards.length) throw new Error("cards is required");
    if (!config.title) throw new Error("title is required");
    this._config = config;
    this._key = String(config.key || config.title);
    this._built = false;
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._inner) this._inner.hass = hass;
  }

  getCardSize() {
    const inner = this._inner && this._inner.getCardSize ? this._inner.getCardSize() : 4;
    return 1 + (this._details && this._details.open ? (typeof inner === "number" ? inner : 4) : 0);
  }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  connectedCallback() {
    this._listen = (ev) => this._set(!!(ev.detail && ev.detail.open));
    window.addEventListener(SECTIONS_EVENT, this._listen);
  }

  disconnectedCallback() {
    if (this._listen) window.removeEventListener(SECTIONS_EVENT, this._listen);
  }

  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .sec { border-bottom: 1px solid var(--divider-color); }
        summary { display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; padding: 10px 4px;
                  font-size: 1.15em; font-weight: 500; color: var(--primary-text-color); }
        summary::-webkit-details-marker { display: none; }
        summary::before { content: "▸"; display: inline-block; width: 1em; color: var(--secondary-text-color);
                          transition: transform .15s; }
        details[open] > summary::before { transform: rotate(90deg); }
        .hint { margin-left: auto; font-size: 0.75em; font-weight: 400; color: var(--secondary-text-color); }
        .body { padding: 4px 0 12px; }
      </style>
      <div class="sec"><details><summary><span class="t"></span><span class="hint"></span></summary><div class="body"></div></details></div>`;
    this._details = this.shadowRoot.querySelector("details");
    this._body = this.shadowRoot.querySelector(".body");
    this.shadowRoot.querySelector(".t").textContent = this._config.title;
    this.shadowRoot.querySelector(".hint").textContent = this._config.hint || "";
    this._details.open = sectionOpen(sectionsLoad(), this._key, this._config.open);
    this._details.addEventListener("toggle", () => {
      this._remember(this._details.open);
      if (this._details.open) this._fill();
    });
    if (this._details.open) this._fill();
  }

  _remember(open) {
    const state = sectionsLoad();
    state[this._key] = open;
    sectionsSave(state);
  }

  _set(open) {
    if (this._details && this._details.open !== open) this._details.open = open;     // the toggle event remembers and fills
  }

  async _fill() {
    if (this._built || !this._config) return;
    this._built = true;
    try {
      const helpers = await window.loadCardHelpers();
      const inner = helpers.createCardElement({ type: "vertical-stack", cards: this._config.cards });
      if (this._hass) inner.hass = this._hass;
      this._inner = inner;
      this._body.replaceChildren(inner);
    } catch (e) {
      this._built = false;
      this._body.textContent = "This section could not be drawn.";
    }
  }
}

class PowerEngineSectionTools extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .tools { display: flex; gap: 14px; justify-content: flex-end; font-size: 0.85em; }
        button { font: inherit; background: none; border: 0; padding: 0; cursor: pointer; color: var(--primary-color); }
      </style>
      <div class="tools"><button data-open="1">Expand all</button><button data-open="0">Collapse all</button></div>`;
    this.shadowRoot.querySelectorAll("button").forEach((b) => b.addEventListener("click", () =>
      window.dispatchEvent(new CustomEvent(SECTIONS_EVENT, { detail: { open: b.dataset.open === "1" } }))));
  }

  getCardSize() { return 1; }

  getGridOptions() { return { columns: "full", rows: 1 }; }
}

if (typeof customElements !== "undefined") {
  if (!customElements.get("powerengine-section-card")) customElements.define("powerengine-section-card", PowerEngineSectionCard);
  if (!customElements.get("powerengine-section-tools")) customElements.define("powerengine-section-tools", PowerEngineSectionTools);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-section-card", name: "PowerEngine section", description: "A titled group of cards that folds away; open or closed is remembered per browser." });
}

// --- Diagnostics export (Health tab) --------------------------------------------------------------------------
// Admin only (HA only lets admins fire and subscribe to events). Asks the app for its file-backed parts (config,
// write journal, plan, recent log), adds live entity states and 24 h of history, and saves one JSON file in the
// browser, to upload when there's no shell to pull files.
const DIAG_REQUEST = "pe_diag_request";
const DIAG_BUNDLE = "pe_diag_bundle";
const DIAG_HISTORY = ["sensor.pe_state_battery_soc", "sensor.pe_state_battery_power", "sensor.pe_state_grid_power",
  "sensor.pe_state_solar_power", "sensor.pe_state_house_power", "sensor.pe_state_decision",
  "sensor.pe_state_operation_mode", "sensor.pe_diag_writes_today", "switch.pe_ctl_pause"];
// raw readings behind the PowerEngine figures (when mapped), for cross-checks such as the grid meter comparison
const DIAG_HISTORY_ROLES = ["grid_power", "grid_power_reference", "battery_power", "house_load_power", "ev_charge_power"];

function diagHistoryIds(cfg) {
  const ids = new Set(DIAG_HISTORY);
  ids.add("sensor.pe_diag_grid_check");
  const inputs = (cfg && cfg.inputs) || {};
  for (const role of DIAG_HISTORY_ROLES) {
    const spec = inputs[role];
    if (spec && typeof spec.entity === "string") ids.add(spec.entity);
  }
  return [...ids];
}

const DIAG_CONTROL = /^(number|select|button|sensor)\.solis_.*(timed_|storage_control|battery_control_override|update_charge)/;

function configEntities(cfg) {
  const out = new Set();
  const walk = (v) => {
    if (!v || typeof v !== "object") return;
    if (typeof v.entity === "string") out.add(v.entity);
    for (const x of Object.values(v)) walk(x);
  };
  walk(cfg && cfg.inputs);
  walk(cfg && cfg.solar_plants);
  return out;
}

const DIAG_PRIVATE = /account|mpan|mprn|serial|meter_point|email|token|password|secret|api_?key|latitude|longitude|address/i;

function diagStates(states, extra) {
  const out = {};
  for (const [id, st] of Object.entries(states || {}).sort()) {
    if (/^[a-z_]+\.pe_/.test(id) || (extra && extra.has(id)) || DIAG_CONTROL.test(id)) {
      const attributes = {};
      for (const [k, v] of Object.entries(st.attributes || {})) attributes[k] = DIAG_PRIVATE.test(k) ? "(removed)" : v;
      out[id] = { state: st.state, attributes, last_changed: st.last_changed };
    }
  }
  return out;
}

function diagFileName(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `powerengine-diagnostics-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.json`;
}

// --- Report a problem (Health tab, in the diagnostics card) --------------------------------------------------
// The report is the diagnostics file with identifying text scrubbed, plus a pre-filled new-issue link. GitHub takes no
// attachments from a link, so the user drags the downloaded file onto the issue; the link carries only the title and a
// short description (a link is practically limited to about 8 KB).
const REPORT_ISSUES = "https://github.com/durkimat/ha-powerengine-controller/issues/new";
const REPORT_TEMPLATE = "report-a-problem.yml";
const REPORT_DESCRIPTION_MAX = 4000;

/** Scrub identifying text from a serialised export. Account, meter and serial numbers (digit runs of 7 or more),
 *  hex site and device ids, emails and postcodes are replaced; the same value always gets the same placeholder
 *  (<n1>, <id2>), so entity names stay distinguishable. Returns the text and how many values were replaced. */
function scrubReport(text) {
  const seen = new Map();
  let count = 0;
  const swap = (tag) => (m) => {
    const key = tag + ":" + m;
    if (!seen.has(key)) seen.set(key, `<${tag}${seen.size + 1}>`);
    count += 1;
    return seen.get(key);
  };
  const out = String(text)
    .replace(/[^\s@"\\]+@[^\s@"\\]+\.[^\s@"\\]+/g, swap("email"))
    .replace(/\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b/gi, (m) => (/\d/.test(m) ? swap("postcode")(m) : m))
    .replace(/(?<![0-9a-z])(?=[0-9a-f_-]*\d)[0-9a-f]{4,8}(?:[_-][0-9a-f]{4,12}){2,}(?![0-9a-z])/gi, swap("id"))
    .replace(/(?<![0-9a-z])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}(?![0-9a-z])/gi, swap("id"))
    .replace(/\d{7,}/g, swap("n"));
  return { text: out, count };
}

function reportFileName(d) {
  return diagFileName(d).replace("diagnostics", "report");
}

/** The new-issue link: the general issue form with the title and description filled in. */
function reportIssueUrl(opts) {
  const o = opts || {};
  const q = new URLSearchParams();
  q.set("template", REPORT_TEMPLATE);
  if (o.title) q.set("title", String(o.title).trim().slice(0, 200));
  q.set("description", String(o.description || "").trim().slice(0, REPORT_DESCRIPTION_MAX));
  const versions = [o.appVersion && `app ${o.appVersion}`, o.cardVersion && `card ${o.cardVersion}`].filter(Boolean).join(", ");
  if (versions) q.set("versions", versions);
  return `${REPORT_ISSUES}?${q.toString()}`;
}

class PowerEngineDiagnosticsCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._render();
  }

  getCardSize() { return 2; }

  async _askApp(id) {
    let unsub = null;
    try {
      const answer = new Promise((resolve) => {
        this._hass.connection.subscribeEvents((ev) => {
          if (ev && ev.data && ev.data.id === id) resolve(ev.data);
        }, DIAG_BUNDLE).then((u) => { unsub = u; });
        setTimeout(() => resolve(null), 20000);
      });
      await new Promise((r) => setTimeout(r, 300));          // let the subscription land first
      await this._hass.callWS({ type: "fire_event", event_type: DIAG_REQUEST, event_data: { id } });
      return (await answer) || { bundle: { error: "PowerEngine didn't answer within 20 s (is AppDaemon running?)" } };
    } catch (err) {
      return { bundle: { error: "Could not ask PowerEngine: " + ((err && err.message) || err) + " (admin users only)" } };
    } finally {
      if (unsub) { try { unsub(); } catch (e) { /* already gone */ } }
    }
  }

  async _history(ids) {
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 3600 * 1000);
    try {
      return await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(),
        end_time: end.toISOString(), entity_ids: ids || DIAG_HISTORY, minimal_response: true, no_attributes: true,
        significant_changes_only: false });
    } catch (err) {
      return { error: String((err && err.message) || err) };
    }
  }

  /** Ask the app, add entity states and history; returns the bundle's text and the app's answer. */
  async _collect() {
    const now = new Date();
    const id = Math.random().toString(36).slice(2) + now.getTime().toString(36);
    const app = await this._askApp(id);
    this._status("Adding entity states and 24 h of history…");
    const history = await this._history(diagHistoryIds(app.bundle && app.bundle.config));
    const cfg = app.bundle && app.bundle.config;
    const bundle = {
      generated: now.toISOString(), card_version: CARD_VERSION,
      browser: typeof navigator !== "undefined" ? navigator.userAgent : "",
      app: app.bundle, app_copy: app.saved || null,
      states: diagStates(this._hass.states, configEntities(cfg)),
      history,
    };
    return { now, app, text: JSON.stringify(bundle, null, 1) };
  }

  async _export() {
    if (!this._hass || this._busy) return;
    this._busy = true;
    this._file = null;
    this._where = "msg";
    this._status("Collecting from PowerEngine…");
    const { now, app, text } = await this._collect();
    this._name = diagFileName(now);
    this._file = new Blob([text], { type: "application/json" });
    this._busy = false;
    this._download();
    this._status(`Ready: ${this._name} (${Math.round(text.length / 1024)} KB). If nothing downloaded, use Share or ` +
      `Copy.${app.saved ? " A copy of PowerEngine's part is also in " + app.saved.replace(/^.*\/powerengine\//, "/homeassistant/powerengine/") + "." : ""}`);
  }

  /** Report a problem, step 1: collect, scrub, and show what would be sent. */
  async _prepareReport() {
    if (!this._hass || this._busy) return;
    this._busy = true;
    this._report = null;
    this._where = "rmsg";
    this._status("Collecting from PowerEngine…");
    const { now, text } = await this._collect();
    const scrubbed = scrubReport(text);
    this._report = { name: reportFileName(now), text: scrubbed.text, count: scrubbed.count,
      blob: new Blob([scrubbed.text], { type: "application/json" }) };
    this._busy = false;
    this._status(`Report ready (${Math.round(scrubbed.text.length / 1024)} KB, ${scrubbed.count} identifying value(s) replaced). ` +
      "Read it below, then download it and open the issue.");
  }

  _downloadReport() {
    if (!this._report) return;
    const url = URL.createObjectURL(this._report.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = this._report.name;
    this.shadowRoot.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  /** Step 2: the file downloads, and the issue opens in a new tab with the title and description filled in. */
  _openIssue() {
    if (!this._report) return;
    this._where = "rmsg";
    this._downloadReport();
    const q = (sel) => this.shadowRoot.querySelector(sel);
    const url = reportIssueUrl({ title: q(".rtitle").value, description: q(".rdesc").value,
      appVersion: ((this._hass.states[VERSION_SENSOR] || {}).state) || "", cardVersion: CARD_VERSION });
    window.open(url, "_blank", "noopener");
    this._status(`Downloaded ${this._report.name}. In the issue that opened, drag that file onto the "Diagnostics" box, then submit.`);
  }

  _download() {
    if (!this._file) return;
    const url = URL.createObjectURL(this._file);
    const a = document.createElement("a");
    a.href = url;
    a.download = this._name;
    this.shadowRoot.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async _share() {
    if (!this._file) return;
    this._where = "msg";
    try {
      const file = new File([this._file], this._name, { type: "application/json" });
      await navigator.share({ files: [file], title: "PowerEngine diagnostics" });
    } catch (err) {
      this._status("Share didn't work here: " + ((err && err.message) || err));
    }
  }

  async _copy() {
    if (!this._file) return;
    this._where = "msg";
    try {
      await navigator.clipboard.writeText(await this._file.text());
      this._status("Copied to the clipboard.");
    } catch (err) {
      this._status("Copy didn't work here: " + ((err && err.message) || err));
    }
  }

  _status(text) {
    this._msgs = Object.assign(this._msgs || {}, { [this._where || "msg"]: text });
    this._render();
  }

  _render() {
    if (!this.shadowRoot) return;
    if (!this._built) {
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { padding: 16px; }
          h2 { margin: 0 0 6px; font-size: 1.2em; font-weight: 500; }
          p { margin: 4px 0 8px; color: var(--secondary-text-color); }
          .row { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
          button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--divider-color);
                   background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; }
          button.second { background: none; color: var(--primary-text-color); }
          button:disabled { opacity: .5; cursor: default; }
          .msg { color: var(--primary-text-color); }
          h3 { margin: 16px 0 6px; font-size: 1.05em; font-weight: 500; }
          input, textarea { font: inherit; width: 100%; box-sizing: border-box; padding: 6px 8px; margin: 4px 0;
                            border: 1px solid var(--divider-color); border-radius: 6px;
                            background: var(--card-background-color); color: var(--primary-text-color); }
          pre { max-height: 240px; overflow: auto; font-size: .75em; white-space: pre-wrap; word-break: break-all; }
        </style>
        <ha-card>
          <h2>Diagnostics export</h2>
          <p>One file with PowerEngine's settings, the inverter write log (48 h), the plan, recent log lines, the
             current state of its entities and the inverter controls, and 24 h of battery, grid and mode history.
             Upload it to Claude when you can't pull files from the shell. Account numbers, serials and similar attributes are left out; no passwords or tokens are included.</p>
          <div class="row">
            <button class="go">Export diagnostics</button>
            <button class="second again">Download again</button>
            <button class="second share">Share</button>
            <button class="second copy">Copy</button>
          </div>
          <p class="msg"></p>
          <h3>Report a problem</h3>
          <p>Opens a GitHub issue with your description. The diagnostics are downloaded as a file with account and meter
             numbers, serials, ids and emails replaced, for you to read first and then drag onto the issue.
             You need a GitHub account.</p>
          <input class="rtitle" placeholder="Short title" maxlength="200" aria-label="Title">
          <textarea class="rdesc" rows="4" placeholder="What happened, and what did you expect? (no personal details)" aria-label="Description"></textarea>
          <div class="row">
            <button class="rprep">Prepare report</button>
            <button class="second ropen">Download and open GitHub issue</button>
          </div>
          <details class="rprev"><summary>What the file contains</summary><pre></pre></details>
          <p class="rmsg"></p>
        </ha-card>`;
      const q = (sel) => this.shadowRoot.querySelector(sel);
      q(".rprep").addEventListener("click", () => this._prepareReport());
      q(".ropen").addEventListener("click", () => this._openIssue());
      q(".go").addEventListener("click", () => this._export());
      q(".again").addEventListener("click", () => this._download());
      q(".share").addEventListener("click", () => this._share());
      q(".copy").addEventListener("click", () => this._copy());
      this._built = true;
    }
    const q = (sel) => this.shadowRoot.querySelector(sel);
    const have = !!this._file;
    q(".go").disabled = !!this._busy;
    q(".again").disabled = !have;
    q(".copy").disabled = !have;
    q(".share").disabled = !have;
    q(".share").style.display = typeof navigator !== "undefined" && navigator.share ? "" : "none";
    q(".msg").textContent = (this._msgs && this._msgs.msg) || "";
    q(".rmsg").textContent = (this._msgs && this._msgs.rmsg) || "";
    q(".rprep").disabled = !!this._busy;
    q(".ropen").disabled = !this._report || !!this._busy;
    q(".rprev").style.display = this._report ? "" : "none";
    if (this._report) q(".rprev pre").textContent = this._report.text.slice(0, 20000) + (this._report.text.length > 20000 ? "\n… (shortened here; the file has it all)" : "");
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-diagnostics-card")) {
  customElements.define("powerengine-diagnostics-card", PowerEngineDiagnosticsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-diagnostics-card", name: "PowerEngine diagnostics export",
    description: "Download one diagnostics file for troubleshooting." });
}

// --- Simulator card: history import (automatic) and heat-pump settings ---------------------------------------
// HA only lets admins read long-term statistics and fire events, so this runs in an admin's browser. When the app
// asks for months of history, the card reads them (hourly energy per sensor) one month at a time and hands each to
// the app. The heat-pump form saves its settings to the app, which uses them in the next overnight run.
const SIM_ENTITY = "sensor.pe_cost_simulator";
const HP_FIELDS = [
  ["gas_kwh_year", "Gas used in a year", "kWh", "From your gas bills (all gas, heating + hot water). Used to work out how much heat the house needs."],
  ["boiler_efficiency", "Boiler efficiency", "%", "About 85% for a modern condensing boiler, 70-75% for an older one."],
  ["heat_loss_kw", "Heat loss (survey)", "kW", "Optional: the heat-loss figure from a survey (at -3 °C). Replaces the gas estimate when set."],
  ["hot_water_kwh_day", "Hot water heat per day", "kWh", "About 5-8 kWh for a family."],
  ["tank_litres", "Hot water tank", "litres", "For the notes; hot water is heated in the day's cheapest hours."],
  ["cop_cold", "Efficiency (COP) at -3 °C", "", "From the pump's datasheet at 45 °C flow; about 2.5 if unknown."],
  ["cop_mild", "Efficiency (COP) at 12 °C", "", "About 4.5 if unknown."],
  ["max_kw", "Pump size", "kW heat", "Heat output; anything beyond it is counted as an electric immersion (COP 1)."],
  ["preheat_h", "Pre-heating allowed", "hours", "How far ahead heating may run in cheaper hours (0-6)."],
  ["gas_price_p", "Gas unit price", "p/kWh", "For the keep-gas comparison."],
  ["gas_standing_p", "Gas standing charge", "p/day", "Saved if the gas supply is removed."],
  ["install_cost", "Installed cost after grants", "£", "For payback in years (shown once there's a year of history)."],
];

const EQ_GROUPS = [
  ["battery_enabled", "Bigger battery (replacing yours)", [
    ["battery_kwh", "Usable capacity", "kWh"], ["battery_kw", "Charge/discharge power", "kW"], ["battery_cost", "Cost", "£"]]],
  ["solar_enabled", "More solar (same roof direction)", [
    ["solar_current_kwp", "Your solar now", "kWp"], ["solar_extra_kwp", "Extra panels", "kWp"], ["solar_cost", "Cost", "£"]]],
  ["ev2_enabled", "A second car", [
    ["ev2_miles_year", "Miles a year", ""], ["ev2_kwh_per_mile", "kWh per mile", ""], ["ev2_charger_kw", "Charger", "kW"]]],
];
const EQ_DEFAULTS = { ev2_kwh_per_mile: 0.3, ev2_charger_kw: 7.4 };

function simHistoryPlan(state) {
  const a = (state && state.attributes) || {};
  const req = a.history_request || {};
  return { months: req.months || [], entities: req.entities || {}, imported: req.imported || [] };
}

function monthRange(month) {
  const [y, m] = month.split("-").map(Number);
  const start = new Date(Date.UTC(y, m - 1, 1) - 86400000);
  const end = new Date(Date.UTC(m === 12 ? y + 1 : y, m === 12 ? 0 : m, 1) + 86400000);
  return { start: start.toISOString(), end: end.toISOString() };
}

class PowerEngineSimCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._build();
    this._render();
    this._maybeImport();
  }

  getCardSize() { return 6; }

  _admin() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }

  async _maybeImport() {
    if (this._importing || !this._admin()) return;
    const plan = simHistoryPlan(this._hass.states[SIM_ENTITY]);
    const month = plan.months.find((m) => !(this._tried || new Set()).has(m));
    if (!month) return;
    const ids = [...new Set(Object.values(plan.entities).flat())];
    if (!ids.length) return;
    this._importing = true;
    this._tried = this._tried || new Set();
    this._tried.add(month);
    this._status = `Reading ${month} from Home Assistant's statistics…`;
    this._render();
    try {
      const { start, end } = monthRange(month);
      const stats = await this._hass.callWS({ type: "recorder/statistics_during_period", start_time: start, end_time: end,
        statistic_ids: ids, period: "hour", types: ["change"] });
      const compact = {};                                  // [start, change] pairs keep the event small
      Object.entries(stats || {}).forEach(([id, rows]) => { compact[id] = rows.map((r) => [r.start, r.change]); });
      await this._hass.callWS({ type: "fire_event", event_type: "pe_sim_history", event_data: { month, stats: compact } });
      this._status = `Imported ${month}.`;
    } catch (err) {
      this._status = `Couldn't import ${month}: ${(err && err.message) || err}`;
    }
    this._importing = false;
    this._render();
    setTimeout(() => this._maybeImport(), 1500);            // next month, once the app has recorded this one
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        h2 { margin: 0 0 6px; font-size: 1.2em; font-weight: 500; }
        h3 { margin: 16px 0 6px; font-size: 1.05em; font-weight: 500; }
        p, .muted { color: var(--secondary-text-color); margin: 4px 0; }
        .grid { display: grid; grid-template-columns: minmax(160px, 1fr) 120px; gap: 6px 12px; align-items: center; }
        .grid .help { grid-column: 1 / -1; font-size: .85em; color: var(--secondary-text-color); margin-top: -4px; }
        input[type=number] { font: inherit; padding: 4px 6px; width: 100%; box-sizing: border-box; }
        button { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--divider-color);
                 background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; margin-top: 10px; }
        button:disabled { opacity: .5; cursor: default; }
        .msg { margin-left: 8px; }
        .ok { color: var(--success-color, #43a047); } .bad { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <h2>Simulator set-up</h2>
        <h3>A year of history</h3>
        <p class="hist"></p>
        <h3>Heat pump</h3>
        <p>Adds a heat pump to your tariff, the heat-pump tariffs and the five best others, and compares each with keeping gas. Saved settings are used in the next overnight run.</p>
        <label><input type="checkbox" class="hp_on"> Include a heat pump</label>
        <div class="grid">${HP_FIELDS.map(([k, label, unit, help]) => `
          <label for="f_${k}">${label}${unit ? ` (${unit})` : ""}</label><input type="number" step="any" id="f_${k}" data-k="${k}">
          <div class="help">${help}</div>`).join("")}
        </div>
        <h3>Equipment</h3>
        <p>Each ticked item (and all of them together) is run on your tariff and the best other tariff, and compared with the same tariff without it. Payback appears once there's a year of history.</p>
        ${EQ_GROUPS.map(([flag, title, fields]) => `
          <label><input type="checkbox" data-e="${flag}"> ${title}</label>
          <div class="grid">${fields.map(([k, label, unit]) => `
            <label for="e_${k}">${label}${unit ? ` (${unit})` : ""}</label><input type="number" step="any" id="e_${k}" data-e="${k}">`).join("")}
          </div>`).join("")}
        <button class="save">Save simulator settings</button><span class="msg"></span>
      </ha-card>`;
    this.shadowRoot.querySelector(".save").addEventListener("click", () => this._save());
    this._fill();
  }

  _fill() {
    const a = ((this._hass.states[SIM_ENTITY] || {}).attributes || {});
    const s = (a.settings || {}).heat_pump || {};
    const defaults = { boiler_efficiency: 85, hot_water_kwh_day: 6, tank_litres: 200, cop_cold: 2.5, cop_mild: 4.5, max_kw: 8, preheat_h: 2, gas_price_p: 6, gas_standing_p: 30 };
    this.shadowRoot.querySelector(".hp_on").checked = !!s.enabled;
    const e = (a.settings || {}).equipment || {};
    this.shadowRoot.querySelectorAll("[data-e]").forEach((inp) => {
      const k = inp.dataset.e;
      if (inp.type === "checkbox") inp.checked = !!e[k];
      else { const v = e[k] ? e[k] : EQ_DEFAULTS[k]; inp.value = v === undefined ? "" : v; }
    });
    this.shadowRoot.querySelectorAll("input[data-k]").forEach((inp) => {
      const k = inp.dataset.k;
      const v = s[k] !== undefined && s[k] !== 0 ? s[k] : defaults[k];
      inp.value = v === undefined ? "" : v;
    });
    this._filled = !!a.settings;
  }

  async _save() {
    const hp = { enabled: this.shadowRoot.querySelector(".hp_on").checked };
    this.shadowRoot.querySelectorAll("input[data-k]").forEach((inp) => { hp[inp.dataset.k] = inp.value === "" ? 0 : Number(inp.value); });
    const equipment = {};
    this.shadowRoot.querySelectorAll("[data-e]").forEach((inp) => {
      equipment[inp.dataset.e] = inp.type === "checkbox" ? inp.checked : (inp.value === "" ? 0 : Number(inp.value));
    });
    const msg = this.shadowRoot.querySelector(".msg");
    try {
      const unsub = await this._hass.connection.subscribeEvents((ev) => {
        msg.textContent = ev.data.message; msg.className = "msg " + (ev.data.ok ? "ok" : "bad"); unsub();
      }, "pe_sim_result");
      await this._hass.callWS({ type: "fire_event", event_type: "pe_sim_settings", event_data: { heat_pump: hp, equipment } });
      msg.textContent = "Saving…"; msg.className = "msg";
    } catch (err) {
      msg.textContent = "Couldn't save: " + ((err && err.message) || err) + " (admin users only)"; msg.className = "msg bad";
    }
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const st = this._hass.states[SIM_ENTITY];
    if (!this._filled && st && st.attributes && st.attributes.settings) this._fill();
    const plan = simHistoryPlan(st);
    const el = this.shadowRoot.querySelector(".hist");
    let text;
    if (!st) text = "Waiting for PowerEngine.";
    else if (!plan.months.length) text = plan.imported.length ? `Imported ${plan.imported.length} month${plan.imported.length === 1 ? "" : "s"} of history from Home Assistant's statistics (hourly energy). The Simulator uses them from the next overnight run.` : "Nothing to import.";
    else if (!this._admin()) text = `${plan.months.length} month(s) of history can be imported from Home Assistant's statistics. Open this page as an admin user and it happens automatically.`;
    else text = `Importing ${plan.months.length} month(s) of hourly energy from Home Assistant's statistics while this page is open (each month takes a few seconds).`;
    el.textContent = text + (this._status ? " " + this._status : "");
    this.shadowRoot.querySelector(".save").disabled = !this._admin();
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-sim-card")) {
  customElements.define("powerengine-sim-card", PowerEngineSimCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-sim-card", name: "PowerEngine simulator set-up", description: "Imports a year of history for the Simulator and holds its heat-pump settings." });
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-config-card")) {
  customElements.define("powerengine-config-card", PowerEngineConfigCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "powerengine-config-card",
    name: "PowerEngine configuration",
    description: "Configure the PowerEngine app (inputs, features, settings, mode).",
  });
  console.info(`%c POWERENGINE-CARD %c v${CARD_VERSION} `, "background:#1f6feb;color:#fff", "");
}

/* ------------------------------------------------------------ finding devices, and the candidate export
 * Used by the "Your system" card. It searches Home Assistant for each part (the entity registry's platform, the device's
 * manufacturer and model, entity ids), suggests each part's inputs from that device's entities only, and checks signs
 * against what the user sees. What to look for comes from the app (sensor.pe_diag_version attribute `wizard`,
 * pe_core/wizard.py); the card holds none of it, so a new inverter definition shows up here by itself. The same search feeds
 * the candidate export (docs/WIZARD.md): a scrubbed list of a device's entities for whoever writes the definition of an
 * unsupported one. The helpers keep their `wizard` names. Pure helpers first (exported for tests). */
const EXPORT_FORMAT = "powerengine-candidates";
const EXPORT_VERSION = 1;
const EXPORT_STATE_MAX = 60;
const WIZARD_ISSUES = "https://github.com/durkimat/ha-powerengine-controller/issues/new";
const WIZARD_PART_ORDER = ["inverter", "tariff", "ev_charger", "forecast", "events"];
// the main solar plant is not a role: its power and today's energy are picked with the inverter's inputs
const WIZARD_PLANT = [["power", "Solar power", "Live solar generation (W).", /pv.*power|solar.*power|power.*pv|pv_?total/i, ["W", "kW"]],
  ["energy_today", "Solar energy today", "Energy generated today (kWh).", /(pv|solar|generation|yield).*(today|daily)|(today|daily).*(pv|solar|generation|yield)/i, ["kWh", "Wh"]]];

/** What the wizard works from, or null when the app doesn't publish `wizard` (the card stays hidden: too old an app). */
function wizardInfo(attrs) {
  const a = attrs || {};
  const w = a.wizard;
  if (!w || typeof w !== "object" || w.v !== 1 || !Array.isArray(w.parts) || !w.parts.length) return null;
  const parts = WIZARD_PART_ORDER.map((k) => w.parts.find((p) => p.part === k)).filter(Boolean);
  w.parts.forEach((p) => { if (!parts.includes(p)) parts.push(p); });
  return { parts, options: a.site_options || {}, site: a.site || {}, setup: a.setup || "configured", detected: a.firmware_detected || null };
}

/** Home Assistant's registries as the wizard uses them: each entity with its integration (platform) and device, and
 *  each device with its manufacturer, model, firmware and entities. `registry` is false when the frontend gives no
 *  registry (then only entity ids can be searched). */
function wizardFacts(hass) {
  const states = (hass && hass.states) || {};
  const regE = (hass && hass.entities) || {};
  const regD = (hass && hass.devices) || {};
  const entities = Object.keys(states).map((id) => {
    const r = regE[id] || {};
    return { id, domain: id.split(".")[0], platform: r.platform || "", device: r.device_id || null };
  });
  const by = {};
  entities.forEach((e) => { if (e.device) (by[e.device] = by[e.device] || []).push(e); });
  const devices = {};
  Object.keys(by).forEach((did) => {
    const d = regD[did] || {};
    devices[did] = { id: did, name: d.name_by_user || d.name || did, manufacturer: d.manufacturer || "", model: d.model || "",
      sw_version: d.sw_version || "", entities: by[did].map((e) => e.id), platforms: [...new Set(by[did].map((e) => e.platform).filter(Boolean))] };
  });
  return { entities, devices, ids: entities.map((e) => e.id), registry: Object.keys(regE).length > 0 };
}

function wizardRegex(list, flags) {
  return (list || []).map((p) => { try { return new RegExp(p, flags); } catch (e) { return null; } }).filter(Boolean);
}

/** What in Home Assistant matches an option's search data: devices (by integration, manufacturer or model), entities
 *  that match by name and belong to no matched device, and whether the integration is present at all. */
function wizardMatch(option, facts) {
  const doms = option.domains || [];
  const man = wizardRegex(option.manufacturers, "i");
  const mod = wizardRegex(option.models, "i");
  const ent = wizardRegex(option.entities, "");
  const devices = [];
  Object.values(facts.devices).forEach((d) => {
    const reason = d.platforms.some((p) => doms.includes(p)) ? "integration"
      : d.manufacturer && man.some((r) => r.test(d.manufacturer)) ? "manufacturer"
      : d.model && mod.some((r) => r.test(d.model)) ? "model" : "";
    if (reason) devices.push(Object.assign({ reason }, d));
  });
  const taken = new Set(devices.map((d) => d.id));
  const entities = facts.entities.filter((e) => !taken.has(e.device) && (ent.some((r) => r.test(e.id)) || (!e.device && doms.includes(e.platform))));
  const present = devices.length > 0 || entities.length > 0 || facts.entities.some((e) => doms.includes(e.platform));
  return { devices, entities, found: present };
}

/** The things the user can pick for a part: each matching device, and the entities found by name. Two options that
 *  find the same device (the Octopus Energy integration serves EDF and Octopus alike) give one candidate: the option
 *  whose entity-name patterns match it, else the first. `nameOf(optionId)` is the adapter's display name ("EDF"). */
function wizardCandidates(part, facts, nameOf) {
  const found = [];
  (part.options || []).forEach((o) => {
    const m = wizardMatch(o, facts);
    const name = (nameOf && nameOf(o.id)) || (o.integration && o.integration.name) || o.id;
    const pats = wizardRegex(o.entities, "");
    const named = (ids) => ids.some((id) => pats.some((r) => r.test(id)));
    m.devices.forEach((d) => found.push({ ident: d.id, option: o.id, named: named(d.entities), device: d, entityIds: d.entities,
      label: `${d.name}${d.model && !d.name.includes(d.model) ? ` (${d.model})` : ""} · ${name}` }));
    if (m.entities.length) {
      const ids = m.entities.map((e) => e.id);
      found.push({ ident: ids.join(","), option: o.id, named: named(ids), device: null, entityIds: ids, label: `${name} entities (found by name)` });
    }
  });
  const out = [];
  found.forEach((c) => {
    const i = out.findIndex((x) => x.ident === c.ident);
    if (i < 0) out.push(c);
    else if (c.named && !out[i].named) out[i] = c;
  });
  return out.map((c) => Object.assign({ key: `${c.option}:${c.device ? c.device.id : "names"}` }, c));
}

/** Which candidate PowerEngine already uses for a part: the one holding an entity mapped in the saved config, else
 *  the one the saved site names (the tariff's "auto" and "none" name none). null when nothing is set up. */
function wizardInUse(part, cands, savedInputs, savedSite) {
  const mapped = new Set();
  (part.roles || []).forEach((k) => { const sp = (savedInputs || {})[k]; if (sp && sp.entity) mapped.add(sp.entity); });
  const byEntity = cands.find((c) => c.entityIds.some((id) => mapped.has(id)));
  if (byEntity) return byEntity;
  const named = savedSite && savedSite[part.part];
  return named && named !== "none" && named !== "auto" ? cands.find((c) => c.option === named) || null : null;
}

/** Every entity a config already uses: mapped inputs and the solar plants' power and energy. */
function wizardUsedEntities(cfg) {
  const used = new Set();
  Object.values((cfg || {}).inputs || {}).forEach((sp) => { if (sp && sp.entity) used.add(sp.entity); });
  ((cfg || {}).solar_plants || []).forEach((pl) => ["power", "energy_today"].forEach((k) => { if (pl && pl[k] && pl[k].entity) used.add(pl[k].entity); }));
  ((cfg || {}).devices || []).forEach((d) => Object.values((d && d.inputs) || {}).forEach((s) => { if (s && s.entity) used.add(s.entity); }));
  return used;
}

/** Energy equipment in Home Assistant that no adapter owns and the config doesn't use yet: a device with a power and an
 *  energy sensor that looks like an inverter, as {..device, kind: "battery" | "solar"}. "battery" has a battery
 *  percentage too (a hybrid inverter PowerEngine can't use yet); "solar" is named like a solar source (a solar-only
 *  inverter or plug-in panels: it can be added as a solar plant). A phone, a smart plug or a heat pump is neither. */
function wizardOthers(parts, facts, states, used) {
  const owned = new Set();
  parts.forEach((p) => wizardCandidates(p, facts).forEach((c) => { if (c.device) owned.add(c.device.id); }));
  const attr = (id) => ((states || {})[id] || {}).attributes || {};
  const solarName = /pv|solar|inverter|generation|yield|micro|balcony/i;
  const out = [];
  Object.values(facts.devices).forEach((d) => {
    if (owned.has(d.id) || d.entities.some((id) => used && used.has(id))) return;
    const power = d.entities.some((id) => ["W", "kW"].includes(attr(id).unit_of_measurement));
    const energy = d.entities.some((id) => ["kWh", "Wh"].includes(attr(id).unit_of_measurement));
    if (!power || !energy) return;
    const battery = d.entities.some((id) => id.startsWith("sensor.") && attr(id).device_class === "battery" && attr(id).unit_of_measurement === "%");
    if (battery) out.push(Object.assign({ kind: "battery" }, d));
    else if (solarName.test(`${d.name} ${d.model} ${d.entities.join(" ")}`)) out.push(Object.assign({ kind: "solar" }, d));
  });
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/** "Fox H1 (Fox ESS H1)": a device's name with its make and model. */
function wizardDeviceName(d) { return `${d.name}${d.manufacturer ? ` (${d.manufacturer}${d.model ? ` ${d.model}` : ""})` : ""}`; }

/** A solar plant for a device: its power and today's-energy entities guessed by name and unit (empty when unsure). */
function wizardPlantFromDevice(device, facts, states) {
  const cand = { entityIds: device.entities };
  const attr = (id) => ((states || {})[id] || {}).attributes || {};
  let power = wizardPlantGuess("power", cand, facts, states).entity;
  if (!power) {                                          // a single power sensor on the device is the one
    const only = device.entities.filter((id) => id.startsWith("sensor.") && ["W", "kW"].includes(attr(id).unit_of_measurement));
    if (only.length === 1) power = only[0];
  }
  let energy = wizardPlantGuess("energy_today", cand, facts, states).entity;
  if (!energy) {                                         // else the one energy sensor that resets daily
    const today = device.entities.filter((id) => id.startsWith("sensor.") && ["kWh", "Wh"].includes(attr(id).unit_of_measurement) && /today|daily/i.test(id));
    if (today.length === 1) energy = today[0];
  }
  return { id: "", name: device.name, forecast: "none", enabled: true, power: power ? { entity: power } : {}, energy_today: energy ? { entity: energy } : {} };
}

/** A plant id not in `taken` (letters, digits, underscore: the app's rule). */
function wizardPlantId(name, taken) { return slugify(name, taken); }

/** The role keys of a part the wizard shows, split into the ones that matter now and the rest, from the catalogue. */
function wizardRoles(part, catalogue) {
  const by = Object.fromEntries((catalogue || []).map((r) => [r.key, r]));
  return (part.roles || []).map((k) => by[k]).filter(Boolean);
}

/** A suggestion for a role: from the picked device's own entities first, else from anywhere (flagged "other"). */
function wizardSuggest(role, cand, facts) {
  const own = cand ? suggestEntity(role, cand.entityIds) : "";
  if (own) return { entity: own, from: "device" };
  const any = suggestEntity(role, facts.ids);
  return any ? { entity: any, from: "other" } : { entity: "", from: "" };
}

/** A guess at the main solar plant's power and today's energy among a device's entities (or all, flagged), by name and unit. */
function wizardPlantGuess(which, cand, facts, states) {
  const spec = WIZARD_PLANT.find((p) => p[0] === which);
  const pick = (ids) => ids.find((id) => id.startsWith("sensor.") && spec[3].test(id)
    && spec[4].includes(((states[id] || {}).attributes || {}).unit_of_measurement)) || "";
  const own = cand ? pick(cand.entityIds) : "";
  return own ? { entity: own, from: "device" } : { entity: "", from: "" };
}

/** Required inputs of the active parts that are still empty: [{part, role}] (battery pair and features respected). */
function wizardMissing(draft, parts, roles, active) {
  const pair = BATTERY_PAIR.every((k) => (draft.inputs[k] || {}).entity);
  const out = [];
  parts.filter((p) => active.includes(p.part)).forEach((p) => {
    wizardRoles(p, roles).forEach((r) => {
      if (roleNeed(r, draft, pair).level !== "req") return;
      const s = draft.inputs[r.key];
      if (!s || !(s.entity || (s.value !== undefined && s.value !== ""))) out.push({ part: p.part, role: r });
    });
    if (p.part === "inverter") WIZARD_PLANT.forEach((x) => {
      const pl = (draft.solar_plants || [])[0];
      if (!pl || !pl[x[0]] || !pl[x[0]].entity) out.push({ part: "inverter", role: { key: `plant_${x[0]}`, label: x[1] } });
    });
  });
  return out;
}

/* ---- live checks */
/** Watts from an entity state ("1.2" kW is 1200), or null. */
function wizardWatts(stateObj) {
  if (!stateObj) return null;
  const n = toNumber(stateObj.state);
  if (n === null) return null;
  return ((stateObj.attributes || {}).unit_of_measurement) === "kW" ? n * 1000 : n;
}

/** The user says what the signed input is doing now ("pos": the first word of its sign note, "neg": the second,
 *  "idle"); is the sign (after Invert) as PowerEngine expects? -> "ok" | "flip" | "idle" | "unknown". */
function wizardSignCheck(says, watts, invert) {
  if (says === "idle" || says === undefined || says === null) return "idle";
  if (watts === null || Math.abs(watts) < 50) return "unknown";
  const v = invert ? -watts : watts;
  return (says === "pos") === (v > 0) ? "ok" : "flip";
}

/** House load should equal grid (+ in) + solar + battery (+ discharging), roughly. {ok, diff, expected} or null if a figure is missing. */
function wizardBalance(v) {
  if (["battery", "solar", "grid", "house"].some((k) => v[k] === null || v[k] === undefined)) return null;
  const expected = v.grid + v.solar + v.battery;
  const diff = Math.round(v.house - expected);
  return { ok: Math.abs(diff) <= Math.max(400, 0.2 * Math.abs(v.house)), diff, expected: Math.round(expected) };
}

/* ---- candidate export (item 1): a scrubbed list of a device's entities for someone who writes the definition */
/** Long digit runs (account and meter numbers, serials) become <n>; text that looks like an email or a postcode is
 *  dropped. A device's manufacturer, model and firmware keep their digits (`keepDigits`): a firmware such as 420044 is
 *  what a definition needs. */
function scrubText(text, keepDigits) {
  const t = String(text === null || text === undefined ? "" : text);
  if (/[^\s@]+@[^\s@]+\.[^\s@]+/.test(t) || /\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b/i.test(t)) return "";
  return keepDigits ? t : t.replace(/\d{6,}/g, "<n>");
}

const EXPORT_DOMAINS = ["sensor", "binary_sensor", "number", "select", "switch", "button", "text", "time", "event", "input_number", "input_boolean"];

/** The export object. `deviceIds` are the devices to list in full; `looseIds` are single entities (matched by name). */
function buildCandidateExport(opts) {
  const { hass, facts, deviceIds, looseIds, chosen, note, now, appVersion } = opts;
  const states = hass.states || {};
  const devs = [];
  const key = {};
  [...new Set(deviceIds || [])].forEach((did) => {
    const d = facts.devices[did];
    if (!d) return;
    key[did] = `d${devs.length + 1}`;
    devs.push({ key: key[did], integration: d.platforms[0] || "", manufacturer: scrubText(d.manufacturer, true), model: scrubText(d.model, true), sw_version: scrubText(d.sw_version, true) });
  });
  const byId = Object.fromEntries(facts.entities.map((e) => [e.id, e]));
  const ids = [...new Set([...(deviceIds || []).flatMap((did) => (facts.devices[did] || { entities: [] }).entities), ...(looseIds || [])])].sort();
  const entities = [];
  ids.forEach((id) => {
    const e = byId[id], st = states[id];
    if (!e || !st || !EXPORT_DOMAINS.includes(e.domain)) return;
    const a = st.attributes || {};
    const out = { id: scrubText(id), domain: e.domain, platform: e.platform || "", device: e.device && key[e.device] ? key[e.device] : null };
    if (a.unit_of_measurement) out.unit = scrubText(a.unit_of_measurement);
    if (a.device_class) out.device_class = String(a.device_class);
    if (a.state_class) out.state_class = String(a.state_class);
    out.state = scrubText(st.state).slice(0, EXPORT_STATE_MAX);
    if (Array.isArray(a.options)) out.options = a.options.map(scrubText);
    ["min", "max", "step"].forEach((k) => { if (typeof a[k] === "number") out[k] = a[k]; });
    out.attributes = Object.keys(a).filter((k) => !["friendly_name", "icon", "unit_of_measurement", "device_class", "state_class", "options", "min", "max", "step"].includes(k)).sort();
    entities.push(out);
  });
  return { format: EXPORT_FORMAT, version: EXPORT_VERSION, generated: (now || new Date()).toISOString(), card_version: CARD_VERSION,
    app_version: appVersion || null, ha_version: (hass.config && hass.config.version) || null, chosen: chosen || {},
    note: scrubText(note || "").slice(0, 300), devices: devs, entities };
}

function candidateFileName(now) { return `powerengine-candidates-${(now || new Date()).toISOString().slice(0, 10)}.json`; }

/** Devices that look like energy equipment (a power, energy or battery sensor), for "my device isn't listed". */
function wizardEnergyDevices(facts, states) {
  return Object.values(facts.devices).filter((d) => d.entities.some((id) => {
    const a = ((states || {})[id] || {}).attributes || {};
    return ["power", "energy", "battery"].includes(a.device_class) || ["W", "kW", "kWh"].includes(a.unit_of_measurement);
  })).sort((a, b) => a.name.localeCompare(b.name));
}

/* ------------------------------------------------------------ "Your system": equipment changes (pure helpers)
 * The Your system card lists what is configured and, behind "Change your system", adds, edits, replaces and removes it
 * (docs/plans/equipment-manager.md in the app repo). A change is an *op* (set or remove one target). The ops are the draft:
 * "Save draft" keeps them in this browser, "Apply to System" applies them to the saved configuration and sends it.
 * targets: a part's name ("inverter", "tariff", "ev_charger", "forecast", "events"), "plant:<id>" or "device:<id>". */
const SYSTEM_DRAFT_KEY = "powerengine.system.draft";
const SYSTEM_GROUPS = ["inverter", "plant", "device", "ev_charger", "tariff", "forecast", "events"];
// features that only make sense with a part: removing the part switches them off (adding one never switches anything on)
const SYSTEM_LEFT_OUT_FEATURES = { ev_charger: ["smart_charge_optimisation", "smart_skip_full_car", "learn_car", "learn_car_min"], events: ["axle", "axle_plus_export", "axle_stop_car", "axle_heating_setback"] };
const SYSTEM_PLANT_NOTE = "Read only: PowerEngine counts its solar in the totals, but never controls it.";

/** What can be added, in the order shown: the app's parts (title and reason come from the app) plus the card's own two. */
function systemKinds(info, devicesOn) {
  const parts = (info && info.parts) || [];
  const byPart = (k) => parts.find((p) => p.part === k);
  const out = [];
  SYSTEM_GROUPS.forEach((k) => {
    if (k === "plant") out.push({ kind: "plant", title: "Solar-only inverter or panels", why: "Counted in total solar. Read only.", single: false, required: false });
    else if (k === "device") { if (devicesOn) out.push({ kind: "device", title: "Another inverter or battery", why: "Its battery and solar are measured and shown. Read only.", single: false, required: false }); }
    else { const p = byPart(k); if (p) out.push({ kind: k, part: k, title: p.title, why: p.why, single: true, required: !!p.required }); }
  });
  return out;
}

/** The equipment in a config (or draft): a row per configured part, extra solar plant and device. The main plant belongs to
 *  the inverter and has no row of its own. */
function systemItems(cfg, info, devicesOn) {
  const site = (cfg && cfg.site) || (info && info.site) || {};
  const options = (info && info.options) || {};
  const out = [];
  ((info && info.parts) || []).forEach((p) => {
    const id = site[p.part];
    if (!id || id === "none") return;
    const row = siteRow(options, p.part, id);
    out.push({ target: p.part, kind: p.part, part: p.part, title: p.title, option: id, required: !!p.required,
      name: row ? row.name : (id === "auto" ? "Detected from your sensors" : id), status: row ? row.status || "" : "" });
  });
  ((cfg && cfg.solar_plants) || []).slice(1).filter((pl) => pl && pl.enabled !== false).forEach((pl) =>
    out.push({ target: `plant:${pl.id}`, kind: "plant", title: "Solar plant", id: pl.id, name: pl.name || pl.id, status: "read only", required: false, plant: pl }));
  if (devicesOn) ((cfg && cfg.devices) || []).forEach((d) =>
    out.push({ target: `device:${d.id}`, kind: "device", title: "Other device", id: d.id, name: d.name || d.id, option: d.adapter, status: "read only", required: false, device: d }));
  return out.sort((a, b) => SYSTEM_GROUPS.indexOf(a.kind) - SYSTEM_GROUPS.indexOf(b.kind));
}

/** Required parts that are not set up, as the app's part objects. */
function systemMissingParts(items, info) {
  const have = new Set(items.map((i) => i.target));
  return ((info && info.parts) || []).filter((p) => p.required && !have.has(p.part));
}

/* ---- ops: one entry per target; a later change to a target replaces the earlier one */
/** Add or replace the change for a target. */
function opsSet(ops, op) {
  return ops.filter((o) => o.target !== op.target).concat([op]);
}

/** Mark a target for removal (undoable). A target that is new in this draft just disappears. `inBase`: it exists in the saved config. */
function opsRemove(ops, target, inBase, extra) {
  const prev = ops.find((o) => o.target === target);
  const rest = ops.filter((o) => o.target !== target);
  if (!inBase) return rest;
  return rest.concat([Object.assign({ target, op: "remove", was: prev && prev.op === "set" ? prev : undefined }, extra || {})]);
}

/** Take a removal back: the change that was there before it returns, if there was one. */
function opsUndoRemove(ops, target) {
  const rm = ops.find((o) => o.target === target && o.op === "remove");
  const rest = ops.filter((o) => o.target !== target);
  return rm && rm.was ? rest.concat([rm.was]) : rest;
}

/** "new" | "changed" | "removed" | null for a target. */
function opsTag(ops, target, inBase) {
  const o = ops.find((x) => x.target === target);
  if (!o) return null;
  return o.op === "remove" ? "removed" : inBase ? "changed" : "new";
}

/** The rows of a summary: [{tag: "add" | "change" | "remove", name, what}] in list order. */
function opsSummary(ops, baseItems, info, devicesOn, workItems) {
  const inBase = new Set(baseItems.map((i) => i.target));
  const byTarget = Object.fromEntries(baseItems.concat(workItems || []).map((i) => [i.target, i]));
  return ops.map((o) => {
    const it = byTarget[o.target] || {};
    const name = it.name || o.label || o.target;
    const what = it.title || o.title || o.target;
    return { tag: o.op === "remove" ? "remove" : inBase.has(o.target) ? "change" : "add", name, what, target: o.target };
  });
}

/** The features switched on in `features` that go off with a part (a list of keys). */
function featuresLeftOut(features, part) {
  return (SYSTEM_LEFT_OUT_FEATURES[part] || []).filter((k) => (features || {})[k]);
}

/** A copy of `draft` with the ops applied. Removing a part switches off the features that need it; nothing else in the features changes. */
function applyOps(draft0, ops, info) {
  const d = JSON.parse(JSON.stringify(draft0));
  d.site = Object.assign({}, d.site || (info && info.site) || {});
  d.inputs = d.inputs || {};
  d.solar_plants = d.solar_plants || [];
  const main = () => {
    if (!d.solar_plants.length) d.solar_plants.push({ id: "main", name: "Main", forecast: "none", enabled: true, power: {}, energy_today: {} });
    return d.solar_plants[0];
  };
  ops.forEach((o) => {
    if (o.kind === "plant") {
      const id = o.target.slice("plant:".length);
      d.solar_plants = d.solar_plants.filter((p, i) => i === 0 || p.id !== id);
      if (o.op === "set") d.solar_plants.push(JSON.parse(JSON.stringify(o.plant)));
    } else if (o.kind === "device") {
      const id = o.target.slice("device:".length);
      d.devices = (Array.isArray(d.devices) ? d.devices : []).filter((x) => x.id !== id);
      if (o.op === "set") d.devices.push(JSON.parse(JSON.stringify(o.device)));
    } else if (o.op === "remove") {
      d.site[o.part] = "none";
      featuresLeftOut(d.features, o.part).forEach((k) => { d.features[k] = false; });
      (o.clear || []).forEach((k) => { delete d.inputs[k]; });
      if (o.part === "forecast") main().forecast = "none";
    } else {
      d.site[o.part] = o.part === "tariff" ? "auto" : o.option;
      if (o.part === "inverter" && o.firmware !== undefined) d.site.inverter_firmware = o.firmware || null;
      Object.entries(o.inputs || {}).forEach(([k, v]) => { if (v) d.inputs[k] = JSON.parse(JSON.stringify(v)); else delete d.inputs[k]; });
      if (o.plant) { const p = main(); p.power = Object.assign({}, o.plant.power || {}); p.energy_today = Object.assign({}, o.plant.energy_today || {}); }
      if (o.part === "forecast") { const p = main(); if (!p.forecast || p.forecast === "none") p.forecast = "solcast_site"; }
    }
  });
  return d;
}

/** The configuration Apply sends: the saved one with the ops applied. Everything else in it (settings, features, mode,
 *  notifications) is carried over as the config page would. */
function buildApplyConfig(saved, roles, settings, ops, info, devicesOn) {
  const base = initialDraft(saved, roles, [], settings).draft;
  base.site = siteFromSelection((saved && saved.site) || (info && info.site) || {}, null);
  if (devicesOn) base.devices = deviceDraft((saved || {}).devices); else delete base.devices;
  // an optional part that was never set up is left out ("none"), as the setup wizard did, so its inputs are not asked for
  const touched = new Set(ops.map((o) => o.target));
  const leftOut = ((info && info.parts) || []).filter((p) => !p.required && !touched.has(p.part) && !base.site[p.part])
    .map((p) => ({ target: p.part, op: "remove", kind: p.part, part: p.part, clear: [] }));
  return buildConfig(applyOps(base, leftOut.concat(ops), info));
}

/** The equipment part of a config, for a fingerprint: the site, the plants, the devices and the inputs of the parts' roles. */
function equipmentOf(cfg, info) {
  const keys = new Set(((info && info.parts) || []).flatMap((p) => p.roles || []));
  const inputs = {};
  Object.keys((cfg && cfg.inputs) || {}).sort().forEach((k) => { if (keys.has(k)) inputs[k] = cfg.inputs[k]; });
  return { site: (cfg && cfg.site) || null, solar_plants: (cfg && cfg.solar_plants) || [], devices: (cfg && cfg.devices) || [], inputs };
}

function systemFingerprint(cfg, info) {
  const s = JSON.stringify(equipmentOf(cfg, info));
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return String(h);
}

/** The config page's draft after the equipment changed underneath it (Apply to System): the saved plants, devices and any
 *  input that changed come from the new saved config; every other edit in the draft stays. */
function overlayEquipment(draft, oldSaved, newSaved, devicesOn) {
  const out = JSON.parse(JSON.stringify(draft));
  out.solar_plants = JSON.parse(JSON.stringify((newSaved || {}).solar_plants || []));
  if (devicesOn) out.devices = deviceDraft((newSaved || {}).devices);
  const a = (oldSaved || {}).inputs || {}, b = (newSaved || {}).inputs || {};
  out.inputs = out.inputs || {};
  new Set([...Object.keys(a), ...Object.keys(b)]).forEach((k) => {
    if (JSON.stringify(a[k] || null) === JSON.stringify(b[k] || null)) return;
    if (b[k]) out.inputs[k] = JSON.parse(JSON.stringify(b[k])); else delete out.inputs[k];
  });
  return out;
}

/** What removing something means, in words. `blocked`: a required part, which can only be replaced. */
function systemImpact(item, info, cfg, roles) {
  const part = item.part ? ((info && info.parts) || []).find((p) => p.part === item.part) : null;
  const label = (k) => { const r = (roles || []).find((x) => x.key === k); return r ? r.label : k; };
  const mapped = part ? (part.roles || []).filter((k) => { const s = ((cfg && cfg.inputs) || {})[k]; return s && (s.entity || s.value !== undefined); }) : [];
  const out = { blocked: !!item.required, level: "warn", lead: "", items: [], roles: mapped.map(label), roleKeys: mapped, features: [] };
  if (item.required) {
    out.lead = `${item.title} is required. Replace it with another one instead of removing it.`;
    return out;
  }
  const table = {
    ev_charger: ["Removing the car charger switches off:", ["Smart-charge slot requests", "Planning around the car's charging"], "bad"],
    forecast: ["Removing the solar forecast means:", ["Planning has no forecast to work from, so overnight charging cannot allow for tomorrow's sun"], "bad"],
    events: ["Removing grid events means:", ["Paid export windows are no longer planned for", "The battery is not held back for them"], "bad"],
    plant: ["Removing this solar source means:", ["Total solar and the energy-flow card drop by this plant", "The forecast comparison will read high"], "warn"],
    device: ["Removing this device means:", ["Its readings are no longer shown or counted"], "warn"],
  }[item.kind];
  if (table) { out.lead = table[0]; out.items = table[1]; out.level = table[2]; }
  if (item.part) {
    out.features = featuresLeftOut((cfg && cfg.features) || {}, item.part).map((k) => T((FEATURES.find((f) => f[0] === k) || [k, k])[1]));
  }
  return out;
}

/* ---- the draft in this browser (local storage; every access can fail, and then the draft lasts until the page closes) */
/** The saved ops, or {ops: [], dropped: true} when the saved config changed since (a draft from an old base is not applied). */
function systemDraftLoad(storage, fingerprint) {
  try {
    const raw = storage && storage.getItem(SYSTEM_DRAFT_KEY);
    if (!raw) return { ops: [], dropped: false };
    const d = JSON.parse(raw);
    if (!d || !Array.isArray(d.ops) || !d.ops.length) return { ops: [], dropped: false };
    return d.base === fingerprint ? { ops: d.ops, dropped: false, at: d.at || null } : { ops: [], dropped: true };
  } catch (e) { return { ops: [], dropped: false }; }
}

function systemDraftSave(storage, ops, fingerprint, now) {
  try {
    if (!storage) return false;
    if (!ops.length) { storage.removeItem(SYSTEM_DRAFT_KEY); return true; }
    storage.setItem(SYSTEM_DRAFT_KEY, JSON.stringify({ base: fingerprint, ops, at: (now || new Date()).toISOString() }));
    return true;
  } catch (e) { return false; }
}

/* ------------------------------------------------------------ "Your system" card
 * The list is read only. "Change your system" opens a panel (an overlay inside this card) where equipment is added, edited,
 * replaced and removed. The panel works on a list of ops (above); nothing is live until "Apply to System". */
const SYSTEM_GROUP_LABELS = { inverter: "Inverter and battery", plant: "Extra solar", device: "Other devices", ev_charger: "Car charger",
  tariff: "Tariff", forecast: "Solar forecast", events: "Grid events" };
const SYSTEM_STEPS = ["What is it?", "Find it", "Which entities?", "Review"];

class PowerEngineSystemCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._live = [];
    this._pickers = [];
  }

  getCardSize() { return 6; }

  getGridOptions() { return { columns: "full", rows: "auto" }; }

  set hass(hass) {
    this._hass = hass;
    setNames(hass);
    (this._pickers || []).forEach((p) => { p.hass = hass; });
    if (!this._started) this._start();
    else this._update();
  }

  async _start() {
    this._started = true;
    if (!this._read()) { this.style.display = "none"; this._started = false; return; }
    this.style.display = "";
    this._usePicker = await ensureEntityPicker();
    this._readOnly = !(this._hass.user && this._hass.user.is_admin);
    const loaded = systemDraftLoad(this._storage(), this._fp);
    this._ops = loaded.ops;
    this._opsFp = this._fp;
    this._notice = loaded.dropped ? "A draft from earlier was dropped because your system has changed since." : (loaded.ops.length ? "Draft from earlier, kept in this browser." : "");
    this._panel = null;
    this._renderAll();
    this._subscribe();
  }

  /** Read what the app publishes. False when it doesn't (an app too old for this card, or not running). */
  _read() {
    const s = this._hass.states;
    const ver = s[VERSION_SENSOR], cat = catalogueAttrs(s, CATALOGUE_SENSOR), map = s[MAPPING_SENSOR];
    const info = ver && ver.state !== "unavailable" ? wizardInfo(ver.attributes) : null;
    if (!info || !cat || !cat.roles) return false;
    this._verObj = ver; this._mapObj = map;
    this._info = info;
    this._roles = (cat.roles || []).map((r) => Object.assign({ required: "yes" }, r));
    this._settings = cat.settings || catalogueAttrs(s, SETTINGS_SENSOR) || {};
    this._saved = (((map || {}).attributes) || {}).config || {};
    this._retest = (ver.attributes || {}).retest_required === true;
    this._devicesOn = devicesSupported(ver.state);
    this._fp = systemFingerprint(Object.assign({}, this._saved, { site: this._info.site }), this._info);
    return true;
  }

  _update() {
    if (this._hass.states[VERSION_SENSOR] !== this._verObj || this._hass.states[MAPPING_SENSOR] !== this._mapObj) {
      if (!this._read()) return;
      if ((this._ops || []).length && this._opsFp !== this._fp && !this._applying) {   // the saved system moved: an old draft no longer fits
        this._ops = []; this._opsFp = this._fp; systemDraftSave(this._storage(), [], this._fp);
        this._notice = "Your system changed, so the draft was dropped.";
      }
      this._opsFp = this._opsFp === undefined ? this._fp : this._opsFp;
      this._renderPage();
    } else { this._tick(); this._tickFlow(); }
  }

  async _subscribe() {
    try {
      this._unsub = await this._hass.connection.subscribeEvents((ev) => {
        if (!this._applying) return;                       // a save from the configuration card is not ours
        const d = ev.data || {};
        this._applying = false;
        if (d.ok) {
          this._ops = []; this._opsFp = undefined; systemDraftSave(this._storage(), [], this._fp);
          this._result = { ok: true, text: `Applied to your system. PowerEngine is reloading. ${d.message || ""}`.trim() };
          this._panel = null; this._work = null; this._notice = "";
          window.dispatchEvent(new CustomEvent("powerengine-config-applied"));
        } else this._result = { ok: false, text: `Not applied: ${d.message}` };
        this._renderAll();
      }, RESULT_EVENT);
    } catch (e) { /* non-admin users can't subscribe; applying is admin-only anyway */ }
  }

  disconnectedCallback() { if (this._unsub) { this._unsub(); this._unsub = null; } }

  _storage() { try { return typeof localStorage !== "undefined" ? localStorage : null; } catch (e) { return null; } }

  // ---- state helpers
  _facts() { return wizardFacts(this._hass); }
  _part(k) { return this._info.parts.find((p) => p.part === k) || null; }
  _nameOf(part) { return (id) => { const r = siteRow(this._info.options, part, id); return r ? r.name : id; }; }
  _cands(part) { return wizardCandidates(part, this._facts(), this._nameOf(part.part)); }

  /** The saved configuration as a draft, with the site the app publishes. */
  _baseDraft() {
    const d = initialDraft(this._saved, [], [], this._settings).draft;
    d.site = siteFromSelection(this._info.site, null);
    if (this._devicesOn) d.devices = deviceDraft(this._saved.devices); else delete d.devices;
    return d;
  }

  _draftWith(ops) { return applyOps(this._baseDraft(), ops || [], this._info); }
  _baseItems() { return systemItems(this._baseDraft(), this._info, this._devicesOn); }

  /** The rows the panel shows: the equipment with the ops applied, plus what is marked for removal (struck through). */
  _panelItems() {
    const work = this._work || [];
    const base = this._baseItems();
    const inBase = new Set(base.map((i) => i.target));
    const items = systemItems(this._draftWith(work.filter((o) => o.op !== "remove")), this._info, this._devicesOn).filter((i) => !work.some((o) => o.op === "remove" && o.target === i.target));
    work.filter((o) => o.op === "remove").forEach((o) => { const b = base.find((i) => i.target === o.target); if (b) items.push(b); });
    items.forEach((i) => { i.tag = opsTag(work, i.target, inBase.has(i.target)); });
    return items.sort((a, b) => SYSTEM_GROUPS.indexOf(a.kind) - SYSTEM_GROUPS.indexOf(b.kind));
  }

  _dirty() { return JSON.stringify(this._work || []) !== this._work0; }

  // ---- drawing
  _style() {
    return el("style", {}, `
      :host { display: block; }
      .content { padding: 0 16px 16px; }
      h2 { margin: 0; font-size: 1.2em; font-weight: 600; } h3 { margin: 12px 0 4px; }
      .top { display: flex; justify-content: space-between; align-items: center; gap: 8px 12px; flex-wrap: wrap; padding: 16px 16px 4px; }
      .muted { color: var(--secondary-text-color); font-size: .9em; }
      .group { font-size: .78em; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); margin: 14px 0 2px; }
      .item { display: flex; gap: 8px 12px; align-items: center; flex-wrap: wrap; padding: 10px 0; border-top: 1px solid var(--divider-color); }
      .item .main { flex: 1 1 220px; min-width: 0; }
      .item .name { font-weight: 600; } .item .sub, .item .live { color: var(--secondary-text-color); font-size: .88em; overflow-wrap: anywhere; }
      .item.gone .name { text-decoration: line-through; color: var(--secondary-text-color); }
      .badge { font-size: .75em; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--divider-color); white-space: nowrap; }
      .badge.req { background: rgba(219,68,55,.14); border-color: transparent; }
      .badge.ok { color: var(--success-color, #43a047); border-color: var(--success-color, #43a047); }
      .badge.warn { background: rgba(255,160,0,.18); color: var(--warning-color, #b26a00); border-color: transparent; }
      .tag { font-size: .75em; font-weight: 600; padding: 1px 8px; border-radius: 4px; }
      .tag.new { background: rgba(67,160,71,.18); color: var(--success-color, #2e7d32); }
      .tag.changed { background: rgba(255,160,0,.2); color: var(--warning-color, #b26a00); }
      .tag.removed { background: rgba(219,68,55,.16); color: var(--error-color, #c62828); }
      .banner { padding: 8px 12px; border-radius: 6px; margin: 8px 16px; background: var(--secondary-background-color); display: flex; justify-content: space-between; gap: 8px 12px; align-items: center; flex-wrap: wrap; }
      .banner.ok { background: rgba(67,160,71,.15); } .banner.error { background: rgba(219,68,55,.15); } .banner.warn { background: rgba(255,160,0,.18); }
      .panel .banner { margin: 0; }
      .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; justify-content: flex-end; }
      button { min-height: 36px; padding: 0 14px; cursor: pointer; font: inherit; }
      button.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border: none; border-radius: 6px; }
      button.primary:disabled { opacity: .5; cursor: default; }
      button.danger { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
      button.link { background: none; border: none; color: var(--primary-color); padding: 0; min-height: 0; text-decoration: underline; }
      a { color: var(--primary-color); }
      .shade { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 8; display: flex; justify-content: center; align-items: stretch; padding: 16px; box-sizing: border-box; }
      .panel { background: var(--card-background-color, #fff); color: var(--primary-text-color); border-radius: 12px; width: 100%; max-width: 860px; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
      .panel > header { padding: 14px 16px; border-bottom: 1px solid var(--divider-color); display: flex; justify-content: space-between; align-items: center; gap: 8px; }
      .panel > .body { padding: 16px; overflow: auto; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
      .panel > footer { padding: 12px 16px; border-top: 1px solid var(--divider-color); display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; align-items: center; }
      .steps { display: flex; gap: 6px; flex-wrap: wrap; font-size: .88em; color: var(--secondary-text-color); }
      .steps span { padding: 2px 10px; border-radius: 99px; background: var(--secondary-background-color); }
      .steps span.now { background: var(--primary-color); color: var(--text-primary-color, #fff); } .steps span.done { color: var(--primary-text-color); }
      .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
      .tile { text-align: left; padding: 12px; display: flex; flex-direction: column; gap: 4px; height: 100%; border: 1px solid var(--divider-color); border-radius: 8px; background: none; color: inherit; }
      .tile small { color: var(--secondary-text-color); }
      .cand { display: flex; gap: 8px 12px; align-items: center; border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px 12px; cursor: pointer; flex-wrap: wrap; }
      .cand .main { flex: 1 1 200px; min-width: 0; } .cand.sel { border-color: var(--primary-color); box-shadow: 0 0 0 1px var(--primary-color); } .cand.used { opacity: .55; cursor: default; }
      .row { padding: 6px 0; border-top: 1px solid var(--divider-color); }
      .row .head { display: flex; gap: 6px 10px; align-items: center; flex-wrap: wrap; } .row .label { font-weight: 600; }
      .row .desc, .row .live { color: var(--secondary-text-color); font-size: .88em; } .row .problem { color: var(--error-color, #db4437); font-size: .88em; }
      .row.bad .label { color: var(--error-color, #db4437); }
      .ctl { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin-top: 4px; }
      .ctl select, .ctl input[type=text], .ctl input[type=number], input.search { min-height: 36px; box-sizing: border-box; }
      input.search { width: 100%; }
      ha-entity-picker { display: block; min-width: 260px; flex: 1; }
      .impact { border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 6px; background: rgba(255,160,0,.18); }
      .impact.bad { background: rgba(219,68,55,.14); } .impact ul { margin: 0; padding-left: 18px; }
      details { margin: 6px 0; } summary { cursor: pointer; } ul { margin: 4px 0; padding-left: 20px; } textarea { width: 100%; box-sizing: border-box; }
      .sumrow { display: flex; gap: 8px 12px; align-items: baseline; padding: 6px 0; border-top: 1px solid var(--divider-color); flex-wrap: wrap; }
    `);
  }

  _renderAll() {
    const root = this.shadowRoot;
    if (!root || !this._info) return;
    this._pageBox = el("ha-card", {});
    this._shadeBox = el("div", {});
    root.replaceChildren(this._style(), this._pageBox, this._shadeBox);
    this._renderPage();
    this._renderShade();
  }

  _renderPage() {
    if (!this._pageBox) return;
    this._live = [];
    const box = this._pageBox;
    const kids = [];
    const change = el("button", { class: "primary", disabled: this._readOnly, onclick: () => this._open() }, "Change your system");
    kids.push(el("div", { class: "top" }, el("h2", {}, "Your system"), change));
    if (this._readOnly) kids.push(el("div", { class: "banner" }, "View only: log in as an admin to change your system."));
    if (this._result) kids.push(el("div", { class: `banner ${this._result.ok ? "ok" : "error"}` }, this._result.text));
    if (this._retest) kids.push(el("div", { class: "banner error" }, SITE_RETEST));
    if (this._notice) kids.push(el("div", { class: "banner" }, this._notice));
    const ops = this._ops || [];
    if (ops.length) {
      const sum = opsSummary(ops, this._baseItems(), this._info, this._devicesOn, systemItems(this._draftWith(ops), this._info, this._devicesOn));
      const text = sum.map((r) => `${r.tag} ${r.name}`).join("; ");
      const actions = this._discardAsk
        ? [el("span", {}, "Discard the draft?"), el("button", { class: "danger", onclick: () => { this._ops = []; this._discardAsk = false; this._notice = ""; systemDraftSave(this._storage(), [], this._fp); this._renderPage(); } }, "Yes, discard"),
          el("button", { onclick: () => { this._discardAsk = false; this._renderPage(); } }, "Keep")]
        : [el("button", { onclick: () => this._open() }, "Edit draft"), el("button", { class: "danger", onclick: () => { this._discardAsk = true; this._renderPage(); } }, "Discard draft"),
          el("button", { class: "primary", disabled: this._readOnly, onclick: () => this._open("apply") }, "Apply to System")];
      kids.push(el("div", { class: "banner warn" }, el("span", {}, el("strong", {}, `Draft: ${ops.length} change${ops.length === 1 ? "" : "s"}, not applied to your system. `), text, "."), el("div", { class: "actions" }, actions)));
    }
    const items = this._baseItems();
    const facts = this._facts();
    const missing = systemMissingParts(items, this._info);
    if (missing.length) kids.push(el("div", { class: "banner warn" }, `Still to add: ${missing.map((p) => p.title).join(", ")}. PowerEngine needs ${missing.length === 1 ? "it" : "them"} to work.`));
    const list = el("div", { class: "content" });
    if (!items.length) list.append(el("div", { class: "muted", style: "padding: 12px 0" }, "Nothing is set up yet. Choose Change your system to add your inverter and tariff."));
    let last = "";
    items.forEach((it) => {
      if (it.kind !== last) { list.append(el("div", { class: "group" }, SYSTEM_GROUP_LABELS[it.kind] || it.title)); last = it.kind; }
      list.append(this._itemRow(it, null, facts, this._live));
    });
    kids.push(list);
    box.replaceChildren(...kids);
    this._tick();
  }

  /** One row of the list: name, status, a line about what it is, and a live value that updates without a redraw. */
  _itemRow(it, controls, facts, sink) {
    const cfg = this._saved;
    let sub = "", name = it.name;
    if (it.part) {
      const part = this._part(it.part);
      const cands = part ? wizardCandidates(part, facts, this._nameOf(it.part)) : [];
      const inUse = part ? wizardInUse(part, cands, cfg.inputs, this._info.site) : null;
      if (it.option === "auto" && inUse) name = this._nameOf(it.part)(inUse.option);
      sub = inUse && inUse.device ? wizardDeviceName(inUse.device) : "";
      if (it.kind === "inverter") {
        const fw = this._info.site.inverter_firmware;
        sub = [sub, fw ? `firmware ${fw}` : (this._info.detected ? `firmware ${this._info.detected} (detected)` : "")].filter(Boolean).join(", ");
      }
    } else if (it.plant) sub = `Power: ${(it.plant.power || {}).entity || "not set"}. ${SYSTEM_PLANT_NOTE}`;
    else if (it.device) sub = `${(siteRow(this._info.options, "inverter", it.device.adapter) || { name: it.device.adapter }).name}${it.device.firmware ? `, firmware ${it.device.firmware}` : ""}. Read only.`;
    const live = el("span", { class: "live" });
    sink.push(() => { live.textContent = this._liveText(it); });
    const badge = it.status ? el("span", { class: `badge ${it.status === "verified" ? "ok" : it.status === "read only" ? "" : "warn"}`,
      title: it.status === "verified" ? "Tested on real hardware" : it.status === "read only" ? "" : "Not fully tested: Active is refused until it is" }, it.status === "verified" ? "verified" : it.status) : null;
    return el("div", { class: `item${it.tag === "removed" ? " gone" : ""}` },
      el("div", { class: "main" }, el("div", { class: "name" }, name), sub ? el("div", { class: "sub" }, sub) : null), live, badge,
      it.tag ? el("span", { class: `tag ${it.tag}` }, it.tag === "removed" ? "will be removed" : it.tag) : null, controls);
  }

  _liveText(it) {
    const st = (id) => (id ? this._hass.states[id] : null);
    const show = (s) => (s ? `${s.state} ${((s.attributes || {}).unit_of_measurement) || ""}`.trim() : "");
    if (it.plant) return show(st((it.plant.power || {}).entity));
    if (it.device) { const r = deviceReadout(this._hass.states, it.device); return r.startsWith("No inputs") || r.startsWith("Waiting") ? "" : r; }
    const part = it.part ? this._part(it.part) : null;
    if (!part) return "";
    const keys = it.kind === "inverter" ? ["battery_soc"].concat(part.roles || []) : (part.roles || []);
    for (const k of keys) {
      const sp = (this._saved.inputs || {})[k];
      const s = sp && sp.entity ? st(sp.entity) : null;
      if (s && s.state !== "unavailable") { const r = this._roles.find((x) => x.key === k); return `${r ? r.label : k}: ${show(s)}`; }
    }
    return "";
  }

  _tick() { (this._live || []).forEach((f) => { try { f(); } catch (e) { /* a row that can't update is left as it was */ } }); }

  // ---- the panel
  _open(view) {
    this._work = (this._ops || []).slice();
    this._work0 = JSON.stringify(this._work);
    this._result = null;
    this._panel = { view: view || "list" };
    this._renderShade();
  }

  _closePanel() { this._panel = null; this._work = null; this._f = null; this._renderShade(); this._renderPage(); }

  _tryClose() { if (this._dirty()) { this._panel = { view: "close" }; this._renderShade(); } else this._closePanel(); }

  _saveDraft() {
    this._ops = (this._work || []).slice();
    this._opsFp = this._fp;
    this._notice = this._ops.length ? "Draft saved in this browser." : "";
    if (!systemDraftSave(this._storage(), this._ops, this._fp)) this._notice = this._ops.length ? "Draft kept until you close this page (this browser would not store it)." : "";
    this._closePanel();
  }

  _toList() { this._panel = { view: "list" }; this._f = null; this._renderShade(); }

  _renderShade() {
    const box = this._shadeBox;
    if (!box) return;
    this._pickers = [];
    this._flowLive = [];
    if (!this._panel) { box.replaceChildren(); return; }
    const p = this._panel;
    const body = { list: this._viewList, flow: this._viewFlow, remove: this._viewRemove, apply: this._viewApply, close: this._viewClose }[p.view].call(this);
    const panel = el("div", { class: "panel", role: "dialog", "aria-modal": "true", "aria-label": "Change your system" }, ...body);
    const shade = el("div", { class: "shade", tabindex: "-1", onclick: (ev) => { if (ev.target === shade && p.view === "list") this._tryClose(); },
      onkeydown: (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); if (p.view === "list") this._tryClose(); else this._toList(); } } }, panel);
    box.replaceChildren(shade);
    const first = panel.querySelector("button.primary:not(:disabled)") || panel.querySelector("button");
    if (first && !p.noFocus) first.focus();
    this._tickFlow();
  }

  _tickFlow() { (this._flowLive || []).forEach((f) => { try { f(); } catch (e) { /* leave as is */ } }); }

  _viewList() {
    const items = this._panelItems();
    const n = (this._work || []).length;
    const facts = this._facts();
    const body = el("div", { class: "body" }, el("div", { class: "top", style: "padding: 0" },
      el("span", { class: "muted" }, "Nothing is live until you choose Apply to System. Save draft keeps your changes for later."),
      el("button", { class: "primary", onclick: () => { this._f = this._newFlow(); this._panel = { view: "flow" }; this._renderShade(); } }, "+ Add")));
    let last = "";
    items.forEach((it) => {
      if (it.kind !== last) { body.append(el("div", { class: "group" }, SYSTEM_GROUP_LABELS[it.kind] || it.title)); last = it.kind; }
      const ctl = el("div", { class: "actions" });
      if (it.tag === "removed") ctl.append(el("button", { onclick: () => { this._work = opsUndoRemove(this._work, it.target); this._renderShade(); } }, "Undo"));
      else {
        ctl.append(el("button", { onclick: () => this._edit(it) }, "Edit"));
        if (it.required) ctl.append(el("button", { onclick: () => this._replace(it) }, "Replace"));
        else ctl.append(el("button", { class: "danger", onclick: () => { this._panel = { view: "remove", target: it.target }; this._renderShade(); } }, "Remove"));
      }
      body.append(this._itemRow(it, ctl, facts, this._flowLive));
    });
    if (!items.length) body.append(el("div", { class: "muted" }, "Nothing set up. Choose + Add to start with your inverter."));
    const missing = systemMissingParts(systemItems(this._draftWith(this._work), this._info, this._devicesOn), this._info);
    if (missing.length) body.append(el("div", { class: "banner warn" }, `Still to add: ${missing.map((m) => m.title).join(", ")}.`));
    return [el("header", {}, el("h2", {}, "Change your system"), el("button", { onclick: () => this._tryClose() }, "Close")), body,
      el("footer", {}, el("span", { class: "muted" }, n ? `${n} change${n === 1 ? "" : "s"} in this draft` : "No changes"),
        el("div", { class: "actions" }, el("button", { onclick: () => this._tryClose() }, this._dirty() ? "Cancel" : "Close"),
          el("button", { disabled: !this._dirty(), onclick: () => this._saveDraft() }, "Save draft"),
          el("button", { class: "primary", disabled: !n, onclick: () => { this._panel = { view: "apply" }; this._renderShade(); } }, "Apply to System")))];
  }

  _viewClose() {
    const n = (this._work || []).length;
    return [el("header", {}, el("h2", {}, "Close without applying?")),
      el("div", { class: "body" }, el("div", {}, `You have unsaved edits (${n} change${n === 1 ? "" : "s"} in the draft).`),
        el("div", { class: "muted" }, "Save draft keeps them in this browser so you can come back. Discard changes throws them away.")),
      el("footer", {}, el("button", { onclick: () => this._toList() }, "Keep editing"),
        el("div", { class: "actions" }, el("button", { class: "danger", onclick: () => this._closePanel() }, "Discard changes"),
          el("button", { class: "primary", onclick: () => this._saveDraft() }, "Save draft")))];
  }

  _viewRemove() {
    const it = this._panelItems().find((i) => i.target === this._panel.target);
    if (!it) return this._viewList();
    const im = systemImpact(it, this._info, this._draftWith(this._work), this._roles);
    const body = el("div", { class: "body" }, el("h3", { style: "margin: 0" }, `Remove ${it.name}?`),
      el("div", { class: `impact ${im.level}` }, el("div", {}, T(im.lead)), im.items.length ? el("ul", {}, im.items.map((x) => el("li", {}, T(x)))) : null,
        im.features.length ? el("div", {}, `Switched off with it: ${im.features.join(", ")}.`) : null,
        im.roles.length ? el("div", {}, `Inputs that will no longer be used: ${im.roles.join(", ")}.`) : null));
    let clearBox = null;
    if (it.part && im.roleKeys.length) {
      clearBox = el("input", { type: "checkbox", checked: true });
      body.append(el("label", { class: "ctl" }, clearBox, " Also clear the saved entity mappings for it"));
    }
    body.append(el("div", { class: "muted" }, "PowerEngine does not change Active, Passive or Pause itself. You can undo this until you apply to the system."));
    const confirm = el("button", { class: "danger", onclick: () => {
      const extra = { kind: it.kind, part: it.part, title: it.title, label: it.name, clear: it.part && clearBox && clearBox.checked ? im.roleKeys : [] };
      this._work = opsRemove(this._work, it.target, this._baseItems().some((b) => b.target === it.target), extra);
      this._toList();
    } }, "Mark for removal");
    return [el("header", {}, el("h2", {}, "Remove from your system"), el("button", { onclick: () => this._toList() }, "Cancel")), body,
      el("footer", {}, el("span", {}), el("div", { class: "actions" }, el("button", { class: "primary", onclick: () => this._toList() }, "Keep it"), im.blocked
        ? el("button", { onclick: () => this._replace(it) }, "Replace instead") : confirm))];
  }

  _viewApply() {
    const ops = this._work || [];
    const base = this._baseItems();
    const after = systemItems(this._draftWith(ops), this._info, this._devicesOn);
    const sum = opsSummary(ops, base, this._info, this._devicesOn, after);
    const body = el("div", { class: "body" }, el("h3", { style: "margin: 0" }, "Apply these changes to your system?"));
    sum.forEach((r) => body.append(el("div", { class: "sumrow" }, el("span", { class: `tag ${r.tag === "add" ? "new" : r.tag === "change" ? "changed" : "removed"}` }, r.tag), el("strong", {}, r.name), el("span", { class: "muted" }, r.what))));
    const next = this._draftWith(ops).site;
    if (this._info.site.inverter && siteNeedsWarning(this._info.options, siteFromSelection(this._info.site, null), next)) body.append(el("div", { class: "impact" }, SITE_WARNING));
    const missing = systemMissingParts(after, this._info);
    if (missing.length) body.append(el("div", { class: "impact" }, `Still missing after this: ${missing.map((m) => m.title).join(", ")}. PowerEngine will start but cannot work until ${missing.length === 1 ? "it is" : "they are"} added.`));
    body.append(el("div", { class: "muted" }, "This writes the configuration and PowerEngine reloads it. Your other settings are not touched. A backup of the old configuration is kept."));
    if (this._result && !this._result.ok) body.append(el("div", { class: "banner error" }, this._result.text));
    return [el("header", {}, el("h2", {}, "Apply to System")), body,
      el("footer", {}, el("button", { disabled: this._applying, onclick: () => this._toList() }, "Back"),
        el("div", { class: "actions" }, el("button", { disabled: this._applying, onclick: () => this._saveDraft() }, "Save draft instead"),
          el("button", { class: "primary", disabled: this._applying || this._readOnly || !ops.length, onclick: () => this._apply() }, this._applying ? "Applying…" : "Apply to System")))];
  }

  async _apply() {
    const cfg = buildApplyConfig(this._saved, this._roles, this._settings, this._work, this._info, this._devicesOn);
    this._applying = true;
    this._result = null;
    this._renderShade();
    try {
      await this._hass.callWS({ type: "fire_event", event_type: SAVE_EVENT, event_data: { config: cfg } });
      setTimeout(() => { if (this._applying) { this._applying = false; this._result = { ok: false, text: SAVE_NO_REPLY }; this._renderShade(); } }, SAVE_WAIT_MS);
    } catch (e) {
      this._applying = false;
      this._result = { ok: false, text: `Could not send: ${e.message || e}. Applying needs an admin user.` };
      this._renderShade();
    }
  }

  // ---- add, edit and replace
  _newFlow() { return { mode: "add", step: 1, kind: null, pick: null, inputs: {}, plant: null, device: null, firmware: undefined, says: {}, showAll: false, notice: "", note: "", auto: {}, open: false }; }

  /** The working draft's current inputs for a part's roles (a copy). */
  _partInputs(part) {
    const d = this._draftWith(this._work);
    const out = {};
    (part.roles || []).forEach((k) => { if (d.inputs[k]) out[k] = JSON.parse(JSON.stringify(d.inputs[k])); });
    return out;
  }

  _edit(it) {
    const f = this._newFlow();
    f.mode = "edit"; f.kind = it.kind; f.target = it.target; f.step = 3; f.showAll = false;
    const d = this._draftWith(this._work);
    if (it.part) {
      const part = this._part(it.part);
      f.inputs = this._partInputs(part);
      f.firmware = d.site.inverter_firmware || null;
      const cands = this._cands(part);
      const inUse = wizardInUse(part, cands, d.inputs, d.site);
      f.pick = inUse ? { cand: inUse.key, option: inUse.option } : { option: d.site[it.part] === "auto" ? (cands[0] || {}).option || "auto" : d.site[it.part] };
      if (!inUse) f.showAll = true;
      if (it.part === "inverter") { const pl = d.solar_plants[0] || {}; f.plant = { power: Object.assign({}, pl.power || {}), energy_today: Object.assign({}, pl.energy_today || {}) }; }
    } else if (it.plant) { f.plant = JSON.parse(JSON.stringify(it.plant)); f.plant.power = f.plant.power || {}; f.plant.energy_today = f.plant.energy_today || {}; f.showAll = true; }
    else if (it.device) { f.device = JSON.parse(JSON.stringify(it.device)); f.device.inputs = f.device.inputs || {}; f.showAll = true; }
    this._f = f;
    this._panel = { view: "flow" };
    this._renderShade();
  }

  _replace(it) {
    const f = this._newFlow();
    f.mode = "replace"; f.kind = it.kind; f.target = it.target; f.step = 2;
    this._f = f;
    this._panel = { view: "flow" };
    this._renderShade();
  }

  _chooseKind(kind) {
    const f = this._f;
    f.kind = kind.kind; f.step = 2;
    if (kind.part) {
      f.target = kind.part;
      if (this._panelItems().some((i) => i.target === kind.part && i.tag !== "removed")) f.mode = "replace";
    } else if (kind.kind === "plant") { f.plant = { id: "", name: "", forecast: "none", enabled: true, power: {}, energy_today: {} }; }
    else if (kind.kind === "device") { f.device = { id: "", adapter: (siteRows(this._info.options, "inverter")[0] || {}).id || "", name: "", firmware: "", control: "read_only", inputs: {} }; }
    this._renderShade();
  }

  _kindInfo(kind) { return systemKinds(this._info, this._devicesOn).find((k) => k.kind === kind) || { kind, title: kind }; }

  /** The op the flow would stage (also used to preview the draft it makes, for the "required" badges and checks). */
  _flowOp(f) {
    if (f.kind === "plant") {
      const id = f.target ? f.target.slice("plant:".length) : wizardPlantId(f.plant.name || "plant", this._draftWith(this._work).solar_plants.map((p) => p.id));
      return { target: `plant:${id}`, op: "set", kind: "plant", title: "Solar plant", label: f.plant.name || id, plant: Object.assign({}, f.plant, { id, name: f.plant.name || id, enabled: true }) };
    }
    if (f.kind === "device") {
      const id = f.target ? f.target.slice("device:".length) : deviceNewId(f.device.name || "device", (this._draftWith(this._work).devices || []).map((x) => x.id));
      return { target: `device:${id}`, op: "set", kind: "device", title: "Other device", label: f.device.name || id, device: Object.assign({}, f.device, { id, name: f.device.name || id, control: "read_only" }) };
    }
    const part = this._part(f.kind);
    const inputs = {};
    (part.roles || []).forEach((k) => { inputs[k] = f.inputs[k] || null; });
    const op = { target: f.kind, op: "set", kind: f.kind, part: f.kind, title: part.title, label: part.title, option: (f.pick || {}).option || (this._draftWith(this._work).site[f.kind]), inputs };
    if (f.kind === "inverter") { op.firmware = f.firmware === undefined ? this._draftWith(this._work).site.inverter_firmware || null : f.firmware; op.plant = { power: (f.plant || {}).power || {}, energy_today: (f.plant || {}).energy_today || {} }; }
    return op;
  }

  _eff(f) { return applyOps(this._draftWith(this._work), [this._flowOp(f)], this._info); }

  _cand(f) {
    if (!f.pick || !f.pick.cand) return null;
    const part = this._part(f.kind);
    return part ? this._cands(part).find((c) => c.key === f.pick.cand) || null : null;
  }

  /** Fill a part's empty inputs from the picked device (never over what the user chose). */
  _suggest(force) {
    const f = this._f;
    if (!f.kind || !this._part(f.kind)) return;
    const part = this._part(f.kind), cand = this._cand(f), facts = this._facts();
    wizardRoles(part, this._roles).forEach((r) => {
      const cur = f.inputs[r.key];
      const have = cur && (cur.entity || cur.value !== undefined);
      if (r.kind === "static" || (have && !(force || (cur.entity && f.auto[r.key] === cur.entity)))) return;
      const sg = wizardSuggest(r, cand, facts);
      if (sg.entity) { f.inputs[r.key] = { entity: sg.entity }; f.auto[r.key] = sg.entity; }
      else if (force && cur && cur.entity && f.auto[r.key] === cur.entity) delete f.inputs[r.key];
    });
    if (f.kind === "inverter") {
      f.plant = f.plant || { power: {}, energy_today: {} };
      WIZARD_PLANT.forEach(([which]) => {
        if ((f.plant[which] || {}).entity) return;
        const g = wizardPlantGuess(which, cand, facts, this._hass.states);
        if (g.entity) f.plant[which] = { entity: g.entity };
      });
    }
  }

  /** Why the user can't leave a step yet, in words, or "". */
  _blocked(step) {
    const f = this._f;
    if (step === 2) {
      if (f.kind === "plant" || f.kind === "device") return "";
      if (!f.pick || !f.pick.option) {
        const p = this._part(f.kind);
        return `Pick the ${p.title.toLowerCase()} to use, or choose its type by hand below.`;
      }
    }
    if (step === 3) {
      if (f.kind === "plant") {
        const p = f.plant;
        if (!(p.power && p.power.entity) || !(p.energy_today && p.energy_today.entity)) return "A solar plant needs both its power and its energy today.";
        return "";
      }
      if (f.kind === "device") {
        if (!f.device.adapter) return "Choose what kind of inverter or battery it is.";
        if (!Object.values(f.device.inputs || {}).some((s) => s && s.entity)) return "Choose at least one input.";
        return "";
      }
      const miss = wizardMissing(this._eff(f), this._info.parts, this._roles, [f.kind]);
      if (miss.length) return `${miss.length} required input${miss.length === 1 ? " is" : "s are"} still empty: ${miss.slice(0, 4).map((m) => m.role.label).join(", ")}${miss.length > 4 ? " and more" : ""}.`;
    }
    return "";
  }

  _go(step) {
    const f = this._f;
    if (step > f.step) {
      const why = this._blocked(f.step);
      if (why) { f.notice = why; this._renderShade(); return; }
    }
    f.notice = "";
    if (step === 3 && f.mode !== "edit") this._suggest(false);
    f.step = step;
    this._renderShade();
  }

  _viewFlow() {
    const f = this._f;
    const bar = el("div", { class: "steps" }, SYSTEM_STEPS.map((l, i) => el("span", { class: i + 1 === f.step ? "now" : i + 1 < f.step ? "done" : "" }, `${i + 1}. ${l}`)));
    const body = el("div", { class: "body" }, bar, [this._stepKind, this._stepFind, this._stepEntities, this._stepReview][f.step - 1].call(this));
    if (f.notice) body.append(el("div", { class: "banner warn" }, f.notice));
    const back = () => {
      if (f.step === 1 || (f.step === 2 && f.mode === "replace") || (f.step === 3 && f.mode === "edit")) this._toList();
      else if (f.step === 4 && f.mode === "edit") { f.step = 3; this._renderShade(); }
      else { f.notice = ""; f.step -= 1; this._renderShade(); }
    };
    const last = f.step === 4;
    const next = f.step === 1 ? null : el("button", { class: "primary", onclick: () => (last ? this._stage() : this._go(f.step + 1)) },
      last ? (f.mode === "add" ? "Add to list" : "Done") : "Next");
    const title = f.mode === "edit" ? "Edit" : f.mode === "replace" ? "Replace" : "Add to your system";
    return [el("header", {}, el("h2", {}, title), el("button", { onclick: () => this._toList() }, "Cancel")), body,
      el("footer", {}, el("button", { onclick: back }, "Back"), next || el("span", {}))];
  }

  _stepKind() {
    const f = this._f;
    const tiles = el("div", { class: "tiles" });
    systemKinds(this._info, this._devicesOn).forEach((k) => {
      const have = k.single && this._panelItems().some((i) => i.target === k.part && i.tag !== "removed");
      tiles.append(el("button", { class: "tile", onclick: () => this._chooseKind(k) }, el("strong", {}, k.title), el("small", {}, k.why),
        have ? el("small", { style: "color: var(--warning-color, #b26a00)" }, "You have one already: this replaces it") : null));
    });
    return el("div", { style: "display: flex; flex-direction: column; gap: 12px" }, el("div", {}, "What are you adding?"), tiles);
  }

  /** Cards for HA devices (a part's candidates, or energy devices for a plant or other device). */
  _stepFind() {
    const f = this._f;
    const kids = [];
    const facts = this._facts();
    if (f.kind === "plant" || f.kind === "device") return this._findDevice(f, facts);
    const part = this._part(f.kind);
    const cands = this._cands(part);
    const saved = this._baseDraft();
    const inUse = wizardInUse(part, cands, saved.inputs, saved.site);
    kids.push(el("div", {}, f.mode === "replace" ? `Replace your ${part.title.toLowerCase()} with:` : `Found in Home Assistant for ${part.title.toLowerCase()}:`));
    const search = el("input", { type: "search", class: "search", placeholder: "Search by name, brand or model", "aria-label": "Search", oninput: (ev) => {
      const q = ev.target.value;
      list.querySelectorAll(".cand").forEach((c) => { c.style.display = matchesSearch(c.dataset.text, q) ? "" : "none"; });
    } });
    const list = el("div", { style: "display: flex; flex-direction: column; gap: 8px" });
    if (cands.length > 6) kids.push(search);
    cands.forEach((c) => {
      const used = !!inUse && inUse.key === c.key && f.mode === "replace";
      const row = this._optionRowOf(f.kind, c.option);
      const card = el("div", { class: `cand${f.pick && f.pick.cand === c.key ? " sel" : ""}${used ? " used" : ""}`, role: "button", tabindex: used ? "-1" : "0", "data-text": c.label,
        onclick: () => { if (used) return; f.pick = { cand: c.key, option: c.option }; f.showAll = false; f.notice = ""; this._renderShade(); },
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); card.click(); } } },
        el("div", { class: "main" }, el("div", { class: "name" }, c.label), el("div", { class: "sub" }, c.device ? `${c.device.manufacturer} ${c.device.model}`.trim() : "Found by entity name")),
        row ? el("span", { class: `badge ${row.status === "verified" ? "ok" : "warn"}` }, row.status === "verified" ? "verified" : row.status) : null,
        inUse && inUse.key === c.key ? el("span", { class: "badge" }, "in use now") : null);
      list.append(card);
    });
    kids.push(list);
    if (!cands.length) kids.push(el("div", { class: "muted" }, "Nothing found in Home Assistant for this. Install and set up its integration first, or choose its type by hand below."));
    const opts = siteRows(this._info.options, f.kind).filter((r) => r.id !== "auto");
    if (opts.length) {
      const sel = el("select", { "aria-label": "Type, by hand", onchange: (ev) => { f.pick = ev.target.value ? { option: ev.target.value } : null; f.showAll = true; f.notice = ""; this._renderShade(); } },
        el("option", { value: "" }, "Choose its type by hand…"), opts.map((r) => el("option", { value: r.id }, siteOptionLabel(r))));
      sel.value = f.pick && !f.pick.cand ? f.pick.option : "";
      kids.push(el("div", { class: "ctl" }, sel));
    }
    if (f.pick && f.pick.option) {
      const row = this._optionRowOf(f.kind, f.pick.option);
      if (row && row.status !== "verified") kids.push(el("div", { class: "muted" }, "PowerEngine will only run Passive on this until it has been tested on real hardware."));
    }
    kids.push(el("div", { class: "muted" }, "Not in the list? ", el("button", { class: "link", onclick: () => { f.open = !f.open; this._renderShade(); } }, "Send us its entity list"), " so support can be added."));
    if (f.open) kids.push(this._exportBox(part, facts));
    return el("div", { style: "display: flex; flex-direction: column; gap: 10px" }, kids);
  }

  _optionRowOf(part, id) { return siteRow(this._info.options, part, id); }

  _usedEntities() { return wizardUsedEntities(this._draftWith(this._work)); }

  /** Devices to pick from for an extra solar plant or another device. */
  _findDevice(f, facts) {
    const kids = [el("div", {}, f.kind === "plant" ? "Which device is it? (Or skip this and choose the entities by hand in the next step.)" : "Which device is it? (Optional: it only narrows the entity lists in the next step.)")];
    const used = this._usedEntities();
    const owned = new Set();
    this._info.parts.forEach((p) => wizardCandidates(p, facts).forEach((c) => { if (c.device) owned.add(c.device.id); }));
    const found = wizardOthers(this._info.parts, facts, this._hass.states, used);
    const foundIds = new Set(found.map((x) => x.id));
    const rest = wizardEnergyDevices(facts, this._hass.states).filter((x) => !foundIds.has(x.id) && !owned.has(x.id) && !x.entities.some((id) => used.has(id)));
    const list = el("div", { style: "display: flex; flex-direction: column; gap: 8px" });
    const card = (d, hint) => {
      const c = el("div", { class: `cand${f.hadev === d.id ? " sel" : ""}`, role: "button", tabindex: "0", "data-text": wizardDeviceName(d),
        onclick: () => {
          f.hadev = d.id;
          if (f.kind === "plant") { const g = wizardPlantFromDevice(d, facts, this._hass.states); f.plant.name = f.plant.name || g.name; f.plant.power = g.power; f.plant.energy_today = g.energy_today; }
          else { f.device.name = f.device.name || d.name; f.device.firmware = f.device.firmware || d.sw_version || ""; }
          this._renderShade();
        }, onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); c.click(); } } },
        el("div", { class: "main" }, el("div", { class: "name" }, wizardDeviceName(d)), el("div", { class: "sub" }, hint)));
      return c;
    };
    found.forEach((d) => list.append(card(d, d.kind === "solar" ? "Looks like a solar source" : "Has a battery")));
    rest.forEach((d) => list.append(card(d, "Reports power and energy")));
    if (!found.length && !rest.length) list.append(el("div", { class: "muted" }, "No unused energy devices found in Home Assistant."));
    kids.push(list);
    if (f.kind === "device") {
      const adapters = siteRows(this._info.options, "inverter");
      const sel = el("select", { "aria-label": "Kind of inverter or battery", onchange: (ev) => { f.device.adapter = ev.target.value; this._renderShade(); } },
        adapters.map((r) => el("option", { value: r.id }, siteOptionLabel(r))));
      sel.value = f.device.adapter;
      kids.push(el("div", { class: "ctl" }, el("span", {}, "What kind is it?"), sel));
      kids.push(el("div", { class: "muted" }, "Not supported yet? ", el("button", { class: "link", onclick: () => { f.open = !f.open; this._renderShade(); } }, "Send us its entity list"), "."));
      if (f.open) kids.push(this._exportBox(this._part("inverter"), facts));
    }
    return el("div", { style: "display: flex; flex-direction: column; gap: 10px" }, kids);
  }

  /** The export for an unsupported device: a scrubbed entity list, downloaded or copied, and a link to a new issue. */
  _exportBox(part, facts) {
    const f = this._f;
    const box = el("div", { class: "exportbox", style: "display: flex; flex-direction: column; gap: 8px" });
    box.append(el("div", { class: "banner warn" }, "Send us a list of the device's entities and we can write support for it. The list holds entity names, units and current values only; long numbers (meter and account numbers, serials) are removed first, and you can read the file before you send it."));
    const cand = this._cand(f);
    const devs = wizardEnergyDevices(facts, this._hass.states);
    const sel = el("select", { "aria-label": "Device", onchange: (ev) => { f.exportDevice = ev.target.value; } },
      el("option", { value: "" }, "Choose the device…"), devs.map((d) => el("option", { value: d.id }, wizardDeviceName(d))));
    f.exportDevice = f.exportDevice || f.hadev || (cand && cand.device ? cand.device.id : "");
    sel.value = f.exportDevice || "";
    const note = el("textarea", { rows: 2, placeholder: "Anything we should know? (make, model, firmware: no personal details)" });
    note.value = f.note || "";
    note.addEventListener("input", () => { f.note = note.value; });
    const flash = (text) => { let n = box.querySelector(".flash"); if (!n) { n = el("div", { class: "banner ok flash" }); box.append(n); } n.textContent = text; };
    const go = (how) => {
      const did = sel.value;
      const looseIds = cand && !cand.device ? cand.entityIds : [];
      if (!did && !looseIds.length) { flash("Choose the device first."); return; }
      const data = buildCandidateExport({ hass: this._hass, facts, deviceIds: did ? [did] : [], looseIds, note: f.note, now: new Date(),
        appVersion: ((this._hass.states[VERSION_SENSOR] || {}).state) || null, chosen: { [f.kind]: f.pick && f.pick.option ? f.pick.option : "unlisted" } });
      const text = JSON.stringify(data, null, 1);
      if (how === "copy") {
        (navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(text) : Promise.reject(new Error("no clipboard")))
          .then(() => flash(`Copied ${data.entities.length} entities.`), () => flash("Could not copy: use Download."));
      } else {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
        a.download = candidateFileName(new Date());
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        flash(`Downloaded ${data.entities.length} entities from ${data.devices.length} device(s).`);
      }
    };
    box.append(el("div", { class: "ctl" }, sel), note, el("div", { class: "ctl" },
      el("button", { class: "primary", onclick: () => go("download") }, "Download candidate entities"), el("button", { onclick: () => go("copy") }, "Copy"),
      el("a", { href: WIZARD_ISSUES, target: "_blank", rel: "noopener" }, "Open a GitHub issue and attach it")));
    return box;
  }

  /** An entity picker narrowed to `ids` (all when empty), or a text box with a list when the picker isn't available. */
  _picker(value, domains, ids, onChange, label) {
    if (this._usePicker) {
      const pk = document.createElement("ha-entity-picker");
      pk.hass = this._hass; pk.value = value || ""; pk.includeDomains = domains; pk.allowCustomEntity = true;
      if (ids && ids.length) pk.includeEntities = ids;
      pk.label = label;
      pk.addEventListener("value-changed", (ev) => onChange(ev.detail.value || ""));
      this._pickers.push(pk);
      return pk;
    }
    const listId = `pe-s-${Math.random().toString(36).slice(2)}`;
    const all = (ids && ids.length ? ids : this._facts().ids).filter((id) => domains.includes(id.split(".")[0])).sort();
    return el("span", { style: "display: contents" }, el("input", { type: "text", value: value || "", list: listId, placeholder: label, onchange: (ev) => onChange(ev.target.value.trim()) }),
      el("datalist", { id: listId }, all.map((id) => el("option", { value: id }))));
  }

  /** Entities to offer: the picked device's own, unless "show all" is on or there is none. */
  _idsFor(f) {
    if (f.showAll) return [];
    if (f.hadev) { const d = this._facts().devices[f.hadev]; return d ? d.entities : []; }
    const c = this._cand(f);
    return c ? c.entityIds : [];
  }

  _stepEntities() {
    const f = this._f;
    if (f.kind === "plant") return this._entitiesPlant(f);
    if (f.kind === "device") return this._entitiesDevice(f);
    const part = this._part(f.kind);
    const cand = this._cand(f);
    const kids = [el("div", { class: "muted" }, "Each input is filled from your chosen device's own entities where it can be. Check the live value beside each one. Inputs with no match are left empty, never guessed.")];
    const showAll = el("input", { type: "checkbox", onchange: (ev) => { f.showAll = ev.target.checked; this._renderShade(); } });
    showAll.checked = !!f.showAll;
    kids.push(el("div", { class: "ctl" }, el("label", {}, showAll, " Show all entities (for something on another device, like a separate CT clamp)"),
      el("button", { onclick: () => { this._suggest(true); this._renderShade(); } }, "Use suggestions again")));
    const eff = this._eff(f);
    const pair = BATTERY_PAIR.every((k) => (eff.inputs[k] || {}).entity);
    const roles = wizardRoles(part, this._roles);
    const main = [], later = [];
    roles.forEach((r) => (roleNeed(r, eff, pair).level === "req" ? main : later).push(r));
    if (f.kind === "inverter") WIZARD_PLANT.forEach(([which, label, desc]) => main.push({ key: `plant_${which}`, plant: which, label, description: desc, kind: "plant", domains: ["sensor"], required: "yes" }));
    if (!roles.length && f.kind !== "inverter") kids.push(el("div", { class: "muted" }, "Nothing to map for this one: PowerEngine reads it through its own integration."));
    main.forEach((r) => kids.push(this._roleRow(r, f, cand, eff)));
    if (later.length) {
      const d = el("details", {}, el("summary", {}, `Optional and later (${later.length}): useful extras, and the controls PowerEngine needs to go live`));
      d.open = !!f.openLater;
      d.addEventListener("toggle", () => { f.openLater = d.open; });
      later.forEach((r) => d.append(this._roleRow(r, f, cand, eff)));
      kids.push(d);
    }
    if (f.kind === "inverter") {
      kids.push(this._firmwareBox(f));
      kids.push(this._balanceBox(f));
    }
    return el("div", { style: "display: flex; flex-direction: column; gap: 6px" }, kids);
  }

  _firmwareBox(f) {
    const id = (f.pick || {}).option;
    const list = siteFirmwareOptions(this._info.options, id);
    if (!list.length) return el("span", {});
    const cur = f.firmware === undefined ? (this._draftWith(this._work).site.inverter_firmware || null) : f.firmware;
    f.firmware = cur;
    const sel = el("select", { "aria-label": "Firmware", onchange: (ev) => { f.firmware = ev.target.value || null; } },
      list.map((x) => el("option", { value: x }, x)), el("option", { value: "" }, SITE_UNKNOWN_FW));
    sel.value = cur || "";
    return el("div", { class: "ctl" }, el("span", {}, "Firmware"), sel, el("span", { class: "muted" }, siteDetectedLine(this._info.detected, cur)));
  }

  /** One input: an entity picker (or a number for a static one), its live value, and for a signed power its sign check. */
  _roleRow(role, f, cand, eff) {
    const getSpec = () => (role.plant ? (f.plant || {})[role.plant] : f.inputs[role.key]);
    const setSpec = (v) => {
      if (role.plant) { f.plant = f.plant || { power: {}, energy_today: {} }; f.plant[role.plant] = v ? { entity: v.entity } : {}; }
      else if (v) f.inputs[role.key] = v; else delete f.inputs[role.key];
      this._tickFlow();
    };
    const row = el("div", { class: "row" });
    const pair = BATTERY_PAIR.every((k) => (eff.inputs[k] || {}).entity);
    const need = role.plant ? { level: "req", badge: "Required" } : roleNeed(role, eff, pair);
    row.append(el("div", { class: "head" }, el("span", { class: "label" }, role.label), el("span", { class: `badge ${need.level === "req" ? "req" : ""}` }, need.badge)));
    row.append(el("div", { class: "desc" }, role.description));
    const ctl = el("div", { class: "ctl" });
    const cur = getSpec() || {};
    if (role.kind === "static") {
      ctl.append(el("input", { type: "number", step: "any", value: cur.value !== undefined ? cur.value : "", onchange: (ev) => setSpec(ev.target.value === "" ? null : { value: ev.target.value }) }),
        el("span", { class: "muted" }, role.static_unit || ""));
    } else {
      const ids = this._idsFor(f).filter((id) => (role.domains || ["sensor"]).includes(id.split(".")[0]));
      ctl.append(this._picker(cur.entity, role.domains || ["sensor"], ids, (v) => { setSpec(v ? Object.assign({}, role.plant ? {} : { invert: cur.invert }, { entity: v }) : null); f.auto[role.key] = ""; }, role.label));
      if (role.signed && !role.plant) {
        const cb = el("input", { type: "checkbox", onchange: (ev) => { const s = f.inputs[role.key]; if (!s) return; if (ev.target.checked) s.invert = true; else delete s.invert; this._tickFlow(); } });
        cb.checked = !!cur.invert;
        ctl.append(el("label", {}, cb, " Invert"));
      }
    }
    row.append(ctl);
    const live = el("div", { class: "live" }), problem = el("div", { class: "problem" });
    row.append(live, problem);
    if (role.signed && !role.plant && role.sign_note) row.append(this._signCheck(role, f));
    this._flowLive.push(() => {
      const s = getSpec() || {};
      const st = s.entity ? this._hass.states[s.entity] : null;
      const rr = role.plant ? { text: st ? `${st.state} ${((st.attributes || {}).unit_of_measurement) || ""}`.trim() : "", readsAs: "" } : readout(role, s, st, undefined, inUseFor(this._hass, role.key));
      live.textContent = rr.text ? `${nowText(rr)}${rr.readsAs ? ` (${rr.readsAs})` : ""}` : "";
      const spec = role.plant ? { entity: s.entity } : s;
      const rl = role.plant ? { key: role.key, kind: role.plant === "power" ? "power" : "energy", domains: ["sensor"], required: "yes" } : role;
      const p = (s.entity || s.value !== undefined) ? instantProblem(rl, spec, st) : "";
      problem.textContent = p;
      row.classList.toggle("bad", !!p);
    });
    return row;
  }

  /** "Right now my battery is…": the user says what the signed input is doing and the card says if the sign matches. */
  _signCheck(role, f) {
    const words = parseSignNote(role.sign_note);
    const wrap = el("div", { class: "ctl" });
    const result = el("span", {});
    const sel = el("select", { "aria-label": `What ${role.label} is doing now`, onchange: (ev) => { f.says[role.key] = ev.target.value; this._tickFlow(); } },
      el("option", { value: "" }, "Check the sign…"), el("option", { value: "pos" }, `Right now it is ${words.pos}`), el("option", { value: "neg" }, `Right now it is ${words.neg}`), el("option", { value: "idle" }, "Neither / not sure"));
    sel.value = f.says[role.key] || "";
    wrap.append(sel, result);
    this._flowLive.push(() => {
      const s = f.inputs[role.key] || {};
      const w = wizardWatts(this._hass.states[s.entity]);
      const v = wizardSignCheck(f.says[role.key], w, !!s.invert);
      result.className = "muted";
      result.textContent = !f.says[role.key] ? "" : v === "ok" ? "✓ That matches what PowerEngine expects."
        : v === "flip" ? "That reads the wrong way round: tick Invert." : v === "unknown" ? "Too close to zero to tell: try again when it is charging or discharging hard." : "";
    });
    return wrap;
  }

  _balanceBox(f) {
    const box = el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, "Does it add up?")),
      el("div", { class: "desc" }, "House load should be about grid + solar + battery output."));
    const text = el("div", {});
    box.append(text);
    this._flowLive.push(() => {
      const v = (k) => { const s = f.inputs[k]; if (!s || !s.entity) return null; const w = wizardWatts(this._hass.states[s.entity]); return w === null ? null : (s.invert ? -w : w); };
      const pe = f.plant && f.plant.power && f.plant.power.entity ? wizardWatts(this._hass.states[f.plant.power.entity]) : null;
      const r = wizardBalance({ battery: v("battery_power"), solar: pe, grid: v("grid_power"), house: v("house_load_power") });
      text.className = r ? `banner ${r.ok ? "ok" : "warn"}` : "muted";
      text.textContent = r ? (r.ok ? `✓ Close enough (house ${Math.round(v("house_load_power"))} W against ${r.expected} W from the others).`
        : `Off by ${Math.abs(r.diff)} W (house ${Math.round(v("house_load_power"))} W against ${r.expected} W). Check the signs and units above. A car charging, or a moment of change, can also cause a gap.`)
        : "Needs battery power, solar power, grid power and house load all mapped and reading.";
    });
    return box;
  }

  _entitiesPlant(f) {
    const p = f.plant;
    const kids = [];
    const ids = this._idsFor(f);
    kids.push(el("div", { class: "ctl" }, el("input", { type: "text", value: p.name || "", placeholder: "Name", "aria-label": "Name", onchange: (ev) => { p.name = ev.target.value; } }),
      (() => {
        const fc = el("select", { "aria-label": "Forecast", onchange: (ev) => { p.forecast = ev.target.value; } },
          el("option", { value: "none" }, "Forecast: none (actuals only)"), el("option", { value: "solcast_site" }, "Forecast: from the forecast service"), el("option", { value: "scaled" }, "Forecast: scaled from the main plant"));
        fc.value = p.forecast || "none";
        return fc;
      })()));
    const showAll = el("input", { type: "checkbox", onchange: (ev) => { f.showAll = ev.target.checked; this._renderShade(); } });
    showAll.checked = !!f.showAll || !ids.length;
    if (ids.length || f.showAll) kids.push(el("div", { class: "ctl" }, el("label", {}, showAll, " Show all entities")));
    [["power", "Solar power (W)", "Live solar power"], ["energy_today", "Solar energy today (kWh)", "Solar energy today"]].forEach(([k, label, desc]) => {
      const row = el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, desc), el("span", { class: "badge req" }, "Required")));
      row.append(el("div", { class: "ctl" }, this._picker((p[k] || {}).entity, ["sensor"], ids, (v) => { p[k] = v ? { entity: v } : {}; this._tickFlow(); }, label)));
      const live = el("div", { class: "live" });
      row.append(live);
      this._flowLive.push(() => { const s = (p[k] || {}).entity ? this._hass.states[p[k].entity] : null; live.textContent = s ? `Now: ${s.state} ${((s.attributes || {}).unit_of_measurement) || ""}`.trim() : ""; });
      kids.push(row);
    });
    kids.push(el("div", { class: "muted" }, SYSTEM_PLANT_NOTE));
    return el("div", { style: "display: flex; flex-direction: column; gap: 6px" }, kids);
  }

  _entitiesDevice(f) {
    const d = f.device;
    const kids = [];
    const ids = this._idsFor(f);
    const adapters = siteRows(this._info.options, "inverter");
    kids.push(el("div", { class: "ctl" }, el("input", { type: "text", value: d.name || "", placeholder: "Name", "aria-label": "Name", onchange: (ev) => { d.name = ev.target.value; } }),
      el("input", { type: "text", value: d.firmware || "", placeholder: "Firmware (optional)", "aria-label": "Firmware", onchange: (ev) => { d.firmware = ev.target.value.trim(); } })));
    if (!adapters.find((r) => r.id === d.adapter)) kids.push(el("div", { class: "banner warn" }, "Choose the kind of device in the previous step."));
    if (f.hadev || f.showAll) {
      const showAll = el("input", { type: "checkbox", onchange: (ev) => { f.showAll = ev.target.checked; this._renderShade(); } });
      showAll.checked = !!f.showAll;
      kids.push(el("div", { class: "ctl" }, el("label", {}, showAll, " Show all entities")));
    }
    DEVICE_INPUTS.forEach(([key, label, invertible]) => {
      d.inputs[key] = d.inputs[key] || {};
      const spec = d.inputs[key];
      const row = el("div", { class: "row" }, el("div", { class: "head" }, el("span", { class: "label" }, label)));
      const ctl = el("div", { class: "ctl" }, this._picker(spec.entity, ["sensor"], ids, (v) => { spec.entity = v; if (!v) delete spec.invert; this._tickFlow(); }, label));
      if (invertible) {
        const cb = el("input", { type: "checkbox", onchange: (ev) => { spec.invert = ev.target.checked; } });
        cb.checked = !!spec.invert;
        ctl.append(el("label", {}, cb, " Invert"));
      }
      row.append(ctl);
      const live = el("div", { class: "live" });
      row.append(live);
      this._flowLive.push(() => { const s = spec.entity ? this._hass.states[spec.entity] : null; live.textContent = s ? `Now: ${s.state} ${((s.attributes || {}).unit_of_measurement) || ""}`.trim() : ""; });
      kids.push(row);
    });
    kids.push(el("div", { class: "muted" }, "Read only: PowerEngine measures it and counts its solar, but only the inverter in Your system is controlled."));
    return el("div", { style: "display: flex; flex-direction: column; gap: 6px" }, kids);
  }

  _stepReview() {
    const f = this._f;
    const kids = [];
    const kind = this._kindInfo(f.kind);
    const lines = [];
    if (f.kind === "plant") {
      kids.push(el("div", {}, el("strong", {}, f.plant.name || "New solar plant")));
      lines.push("It is read only: its solar is counted in the totals and PowerEngine never controls it.");
    } else if (f.kind === "device") {
      kids.push(el("div", {}, el("strong", {}, f.device.name || "New device")));
      lines.push("It starts read only: its readings are shown and counted. PowerEngine does not control it.");
    } else {
      const part = this._part(f.kind);
      const row = this._optionRowOf(f.kind, (f.pick || {}).option);
      const n = Object.values(f.inputs).filter((s) => s && (s.entity || s.value !== undefined)).length;
      kids.push(el("div", {}, el("strong", {}, `${part.title}: ${row ? row.name : (f.pick || {}).option || ""}`), el("div", { class: "muted" }, `${n} input${n === 1 ? "" : "s"} mapped.`)));
      if (kind.required) lines.push(f.kind === "inverter" && f.mode !== "add" ? SITE_WARNING : "This is a required part.");
      if (f.kind === "inverter" && f.mode === "add") lines.push("PowerEngine starts in Passive: it watches and plans, and writes nothing to your inverter until you set it to Active.");
      if (f.kind === "ev_charger") lines.push("Smart-charge slot requests and planning around the car's charging turn on.");
      if (row && row.status !== "verified") lines.push("This hardware is not tested by the project, so Active is refused for it.");
    }
    if (lines.length) kids.push(el("div", { class: "impact" }, lines.map((l) => el("div", {}, T(l)))));
    kids.push(el("div", { class: "muted" }, "This goes into your draft. Nothing is live until you choose Apply to System."));
    return el("div", { style: "display: flex; flex-direction: column; gap: 10px" }, kids);
  }

  /** The flow is done: its change goes into the working list, and the panel returns to it. */
  _stage() {
    const f = this._f;
    const why = this._blocked(3);
    if (why) { f.notice = why; f.step = 3; this._renderShade(); return; }
    if (f.mode === "edit") {                               // nothing changed: leave the list as it was
      const same = JSON.stringify(buildConfig(this._eff(f))) === JSON.stringify(buildConfig(this._draftWith(this._work)));
      if (same) { this._toList(); return; }
    }
    this._work = opsSet(this._work, this._flowOp(f));
    this._toList();
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-system-card")) {
  customElements.define("powerengine-system-card", PowerEngineSystemCard);
  // the old name, for a dashboard written before this card existed (the app rewrites the dashboard at its next start)
  customElements.define("powerengine-wizard-card", class extends PowerEngineSystemCard {});
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "powerengine-system-card",
    name: "PowerEngine your system",
    description: "Lists your equipment and lets you add, change, replace and remove it, with a draft you can apply to the system when ready.",
  });
}

// --- Plan history day picker -------------------------------------------------------------------------------------
// Picks the day the Plan history tab shows: a date box with previous / next arrows either side. It fires the event
// pe_history_day {date: "YYYY-MM-DD"} (admins only: HA's fire_event needs admin). The app answers by showing that day
// on the tab. It hides itself when the app doesn't publish `earliest` / `latest` on the plan history sensor.
const HISTORY_DAY_EVENT = "pe_history_day";
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The fire_event call for a picked day, or null if it isn't a date inside [earliest, latest] (all YYYY-MM-DD). */
function historyDayPayload(value, earliest, latest, eventType) {
  if (typeof value !== "string" || !ISO_DAY.test(value)) return null;
  if (ISO_DAY.test(earliest || "") && value < earliest) return null;
  if (ISO_DAY.test(latest || "") && value > latest) return null;
  return { type: "fire_event", event_type: eventType || HISTORY_DAY_EVENT, event_data: { date: value } };
}

/** The day `delta` days from `day` (YYYY-MM-DD), or null if that is outside [earliest, latest] or `day` isn't a date. */
function shiftHistoryDay(day, delta, earliest, latest) {
  if (typeof day !== "string" || !ISO_DAY.test(day)) return null;
  const d = new Date(`${day}T12:00:00Z`);                 // noon UTC: adding days never lands on a DST edge
  if (Number.isNaN(d.getTime())) return null;
  d.setUTCDate(d.getUTCDate() + delta);
  const out = d.toISOString().slice(0, 10);
  return historyDayPayload(out, earliest, latest) ? out : null;
}

class PowerEngineHistoryDateCard extends (typeof HTMLElement !== "undefined" ? HTMLElement : class {}) {
  setConfig(config) {
    this._config = { entity: "sensor.pe_plan_history", ...(config || {}) };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._built = false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 1; }

  getGridOptions() { return { columns: "full", rows: 1, min_rows: 1 }; }

  _admin() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }

  _attrs() {
    const st = this._hass && this._config && this._hass.states[this._config.entity];
    return (st && st.attributes) || {};
  }

  /** The day shown: one just asked for (until the app confirms it) so quick clicks add up, else the app's. */
  _day() {
    const a = this._attrs();
    if (this._pending && Date.now() - this._pending.at < 8000 && a.date !== this._pending.day) return this._pending.day;
    this._pending = null;
    return a.date;
  }

  async _pick(value) {
    const a = this._attrs();
    const payload = historyDayPayload(value, a.earliest, a.latest);
    const note = this.shadowRoot.querySelector(".note");
    if (!payload) { note.textContent = "That day isn't available."; return; }
    this._pending = { day: value, at: Date.now() };
    this._render();
    try {
      await this._hass.callWS(payload);
      note.textContent = "";
    } catch (err) {
      this._pending = null;
      note.textContent = `Couldn't change the day (${err && err.message ? err.message : err}).`;
      this._render();
    }
  }

  _step(delta) {
    const a = this._attrs();
    const next = shiftHistoryDay(this._day(), delta, a.earliest, a.latest);
    if (next) this._pick(next);
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const a = this._attrs();
    const usable = !!(a.earliest && a.latest);
    if (!this._built) {
      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          .row { display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap;
                 color: var(--primary-text-color); }
          input, button { font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
                          border: 1px solid var(--divider-color); border-radius: 6px; }
          input { padding: 6px 8px; }
          button { min-width: 40px; padding: 6px 10px; font-size: 1.2em; line-height: 1; cursor: pointer; }
          button:disabled, input:disabled { opacity: .45; cursor: default; }
          .note { color: var(--secondary-text-color); font-size: .9em; }
        </style>
        <div class="row">
          <button class="prev" type="button" aria-label="Previous day">&#8249;</button>
          <input type="date" aria-label="Day">
          <button class="next" type="button" aria-label="Next day">&#8250;</button>
          <span class="note"></span>
        </div>`;
      const root = this.shadowRoot;
      root.querySelector("input").addEventListener("change", (ev) => this._pick(ev.target.value));
      root.querySelector(".prev").addEventListener("click", () => this._step(-1));
      root.querySelector(".next").addEventListener("click", () => this._step(1));
      this._built = true;
    }
    this.style.display = usable ? "block" : "none";
    const root = this.shadowRoot;
    const input = root.querySelector("input");
    const day = this._day();
    input.min = a.earliest || "";
    input.max = a.latest || "";
    if (day && root.activeElement !== input) input.value = day;
    const admin = this._admin();
    const why = admin ? "" : "Only an admin user can change the day here";
    input.disabled = !admin;
    input.title = why || "Show this day's plan and what happened";
    root.querySelector(".prev").disabled = !admin || !shiftHistoryDay(day, -1, a.earliest, a.latest);
    root.querySelector(".next").disabled = !admin || !shiftHistoryDay(day, 1, a.earliest, a.latest);
    root.querySelector(".prev").title = why || "Previous day";
    root.querySelector(".next").title = why || "Next day";
  }
}

if (typeof customElements !== "undefined" && !customElements.get("powerengine-history-date-card")) {
  customElements.define("powerengine-history-date-card", PowerEngineHistoryDateCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "powerengine-history-date-card", name: "PowerEngine history day",
    description: "Pick a day for the Plan history tab, with previous and next arrows." });
}

// --- Engine v2: Monitoring panel, plan (timeline and value map), health, and the config helpers ----------------------
// Engine v2 acts on conditions (a level reached, a price change) instead of a half-hour plan. The app publishes its state
// as sensor.pe_state_engine ("v1" or "v2") and sensor.pe_v2_* / sensor.pe_diag_v2* (docs/plans/engine-v2-build.md,
// "Published sensors"). Every piece here copes with a missing sensor by saying so in one plain line. Pure helpers first
// (exported for tests/engine_v2.test.cjs), then the cards, which draw SVG themselves.
const ENGINE_SENSOR = "sensor.pe_state_engine";
const V2_MODE = "sensor.pe_v2_mode";
const V2_VALUE = "sensor.pe_v2_value";
const V2_TIMELINE = "sensor.pe_v2_timeline";
const V2_CURVE = "sensor.pe_v2_value_curve";
const V2_RECENT = "sensor.pe_v2_recent";            // the last 18 hours as run (newer apps); the plan chart's scrollback
const V2_TRIGGERS = "sensor.pe_v2_triggers";
const V2_DIAG = "sensor.pe_diag_v2";
const V2_SETTINGS = "sensor.pe_diag_v2_settings";
const V2_PROFILES = "sensor.pe_diag_v2_profiles";      // the settings profiles (0.9.137+); the catalogue above is near HA's size limit
const V2_NOT_RUNNING = "Engine v2 is not running (engine v1 is).";
const V2_NO_DATA = "Engine v2 has not published anything yet.";
const V2_PREVIEW_LINE = "Preview: engine v1 is in control. Nothing is sent.";

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

function v2Attrs(states, id) {
  const s = (states || {})[id];
  if (!s || s.state === "unavailable" || s.state === "unknown") return null;
  return s.attributes && typeof s.attributes === "object" ? s.attributes : {};
}
function v2Clamp(x, lo, hi) { return Math.min(hi, Math.max(lo, x)); }
function v2Ms(iso) { const t = Date.parse(iso); return Number.isNaN(t) ? null : t; }
function v2Hm(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function v2P(n, digits) { const v = toNumber(n); return v === null ? "" : `${(Math.round(v * Math.pow(10, digits === undefined ? 2 : digits)) / Math.pow(10, digits === undefined ? 2 : digits))}p`; }
function v2Pct(n) { const v = toNumber(n); return v === null ? "" : `${Math.round(v * 10) / 10}%`; }
function lowerFirst(s) { const t = String(s || ""); return t.charAt(0).toLowerCase() + t.slice(1); }

/** Which engine runs: "v1", "v2", or null when the app doesn't say. */
function v2Engine(states) {
  const s = (states || {})[ENGINE_SENSOR];
  return s && (s.state === "v1" || s.state === "v2") ? s.state : null;
}
/** Is engine v2 only previewing (engine v1 is in control and the app publishes v2's working anyway)? The app says so on
 *  sensor.pe_state_engine (v2_preview) and on sensor.pe_v2_mode (preview); the engine sensor wins when it exists, so a
 *  preview that was switched off does not linger. */
function v2Preview(states) {
  const e = (states || {})[ENGINE_SENSOR];
  const m = v2Attrs(states, V2_MODE);
  if (e && (e.state === "v1" || e.state === "v2")) {
    return e.state === "v1" && !!(e.attributes && e.attributes.v2_preview === true) && !!m;
  }
  return !!(m && m.preview === true);
}
/** The mode's name as a "would" while only previewing: "Charging from the grid" becomes "Would be charging from the grid". */
function previewLabel(label, preview) { return preview ? `Would be ${lowerFirst(label)}` : label; }
function previewBanner(preview) { return preview ? `<p class="preview-line" role="note">${escHtml(V2_PREVIEW_LINE)}</p>` : ""; }
/** Does the app publish the v2 settings catalogue (so the v2 keys may be sent and shown)? */
/** The settings catalogue with the profiles sensor's attributes as `profiles` (and `profile`, the one in use), when the app has them. */
function v2WithProfiles(states) {
  const cat = states[V2_SETTINGS].attributes, p = v2Attrs(states, V2_PROFILES);
  return p && Array.isArray(p.builtin) ? Object.assign({}, cat, { profile: p.profile, profiles: p }) : cat;
}
function v2Supported(states) {
  const a = v2Attrs(states, V2_SETTINGS);
  return !!(a && Array.isArray(a.settings) && a.settings.length);
}
/** What a v2 card says instead of its content: null when it can show content. */
function v2Gate(states, needIds) {
  if (v2Engine(states) === "v1" && !v2Preview(states)) return V2_NOT_RUNNING;
  const missing = (needIds || []).every((id) => !v2Attrs(states, id) && !((states || {})[id]));
  return missing ? V2_NO_DATA : null;
}

/** Positions for the value bar: the marker (value of a stored kWh at the level now) and the two real-price lines on one
 *  0 to `max` scale. Pure; the card turns the numbers into SVG. */
function valueBarGeometry(v, opts) {
  const o = opts || {};
  const width = o.width || 384, x0 = o.x0 === undefined ? 8 : o.x0;
  const max = toNumber((v || {}).scale_max_p) > 0 ? toNumber(v.scale_max_p) : (o.max || 40);
  const x = (p) => x0 + (v2Clamp(p, 0, max) / max) * width;
  const val = toNumber((v || {}).value_p), buy = toNumber((v || {}).buy_line_p), sell = toNumber((v || {}).sell_line_p);
  const anchor = (px) => (px > x0 + width * 0.7 ? "end" : "start");
  const step = max <= 60 ? 10 : max <= 150 ? 25 : 50;
  const ticks = [];
  for (let p = 0; p <= max + 1e-9; p += step) ticks.push({ p, x: x(p) });
  return {
    max, x0, x1: x0 + width, ticks,
    marker: val === null ? null : { x: x(val), p: val, off: val > max },
    buy: buy === null ? null : { x: x(buy), p: buy, anchor: anchor(x(buy)) },
    sell: sell === null ? null : { x: x(sell), p: sell, anchor: anchor(x(sell)) },
    sellFill: sell === null ? null : { x: x0, w: x(sell) - x0 },
    buyFill: buy === null ? null : { x: x(buy), w: x0 + width - x(buy) },
    markerAnchor: val === null ? "middle" : (x(val) < x0 + 50 ? "start" : x(val) > x0 + width - 50 ? "end" : "middle"),
  };
}

/** "until the battery reaches 88% · expected about 02:40 · since 23:30" */
function modeSubtitle(m) {
  const exits = Array.isArray(m.exits) ? m.exits : [];
  const first = exits.find((e) => e && e.first) || exits[0];
  const parts = [];
  if (first && first.text) {
    let t = `until ${lowerFirst(first.text.replace(/^The /, "the "))}`;
    if (first.expected_at && v2Hm(first.expected_at)) t += ` · expected about ${v2Hm(first.expected_at)}`;
    parts.push(t);
  }
  if (m.since && v2Hm(m.since)) parts.push(`since ${v2Hm(m.since)}`);
  return parts.join(" · ");
}

/** The rows of "This ends when": text, the time it is expected (or "any time"), the swatch colour variable. */
function exitRows(exits, modeKey) {
  return (Array.isArray(exits) ? exits : []).filter((e) => e && e.text).map((e) => {
    const t = e.expected_at ? v2Hm(e.expected_at) : "";
    const when = !t ? "any time" : e.kind === "level" ? `about ${t}` : t;
    const colour = e.kind === "level" ? v2Mode(modeKey).colour : e.kind === "price" || e.kind === "time" ? "--v2-price"
      : e.kind === "deadline" ? "--divider-color" : "--m-hold";
    return { text: e.text, when, first: !!e.first, colour };
  });
}

/** Everything the Monitoring panel shows, from the states. kind: "v1" | "none" | "ok". */
function engineCardView(states) {
  const gate = v2Gate(states, [V2_MODE, V2_VALUE]);
  if (gate) return { kind: v2Engine(states) === "v1" ? "v1" : "none", text: gate };
  const m = v2Attrs(states, V2_MODE);
  if (!m) return { kind: "none", text: V2_NO_DATA };
  const key = (states[V2_MODE] || {}).state;
  const mode = v2Mode(key);
  const val = v2Attrs(states, V2_VALUE);
  const power = toNumber(m.power_w);
  const figs = [
    { k: "Battery (reading)", v: toNumber(m.level_reported) === null ? "–" : v2Pct(m.level_reported) },
    { k: "Battery (filtered)", v: toNumber(m.level_filtered) === null ? "–" : v2Pct(m.level_filtered) },
    { k: key === "charge" || key === "free" ? "Charging at" : key === "export" || key === "event" ? "Exporting at" : "Power",
      v: power === null || power === 0 ? "–" : `${Math.round(Math.abs(power) / 100) / 10} kW` },
    { k: "Values worked out", v: m.values_at && v2Hm(m.values_at) ? v2Hm(m.values_at) : "–" },
  ];
  const preview = v2Preview(states);
  const notes = [];
  if (m.sending === false && !preview) notes.push(m.not_sending_reason ? `Not sending to the inverter: ${m.not_sending_reason}` : "Not sending to the inverter.");
  if (m.deadline && v2Hm(m.deadline)) notes.push(`Deadline to look again: ${v2Hm(m.deadline)}`);
  if (m.values_because) notes.push(`Values last worked out because: ${lowerFirst(m.values_because)}`);
  const sv = (states[V2_VALUE] || {}).state;
  const bar = val ? valueBarGeometry(Object.assign({}, val, { value_p: toNumber(val.value_p) !== null ? val.value_p : sv })) : null;
  const barText = bar && bar.marker ? valueBarSummary(bar) : "";
  return {
    kind: "ok", key, mode, preview, label: previewLabel(m.label || mode.name, preview), subtitle: modeSubtitle(m), why: m.why || "", figs, notes, bar, barText,
    exits: exitRows(m.exits, key), val: val || {},
    offScale: !!(bar && bar.marker && bar.marker.off),
  };
}
function valueBarSummary(g) {
  const bits = [`A stored kWh is worth ${v2P(g.marker.p, 1)}`];
  if (g.buy) bits.push(g.marker.p > g.buy.p ? `above the ${v2P(g.buy.p, 1)} buy line, so charging pays` : `below the ${v2P(g.buy.p, 1)} buy line, so charging does not pay`);
  if (g.sell) bits.push(g.marker.p < g.sell.p ? `below the ${v2P(g.sell.p, 1)} sell line, so selling pays` : `above the ${v2P(g.sell.p, 1)} sell line, so selling does not pay`);
  return bits.join("; ");
}

// ---- plan: timeline layout ----------------------------------------------------------------------------------------
const HOUR_MS = 3600000;
const HISTORY_HOURS = 18;          // how far back the plan chart scrolls
const VIEW_PAST_HOURS = 1;         // how much of that is in view before the person scrolls (now sits near the left edge)
const FORECAST_HOURS = 48 - HISTORY_HOURS;   // how far ahead the plan chart reaches (its layout is 48 hours, HISTORY_HOURS of them already run); the history chart reaches as far
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
      exportP: toNumber(x.export_p), valueP: toNumber(x.value_p), buyP: toNumber(x.buy_p), sellP: toNumber(x.sell_p), sent: x.sent !== false, preview: x.preview === true, slot: x.slot === true, free: x.free === true }))
    .filter((x) => x.b > from && x.a < nowMs).sort((p, q) => p.a - q.a);
}

/** The `runs` an app publishes (sensor.pe_v2_history, sensor.pe_v2_recent): the day as it ran, one row [at, mode, level at the start, 1 if only
 *  previewed] per mode change. As {a, b (ms), mode, level (at a), preview}, each ending where the next begins and the last at `untilMs`
 *  (null: leave the last one out, its end is not known). Empty when the app publishes none (an older app): the half-hours are then all there is. */
function runRows(runs, untilMs) {
  const rows = (Array.isArray(runs) ? runs : []).filter((r) => Array.isArray(r) && v2Ms(r[0]) !== null)
    .map((r) => ({ a: v2Ms(r[0]), mode: r[1] || "none", level: toNumber(r[2]), preview: r[3] === 1 })).sort((p, q) => p.a - q.a);
  rows.forEach((r, i) => { r.b = i + 1 < rows.length ? rows[i + 1].a : untilMs; });
  return rows.filter((r) => r.b !== null && r.b !== undefined && r.b > r.a);
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
  // the hours already run as they ran (`runs`: each change where it happened), up to the last whole half-hour; the half-hours are the fallback
  const runs = histStop !== null && o.recent ? runRows(o.recent.runs, histStop).filter((r) => r.b > hist[0].a) : [];
  if (runs.length) {
    runs.forEach((r, i) => {
      const a = h(r.a), b = Math.min(h(r.b), h(histStop));
      if (b <= a) return;
      const next = runs[i + 1];
      histBands.push({ a: Math.max(0, a), b, mode: r.mode, until: "", reason: "", levelStart: a < 0 ? null : r.level, levelEnd: next ? next.level : hist[hist.length - 1].level,
        state: "past", startMs: r.a, endMs: Math.min(r.b, histStop), ran: true, preview: r.preview });
    });
  } else hist.forEach((r) => {
    const last = histBands[histBands.length - 1];
    const a = h(r.a), b = h(r.b);
    if (last && last.mode === r.mode && last.preview === !r.sent && Math.abs(last.b - a) < 1e-6) { last.b = b; last.endMs = r.b; last.levelEnd = r.level; return; }
    histBands.push({ a: Math.max(0, a), b, mode: r.mode, until: "", reason: "", levelStart: null, levelEnd: r.level, state: "past", startMs: r.a, endMs: r.b,
      ran: true, preview: !r.sent });
  });
  const bands = histBands.concat(planBands);
  const histSteps = hist.filter((r) => r.importP !== null).map((r) => ({ a: Math.max(0, h(r.a)), b: h(r.b), importP: r.importP, exportP: r.exportP,
    slot: r.slot === true, slotProb: null, event: false, free: r.free === true, estimated: false, ran: true }));
  const planSteps = prices.map((x) => {
    const c = clip(x);
    return Object.assign(c, { importP: toNumber(x.import_p), exportP: toNumber(x.export_p), slot: x.slot_prob !== null && x.slot_prob !== undefined,
      slotProb: toNumber(x.slot_prob), standardP: toNumber(x.standard_p), event: !!x.event, free: !!x.free, estimated: !!x.estimated });
  }).filter((p) => p.b > p.a && p.importP !== null);
  const steps = histSteps.concat(planSteps);
  const pts = (arr) => (Array.isArray(arr) ? arr : []).map((v, i) => ({ h: h(v2Ms(path.start) + i * stepMs), level: toNumber(v) }))
    .filter((p) => p.level !== null && p.h >= lo - 1e-9 && p.h <= spanH + 1e-9);
  const histLevel = hist.filter((r) => r.level !== null).map((r) => ({ h: h(r.b), level: r.level }))
    .concat(runs.filter((r) => r.level !== null && h(r.a) >= 0 && r.a < histStop).map((r) => ({ h: h(r.a), level: r.level }))).sort((p, q) => p.h - q.h);
  const pathValue = path && Array.isArray(path.value) ? path.value.map((v, i) => ({ h: h(v2Ms(path.start) + i * stepMs), value: toNumber(v) }))
    .filter((p) => p.value !== null && p.h >= lo - 1e-9 && p.h <= spanH + 1e-9) : [];
  const mid = histLevel.concat(path ? pts(path.mid) : []), low = path ? pts(path.low) : [], high = path ? pts(path.high) : [];
  const sun = sunLayout(a.sun, h, spanH);
  const ticks = [];
  const d = new Date(t0); d.setMinutes(0, 0, 0);
  while (d.getTime() < t0 || d.getHours() % 3 !== 0) d.setTime(d.getTime() + HOUR_MS);
  for (let t = d.getTime(); t <= t1; t += 3 * HOUR_MS) ticks.push({ h: h(t), ms: t });
  const nowH = h(now);
  const viewH = hist.length ? Math.max(0, nowH - (o.viewPastHours || VIEW_PAST_HOURS)) : 0;
  const histLines = hist.filter((r) => r.buyP !== null || r.sellP !== null).map((r) => ({ a: Math.max(0, h(r.a)), b: h(r.b), buy: r.buyP, sell: r.sellP }));
  const replans = (o.recent && Array.isArray(o.recent.replans) ? o.recent.replans : []).map(v2Ms).filter((m) => m !== null && m >= t0 && m <= t1).map(h);
  const histValue = hist.filter((r) => r.valueP !== null).map((r) => ({ h: h(r.b), value: r.valueP }));      // at each half-hour's end, as the history chart has it
  return { t0, t1, now, spanH, nowH, bands, steps, mid, low, high, sun, ticks, hist: hist.length, viewH, histValue, histLines, replans, pathValue,
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
function timelineHover(L, hour, clock, TV, TLN) {
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
  if (TV) {
    const pts = hour < L.nowH ? TV.past : TV.future;
    const near = pts.length ? pts.reduce((m, p) => (Math.abs(p.h - hour) < Math.abs(m.h - hour) ? p : m)) : null;
    if (near && Math.abs(near.h - hour) <= 1) lines.push(`Value of a stored kWh: ${v2P(near.value, 1)}${hour < L.nowH ? "" : " (expected)"}`);
  }
  if (TLN) {
    const at = (steps) => { const s = steps.find((q) => hour >= q.a && hour < q.b); return s ? s.p : null; };
    const buy = at(TLN.buy), sell = at(TLN.sell);
    if (buy !== null || sell !== null) lines.push(`Buy line ${buy !== null ? v2P(buy, 1) : "–"}, sell line ${sell !== null ? v2P(sell, 1) : "–"}`);
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

// ---- plan: value map ----------------------------------------------------------------------------------------------
/** Colour of a value cell: pale (worth little) to deep blue (worth a lot), grid-event values in the event colour. */
function heatColour(v, opts) {
  const o = opts || {};
  const max = o.max > 0 ? o.max : 40;
  const eventAbove = o.eventAbove > 0 ? o.eventAbove : max * 1.5;
  const n = toNumber(v);
  if (n === null) return { kind: "none", css: "transparent", t: 0 };
  if (n > eventAbove) return { kind: "event", css: "var(--m-event)", t: 1 };
  const t = v2Clamp(n / max, 0, 1);
  const from = o.dark ? [36, 40, 46] : [247, 244, 232], to = o.dark ? [100, 181, 246] : [13, 71, 161];
  const m = from.map((c, i) => Math.round(c + (to[i] - c) * Math.pow(t, 0.85)));
  return { kind: "scale", css: `rgb(${m.join(",")})`, t };
}

/** Why a map cell is off the scale (more than 1.5 times the scale): "event" where a grid event or free-power step covers it in time, else
 *  "floor": the plan prices the battery's lowest levels very high (running out means buying at the dear rate, and the reserve and floor
 *  are protected), which is not an event. Null when the value is on the scale. `steps` are the layout's price steps. */
function offScaleReason(cell, steps, scaleMax) {
  if (!cell || heatColour(cell.value, { max: scaleMax }).kind !== "event") return null;
  return (steps || []).some((s) => s.event && s.a < cell.b && s.b > cell.a) ? "event" : "floor";
}
/** The value curve as cells: one row per step_min from `start`, one column per level, in hours from t0 (clipped to the
 *  span). Each level covers half a step either side (0 and 100 only half). */
function valueMapGrid(curve, t0, spanH) {
  const c = curve || {};
  const levels = (Array.isArray(c.levels) ? c.levels : []).map(toNumber);
  const rows = Array.isArray(c.values) ? c.values : [];
  const startMs = v2Ms(c.start);
  if (startMs === null || !levels.length || !rows.length || levels.some((l) => l === null)) return null;
  const stepMs = (toNumber(c.step_min) > 0 ? toNumber(c.step_min) : 60) * 60000;
  const origin = t0 === undefined || t0 === null ? startMs : t0;
  const span = spanH === undefined ? (rows.length * stepMs) / HOUR_MS : spanH;
  const halfs = levels.map((l, i) => {
    const lo = i === 0 ? l : (levels[i - 1] + l) / 2, hi = i === levels.length - 1 ? l : (l + levels[i + 1]) / 2;
    return [lo, hi];
  });
  // a single level has no height; give it the whole axis
  const cells = [];
  rows.forEach((row, r) => {
    const a = (startMs + r * stepMs - origin) / HOUR_MS, b = a + stepMs / HOUR_MS;
    if (b <= 0 || a >= span || !Array.isArray(row)) return;
    levels.forEach((l, i) => {
      const v = toNumber(row[i]);
      if (v !== null) cells.push({ a: Math.max(0, a), b: Math.min(span, b), lo: levels.length === 1 ? 0 : halfs[i][0], hi: levels.length === 1 ? 100 : halfs[i][1], level: l, value: v, row: r });
    });
  });
  return { startMs, stepMs, levels, cells, rows: rows.length, unit: c.unit || "p/kWh" };
}
/** The cell under a point (hours from t0, battery %), or null. */
function mapCellAt(grid, hour, level) {
  if (!grid) return null;
  return grid.cells.find((c) => hour >= c.a && hour < c.b && level >= c.lo && level <= c.hi) || null;
}
/** Value of a stored kWh at `level` in one curve row (linear between the levels, flat beyond the ends). */
function interpValue(levels, row, level) {
  if (!Array.isArray(levels) || !Array.isArray(row) || !levels.length) return null;
  const vals = levels.map((_, i) => toNumber(row[i]));
  if (level <= levels[0]) return vals[0];
  for (let i = 1; i < levels.length; i++) {
    if (level <= levels[i]) {
      const a = vals[i - 1], b = vals[i];
      if (a === null || b === null) return a === null ? b : a;
      return a + (b - a) * ((level - levels[i - 1]) / (levels[i] - levels[i - 1]));
    }
  }
  return vals[levels.length - 1];
}
/** The value of a stored kWh along the expected battery path: [{h, value}] for each path point (hours from t0). */
function pathValueSeries(curve, layout) {
  const c = curve || {};
  const startMs = v2Ms(c.start), levels = c.levels, rows = c.values;
  if (startMs === null || !layout || !layout.mid.length || !Array.isArray(rows) || !rows.length) return [];
  const stepMs = (toNumber(c.step_min) > 0 ? toNumber(c.step_min) : 60) * 60000;
  const out = [];
  layout.mid.forEach((p) => {
    const t = layout.t0 + p.h * HOUR_MS;
    // between two rows in time as well as between levels: a row is one hour's value, so reading the row the time falls in made the line
    // fall within each hour and jump back on the hour (a sawtooth with a period of one hour, which the plan never meant)
    const x = Math.max(0, (t - startMs) / stepMs), r = v2Clamp(Math.floor(x), 0, rows.length - 1), f = Math.min(1, x - r);
    const v0 = interpValue(levels, rows[r], p.level), v1 = r + 1 < rows.length ? interpValue(levels, rows[r + 1], p.level) : null;
    const v = v0 !== null && v1 !== null ? v0 + (v1 - v0) * f : v0;
    if (v !== null) out.push({ h: p.h, value: v });
  });
  return out;
}
/** The value of a stored kWh for the timeline chart: what it was over the hours already run (the recorded value at each half-hour's end)
 *  and what the plan expects along the battery's expected path from now on. [{h, value}] each, hours from the chart's left edge. */
function timelineValue(curve, L) {
  // newer apps send the value at each point of the path (what the controller reads at that level); else it is read off the 10% curve
  const own = (L.pathValue || []).filter((p) => p.h >= L.nowH - 1e-9);
  const future = own.length ? own : pathValueSeries(curve, L).filter((p) => p.h >= L.nowH - 1e-9);
  return { past: (L.histValue || []).filter((p) => p.h <= L.nowH + 1e-9), future };
}
/** The buy and sell lines for the timeline chart as steps {a, b, p} in hours from its left edge: as recorded over the hours already
 *  run, and from now on the plan's prices scaled by the losses the value sensor reports now (pathLines). */
function timelineLines(L, val) {
  const ahead = pathLines({ steps: L.steps.filter((s) => !s.ran) }, val);
  const run = L.histLines || [];
  return {
    buy: run.filter((r) => r.buy !== null).map((r) => ({ a: r.a, b: r.b, p: r.buy })).concat(ahead.buy),
    sell: run.filter((r) => r.sell !== null).map((r) => ({ a: r.a, b: r.b, p: r.sell })).concat(ahead.sell),
  };
}
/** The real prices as lines in value terms: import and export steps scaled by what the value sensor says about losses
 *  now (buy_line_p / import_p, sell_line_p / export_p). With no figures the prices are drawn as they are (k = 1). */
function pathLines(layout, val) {
  const v = val || {};
  const ratio = (line, price) => (toNumber(line) !== null && toNumber(price) > 0 ? toNumber(line) / toNumber(price) : null);
  const kb = ratio(v.buy_line_p, v.import_p), ks = ratio(v.sell_line_p, v.export_p);
  const steps = (layout && layout.steps) || [];
  return {
    afterLosses: kb !== null || ks !== null,
    buy: steps.map((s) => ({ a: s.a, b: s.b, p: plannedImportP(s) * (kb === null ? 1 : kb), slot: s.slot, event: s.event })),
    sell: steps.filter((s) => s.exportP !== null).map((s) => ({ a: s.a, b: s.b, p: s.exportP * (ks === null ? 1 : ks), event: s.event })),
  };
}
/** Sentence for a point on the map. `buyLineAt` = the buy line (p) at that time, or null. */
function mapReadoutText(cell, clock, buyLineAt, offReason) {
  if (!cell) return "Point at the map: the value of one more kWh at that time and battery level.";
  const lo = Math.round(cell.lo), hi = Math.round(cell.hi);
  let t = `At ${clock} with the battery at ${lo === hi ? lo : `${lo}–${hi}`}%, one more kWh is worth ${v2P(cell.value, 1)}.`;
  if (buyLineAt !== null && buyLineAt !== undefined) {
    t += ` Buying then costs ${v2P(buyLineAt, 1)} per stored kWh: ${cell.value >= buyLineAt ? "worth it, so a charge would run" : "not worth it, so no charge"}.`;
  }
  if (offReason === "floor") t += " That is off the scale: this low the battery is near its floor, so running out is priced very high. It is not a grid event.";
  else if (offReason === "event") t += " That is off the scale: a grid event pays far more than the prices.";
  return t;
}
/** The price the plan worked with for a step. A smart slot that is only a chance is drawn at the offered price (app 0.9.134 and later),
 *  but the plan priced it as the chance of that price plus the rest at the standard rate (`standard_p` beside it), so a buy line that
 *  says "worth it" has to use that one. */
function plannedImportP(s) {
  if (s && s.slot && s.slotProb !== null && s.slotProb !== undefined && s.slotProb < 1 && s.standardP !== null && s.standardP !== undefined) {
    return s.slotProb * s.importP + (1 - s.slotProb) * s.standardP;
  }
  return s.importP;
}
/** The plan card's content: kind "v1" | "none" | "ok" (needs at least the timeline or the curve). */
function planCardView(states, now) {
  const gate = v2Gate(states, [V2_TIMELINE, V2_CURVE]);
  if (gate) return { kind: v2Engine(states) === "v1" ? "v1" : "none", text: gate };
  const tl = v2Attrs(states, V2_TIMELINE), curve = v2Attrs(states, V2_CURVE);
  const hasTl = !!(tl && Array.isArray(tl.items) && tl.items.length);
  const layout = hasTl ? timelineLayout(tl, { now }) : null;
  // the timeline chart also shows the hours already run (and scrolls back over them); the value map below keeps the plan's own extent
  const tlayout = hasTl ? timelineLayout(tl, { now, recent: v2Attrs(states, V2_RECENT) }) : null;
  const grid = curve ? valueMapGrid(curve, layout ? layout.t0 : null, layout ? layout.spanH : undefined) : null;
  if (!layout && !grid) return { kind: "none", text: V2_NO_DATA };
  const val = v2Attrs(states, V2_VALUE) || {};
  const worked = (states[V2_TIMELINE] || {}).state;
  const cost = tl && toNumber(tl.cost_expected) !== null && toNumber(tl.cost_selfuse) !== null
    ? `Expected cost £${toNumber(tl.cost_expected).toFixed(2)} against £${toNumber(tl.cost_selfuse).toFixed(2)} on Self-use`
 : "";
  return { kind: "ok", preview: v2Preview(states), tl, layout, tlayout, grid, curve, val, scaleMax: toNumber(val.scale_max_p) > 0 ? toNumber(val.scale_max_p) : 40,
    lines: layout ? pathLines(layout, val) : null, series: layout && curve ? pathValueSeries(curve, layout) : [],
    tvalue: tlayout ? timelineValue(curve, tlayout) : { past: [], future: [] },
    tlines: tlayout ? timelineLines(tlayout, val) : { buy: [], sell: [] },
    chip: [worked && v2Hm(worked) ? `values worked out ${v2Hm(worked)}` : "", tl && tl.because ? lowerFirst(tl.because) : ""].filter(Boolean).join(" · "),
    cost };
}

// ---- health -------------------------------------------------------------------------------------------------------
const TRIGGER_CAUSES = {
  drift: "Forecast drift", slots_changed: "Smart slots changed", slots: "Smart slots changed", car: "Car started or stopped",
  car_start: "Car started", car_stop: "Car stopped", price: "New prices", prices: "New prices", backstop: "Backstop (2 h)",
  forecast: "Forecast changed", deadline: "Deadline check", level: "Level reached", event: "Grid event changed",
  settings: "Settings changed", start: "App started",
};
function causeLabel(key) {
  return TRIGGER_CAUSES[key] || capFirst(String(key).replace(/_/g, " "));
}
/** Bars for "Values worked out N times, because": biggest first, backstop flagged, widths as a share of the largest. */
function causeBars(today) {
  const t = today || {};
  const causes = Object.assign({}, t.causes || {});
  if (causes.backstop === undefined && toNumber(t.backstop) > 0) causes.backstop = toNumber(t.backstop);
  const rows = Object.entries(causes).map(([k, n]) => ({ key: k, label: causeLabel(k), n: toNumber(n) || 0, warn: /^backstop/.test(k) }))
    .filter((r) => r.n > 0).sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
  const max = Math.max(1, ...rows.map((r) => r.n));
  return rows.map((r) => Object.assign(r, { pct: Math.round((r.n / max) * 100) }));
}
const WEIGHT_PARTS = [["morning", "Morning"], ["midday", "Midday"], ["afternoon", "Afternoon"]];
function weightPct(arr) { return Array.isArray(arr) ? arr.map((x) => (toNumber(x) === null ? "–" : Math.round(toNumber(x) * 100))) : null; }
/** The everyday-range sentence from the newest entry of sensor.pe_diag_v2's `comfort` (hours above and below it). */
function comfortSummary(diag, values) {
  const list = diag && Array.isArray(diag.comfort) ? diag.comfort : [];
  const c = list[list.length - 1];
  if (!c) return null;
  const v = values || {};
  const lo = toNumber(v.range_low_soc), hi = toNumber(v.range_high_soc);
  const hrs = (n) => (toNumber(n) ? `${Math.round(toNumber(n) * 10) / 10} h` : "none");
  return {
    title: lo !== null && hi !== null ? `Everyday range (${lo} to ${hi}%)` : "Everyday range",
    text: `${hrs(c.hours_above)} above${hi !== null ? ` ${hi}%` : ""}, ${hrs(c.hours_below)} below${lo !== null ? ` ${lo}%` : ""}.`,
    date: c.date || "",
  };
}
/** What the health card shows, from the states. */
function healthCardView(states) {
  const gate = v2Gate(states, [V2_TRIGGERS, V2_DIAG]);
  if (gate) return { kind: v2Engine(states) === "v1" ? "v1" : "none", text: gate };
  const trig = v2Attrs(states, V2_TRIGGERS) || {}, diag = v2Attrs(states, V2_DIAG) || {};
  const today = trig.today || {};
  const settings = v2Attrs(states, V2_SETTINGS);
  const fig = (k, v, bad) => ({ k, v, bad: !!bad });
  const n = (x) => (toNumber(x) === null ? "–" : String(toNumber(x)));
  const w = diag.weights || {};
  const solar = w.solar || {};
  const weights = WEIGHT_PARTS.map(([key, label]) => ({ label, pct: weightPct(solar[key]) })).filter((r) => r.pct);
  const start = w.start && w.start.solar ? weightPct(w.start.solar) : null;
  const off = diag.soc_offset || {};
  const recent = (Array.isArray(trig.recent) ? trig.recent : []).filter((r) => r && v2Ms(r.at) !== null).sort((a, b) => v2Ms(b.at) - v2Ms(a.at)).slice(0, 30).map((r) => ({
    at: v2Hm(r.at), text: r.text || causeLabel(r.kind), effect: r.effect || "" }));
  const filter = [];
  [["charging", "while charging"], ["holding", "while holding"], ["discharging", "while discharging"]].forEach(([k, label]) => {
    const x = toNumber(off[k]);
    if (x !== null && Math.abs(x) >= 0.05) filter.push(`${Math.abs(Math.round(x * 10) / 10)} point${Math.abs(x) === 1 ? "" : "s"} ${x < 0 ? "low" : "high"} ${label}`);
  });
  return {
    kind: "ok", preview: v2Preview(states), learning: (states[V2_DIAG] || {}).state === "learning",
    revalues: toNumber(today.revalues) === null ? toNumber((states[V2_TRIGGERS] || {}).state) : toNumber(today.revalues),
    bars: causeBars(today),
    figs: [fig("Mode changes", n(today.mode_changes)), fig("Flip-flops", n(today.flip_flops), toNumber(today.flip_flops) > 0),
      fig("Deadlines missed", n(today.deadlines_missed), false), fig("Longest calculation", toNumber(today.longest_calc_s) === null ? "–" : `${toNumber(today.longest_calc_s)} s`)],
    comfort: comfortSummary(diag, settings && settings.values),
    weights, weightsStart: start, weightDays: toNumber(w.days),
    filterText: filter.length ? `Reading is ${filter.join(", ")} (learned).` : "",
    filterGap: toNumber(diag.filter_gap_max_today),
    recent,
  };
}

// ---- config: engine choice and groups ---------------------------------------------------------------------------
// The planning topics only engine v1 uses, as sections of their own under "Engine v1 settings". Everything else in the
// existing topics stays in the groups both engines use. The cheap threshold (and its automatic switch) and the grid-charge
// target stay there too: the car's smart-charge requests read them whichever engine runs. Only the sections' keys that the app lists are shown.
const V1_SECTIONS = [
  { key: "v1_planning", title: "Planning", features: ["optimised_plan", "fill_when_cheap"],
    settings: ["window_switch_cost_p", "overnight_switch_cost_p", "ram_switch_cost_p", "charge_hysteresis_soc"] },
  { key: "v1_selling", title: "Selling (arbitrage band)", features: ["arbitrage", "deep_overnight"],
    settings: ["battery_wear_p", "arbitrage_min_margin_p", "arbitrage_min_soc", "arbitrage_max_soc", "arbitrage_band_penalty_p"] },
  { key: "v1_events", title: "Grid events and reserve", features: [], settings: ["min_reserve_soc", "pre_axle_lookahead_h", "axle_margin_soc"] },
];
/** Which topics and keys go to which group. With `v2On` false nothing moves (house = the old topic plan). */
function engineGrouping(roleKeys, settingKeys, featureKeys, systemKeys, v2On) {
  if (!v2On) return { house: topicPlan(roleKeys, settingKeys, featureKeys, systemKeys), v1: [], grouped: false };
  const v1Settings = new Set(), v1Features = new Set();
  V1_SECTIONS.forEach((s) => { s.settings.forEach((k) => v1Settings.add(k)); s.features.forEach((k) => v1Features.add(k)); });
  const house = topicPlan(roleKeys, settingKeys.filter((k) => !v1Settings.has(k)), featureKeys.filter((k) => !v1Features.has(k)),
    systemKeys.filter((k) => k !== "engine"));
  const v1 = V1_SECTIONS.map((s) => Object.assign({}, s, { settings: s.settings.filter((k) => settingKeys.includes(k)),
    features: s.features.filter((k) => featureKeys.includes(k)) })).filter((s) => s.settings.length || s.features.length);
  return { house, v1, grouped: true };
}
/** The engine in use: what the app says is running, else the saved choice, else v1. */
function engineInUse(states, saved) {
  const e = (states || {})[ENGINE_SENSOR];
  const choice = e && e.attributes ? e.attributes.choice : null;
  if (v2Engine(states) === "v2" && choice === "v2_1") return "v2_1";     // engine 2.1 beta runs as engine v2 (the sensor says "v2")
  const savedChoice = ((saved || {}).system || {}).engine;
  return v2Engine(states) || (savedChoice === "v2" || savedChoice === "v2_1" ? savedChoice : "v1");
}
/** With profiles (an app that publishes `profiles`), engine 2.1 beta is a profile of engine v2, not an engine. */
function uiEngine(v2cat, eng) { return eng === "v2_1" && v2cat && v2cat.profiles ? "v2" : eng; }
/** The profile fields of a draft: the profile in use (what the app runs when the config names none) and your saved ones. */
function profileDraft(v2cat, saved) {
  const profiles = (v2cat || {}).profiles ? Object.assign({}, (saved || {}).v2_profiles || {}) : {};
  return { v2_profile: (saved || {}).v2_profile || (v2cat || {}).profile || "defaults", v2_profiles: profiles };
}
/** Every profile you can load: the built-in ones, then your saved ones. */
function profileList(v2cat, savedProfiles) {
  const p = (v2cat || {}).profiles || {};
  return [...(p.builtin || []).map((b) => ({ name: b.name, builtin: true, note: b.note })),
    ...Object.keys(savedProfiles || {}).sort().map((name) => ({ name, builtin: false, note: "" }))];
}
/** The values a profile gives (only those that differ from engine v2's defaults), or null when it is unknown. */
function profileBase(v2cat, savedProfiles, name) {
  const b = (((v2cat || {}).profiles || {}).builtin || []).find((x) => x.name === name);
  if (b) return Object.assign({}, b.values);
  return savedProfiles && savedProfiles[name] ? Object.assign({}, savedProfiles[name]) : null;
}
function isSiteKey(v2cat, key) { return (((v2cat || {}).profiles || {}).site_keys || []).includes(key); }
/** The settings in the changes block that a profile would hold (house settings are not part of a profile). */
function profileChanges(v2cat, block) {
  return Object.keys(block || {}).filter((k) => !isSiteKey(v2cat, k));
}
function withoutProfileKeys(v2cat, block) {
  const out = {};
  Object.entries(block || {}).forEach(([k, v]) => { if (isSiteKey(v2cat, k)) out[k] = v; });
  return out;
}
/** Loading a profile: it becomes the one in use and the changes made on top of the old one are dropped. */
function profileLoadFields(v2cat, savedProfiles, block, name) {
  if (!profileBase(v2cat, savedProfiles, name)) return { error: `No profile called “${name}”.` };
  return { v2_profile: name, v2_profiles: Object.assign({}, savedProfiles), engine_v2: withoutProfileKeys(v2cat, block) };
}
/** Saving: the profile in use plus your changes, under `name` (an existing saved name is replaced). */
function profileSaveFields(v2cat, savedProfiles, active, block, name) {
  const n = String(name || "").trim();
  const p = (v2cat || {}).profiles || {};
  if (!n) return { error: "Give the profile a name." };
  if (n.length > (p.max_name || 30)) return { error: `A profile name is at most ${p.max_name || 30} characters.` };
  if ((p.builtin || []).some((b) => b.name.toLowerCase() === n.toLowerCase())) return { error: `“${n}” is a built-in profile; pick another name.` };
  const exists = !!(savedProfiles || {})[n];
  if (!exists && Object.keys(savedProfiles || {}).length >= (p.max_saved || 10)) return { error: `Up to ${p.max_saved || 10} profiles can be saved; delete one first.` };
  const base = profileBase(v2cat, savedProfiles, active) || {};
  const changes = {};
  profileChanges(v2cat, block).forEach((k) => { changes[k] = block[k]; });
  const values = cleanV2Block(Object.assign({}, base, changes));
  return { v2_profile: n, v2_profiles: Object.assign({}, savedProfiles, { [n]: values }), engine_v2: withoutProfileKeys(v2cat, block) };
}
/** Deleting the profile in use: back to the built-in defaults profile, with your changes kept on top. */
function profileDeleteFields(v2cat, savedProfiles, active, block) {
  const rest = Object.assign({}, savedProfiles);
  delete rest[active];
  return { v2_profile: "defaults", v2_profiles: rest, engine_v2: Object.assign({}, block) };
}
/** The name of an engine choice, for messages. */
function engineName(id) { return id === "v2_1" ? "engine 2.1 beta" : `engine ${id}`; }
/** The text of the inline confirmation, the same in both directions. */
function engineConfirm(from, to) {
  const beta = to === "v2_1" ? " It is a beta: it follows its own plan and keeps the battery below about 95%; watch it for a few days." : "";
  return {
    title: `Switch to ${engineName(to)}?`,
    text: `It takes over within a few seconds. Active, Passive and Pause stay as they are; the inverter keeps its current command until ${engineName(to)} has decided. ${engineName(from)[0].toUpperCase()}${engineName(from).slice(1)}'s settings are kept, and you can switch back here at any time.${beta}`,
    confirm: `Switch to ${to === "v2_1" ? "2.1 beta" : to}`, cancel: `Keep ${from === "v2_1" ? "2.1 beta" : from}`,
  };
}
/** A v2 settings block ready to send: numbers as numbers, flags as booleans, nothing empty. */
function cleanV2Block(block) {
  const out = {};
  Object.entries(block || {}).forEach(([k, v]) => {
    if (typeof v === "boolean") out[k] = v;
    else if (typeof v === "number") { if (Number.isFinite(v)) out[k] = v; }
    else if (typeof v === "string" && v.trim() !== "") out[k] = toNumber(v) !== null ? toNumber(v) : v.trim();
  });
  return out;
}
/** The extra keys a save carries for engine v2. Only when the app publishes the v2 settings (an older app rejects
 *  unknown keys): `system.engine` and the `engine_v2` block (left out while empty). */
function engineFields(supported, engine, block) {
  if (!supported) return {};
  const out = {};
  if (engine === "v1" || engine === "v2" || engine === "v2_1") out.system = { engine };
  const clean = cleanV2Block(block);
  if (Object.keys(clean).length) out.engine_v2 = clean;
  return out;
}
/** Are two setting values the same (numbers compared as numbers, "5" and 5 alike)? */
function v2Same(a, b) {
  if (typeof a === "boolean" || typeof b === "boolean") return asBool(a) === asBool(b);
  const x = toNumber(a), y = toNumber(b);
  return x !== null && y !== null ? x === y : String(a) === String(b);
}
/** Problem with one v2 setting's value, or "". Kinds: number, int, bool, choice. */
function v2Problem(st, value) {
  if (st.kind === "bool") return "";
  if (st.kind === "choice") return (st.options || []).includes(value) ? "" : "Choose one of the options";
  const n = toNumber(value);
  if (n === null) return "Enter a number";
  if (st.kind === "int" && !Number.isInteger(n)) return "Enter a whole number";
  if (n < st.min || n > st.max) return `Must be between ${st.min} and ${st.max}`;
  return "";
}
/** Settings that contradict each other: { key: message } (the app checks the same). */
function v2Contradictions(values) {
  const v = values || {}, out = {};
  const lo = toNumber(v.range_low_soc), hi = toNumber(v.range_high_soc);
  if (lo !== null && hi !== null && lo >= hi) out.range_low_soc = "The bottom must be below the top";
  return out;
}
/** The values to show in the v2 group: what the app uses now, overlaid with the draft's own block. */
function v2Values(catalogue, block) {
  return Object.assign({}, (catalogue && catalogue.values) || {}, block || {});
}
/** Readout under the battery's range, from sensor.pe_diag_v2. */
function comfortReadout(diag, values) {
  const c = comfortSummary(diag, values);
  return c ? `Latest day (${c.date || "today"}): ${c.text}` : null;
}
/** Readout under the forecast weights: what has been learned. */
function weightsReadout(diag) {
  const w = (diag && diag.weights) || {};
  const mid = w.solar && weightPct(w.solar.midday);
  if (!mid) return null;
  const days = toNumber(w.days);
  return `Learned now (midday): ${mid.join(" / ")}${days ? ` from ${days} day${days === 1 ? "" : "s"}` : ""}. Morning and afternoon are on the engine's Health page.`;
}
/** Readout under the floors: the shared hard floor and where a grid event stops. */
function floorsReadout(safety, values, inUse) {
  const typed = toNumber((safety || {}).battery_floor_soc);
  if (typed === null) return null;
  const used = inUse && inUse.used ? toNumber(String(inUse.used).replace("%", "")) : null;
  const f = used !== null ? used : typed;                          // the floor really in use (raised where the battery was seen to stop)
  const m = toNumber((values || {}).hard_floor_margin_pct);
  const why = f !== typed ? `raised from the ${typed}% you entered to where the battery was seen to stop` : "the battery's own limit, set under Your house";
  return `Hard floor ${f}% (${why}).${m !== null ? ` Grid events stop at ${f + m}%.` : ""}`;
}

// ---- the cards -------------------------------------------------------------------------------------------------
const V2_CSS = `
  :host { display: block; --m-self: #00897b; --m-hold: #78909c; --m-charge: #43a047; --m-export: #e53935; --m-event: #8e24aa; --m-free: #f9a825;
    --v2-battery: #1565c0; --v2-price: #fb8c00; --v2-sun: #f2a900; --v2-house: #78909c; --v2-good: #2e7d32; --v2-warn: #ef6c00; --v2-buy: #00838f; --v2-sell: #c2185b; }
  :host(.dark) { --m-self: #26a69a; --m-hold: #90a4ae; --m-charge: #66bb6a; --m-export: #ef5350; --m-event: #ba68c8; --m-free: #ffd54f;
    --v2-battery: #64a6f0; --v2-price: #ffa940; --v2-sun: #ffd54f; --v2-house: #b0bec5; --v2-good: #66bb6a; --v2-warn: #ffa726; --v2-buy: #26c6da; --v2-sell: #f06292; }
  ha-card { padding: 16px; display: flex; flex-direction: column; gap: 14px; min-width: 0; box-sizing: border-box; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .title { font-size: 16px; font-weight: 500; }
  .key { display: flex; flex-wrap: wrap; gap: 2px 12px; font-size: 12px; color: var(--secondary-text-color); flex: 1 1 auto; justify-content: flex-end; }
  .key label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; white-space: nowrap; }
  .key input { margin: 0; accent-color: var(--primary-color); cursor: pointer; }
  .chip { display: inline-flex; align-items: center; font-size: 12px; font-weight: 500; padding: 2px 10px; border-radius: 999px;
    background: rgba(3,169,244,.14); color: var(--primary-color); white-space: nowrap; }
  .chip.muted { background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .muted { color: var(--secondary-text-color); }
  .preview-line { margin: 0; padding: 8px 12px; border-radius: 8px; font-size: 13px; font-weight: 500;
    background: rgba(3,169,244,.14); color: var(--primary-text-color); }
  .small { font-size: 12px; }
  .num { font-family: var(--code-font-family, monospace); font-variant-numeric: tabular-nums; }
  h3 { font-size: 13px; font-weight: 500; margin: 0 0 6px; text-transform: uppercase; letter-spacing: .06em; color: var(--secondary-text-color); }
  p { margin: 0; }
  .line { padding: 4px 0; }
  .figs { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
  .fig { background: var(--secondary-background-color); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
  .fig .k { font-size: 12px; color: var(--secondary-text-color); }
  .fig .v { font-family: var(--code-font-family, monospace); font-size: 16px; font-variant-numeric: tabular-nums; }
  .fig .v.bad { color: var(--error-color, #db4437); }
  .fig .v.good { color: var(--v2-good); }
  .sw { width: 12px; height: 12px; border-radius: 3px; display: inline-block; flex: none; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--secondary-text-color); }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .chart { overflow-x: auto; }
  .chart svg { display: block; width: 100%; min-width: 620px; height: auto; }
  svg text { font-family: inherit; }
`;

const V2Base = typeof HTMLElement !== "undefined" ? HTMLElement : class {};
/** The key above a chart: a tick box per line that can be hidden. Which are hidden is kept in this browser only (localStorage, tried and caught). */
const CHART_KEYS = {
  value: ["--v2-good", "Value of a stored kWh"], buy: ["--v2-buy", "Buy line"], sell: ["--v2-sell", "Sell line"],
  replan: ["--primary-text-color", "Re-plans"], energy: ["--v2-price", "Grid energy"], sun: ["--v2-sun", "Sun and house"],
};
function chartHidden(id) {
  try { const raw = typeof localStorage !== "undefined" ? localStorage.getItem(`pe_chart_hide_${id}`) : null; return new Set(raw ? JSON.parse(raw) : []); } catch (e) { return new Set(); }
}
function chartHide(id, hidden) { try { localStorage.setItem(`pe_chart_hide_${id}`, JSON.stringify(Array.from(hidden))); } catch (e) { /* no storage: it still works for now */ } }
function chartKeyHtml(keys, hidden) {
  return `<span class="key" role="group" aria-label="Lines to show">${keys.map((k) => `<label><input type="checkbox" data-key="${k}" ${hidden.has(k) ? "" : "checked"}><i class="sw" style="background:var(${CHART_KEYS[k][0]})"></i>${escHtml(CHART_KEYS[k][1])}</label>`).join("")}</span>`;
}
class PowerEngineV2Card extends V2Base {
  /** The lines hidden on this chart, and the tick boxes that change it. `id` names the chart ("plan", "history"). */
  _hidden(id) { if (!this._hid) this._hid = {}; return this._hid[id] || (this._hid[id] = chartHidden(id)); }
  _wireKey(id) {
    const hidden = this._hidden(id);
    this.shadowRoot.querySelectorAll("input[data-key]").forEach((box) => box.addEventListener("change", () => {
      if (box.checked) hidden.delete(box.dataset.key); else hidden.add(box.dataset.key);
      chartHide(id, hidden);
      if (box.blur) box.blur();
      this._draw();
    }));
  }
  /** Run `draw` once the scroll box has a width. Inside Home Assistant `ha-card` is itself a custom element that renders its slot a
   *  moment after it is created, so a box built in the same breath has no width: the plot was sized for a made-up width and the scroll
   *  position was ignored, which is why the chart opened at the start of the history and went back there on every redraw (a plain
   *  `ha-card`, as in a test page, has a width at once and hid this). Waits up to ~1 s of frames, then draws anyway and stays stale. */
  _whenLaidOut(draw, boxId) {
    const token = (this._layoutToken = (this._layoutToken || 0) + 1);
    const box = () => this.shadowRoot && this.shadowRoot.getElementById(boxId || "tl-scroll");
    const ready = () => { const b = box(); return !b || b.clientWidth > 0; };
    if (ready() || typeof requestAnimationFrame === "undefined") { draw(); return; }
    this._stale = true;
    let frames = 0;
    const step = () => {
      if (token !== this._layoutToken) return;                // a newer render replaced this box
      if (ready() || ++frames > 60) { draw(); if (ready()) this._stale = false; return; }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }
  getGridOptions() { return { columns: "full", rows: "auto" }; }
  /** Ids whose changes redraw the card. */
  _ids() { return []; }
  set hass(hass) {
    this._hass = hass;
    const dark = !!(hass && hass.themes && hass.themes.darkMode);
    if (this.classList) this.classList.toggle("dark", dark);
    const st = (hass && hass.states) || {};
    const sig = [ENGINE_SENSOR].concat(this._ids()).map((id) => (st[id] ? `${st[id].last_updated || ""}${st[id].state}` : "-")).join("|") + dark;
    if (sig === this._sig) return;
    this._sig = sig;
    this._draw();
  }
  _draw() {
    if (!this.shadowRoot || !this._hass) return;
    try { this._render(this._hass.states || {}); } catch (e) {
      this.shadowRoot.innerHTML = `<style>${V2_CSS}</style><ha-card><p class="muted">This card could not be drawn (${escHtml((e && e.message) || e)}).</p></ha-card>`;
    }
  }
  _message(title, text) {
    this.shadowRoot.innerHTML = `<style>${V2_CSS}</style><ha-card><div class="head"><span class="title">${escHtml(title)}</span></div><p class="muted">${escHtml(text)}</p></ha-card>`;
  }
}

function svgEl(tag, attrs, parent, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  const style = [];
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if ((k === "fill" || k === "stroke") && String(v).startsWith("var(")) style.push(`${k}:${v}`);
    else e.setAttribute(k, v);
  });
  if (style.length) e.setAttribute("style", style.join(";"));
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

/* powerengine-engine-card: the Monitoring panel for engine v2 -------------------------------------------------------
 *   type: custom:powerengine-engine-card
 * Reads sensor.pe_v2_mode and sensor.pe_v2_value; says "Engine v2 is not running" while engine v1 runs. */
class PowerEngineEngineCard extends PowerEngineV2Card {
  getCardSize() { return 7; }
  _ids() { return [V2_MODE, V2_VALUE, MODE_SENSOR]; }
  _render(states) {
    const v = engineCardView(states);
    if (v.kind !== "ok") { this._message("PowerEngine", v.text); return; }
    const op = (states[MODE_SENSOR] || {}).state;
    const chips = (v.preview ? '<span class="chip muted">Preview</span>' : op === "active" || op === "passive" ? `<span class="chip${op === "active" ? "" : " muted"}">${op === "active" ? "Active" : "Passive"}</span>` : "") + '<span class="chip">Engine v2</span>';
    const exits = v.exits.length ? `<div><h3>This ends when</h3><dl class="exits">${v.exits.map((e) =>
      `<dt class="${e.first ? "first" : ""}"><span class="sw" style="background:var(${e.colour})"></span>${escHtml(e.text)}</dt><dd class="num">${escHtml(e.when)}</dd>`).join("")}</dl></div>` : "";
    this.shadowRoot.innerHTML = `<style>${V2_CSS}
      .mode-now { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; align-items: center; }
      .badge { width: 56px; height: 56px; border-radius: 14px; display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 22px; }
      .mode-name { font-size: 22px; font-weight: 500; }
      .why { background: var(--secondary-background-color); border-radius: 8px; padding: 10px 12px; }
      .exits { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 12px; margin: 0; }
      .exits dt { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
      .exits dd { margin: 0; color: var(--secondary-text-color); text-align: right; }
      .exits .first { font-weight: 500; }
      .bar svg { display: block; width: 100%; max-width: 560px; height: auto; }
      .offnote { font-size: 12px; color: var(--secondary-text-color); }
    </style>
    <ha-card>
      ${previewBanner(v.preview)}
      <div class="head"><span class="title">PowerEngine</span><span class="chips">${chips}</span></div>
      <div class="mode-now">
        <div class="badge" aria-hidden="true" style="background:var(${v.mode.colour})">${escHtml(v.mode.glyph)}</div>
        <div><div class="mode-name">${escHtml(v.label)}</div><div class="muted">${escHtml(v.subtitle)}</div></div>
      </div>
      ${v.why ? `<p class="why">${escHtml(v.why)}</p>` : ""}
      <div class="bar"><h3>What a stored kWh is worth now, against today's prices</h3><div id="bar"></div>
        <p class="muted small">The marker is the value at the battery's level now; it moves as the battery fills and when the values are worked out again.
        The lines are the real prices after charging and discharging losses: buy while a stored kWh is worth more than the buy line, sell when it is worth less than the sell line.
        ${v.offScale ? "The marker is off the scale: a grid event, or a battery so low that running out is priced very high." : "Grid-event values, and the battery's lowest levels, are off the scale."}</p></div>
      ${exits}
      <div class="figs">${v.figs.map((f) => `<div class="fig"><span class="k">${escHtml(f.k)}</span><span class="v">${escHtml(f.v)}</span></div>`).join("")}</div>
      ${v.notes.map((n) => `<p class="muted small">${escHtml(n)}</p>`).join("")}
    </ha-card>`;
    const host = this.shadowRoot.getElementById("bar");
    if (host && v.bar && v.bar.marker) this._drawBar(host, v);
    else if (host) host.textContent = "The value of a stored kWh is not published yet.";
  }
  _drawBar(host, v) {
    const g = v.bar;
    const svg = svgEl("svg", { viewBox: "0 0 400 70", role: "img", "aria-label": v.barText }, host);
    const track = { y: 24, h: 10 };
    svgEl("rect", { x: g.x0, y: track.y, width: g.x1 - g.x0, height: track.h, rx: 5, fill: "var(--secondary-background-color)" }, svg);
    if (g.sellFill) svgEl("rect", { x: g.sellFill.x, y: track.y, width: Math.max(0, g.sellFill.w), height: track.h, rx: 5, fill: "var(--m-export)", "fill-opacity": 0.25 }, svg);
    if (g.buyFill) svgEl("rect", { x: g.buyFill.x, y: track.y, width: Math.max(0, g.buyFill.w), height: 4, fill: "var(--m-charge)", "fill-opacity": 0.55 }, svg);
    [[g.buy, `buy above ${v2P(g.buy && g.buy.p, 1)}`, "var(--m-charge)", 50], [g.sell, `sell below ${v2P(g.sell && g.sell.p, 1)}`, "var(--m-export)", 64]].forEach(([l, t, c, y]) => {
      if (!l) return;
      svgEl("rect", { x: l.x - 1, y: 18, width: 2, height: 22, fill: c }, svg);
      svgEl("text", { x: l.anchor === "end" ? l.x - 4 : l.x + 4, y, "text-anchor": l.anchor, "font-size": 10.5, fill: "var(--secondary-text-color)" }, svg, t);
    });
    g.ticks.forEach((t, i) => { if (i === 0 || i === g.ticks.length - 1) svgEl("text", { x: t.x, y: 50, "text-anchor": i === 0 ? "start" : "end", "font-size": 9.5, fill: "var(--secondary-text-color)" }, svg, `${t.p}p`); });
    svgEl("circle", { cx: g.marker.x, cy: 29, r: 8, fill: "var(--v2-battery)", stroke: "var(--card-background-color, #fff)", "stroke-width": 2 }, svg);
    svgEl("text", { x: g.marker.x, y: 12, "text-anchor": g.markerAnchor, "font-size": 11.5, "font-weight": 500, fill: "var(--primary-text-color)" }, svg,
      `worth ${v2P(g.marker.p, 1)}${g.marker.off ? " (off the scale)" : ""}`);
  }
}

/* "Re-plan now" under the expected timeline: fires pe_v2_replan (admins only: HA's fire_event needs admin) and says what happened.
   The plan sensor's state is the time the values were worked out, so a new state is the proof that a new plan has been made. */
const REPLAN_EVENT = "pe_v2_replan";
const REPLAN_WAIT_MS = 20000;
/** What to say next to the button. `ask` is {at (ms), before (the sensor's state when asked)} or null; `worked` is its state now. */
function replanStatus(ask, worked, nowMs) {
  if (!ask) return { text: "", busy: false, done: false };
  if (worked !== undefined && worked !== null && String(worked) !== String(ask.before)) {
    const hm = v2Hm(worked);
    return { text: hm ? `Re-planned at ${hm}` : "Re-planned just now", busy: false, done: true };
  }
  if (nowMs - ask.at > REPLAN_WAIT_MS) {
    return { text: "No new plan yet. The app may be busy, not in Engine v2, or it did not get the request.", busy: false, done: false };
  }
  return { text: "Re-planning...", busy: true, done: false };
}

/* powerengine-v2-plan-card: the expected timeline, and the value map with a toggle to "along the expected path" ------ */
class PowerEngineV2PlanCard extends PowerEngineV2Card {
  getCardSize() { return 9; }
  _ids() { return [V2_TIMELINE, V2_CURVE, V2_VALUE, V2_RECENT]; }
  // "Now" comes from the browser's clock, not from when the app last published. The card only redraws when a sensor changes, so
  // without its own tick the line stood still between publishes. The tick redraws about once a minute while the card is on screen.
  connectedCallback() { this._startTick(); }
  disconnectedCallback() { this._stopTick(); }
  _startTick() {
    if (this._timer || typeof setInterval === "undefined") return;
    this._timer = setInterval(() => this._onTick(false), 30000);
    this._onVisible = () => { if (!document.hidden) this._onTick(true); };
    document.addEventListener("visibilitychange", this._onVisible);
    if (typeof ResizeObserver !== "undefined") {
      this._resize = new ResizeObserver(() => {
        const w = Math.round(this.clientWidth || 0);
        if (!w || (!this._stale && Math.abs(w - (this._drawnWidth || 0)) < 3)) return;
        clearTimeout(this._resizeTimer);
        this._resizeTimer = setTimeout(() => this._onTick(true), 150);
      });
      this._resize.observe(this);
    }
  }
  _stopTick() {
    clearInterval(this._timer); this._timer = null;
    clearTimeout(this._resizeTimer); clearTimeout(this._idleTimer);
    if (this._onVisible) document.removeEventListener("visibilitychange", this._onVisible);
    if (this._resize) { this._resize.disconnect(); this._resize = null; }
  }
  /** Redraw with the clock's "now". Not while the page is hidden or the card is not on screen (it redraws when it comes back), and
   *  `_draw` itself holds off while the person is touching or scrolling the chart. `force` redraws even when the minute has not
   *  changed (back on screen, resized). */
  _onTick(force) {
    if (!this._hass || !this.shadowRoot || (typeof document !== "undefined" && document.hidden)) return;
    if (!this.offsetWidth) { this._stale = true; return; }
    if (!force && Math.floor(Date.now() / 60000) === this._drawnMinute) return;
    this._draw();
  }
  /** Is the person's finger on the chart, or is it still scrolling? A redraw replaces the scroll box, which would cancel their
   *  gesture and put the view back, so a redraw that arrives meanwhile (a sensor update, the clock) waits until they let go. */
  _busy() { return (!!this._touching && Date.now() - (this._touchAt || 0) < 10000) || Date.now() - (this._lastScrollAt || 0) < 700; }
  _draw() {
    if (this._busy()) { this._pendingDraw = true; this._armIdle(); return; }
    this._pendingDraw = false;
    super._draw();
  }
  _armIdle() {
    clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => {
      if (this._busy()) { this._armIdle(); return; }
      if (this._pendingDraw) this._draw();
    }, 800);
  }
  _render(states) {
    const v = planCardView(states, Date.now());
    if (v.kind !== "ok") { this._message("Engine v2 plan", v.text); return; }
    this._v = v;
    this._view = this._view || "map";
    const dark = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    this._dark = dark;
    const legend = (items) => items.map(([c, t]) => `<span><i class="sw" style="background:${c}"></i>${escHtml(t)}</span>`).join("");
    const L = v.layout, T = v.tlayout || v.layout;
    const keepRead = (this.shadowRoot.getElementById("treadout") || {}).innerHTML || null;
    // read where the person has the chart now, from the live box, before it is replaced (scroll events can lag behind a flick)
    const prevBox = this.shadowRoot.getElementById("tl-scroll");
    const intent = prevBox ? scrollIntent({ placed: prevBox._placed === true, laidOut: prevBox.clientWidth > 0, scrollLeft: prevBox.scrollLeft }, this._tlGeom) : null;
    if (intent) { this._tlUser = intent.user; this._tlLeftMs = intent.leftMs; }
    this._drawnMinute = Math.floor(Date.now() / 60000);
    this._stale = false;
    const rp = replanStatus(this._replanAsk, (states[V2_TIMELINE] || {}).state, Date.now());
    if (rp.busy) { clearTimeout(this._replanTimer); this._replanTimer = setTimeout(() => this._draw(), REPLAN_WAIT_MS + 500); }
    this.shadowRoot.innerHTML = `<style>${V2_CSS}
      .tlbox { display: grid; grid-template-columns: 40px minmax(0, 1fr) 46px; align-items: start; min-width: 0; }
      .tlbox svg { display: block; }
      .tlscroll { overflow-x: auto; overflow-y: hidden; overscroll-behavior-x: contain; min-width: 0; }
      .seg { display: inline-flex; background: var(--secondary-background-color); border-radius: 8px; padding: 3px; gap: 2px; }
      .seg button { font: inherit; font-size: 13px; font-weight: 500; border: 0; background: none; color: var(--secondary-text-color); padding: 5px 12px; border-radius: 6px; cursor: pointer; }
      .seg button[aria-pressed="true"] { background: var(--card-background-color); color: var(--primary-text-color); box-shadow: 0 1px 2px rgba(0,0,0,.2); }
      .readout { font-size: 13px; min-height: 2.6em; background: var(--secondary-background-color); border-radius: 8px; padding: 8px 12px; }
      .gap { display: flex; flex-direction: column; gap: 14px; }
      .readout.tl { min-height: 8.5em; line-height: 1.45; }
      .replan { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 4px 16px 12px; }
      .replan button { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--primary-color); background: none; color: var(--primary-color); padding: 6px 14px; border-radius: 8px; cursor: pointer; }
      .replan button[disabled] { opacity: .5; cursor: default; }
    </style>
    <div class="gap">
    ${T ? `<ha-card>
      ${previewBanner(v.preview)}
      <div class="head"><span class="title">Expected timeline · next ${Math.round(T.spanH - T.nowH)} hours</span>${chartKeyHtml(["value", "buy", "sell", "replan"], this._hidden("plan"))}${v.chip ? `<span class="chip muted">${escHtml(v.chip)}</span>` : ""}</div>
      <div class="tlbox"><svg id="tl-axl" aria-hidden="true"></svg><div class="tlscroll" id="tl-scroll"><div id="timeline"></div></div><svg id="tl-axr" aria-hidden="true"></svg></div>
      <p class="readout tl" id="treadout" aria-live="polite">${escHtml(TIMELINE_HINT)}</p>
      <div class="replan"><button id="replan" ${rp.busy ? "disabled" : ""}>Re-plan now</button><span class="muted small" id="replan-s" aria-live="polite">${escHtml(rp.text)}</span></div>
      <div class="legend">${legend(Object.keys(V2_MODES).filter((k) => k !== "none" && k !== "free").map((k) => [`var(${V2_MODES[k].colour})`, V2_MODES[k].name]))
        .concat(legend([["var(--v2-battery)", "Battery (shaded: likely range)"], ["var(--v2-price)", "Import price (dashed: smart slot that may not come)"]]))
        .concat(T.sun ? legend([["var(--v2-sun)", T.sun.peak > 0 ? `Sun forecast (shaded: low to high; peak ${T.sun.peak.toFixed(1)} kW)` : "Sun forecast (none expected in this window)"], ["var(--v2-house)", "House use expected (strip is 0 to " + T.sun.max + " kW)"]]) : "")}</div>
      <p class="muted small">Bands to the left of <b>now</b> are what happened${T.hist ? ` (scroll left for the last ${HISTORY_HOURS} hours)` : ""}. Bands to the right are expected: each says what ends it, and its edge moves when the condition is met earlier or later.${v.cost ? ` ${escHtml(v.cost)}.` : ""}</p>
    </ha-card>` : ""}
    ${v.grid ? `<ha-card>${L ? "" : previewBanner(v.preview)}
      <div class="head"><span class="title">What a stored kWh is worth</span>
        <div class="seg" role="group" aria-label="Value view"><button data-view="map" aria-pressed="${this._view === "map"}">Map</button><button data-view="path" aria-pressed="${this._view === "path"}">Along the expected path</button></div></div>
      <div class="chart" id="valuemap"></div>
      <div class="legend" id="vlegend"></div>
      <p class="readout" id="vreadout" aria-live="polite"></p>
    </ha-card>` : ""}
    </div>`;
    this.shadowRoot.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => { this._view = b.dataset.view; this._renderValue(); this.shadowRoot.querySelectorAll(".seg button").forEach((o) => o.setAttribute("aria-pressed", String(o === b))); }));
    const rb = this.shadowRoot.getElementById("replan");
    if (rb) rb.addEventListener("click", () => this._replan(states));
    this._wireKey("plan");
    if (T) this._whenLaidOut(() => this._drawTimeline(this.shadowRoot.getElementById("timeline"), v));
    if (keepRead) { const ro = this.shadowRoot.getElementById("treadout"); if (ro) ro.innerHTML = keepRead; }
    if (v.grid) this._renderValue();
  }
  /** "Re-plan now": ask the app to work the values out again from the live readings, then redraw. */
  async _replan(states) {
    this._replanAsk = { at: Date.now(), before: (states[V2_TIMELINE] || {}).state };
    this._draw();
    try {
      await this._hass.callWS({ type: "fire_event", event_type: REPLAN_EVENT, event_data: {} });
    } catch (err) {
      this._replanAsk = null;
      const el = this.shadowRoot.getElementById("replan-s");
      if (el) el.textContent = "Could not ask for a re-plan: only a Home Assistant administrator can.";
      const b = this.shadowRoot.getElementById("replan");
      if (b) b.disabled = false;
    }
  }
  _geom(extra) {
    return Object.assign({ W: 900, H: 300, L: 40, R: 46, T: 14, B: 34 }, extra || {});
  }
  /** The timeline in pixels: the plot is as wide as its hours need (VIEW hours fill the box, the hours already run lie to the left,
   *  reached by scrolling), with the percentage and price scales fixed either side so they stay in view while it scrolls. */
  _drawTimeline(host, v) {
    const L = v.tlayout || v.layout, bandH = 26;
    const root = this.shadowRoot, scroller = root.getElementById("tl-scroll"), axl = root.getElementById("tl-axl"), axr = root.getElementById("tl-axr");
    const G = { H: 300, T: 14, B: 34 };
    const boxW = Math.max(260, scroller.clientWidth || 620);
    const pph = Math.max(boxW, TIMELINE_MIN_W) / Math.max(1, L.spanH - L.viewH);   // pixels per hour; never so tight the hour labels collide (a phone scrolls)
    const plotW = Math.max(boxW, L.spanH * pph);
    this._drawnWidth = Math.round(this.clientWidth || 0);
    const svg = svgEl("svg", { width: plotW, height: G.H, viewBox: `0 0 ${plotW} ${G.H}`, role: "img", "aria-label": "Modes, battery level and import price over the last hours and the coming hours" }, host);
    const x = (hr) => hr * pph;
    const top = G.T + bandH + 10;
    const yL = (p) => top + (1 - p / 100) * (G.H - top - G.B);
    const hid = this._hidden("plan");
    const TV = v.tvalue || { past: [], future: [] }, TLN = v.tlines || { buy: [], sell: [] };
    const maxPrice = Math.max(10, Math.ceil(Math.max(0, ...L.steps.map((s) => s.importP), ...TV.past.map((p) => Math.min(p.value, 60)), ...TV.future.map((p) => Math.min(p.value, 60)), ...TLN.buy.map((s) => s.p), ...TLN.sell.map((s) => s.p)) / 5) * 5);
    const yP = (p) => top + (1 - Math.min(p, maxPrice) / maxPrice) * (G.H - top - G.B);
    const muted = "var(--secondary-text-color)";
    axl.setAttribute("width", 40); axl.setAttribute("height", G.H); axr.setAttribute("width", 46); axr.setAttribute("height", G.H);
    if (L.hist) svgEl("rect", { x: 0, y: top - 4, width: Math.max(0, x(L.nowH)), height: G.H - top - G.B + 8, fill: "var(--primary-text-color)", "fill-opacity": 0.04 }, svg);
    for (let p = 0; p <= 100; p += 25) {
      svgEl("line", { x1: 0, x2: plotW, y1: yL(p), y2: yL(p), stroke: "var(--divider-color)" }, svg);
      svgEl("text", { x: 34, y: yL(p) + 4, "text-anchor": "end", "font-size": 11, fill: muted }, axl, `${p}%`);
    }
    for (let p = 0; p <= maxPrice; p += maxPrice <= 20 ? 5 : 10) svgEl("text", { x: 6, y: yP(p) + 4, "font-size": 11, fill: "var(--v2-price)" }, axr, `${p}p`);
    L.ticks.forEach((t) => {
      const tx = x(t.h);
      if (tx > 24 && tx < plotW - 24) svgEl("text", { x: tx, y: G.H - 12, "text-anchor": "middle", "font-size": 11, fill: muted }, svg, v2Hm(new Date(t.ms).toISOString()));
    });
    L.bands.forEach((b) => {
      const col = `var(${v2Mode(b.mode).colour})`;
      const dim = b.preview ? 0.4 : 1;
      const draw = (s, e, op) => svgEl("rect", { x: x(s), y: G.T, width: Math.max(0, x(e) - x(s) - 1), height: bandH, rx: 4, fill: col, "fill-opacity": op }, svg);
      if (b.state === "now") { draw(b.a, L.nowH, 1); draw(L.nowH, b.b, 0.45); } else draw(b.a, b.b, b.state === "past" ? dim : 0.45);
      const label = bandLabel(b, x(b.b) - x(b.a));
      if (label) svgEl("text", { x: x(b.a) + 6, y: G.T + 17, "font-size": 11.5, "font-weight": 500, fill: b.state === "past" && !b.preview ? "#fff" : "var(--primary-text-color)" }, svg, label);
      if (b.state === "future") svgEl("rect", { x: x(b.a) - 1.5, y: G.T - 3, width: 3, height: bandH + 6, rx: 1, fill: col, "fill-opacity": 0.9 }, svg);
    });
    const future = (pts) => pts.filter((p) => p.h >= L.nowH - 1e-9);
    if (L.sun) {                                         // the sun and the house in a strip along the bottom, behind the battery
      const S = L.sun, stripH = Math.min(70, (G.H - top - G.B) * 0.32), base = G.H - G.B;
      const yS = (kw) => base - (Math.min(kw, S.max) / S.max) * stripH;
      const area = (pts) => pts.map((p) => `${x(p.h)},${yS(p.kw)}`).join(" ");
      if (S.high.length && S.low.length) svgEl("polygon", { points: S.high.map((p) => `${x(p.h)},${yS(p.kw)}`).concat(S.low.slice().reverse().map((p) => `${x(p.h)},${yS(p.kw)}`)).join(" "), fill: "var(--v2-sun)", "fill-opacity": 0.28 }, svg);
      svgEl("polygon", { points: `${x(S.mid[0].h)},${base} ${area(S.mid)} ${x(S.mid[S.mid.length - 1].h)},${base}`, fill: "var(--v2-sun)", "fill-opacity": 0.35 }, svg);
      svgEl("polyline", { points: area(S.mid), fill: "none", stroke: "var(--v2-sun)", "stroke-width": 1.5 }, svg);
      if (S.house.length) svgEl("polyline", { points: area(S.house), fill: "none", stroke: "var(--v2-house)", "stroke-width": 1.5, "stroke-dasharray": "3 3" }, svg);
    }
    if (L.low.length && L.high.length) {
      const hi = future(L.high), lo = future(L.low).slice().reverse();
      if (hi.length && lo.length) svgEl("polygon", { points: hi.concat(lo).map((p) => `${x(p.h)},${yL(p.level)}`).join(" "), fill: "var(--v2-battery)", "fill-opacity": 0.13 }, svg);
    }
    L.steps.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: yP(s.importP), y2: yP(s.importP), stroke: "var(--v2-price)", "stroke-width": 2, "stroke-dasharray": s.slot ? "5 4" : "none" }, svg));
    for (let i = 1; i < L.steps.length; i++) {
      if (Math.abs(L.steps[i].a - L.steps[i - 1].b) < 1e-6) svgEl("line", { x1: x(L.steps[i].a), x2: x(L.steps[i].a), y1: yP(L.steps[i - 1].importP), y2: yP(L.steps[i].importP), stroke: "var(--v2-price)", "stroke-opacity": 0.5 }, svg);
    }
    const stepLines = (steps, col) => steps.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: yP(s.p), y2: yP(s.p), stroke: col, "stroke-width": 1.3, "stroke-opacity": 0.7, "stroke-dasharray": "2 3" }, svg));
    if (!hid.has("buy")) stepLines(TLN.buy, "var(--v2-buy)");              // where it charges (value above) and sells (value below)
    if (!hid.has("sell")) stepLines(TLN.sell, "var(--v2-sell)");
    const vline = (pts, dash) => { if (pts.length > 1) svgEl("polyline", { points: pts.map((p) => `${x(p.h)},${yP(p.value)}`).join(" "), fill: "none", stroke: "var(--v2-good)", "stroke-width": 2.2, "stroke-dasharray": dash }, svg); };
    if (!hid.has("value")) {                                                // the value of a stored kWh, on the price axis (pence)
      vline(TV.past, "none");
      vline(TV.past.slice(-1).concat(TV.future), "6 4");
    }
    const poly = (pts) => pts.map((p) => `${x(p.h)},${yL(p.level)}`).join(" ");
    const past = L.mid.filter((p) => p.h <= L.nowH + 1e-9), fut = future(L.mid);
    if (past.length > 1) svgEl("polyline", { points: poly(past), fill: "none", stroke: "var(--v2-battery)", "stroke-width": 2.5 }, svg);
    if (fut.length > 1) svgEl("polyline", { points: poly(fut), fill: "none", stroke: "var(--v2-battery)", "stroke-width": 2, "stroke-dasharray": "6 4" }, svg);
    if (L.floor !== null) {
      svgEl("line", { x1: 0, x2: plotW, y1: yL(L.floor), y2: yL(L.floor), stroke: "var(--m-export)", "stroke-dasharray": "2 4" }, svg);
      svgEl("text", { x: plotW - 4, y: yL(L.floor) - 4, "text-anchor": "end", "font-size": 10.5, fill: "var(--m-export)" }, svg, `hard floor ${L.floor}%`);
    }
    (hid.has("replan") ? [] : (L.replans || [])).forEach((rh) => svgEl("line", { x1: x(rh), x2: x(rh), y1: G.H - G.B, y2: G.H - G.B + 9, stroke: "var(--primary-text-color)", "stroke-width": 1.5, "stroke-opacity": 0.7 }, svg));
    const nowX = x(L.nowH), nowLabel = `now ${v2Hm(new Date(L.now).toISOString())}`;
    svgEl("line", { x1: nowX, x2: nowX, y1: G.T - 4, y2: G.H - G.B + 4, stroke: "var(--primary-text-color)", "stroke-width": 1.4 }, svg);
    svgEl("text", { x: nowX + 4, y: G.H - G.B - 4, "font-size": 11, "font-weight": 500, fill: "var(--primary-text-color)" }, svg, nowLabel);
    if (L.nowLevel !== null) svgEl("circle", { cx: nowX, cy: yL(L.nowLevel), r: 4.5, fill: "var(--v2-battery)" }, svg);
    this._placeScroll(scroller, L, pph, plotW, boxW);
    this._hoverTimeline(host, svg, v, { H: G.H, T: G.T, B: G.B, pph, plotW });
  }
  /** Where the scroll box opens: on the default view (VIEW_PAST_HOURS before now) until the person scrolls; after that where they left
   *  it, kept by the time at its left edge so a redraw (new data, the clock) does not move it. Back at the default, it follows now again. */
  _placeScroll(scroller, L, pph, plotW, boxW) {
    const maxPx = Math.max(0, plotW - boxW), defPx = Math.min(maxPx, L.viewH * pph);
    let px = defPx;
    if (this._tlUser && this._tlLeftMs !== null && this._tlLeftMs !== undefined) px = Math.min(maxPx, Math.max(0, ((this._tlLeftMs - L.t0) / HOUR_MS) * pph));
    this._tlGeom = { t0: L.t0, pph, defPx };
    const laidOut = scroller.clientWidth > 0;
    scroller.scrollLeft = px;
    scroller._placed = laidOut && Math.abs(scroller.scrollLeft - px) < 2;      // a box with no width ignores the position: that is not the person's choice
    this._tlSetPx = scroller.scrollLeft;                                       // what the box really holds, so our own placing is never taken for a scroll
    if (!laidOut) this._stale = true;                                          // built before it is on screen: draw again when it has a width
    scroller.addEventListener("scroll", () => {
      if (!scroller.isConnected || scroller !== this.shadowRoot.getElementById("tl-scroll")) return;   // a replaced box reports 0 as it goes: not the person
      if (Math.abs(scroller.scrollLeft - this._tlSetPx) < 2) return;          // our own placing
      this._lastScrollAt = Date.now();
      this._tlSetPx = scroller.scrollLeft;
      this._tlLeftMs = L.t0 + (scroller.scrollLeft / pph) * HOUR_MS;
      this._tlUser = Math.abs(scroller.scrollLeft - defPx) > 8;
      this._armIdle();
    });
    scroller.addEventListener("touchstart", () => { this._touching = true; this._touchAt = Date.now(); }, { passive: true });
    const lift = () => { this._touching = false; this._lastScrollAt = Date.now(); this._armIdle(); };
    scroller.addEventListener("touchend", lift, { passive: true });
    scroller.addEventListener("touchcancel", lift, { passive: true });
  }
  /** Hover (and touch) text for the timeline, in the box under the chart as the value map does: a guide line and, in the box,
   *  what the bands, battery, price and sun say at that time, for the values and status the bars have no room to print. */
  _hoverTimeline(host, svg, v, G) {
    const L = v.tlayout || v.layout, ro = this.shadowRoot.getElementById("treadout");
    if (!ro) return;
    const guide = svgEl("line", { y1: G.T - 4, y2: G.H - G.B + 4, stroke: "var(--primary-text-color)", "stroke-opacity": 0.5, "stroke-dasharray": "3 3", visibility: "hidden", "pointer-events": "none" }, svg);
    const cover = svgEl("rect", { x: 0, y: 0, width: G.plotW, height: G.H - G.B + 4, fill: "transparent", style: "cursor:crosshair" }, svg);
    const clock = (h) => v2Hm(new Date(L.t0 + h * HOUR_MS).toISOString());
    const show = (ev) => {
      const r = svg.getBoundingClientRect();
      if (!r.width) return;
      const px = ((ev.clientX - r.left) / r.width) * G.plotW;
      const h = Math.min(L.spanH, Math.max(0, px / G.pph));
      const t = timelineHover(L, h, clock, v.tvalue, v.tlines);
      ro.innerHTML = `<b>${escHtml(t.title)}</b><br>${t.lines.map(escHtml).join("<br>")}`;
      guide.setAttribute("x1", h * G.pph); guide.setAttribute("x2", h * G.pph); guide.setAttribute("visibility", "visible");
    };
    cover.addEventListener("pointermove", show); cover.addEventListener("pointerdown", show);
  }
  _renderValue() {
    const v = this._v;
    if (!v || !v.grid) return;
    const host = this.shadowRoot.getElementById("valuemap"), leg = this.shadowRoot.getElementById("vlegend"), ro = this.shadowRoot.getElementById("vreadout");
    if (!host) return;
    host.innerHTML = ""; leg.innerHTML = "";
    const G = this._geom({ H: 290, T: 10 });
    const spanH = v.layout ? v.layout.spanH : (v.grid.rows * v.grid.stepMs) / HOUR_MS;
    const t0 = v.layout ? v.layout.t0 : v.grid.startMs;
    const x = (h) => G.L + (h / spanH) * (G.W - G.L - G.R);
    const muted = "var(--secondary-text-color)";
    const svg = svgEl("svg", { viewBox: `0 0 ${G.W} ${G.H}`, role: "img", "aria-label": this._view === "map" ? "Heat map of the value of a stored kWh by time and battery level" : "Value of a stored kWh along the expected battery path, with the buy and sell lines" }, host);
    const ticks = v.layout ? v.layout.ticks : [];
    ticks.forEach((t) => svgEl("text", { x: x(t.h), y: G.H - 12, "text-anchor": "middle", "font-size": 11, fill: muted }, svg, v2Hm(new Date(t.ms).toISOString())));
    const swatch = (css, t) => { const s = document.createElement("span"); const i = document.createElement("i"); i.className = "sw"; i.style.background = css; s.append(i, t); leg.appendChild(s); };
    const clock = (h) => v2Hm(new Date(t0 + h * HOUR_MS).toISOString());
    const buyAt = (h) => { const s = v.lines && v.lines.buy.find((b) => h >= b.a && h < b.b); return s ? s.p : null; };
    if (this._view === "map") {
      const y = (p) => G.T + (1 - p / 100) * (G.H - G.T - G.B);
      const heat = { max: v.scaleMax, dark: this._dark };
      const steps = v.layout ? v.layout.steps : [];
      const seen = new Set();
      const off = (c) => { const r = offScaleReason(c, steps, v.scaleMax); if (r) seen.add(r); return r; };
      v.grid.cells.forEach((c) => svgEl("rect", { x: x(c.a), y: y(c.hi), width: x(c.b) - x(c.a) + 0.5, height: y(c.lo) - y(c.hi) + 0.5, fill: off(c) === "floor" ? "var(--v2-warn)" : heatColour(c.value, heat).css }, svg));
      for (let p = 0; p <= 100; p += 25) svgEl("text", { x: G.L - 6, y: y(p) + 4, "text-anchor": "end", "font-size": 11, fill: muted }, svg, `${p}%`);
      if (v.layout && v.layout.mid.length) {
        const pts = v.layout.mid.map((p) => `${x(p.h)},${y(p.level)}`).join(" ");
        svgEl("polyline", { points: pts, fill: "none", stroke: "var(--card-background-color, #fff)", "stroke-width": 5, "stroke-opacity": 0.85 }, svg);
        svgEl("polyline", { points: pts, fill: "none", stroke: "var(--primary-text-color)", "stroke-width": 2 }, svg);
        svgEl("line", { x1: x(v.layout.nowH), x2: x(v.layout.nowH), y1: G.T, y2: G.H - G.B, stroke: "var(--primary-text-color)", "stroke-width": 1.2, "stroke-dasharray": "3 3" }, svg);
      }
      const cover = svgEl("rect", { x: G.L, y: G.T, width: G.W - G.L - G.R, height: G.H - G.T - G.B, fill: "transparent", style: "cursor:crosshair" }, svg);
      const point = (ev) => {
        const r = svg.getBoundingClientRect();
        if (!r.width) return;
        const px = ((ev.clientX - r.left) / r.width) * G.W, py = ((ev.clientY - r.top) / r.height) * G.H;
        const h = ((px - G.L) / (G.W - G.L - G.R)) * spanH, lvl = (1 - (py - G.T) / (G.H - G.T - G.B)) * 100;
        const cell = mapCellAt(v.grid, h, lvl);
        ro.textContent = mapReadoutText(cell, clock(h), cell ? buyAt(h) : null, cell ? offScaleReason(cell, steps, v.scaleMax) : null);
      };
      cover.addEventListener("pointermove", point); cover.addEventListener("pointerdown", point);
      [0, 10, 20, 30].forEach((p) => p <= v.scaleMax && swatch(heatColour(p, heat).css, `${p}p`));
      swatch(heatColour(v.scaleMax, heat).css, `${v.scaleMax}p+`);
      if (seen.has("floor")) swatch("var(--v2-warn)", "off the scale: the battery near its floor");
      if (seen.has("event")) swatch("var(--m-event)", "grid event (off the scale)");
      swatch("var(--primary-text-color)", "expected battery path");
      ro.textContent = mapReadoutText(null);
    } else {
      const max = v.scaleMax;
      const y = (p) => G.T + (1 - Math.min(p, max) / max) * (G.H - G.T - G.B);
      for (let p = 0; p <= max; p += max <= 50 ? 10 : 25) {
        svgEl("line", { x1: G.L, x2: G.W - G.R, y1: y(p), y2: y(p), stroke: "var(--divider-color)" }, svg);
        svgEl("text", { x: G.L - 6, y: y(p) + 4, "text-anchor": "end", "font-size": 11, fill: muted }, svg, `${p}p`);
      }
      const lines = v.lines || { buy: [], sell: [] };
      (v.layout ? v.layout.steps : []).filter((s) => s.event).forEach((s) => {
        svgEl("rect", { x: x(s.a), y: G.T, width: x(s.b) - x(s.a), height: G.H - G.T - G.B, fill: "var(--m-event)", "fill-opacity": 0.18 }, svg);
        svgEl("text", { x: x(s.a) + 3, y: G.T + 14, "font-size": 11, fill: "var(--primary-text-color)" }, svg, "grid event: off the scale");
      });
      lines.buy.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: y(s.p), y2: y(s.p), stroke: "var(--v2-price)", "stroke-width": 2, "stroke-dasharray": s.slot ? "5 4" : "none" }, svg));
      lines.sell.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: y(s.p), y2: y(s.p), stroke: "var(--m-export)", "stroke-width": 1.5, "stroke-dasharray": "2 3" }, svg));
      if (v.series.length > 1) svgEl("polyline", { points: v.series.map((p) => `${x(p.h)},${y(p.value)}`).join(" "), fill: "none", stroke: "var(--v2-battery)", "stroke-width": 2.5 }, svg);
      if (v.layout) svgEl("line", { x1: x(v.layout.nowH), x2: x(v.layout.nowH), y1: G.T, y2: G.H - G.B, stroke: "var(--primary-text-color)", "stroke-width": 1.2, "stroke-dasharray": "3 3" }, svg);
      swatch("var(--v2-battery)", "value of a stored kWh at the expected level");
      swatch("var(--v2-price)", lines.afterLosses ? "buy line (import price after charging losses)" : "import price");
      swatch("var(--m-export)", lines.afterLosses ? "sell line (export price after discharge losses)" : "export price");
      ro.textContent = "Where the blue line is above the orange one, buying is worth it; where it falls below, the charge stops. Where it is below the red line, selling pays.";
    }
  }
}

/* powerengine-v2-health-card: is engine v2 behaving? ---------------------------------------------------------------- */
class PowerEngineV2HealthCard extends PowerEngineV2Card {
  getCardSize() { return 7; }
  _ids() { return [V2_TRIGGERS, V2_DIAG, V2_SETTINGS]; }
  _render(states) {
    const v = healthCardView(states);
    if (v.kind !== "ok") { this._message("Engine v2 today", v.text); return; }
    const bars = v.bars.length ? v.bars.map((b) => `<div class="bar${b.warn ? " backstop" : ""}"><span>${escHtml(b.label)}</span><span class="track"><span class="fill" style="width:${b.pct}%"></span></span><span class="num">${b.n}</span></div>`).join("")
      : '<p class="muted">Nothing has been worked out again today.</p>';
    const weights = v.weights.length ? `<div><h3>Solar weights, learned${v.weightsStart ? ` (started ${v.weightsStart.join(" / ")})` : ""}</h3>
      <div class="wts"><span class="h"></span><span class="h">Low</span><span class="h">Middle</span><span class="h">High</span>${v.weights.map((r) => `<span class="h">${r.label}</span>${r.pct.map((p) => `<span class="num">${p}</span>`).join("")}`).join("")}</div>
      <p class="muted small">${v.weightDays ? `From ${v.weightDays} day${v.weightDays === 1 ? "" : "s"}. ` : ""}More weight on "low" means the sun has been coming in under the forecast, so the engine keeps a little more back.</p></div>` : "";
    const filter = v.filterText || v.filterGap !== null ? `<div><h3>Battery level filter</h3><p>${escHtml(v.filterText)}${v.filterGap !== null ? ` Largest gap between reading and filter today: <span class="num">${v.filterGap}</span> points.` : ""}</p></div>` : "";
    this.shadowRoot.innerHTML = `<style>${V2_CSS}
      .bars { display: flex; flex-direction: column; gap: 6px; }
      .bar { display: grid; grid-template-columns: 150px minmax(0, 1fr) 28px; gap: 8px; align-items: center; font-size: 13px; }
      .track { height: 10px; background: var(--secondary-background-color); border-radius: 5px; overflow: hidden; display: block; }
      .fill { height: 100%; background: var(--primary-color); border-radius: 5px; display: block; }
      .bar.backstop .fill { background: var(--v2-warn); }
      .wts { display: grid; grid-template-columns: 90px repeat(3, minmax(0, 1fr)); gap: 6px; font-size: 13px; align-items: center; }
      .wts .h { color: var(--secondary-text-color); font-size: 12px; }
      .recent { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 4px 10px; font-size: 13px; max-height: 320px; overflow-y: auto; align-content: start; }
      @media (max-width: 520px) { .bar { grid-template-columns: 110px minmax(0, 1fr) 28px; } }
    </style>
    <ha-card>
      ${previewBanner(v.preview)}
      <div class="head"><span class="title">Engine v2 today</span>${v.learning ? '<span class="chip muted">still learning</span>' : ""}</div>
      <div><h3>Values worked out${v.revalues === null ? "" : `: ${v.revalues} time${v.revalues === 1 ? "" : "s"}, because`}</h3><div class="bars">${bars}</div></div>
      <div class="figs">${v.figs.map((f) => `<div class="fig"><span class="k">${escHtml(f.k)}</span><span class="v${f.bad ? " bad" : f.k === "Flip-flops" && f.v !== "–" ? " good" : ""}">${escHtml(f.v)}</span></div>`).join("")}</div>
      ${v.comfort ? `<div><h3>${escHtml(v.comfort.title)}</h3><p>${escHtml(v.comfort.text)}</p></div>` : ""}
      ${weights}${filter}
      ${v.recent.length ? `<div><h3>Latest (newest first)</h3><div class="recent">${v.recent.map((r) => `<span class="num muted">${escHtml(r.at)}</span><span>${escHtml(r.text)}</span><span class="muted">${escHtml(r.effect)}</span>`).join("")}</div></div>` : ""}
    </ha-card>`;
  }
}

// ---- engine pages: icons, badge, v2 history, same-day comparison (docs/plans/engine-pages-and-comparison.md) ---------
// Custom icons for the two engine tabs: `pe:engine-v1` and `pe:engine-v2`. One 24x24 path each: an engine
// outline (intake on top, shaft and flywheel at the sides) with the digit cut out of the block. The digit is traced the
// opposite way round to the outline, so it is a hole under the default (non-zero) fill rule HA's ha-svg-icon uses.
// HA looks them up through window.customIcons; with this file not loaded a tab shows no icon, as for any custom icon.
const ENGINE_OUTLINE = "M6 3.5H12V5H10.5V7H14.5V5.5H17.5V7H19V10H23V17H19V20H4V17H1V10H4V7H7.5V5H6Z";
const ENGINE_ICONS = {
  "engine-v1": `${ENGINE_OUTLINE}M10.5 10.6L10.5 12.3L12.2 11.6L12.2 17.5L14 17.5L14 9.5L12.2 9.5Z`,
  "engine-v2": `${ENGINE_OUTLINE}M9.5 11.2L12.8 11.2L12.8 12.5L9.5 12.5L9.5 17.5L14.5 17.5L14.5 15.8L11.2 15.8L11.2 14.2L14.5 14.2L14.5 9.5L9.5 9.5Z`,
};
function engineIcon(name) { return ENGINE_ICONS[name] ? { path: ENGINE_ICONS[name] } : null; }
if (typeof window !== "undefined") {
  window.customIcons = window.customIcons || {};
  window.customIcons.pe = {
    getIcon: async (name) => engineIcon(name),
    getIconList: async () => Object.keys(ENGINE_ICONS).map((name) => ({ name })),
  };
  window.customIconsets = window.customIconsets || {};         // the older hook, for a Home Assistant that has only this one
  window.customIconsets.pe = async (name) => engineIcon(name);
}
/** HA's ha-icon looks an icon up once, when its `icon` is set. The dashboard's tabs are drawn before this file has loaded,
 *  so a `pe:` icon was looked up while the set was unknown: ha-icon then switched to its legacy mode (an iron-icon HA
 *  doesn't provide, so nothing is drawn) and never looks again. This finds those ha-icons (through the shadow roots),
 *  clears the legacy flag and sets the icon again so it is looked up anew. Returns how many it nudged. */
function refreshEngineIcons(root) {
  let n = 0;
  const walk = (node, depth) => {
    if (!node || depth > 40 || !node.querySelectorAll) return;
    node.querySelectorAll("*").forEach((el) => {
      if (el.localName === "ha-icon" && typeof el.icon === "string" && el.icon.startsWith("pe:")
          && engineIcon(el.icon.slice(3)) && (el._legacy || !el._path)) {
        const icon = el.icon;
        el._legacy = false;
        el.icon = "";
        el.icon = icon;
        n += 1;
      }
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    });
  };
  walk(root || (typeof document !== "undefined" ? document : null), 0);
  return n;
}
let engineIconsTimer = null;
/** Nudge the engine icons now and a few times while the dashboard settles (cheap; at most every 2 s from cards). */
function scheduleEngineIconRefresh() {
  if (typeof window === "undefined" || typeof document === "undefined" || engineIconsTimer) return;
  [0, 700, 2000, 5000, 12000].forEach((ms, i, all) => setTimeout(() => {
    try { refreshEngineIcons(document); } catch (e) { /* never break the card for an icon */ }
    if (i === all.length - 1) engineIconsTimer = null;
  }, ms));
  engineIconsTimer = true;
}
if (typeof window !== "undefined" && typeof document !== "undefined") {
  scheduleEngineIconRefresh();
  window.addEventListener("location-changed", () => setTimeout(() => { try { refreshEngineIcons(document); } catch (e) { /* ignore */ } }, 300));
}

// ---- the Active / Paused / Passive badge at the top of each engine page ----
const PAUSE_SWITCH = "switch.pe_ctl_pause";
const BADGE_LINES = {
  active: "Sending commands to the inverter",
  paused: "Paused: nothing is sent to the inverter",
  passive: "Working out what it would do; nothing is sent",
  off: "Not running: preview is off",
  unknown: "PowerEngine's mode is not available yet",
};
/** What the badge on `engine`'s page says. `engine` is "v1" or "v2". Pure.
 *  Chosen engine + Active (not paused) -> Active; + Active but paused -> Paused; anything else -> Passive.
 *  kind: "active" | "paused" | "passive" | "unknown"; `line` is the one-sentence meaning. */
function engineBadge(states, engine) {
  const st = states || {};
  const eng = engine === "v2" ? "v2" : "v1";
  const chosen = v2Engine(st) || "v1";                       // an app that doesn't say has only engine v1
  const op = st[MODE_SENSOR] && st[MODE_SENSOR].state;
  const pause = st[PAUSE_SWITCH] && st[PAUSE_SWITCH].state === "on";
  const label = (kind) => ({ active: "Active", paused: "Paused", passive: "Passive", unknown: "Unknown" }[kind]);
  const e = st[ENGINE_SENSOR];
  const prof = e && e.attributes && typeof e.attributes.profile === "string" ? e.attributes.profile : "";
  const profile = eng === "v2" && chosen === "v2" ? prof : "";      // the settings profile engine v2 runs on, when it is the engine in use
  const make = (kind, line) => ({ engine: eng, kind, label: label(kind), line: line || BADGE_LINES[kind], chosen, profile });
  if (chosen !== eng) {
    if (eng === "v2" && !v2Preview(st)) return make("passive", BADGE_LINES.off);
    return make("passive");
  }
  if (op === "paused") return make("paused");
  if (op === "active") return make(pause ? "paused" : "active");
  if (op === "passive") return make("passive");
  return make("unknown");
}

class PowerEngineEngineBadgeCard extends PowerEngineV2Card {
  setConfig(config) {
    scheduleEngineIconRefresh();
    super.setConfig(config);
    this._engine = (config && config.engine) === "v2" ? "v2" : "v1";
  }
  getCardSize() { return 1; }
  getGridOptions() { return { columns: "full", rows: "auto" }; }   // the line wraps on a phone: as tall as it needs
  _ids() { return [MODE_SENSOR, PAUSE_SWITCH, V2_MODE, ENGINE_SENSOR]; }
  _render(states) {
    const b = engineBadge(states, this._engine);
    this.shadowRoot.innerHTML = `<style>${V2_CSS}
      ha-card { flex-direction: row; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 16px; }
      .badge-chip { font-size: 14px; font-weight: 600; padding: 4px 14px; border-radius: 999px; white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; }
      .badge-chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
      .badge-chip.active { background: rgba(67,160,71,.18); color: var(--v2-good); }
      .badge-chip.paused { background: rgba(255,167,38,.2); color: var(--v2-warn); }
      .badge-chip.passive, .badge-chip.unknown { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .eng { font-size: 16px; font-weight: 500; }
    </style>
    <ha-card><span class="eng">Engine ${this._engine}${b.profile ? ` · ${escHtml(b.profile)}` : ""}</span><span class="badge-chip ${b.kind}" role="status">${escHtml(b.label)}</span><span class="muted">${escHtml(b.line)}</span></ha-card>`;
  }
}

// ---- engine v2 history: one day of what v2 did (sensor.pe_v2_history) ----
const V2_HISTORY_SENSOR = "sensor.pe_v2_history";
const V2_HISTORY_DAY_EVENT = "pe_v2_history_day";
const V2_FLOWS = "sensor.pe_v2_flows";                 // the day's grid and car at the time they happened (newer apps)
const V2_HISTORY_NONE = "Engine v2's history has not been recorded yet.";
const V2_HISTORY_HINT = "Move over the chart, or tap it, to read the values at that time.";
/** What the timeline sensor expects from `nowMs` on, in milliseconds (the history chart draws it dimmed, to the right of now):
 *  {from, to, bands, steps, mid, low, high, value, sun}; `sun` (null without one) holds mid/low/high/house as {ms, kw} points. Null when there is no usable timeline. Pure. */
function v2Forecast(tl, nowMs) {
  if (!tl || typeof tl !== "object") return null;
  const L = timelineLayout(tl, { now: nowMs });
  const ms = (hr) => L.t0 + hr * HOUR_MS;
  const bands = L.bands.filter((b) => b.state !== "past").map((b) => ({ mode: b.mode, a: b.startMs, b: b.endMs, reason: b.reason, until: b.until, levelStart: b.levelStart, levelEnd: b.levelEnd }));
  const steps = L.steps.map((q) => ({ a: ms(q.a), b: ms(q.b), importP: q.importP, exportP: q.exportP, slot: q.slot, slotProb: q.slotProb, event: q.event, free: q.free, estimated: q.estimated }))
    .filter((q) => q.b > nowMs);
  const line = (pts, key) => pts.map((q) => ({ ms: ms(q.h), y: q[key] })).filter((q) => q.ms >= nowMs - 15 * 60000);
  const kwLine = (pts) => (pts || []).map((q) => ({ ms: ms(q.h), kw: q.kw }));
  const sun = L.sun ? { mid: kwLine(L.sun.mid), low: kwLine(L.sun.low), high: kwLine(L.sun.high), house: kwLine(L.sun.house) } : null;
  const out = { from: nowMs, to: Math.min(L.t1, nowMs + FORECAST_HOURS * HOUR_MS), bands, steps, mid: line(L.mid, "level"), low: line(L.low, "level"), high: line(L.high, "level"), value: line(L.pathValue, "value"), sun };
  return bands.length || steps.length || out.mid.length ? out : null;
}
/** The date (YYYY-MM-DD, this browser's day) the forecast reaches, or "" without one. */
function v2ForecastLastDay(fc) {
  if (!fc || !Number.isFinite(fc.to)) return "";
  const d = new Date(fc.to - 1);
  const two = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}
/** The day-picker call for the v2 history: the same rules as the plan history's (a date inside earliest to latest). */
function v2HistoryDayPayload(value, earliest, latest) {
  return historyDayPayload(value, earliest, latest, V2_HISTORY_DAY_EVENT);
}
/** Split points {h, y} at nulls into runs of at least one point. */
function runsOf(points) {
  const out = []; let cur = [];
  points.forEach((p) => { if (p.y === null) { if (cur.length) out.push(cur); cur = []; } else cur.push(p); });
  if (cur.length) out.push(cur);
  return out;
}
/** Everything the history card draws, from the attributes of sensor.pe_v2_history. kind: "none" | "empty" | "ok".
 *  Hours are from the start of the first half-hour. Pure. */
function v2HistoryView(attrs, nowMs, opts) {
  const o = opts || {};
  const a = attrs && typeof attrs === "object" ? attrs : null;
  if (!a) return { kind: "none", text: V2_HISTORY_NONE };
  const floor = toNumber(o.floor) !== null && o.floor > 0 && o.floor < 60 ? toNumber(o.floor) : 0;     // the battery's own lowest level: the scale starts there
  const date = typeof a.date === "string" ? a.date : "";
  const raw = (Array.isArray(a.series) ? a.series : []).filter((p) => p && v2Ms(p.t) !== null).sort((p, q) => v2Ms(p.t) - v2Ms(q.t));
  const base = { date, earliest: a.earliest || "", latest: a.latest || "", note: a.note ? String(a.note) : "" };
  // today and the days to come also show what the timeline expects, to the right of now, so the chart spans the whole day
  const dayB = (() => { if (!ISO_DAY.test(date)) return null; const [y, m, d] = date.split("-").map(Number); return { a: new Date(y, m - 1, d).getTime(), b: new Date(y, m - 1, d + 1).getTime() }; })();
  const fcOn = !!(o.forecast && dayB && date >= (a.latest || date) && o.forecast.to > dayB.a && o.forecast.from < dayB.b);
  if (!raw.length && !fcOn) return Object.assign(base, { kind: "empty", text: date ? `Engine v2 has no record for ${date}.` : V2_HISTORY_NONE });
  const t0 = fcOn ? dayB.a : v2Ms(raw[0].t);
  const gaps = raw.slice(1).map((p, i) => v2Ms(p.t) - v2Ms(raw[i].t)).filter((g) => g > 0);
  const stepMs = gaps.length ? Math.min(...gaps) : 30 * 60000;
  const h = (ms) => (ms - t0) / HOUR_MS;
  // today runs on to the end of what the timeline covers, as the plan chart does; a coming day is just that day
  const endMs = fcOn ? (date === a.latest ? Math.max(dayB.b, o.forecast.to) : dayB.b) : null;
  const spanH = fcOn ? (endMs - dayB.a) / HOUR_MS : h(v2Ms(raw[raw.length - 1].t) + stepMs);
  const sentOf = (p) => p.sent !== false;
  const bandsRaw = raw.map((p) => ({ mode: p.mode || "none", a: h(v2Ms(p.t)), b: h(v2Ms(p.t) + stepMs), preview: !sentOf(p) }));
  // the day as it ran, to the second, when the app publishes it (`runs`); else the mode that ran longest in each half-hour
  const ranTo = a.runs_until && v2Ms(a.runs_until) !== null ? v2Ms(a.runs_until)
    : (Number.isFinite(nowMs) ? nowMs : (() => { const done = raw.filter((p) => p.mode); return done.length ? v2Ms(done[done.length - 1].t) + stepMs : null; })());
  const runs = runRows(a.runs, ranTo === null ? null : Math.min(ranTo, t0 + spanH * HOUR_MS));
  const exact = runs.length > 0;
  const bandsFrom = exact ? runs.map((r) => ({ mode: r.mode, a: Math.max(0, h(r.a)), b: h(r.b), preview: r.preview })) : bandsRaw;
  const bands = [];
  bandsFrom.forEach((b) => {
    const last = bands[bands.length - 1];
    if (last && last.mode === b.mode && last.preview === b.preview && Math.abs(last.b - b.a) < 1e-6) last.b = b.b; else bands.push(Object.assign({}, b));
  });
  const pts = (key) => raw.map((p) => ({ h: h(v2Ms(p.t) + stepMs), y: toNumber(p[key]) }));     // level and value are at the half-hour's end
  // the level as it ran: also where each run began, so a change inside a half-hour sits where it happened
  const levelPts = pts("level").concat(exact ? runs.filter((r) => r.level !== null).map((r) => ({ h: h(r.a), y: r.level })) : []).sort((p, q) => p.h - q.h);
  const stepPts = (key) => raw.map((p) => ({ a: h(v2Ms(p.t)), b: h(v2Ms(p.t) + stepMs), y: toNumber(p[key]), slot: p.slot === true })).filter((s) => s.y !== null);
  const sentCount = raw.filter(sentOf).length;
  const control = !raw.length ? "forecast" : a.in_control === "v1" || a.in_control === "v2" || a.in_control === "mixed" ? a.in_control : (sentCount === 0 ? "v1" : sentCount === raw.length ? "v2" : "mixed");
  const profile = typeof a.profile === "string" ? a.profile : "";
  const previewOnly = raw.length > 0 && sentCount === 0;
  const partlyPreview = sentCount > 0 && sentCount < raw.length;
  const changes = (Array.isArray(a.changes) ? a.changes : []).filter((c) => c && v2Ms(c.at) !== null).sort((a, b) => v2Ms(b.at) - v2Ms(a.at)).map((c) => ({
    at: v2Hm(c.at), mode: v2Mode(c.mode).name, modeKey: c.mode || "none", reason: c.reason ? String(c.reason) : "",
  }));
  const priceMax = Math.max(10, Math.ceil(Math.max(0, ...stepPts("import_p").map((s) => s.y), ...stepPts("export_p").map((s) => s.y), ...stepPts("buy_p").map((s) => s.y), ...stepPts("sell_p").map((s) => s.y), ...pts("value_p").map((p) => p.y || 0),
    ...(fcOn ? o.forecast.steps.map((q) => Math.max(q.importP || 0, q.exportP || 0)).concat(o.forecast.value.map((q) => Math.min(q.y || 0, 60))) : [])) / 5) * 5);
  const energy = raw.map((p) => ({ a: h(v2Ms(p.t)), b: h(v2Ms(p.t) + stepMs), inK: toNumber(p.grid_in), outK: toNumber(p.grid_out), car: toNumber(p.car) }))
    .filter((e) => e.inK !== null || e.outK !== null || e.car !== null);
  const energyMax = Math.max(1, Math.ceil(Math.max(0, ...energy.map((e) => Math.max(e.inK || 0, e.outK || 0, e.car || 0))) * 2) / 2);
  const replans = (Array.isArray(a.replans) ? a.replans : []).map(v2Ms).filter((m) => m !== null && m >= t0 && m <= t0 + spanH * HOUR_MS).map(h);
  const valueVals = pts("value_p").filter((p) => p.y !== null).map((p) => p.y);
  // what the timeline expects from now on, in hours from the left edge and inside this day
  const nowInDay = fcOn && Number.isFinite(nowMs) && nowMs >= dayB.a && nowMs < dayB.b && date === a.latest;
  const nowH = nowInDay ? h(nowMs) : null;
  const fromH = nowInDay ? nowH : 0;
  const fcClip = (x) => ({ a: Math.max(fromH, h(x.a)), b: Math.min(spanH, h(x.b)) });
  const sunClip = (arr) => {                         // points come in pairs (a step's start and end): keep the part inside, still in pairs
    const out = [];
    for (let i = 0; i + 1 < (arr || []).length; i += 2) {
      const a0 = h(arr[i].ms), b0 = h(arr[i + 1].ms), kw = arr[i].kw;
      if (kw === null || b0 <= fromH + 1e-9 || a0 >= spanH - 1e-9) continue;
      out.push({ h: Math.max(a0, fromH), kw }, { h: Math.min(b0, spanH), kw });
    }
    return out;
  };
  const fcLine = (arr) => (arr || []).map((q) => ({ h: h(q.ms), y: q.y })).filter((q) => q.y !== null && q.h >= fromH - 0.26 && q.h <= spanH + 1e-9);
  const forecast = fcOn ? {
    bands: o.forecast.bands.map((b) => Object.assign({}, b, fcClip(b))).filter((b) => b.b > b.a),
    steps: o.forecast.steps.map((q) => Object.assign({}, q, fcClip(q))).filter((q) => q.b > q.a),
    mid: fcLine(o.forecast.mid), low: fcLine(o.forecast.low), high: fcLine(o.forecast.high), value: fcLine(o.forecast.value),
    sun: o.forecast.sun ? { mid: sunClip(o.forecast.sun.mid), low: sunClip(o.forecast.sun.low), high: sunClip(o.forecast.sun.high), house: sunClip(o.forecast.sun.house) } : null,
  } : null;
  const flowsRaw = o.flows && o.flows.date === date && Array.isArray(o.flows.m) && v2Ms(o.flows.start) !== null ? o.flows : null;
  const flows = flowsRaw ? flowsRaw.m.map((m, i) => {
    const w = (toNumber(flowsRaw.bucket) || 5) * 60000;
    return { a: h(v2Ms(flowsRaw.start) + m * 60000), b: h(v2Ms(flowsRaw.start) + m * 60000 + w), inK: toNumber((flowsRaw.imp || [])[i]) || 0, outK: toNumber((flowsRaw.exp || [])[i]) || 0, car: toNumber((flowsRaw.car || [])[i]) || 0 };
  }) : null;
  const flowKw = (key) => flowsRaw && Array.isArray(flowsRaw[key]) ? flows.map((e, i) => ({ a: e.a, b: e.b, kw: (toNumber(flowsRaw[key][i]) || 0) * 2 })) : null;
  const sunActual = flowKw("sun"), houseActual = flowKw("house");
  const sunTop = Math.max(0, ...(sunActual || []).map((e) => e.kw), ...(houseActual || []).map((e) => e.kw),
    ...(forecast && forecast.sun ? forecast.sun.high.concat(forecast.sun.mid, forecast.sun.house).map((q) => q.kw) : []));
  const sunMax = Math.max(1, Math.ceil(sunTop * 2) / 2);
  const hasSun = sunTop > 0;
  const flowMax = flows ? Math.max(1, Math.ceil(Math.max(0, ...flows.map((e) => Math.max(e.inK, e.outK, e.car))) * 2) / 2) : 0;
  return Object.assign(base, {
    kind: "ok", t0, spanH, stepMs, floor, viewH: nowH === null ? 0 : Math.max(0, nowH - VIEW_PAST_HOURS), bands, previewOnly, partlyPreview, control, profile, forecast, nowH, flows, flowMax,
    sunActual, houseActual, sunMax, hasSun, dayEndH: fcOn ? h(dayB.b) : null,
    banner: !raw.length ? "Forecast: what engine v2 expects from its current plan. Nothing has happened on this day yet."
      : previewOnly ? "Preview only: engine v1 was in control on this day, so nothing was sent to the inverter. The modes below are what engine v2 would have chosen."
      : partlyPreview ? "Control changed during the day: the dimmed bands are where engine v2 was only previewing." : "",
    level: runsOf(levelPts), exact, value: runsOf(pts("value_p")),
    importSteps: stepPts("import_p"), exportSteps: stepPts("export_p"), priceMax, buySteps: stepPts("buy_p"), sellSteps: stepPts("sell_p"),
    energy, energyMax: flows ? Math.max(energyMax, flowMax) : energyMax, replans,
    valueMax: valueVals.length ? Math.max(...valueVals) : 0,
    changes, ticks: (() => {
      const out = []; const d = new Date(t0); d.setMinutes(0, 0, 0);
      while (d.getTime() < t0 || d.getHours() % 3 !== 0) d.setTime(d.getTime() + HOUR_MS);
      for (let t = d.getTime(); t <= t0 + spanH * HOUR_MS + 1; t += 3 * HOUR_MS) out.push({ h: h(t), ms: t, day: t > t0 + 1 && new Date(t).getHours() === 0 });
      return out;
    })(),
  });
}

/** The numbers at `hour` for the labels drawn where the cursor meets each line: {future, battery, low, high, importP, value,
 *  grid ({kind: "import"|"export"|"quiet", kw}), car, sun, sunLow, sunHigh, house}; each null when there is none. Pure. */
function v2HistoryProbe(v, hour) {
  if (!v || v.kind !== "ok" || !Number.isFinite(hour) || hour < 0 || hour > v.spanH) return null;
  const future = v.nowH !== null && v.nowH !== undefined && hour >= v.nowH;
  const fc = v.forecast;
  const pts = (arr) => (arr || []).map((q) => ({ h: q.h, level: q.y }));
  const ran = levelAtHour(pts([].concat(...(v.level || []))), hour);
  const planned = future && fc ? levelAtHour(pts(fc.mid), hour) : null;
  const out = { future, battery: planned !== null ? planned : ran, low: null, high: null, importP: null, value: null, grid: null, car: null, sun: null, sunLow: null, sunHigh: null, house: null };
  if (planned !== null) { out.low = levelAtHour(pts(fc.low), hour); out.high = levelAtHour(pts(fc.high), hour); }
  const step = v.importSteps.find((q) => hour >= q.a && hour < q.b) || (fc ? fc.steps.find((q) => hour >= q.a && hour < q.b) : null);
  if (step) out.importP = step.importP !== undefined ? step.importP : step.y;
  const vals = future && fc ? fc.value : [].concat(...(v.value || [])).map((q) => ({ h: q.h, y: q.y }));
  const near = vals.length ? vals.reduce((m, q) => (Math.abs(q.h - hour) < Math.abs(m.h - hour) ? q : m)) : null;
  if (near && Math.abs(near.h - hour) <= 1) out.value = near.y;
  const flow = v.flows ? v.flows.find((e) => hour >= e.a && hour < e.b) : null;
  const e = flow || v.energy.find((q) => hour >= q.a && hour < q.b);
  if (e && !(future && !flow)) {
    const kwOf = (k) => k * 2;                               // kWh per half-hour -> kW
    const inK = e.inK || 0, outK = e.outK || 0;
    out.grid = inK > 0 ? { kind: "import", kw: kwOf(inK) } : outK > 0 ? { kind: "export", kw: kwOf(outK) } : { kind: "quiet", kw: 0 };
    if (e.car) out.car = kwOf(e.car);
  }
  const stepAt = (arr) => (arr || []).find((q) => hour >= q.a && hour < q.b);
  if (future && fc && fc.sun) {
    const at = (arr) => { for (let i = 0; i + 1 < (arr || []).length; i += 2) if (hour >= arr[i].h - 1e-9 && hour <= arr[i + 1].h + 1e-9) return arr[i].kw; return null; };
    out.sun = at(fc.sun.mid); out.sunLow = at(fc.sun.low); out.sunHigh = at(fc.sun.high); out.house = at(fc.sun.house);
  } else if (!future) {
    const sa = stepAt(v.sunActual), ha = stepAt(v.houseActual);
    out.sun = sa ? sa.kw : null; out.house = ha ? ha.kw : null;
  }
  return out;
}

/** What the history chart says at `hour` (hours from its left edge), for the readout under it: lines, the first being the time.
 *  `clock(hour)` gives "HH:MM". Pure. */
function v2HistoryReadout(v, hour, clock) {
  if (!v || v.kind !== "ok" || !Number.isFinite(hour) || hour < 0 || hour > v.spanH) return [];
  const future = v.nowH !== null && v.nowH !== undefined && hour >= v.nowH;
  const lines = [`${clock(hour)}${future ? " (expected)" : v.nowH !== null && v.nowH !== undefined ? " (happened)" : ""}`];
  const ran = v.bands.find((b) => hour >= b.a && hour < b.b);
  const plan = (v.forecast ? v.forecast.bands : []).find((b) => hour >= b.a && hour < b.b);
  const band = future ? (plan || ran) : (ran || plan);
  if (band) {
    const isPlan = band === plan && band !== ran;
    lines.push(`${v2Mode(band.mode).name} (${isPlan ? "expected" : "happened"}${band.preview ? ", only previewed" : ""}, ${clock(band.a)} to ${clock(band.b)})`);
    if (isPlan && band.until) lines.push(`Until: ${band.until}`);
    if (isPlan && band.reason) lines.push(band.reason);
  }
  const pts = (runs) => [].concat(...(runs || [])).map((q) => ({ h: q.h, level: q.y }));
  const ranLevel = levelAtHour(pts(v.level), hour);
  const planMid = v.forecast ? v.forecast.mid.map((q) => ({ h: q.h, level: q.y })) : [];
  const planLevel = future ? levelAtHour(planMid, hour) : null;
  if (planLevel !== null) {
    const lo = levelAtHour(v.forecast.low.map((q) => ({ h: q.h, level: q.y })), hour), hi = levelAtHour(v.forecast.high.map((q) => ({ h: q.h, level: q.y })), hour);
    lines.push(`Battery: ${v2Pct(planLevel)}${lo !== null && hi !== null ? ` (likely ${v2Pct(lo)} to ${v2Pct(hi)})` : ""}`);
  } else if (ranLevel !== null) lines.push(`Battery: ${v2Pct(ranLevel)}`);
  const step = (v.importSteps.find((q) => hour >= q.a && hour < q.b)) || (v.forecast ? v.forecast.steps.find((q) => hour >= q.a && hour < q.b) : null);
  if (step) {
    const imp = step.importP !== undefined ? step.importP : step.y;
    const tag = step.event ? ", grid event" : step.free ? ", free power" : step.slot ? ", smart slot" : step.estimated ? ", estimated" : "";
    lines.push(`Import price: ${v2P(imp, 2)}${tag}`);
  }
  const ex = v.exportSteps.find((q) => hour >= q.a && hour < q.b) || (v.forecast ? v.forecast.steps.find((q) => hour >= q.a && hour < q.b) : null);
  const exportP = ex ? (ex.exportP !== undefined ? ex.exportP : ex.y) : null;
  if (exportP !== null && exportP !== undefined) lines.push(`Export price: ${v2P(exportP, 2)}`);
  const vals = (future && v.forecast ? v.forecast.value : [].concat(...(v.value || [])).map((q) => ({ h: q.h, y: q.y })));
  const near = vals.length ? vals.reduce((m, q) => (Math.abs(q.h - hour) < Math.abs(m.h - hour) ? q : m)) : null;
  if (near && Math.abs(near.h - hour) <= 1) lines.push(`Value of a stored kWh: ${v2P(near.y, 1)}${future ? " (expected)" : ""}`);
  const kw = (k) => `${(Math.round(k * 20) / 10).toFixed(1)} kW`;           // kWh per half-hour at that power -> kW
  const kwAt = (pts) => { for (let i = 0; i + 1 < (pts || []).length; i += 2) if (hour >= pts[i].h - 1e-9 && hour <= pts[i + 1].h + 1e-9) return pts[i].kw; return null; };   // points come in pairs: a step's start and end
  const fs = future && v.forecast ? v.forecast.sun : null;
  if (fs) {
    const sm = kwAt(fs.mid), sl = kwAt(fs.low), sh = kwAt(fs.high), hs = kwAt(fs.house);
    const k1 = (x) => `${x.toFixed(1)} kW`;
    if (sm !== null) lines.push(`Sun expected: ${k1(sm)}${sl !== null && sh !== null ? ` (${k1(sl)} to ${k1(sh)})` : ""}`);
    if (hs !== null) lines.push(`House use expected: ${k1(hs)}`);
  } else if (!future) {
    const at = (arr) => (arr || []).find((e) => hour >= e.a && hour < e.b);
    const sa = at(v.sunActual), ha = at(v.houseActual);
    if (sa) lines.push(`Sun: ${sa.kw.toFixed(1)} kW`);
    if (ha) lines.push(`House: ${ha.kw.toFixed(1)} kW`);
  }
  const flow = v.flows ? v.flows.find((e) => hour >= e.a && hour < e.b) : null;
  if (flow) {
    lines.push(flow.inK > 0 ? `Grid: importing ${kw(flow.inK)}` : flow.outK > 0 ? `Grid: exporting ${kw(flow.outK)}` : "Grid: quiet");
    if (flow.car > 0) lines.push(`Car: charging at ${kw(flow.car)}`);
  } else {
    const e = v.energy.find((q) => hour >= q.a && hour < q.b);
    if (e) {
      lines.push(`Grid in this half-hour: ${e.inK === null ? "–" : `${e.inK.toFixed(2)} kWh in`}${e.outK ? `, ${e.outK.toFixed(2)} kWh out` : ""}`);
      if (e.car) lines.push(`Car in this half-hour: ${e.car.toFixed(2)} kWh`);
    }
  }
  return lines;
}

class PowerEngineV2HistoryCard extends PowerEngineV2Card {
  setConfig(config) {
    super.setConfig(Object.assign({ entity: V2_HISTORY_SENSOR }, config || {}));
    this._built = false;
  }
  getCardSize() { return 9; }
  _ids() { return [(this._config && this._config.entity) || V2_HISTORY_SENSOR, V2_TIMELINE, V2_FLOWS]; }
  _entity() { return (this._config && this._config.entity) || V2_HISTORY_SENSOR; }
  _attrs(states) { return v2Attrs(states, this._entity()); }
  // the line for now moves between the app's publishes: redraw once a minute
  connectedCallback() {
    if (!this._timer && typeof setInterval !== "undefined") this._timer = setInterval(() => this._draw(), 60000);
    if (!this._resize && typeof ResizeObserver !== "undefined") {              // a box built before it had a width, or a new width: draw again
      this._resize = new ResizeObserver(() => {
        const w = Math.round(this.clientWidth || 0);
        if (!w || (!this._stale && Math.abs(w - (this._drawnWidth || 0)) < 3)) return;
        clearTimeout(this._resizeTimer);
        this._resizeTimer = setTimeout(() => this._draw(), 150);
      });
      this._resize.observe(this);
    }
  }
  disconnectedCallback() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    clearTimeout(this._resizeTimer); clearTimeout(this._idleTimer);
    if (this._resize) { this._resize.disconnect(); this._resize = null; }
  }
  /** Is the person's finger on the chart, or is it still scrolling? A redraw replaces the scroll box, which would cancel the gesture,
   *  so one that arrives meanwhile waits until they let go (as the plan chart does). */
  _busy() { return (!!this._touching && Date.now() - (this._touchAt || 0) < 10000) || Date.now() - (this._lastScrollAt || 0) < 700; }
  _armIdle() {
    clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => {
      if (this._busy()) { this._armIdle(); return; }
      if (this._pendingDraw) this._draw();
    }, 800);
  }
  /** The day to draw: what the app published, or (for a day after today, which it has no record of) that day with no
   *  records, drawn from the timeline's forecast alone. Also the last day the forecast reaches, for the arrows. */
  _model(states) {
    const base = this._attrs(states);
    const tl = v2Attrs(states, V2_TIMELINE);
    const fc = v2Forecast(tl, Date.now());
    this._lastDay = v2ForecastLastDay(fc);
    let attrs = base;
    if (base && this._local && base.latest && this._local > base.latest && this._lastDay && this._local <= this._lastDay) {
      attrs = Object.assign({}, base, { date: this._local, series: [], runs: [], runs_until: null, changes: [], replans: [] });
    } else this._local = null;
    return { attrs, base, fc, flows: v2Attrs(states, V2_FLOWS), floor: tl ? toNumber(tl.floor_soc) : null };
  }
  /** The last day the arrows and the date box reach: the forecast's, when it reaches past today. */
  _latest(a) { return this._lastDay && a.latest && this._lastDay > a.latest ? this._lastDay : a.latest; }
  _admin() { return !!(this._hass && this._hass.user && this._hass.user.is_admin); }
  _draw() {
    if (this.shadowRoot && this.shadowRoot.activeElement && this.shadowRoot.activeElement.tagName === "INPUT") return;   // don't close the date box
    if (this._busy()) { this._pendingDraw = true; this._armIdle(); return; }
    this._pendingDraw = false;
    super._draw();
  }
  _day(a) {
    if (this._local) return this._local;
    if (this._pending && Date.now() - this._pending.at < 8000 && (a || {}).date !== this._pending.day) return this._pending.day;
    this._pending = null;
    return (a || {}).date;
  }
  async _pick(value) {
    const a = this._attrs((this._hass || {}).states) || {};
    if (a.latest && this._lastDay && ISO_DAY.test(value || "") && value > a.latest && value <= this._lastDay) {   // a coming day: nothing to ask the app for
      this._local = value; this._sig = null; this._draw();
      return;
    }
    this._local = null;
    const payload = v2HistoryDayPayload(value, a.earliest, a.latest);
    const note = this.shadowRoot.querySelector(".pick-note");
    if (!payload) { if (note) note.textContent = "That day isn't available."; return; }
    this._pending = { day: value, at: Date.now() };
    this._sig = null; this._draw();
    try { await this._hass.callWS(payload); }
    catch (err) { this._pending = null; this._sig = null; this._draw(); const n = this.shadowRoot.querySelector(".pick-note"); if (n) n.textContent = `Couldn't change the day (${err && err.message ? err.message : err}).`; }
  }
  _pickerHtml(a) {
    const admin = this._admin();
    const day = this._day(a) || "";
    const latest = this._latest(a);
    const prev = shiftHistoryDay(day, -1, a.earliest, latest), next = shiftHistoryDay(day, 1, a.earliest, latest);
    const why = admin ? "" : "Only an admin user can change the day here";
    return `<div class="picker"><button class="prev" type="button" aria-label="Previous day" ${!admin || !prev ? "disabled" : ""} title="${escHtml(why || "Previous day")}">&#8249;</button>
      <input type="date" aria-label="Day" value="${escHtml(day)}" min="${escHtml(a.earliest || "")}" max="${escHtml(latest || "")}" ${admin ? "" : "disabled"} title="${escHtml(why || "Show this day")}">
      <button class="next" type="button" aria-label="Next day" ${!admin || !next ? "disabled" : ""} title="${escHtml(why || "Next day")}">&#8250;</button><span class="muted small pick-note"></span></div>`;
  }
  _wirePicker(a) {
    const root = this.shadowRoot;
    const input = root.querySelector(".picker input");
    if (!input) return;
    input.addEventListener("change", (ev) => this._pick(ev.target.value));
    const day = this._day(a);
    root.querySelector(".prev").addEventListener("click", () => { const n = shiftHistoryDay(day, -1, a.earliest, this._latest(a)); if (n) this._pick(n); });
    root.querySelector(".next").addEventListener("click", () => { const n = shiftHistoryDay(day, 1, a.earliest, this._latest(a)); if (n) this._pick(n); });
  }
  _render(states) {
    const m = this._model(states);
    const attrs = m.attrs;
    const v = v2HistoryView(attrs, Date.now(), { forecast: m.fc, flows: m.flows, floor: m.floor });
    this._v = v;
    const css = `<style>${V2_CSS}
      .readout { font-size: 13px; min-height: 1.5em; margin: 0; color: var(--secondary-text-color); }
      .picker { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
      .picker input, .picker button { font: inherit; color: var(--primary-text-color); background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 6px; }
      .picker input { padding: 6px 8px; }
      .picker button { min-width: 40px; padding: 6px 10px; font-size: 1.2em; line-height: 1; cursor: pointer; }
      .picker button:disabled, .picker input:disabled { opacity: .45; cursor: default; }
      .changes { display: grid; grid-template-columns: auto auto minmax(0, 1fr); gap: 4px 12px; font-size: 13px; align-items: baseline; max-height: 320px; overflow-y: auto; }
      .changes .mode { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
      .hbox { position: relative; display: grid; grid-template-columns: 44px minmax(0, 1fr); align-items: start; min-width: 0; }
      .hbox svg { display: block; }
      .hscroll { overflow-x: auto; overflow-y: hidden; overscroll-behavior-x: contain; min-width: 0; }
      .hbox .over { position: absolute; left: 44px; top: 0; pointer-events: none; }
      @media (max-width: 520px) { .changes { grid-template-columns: auto minmax(0, 1fr); } .changes .num { grid-column: 1 / -1; margin-top: 4px; } }
    </style>`;
    if (v.kind === "none") { this._message("Engine v2 history", v.text); return; }
    // read where the person has the chart now, from the live box, before it is replaced
    if (this._hDate !== v.date) { this._hUser = false; this._hLeftMs = null; this._hGeom = null; }
    const prevBox = this.shadowRoot.getElementById("h-scroll");
    const intent = prevBox ? scrollIntent({ placed: prevBox._placed === true, laidOut: prevBox.clientWidth > 0, scrollLeft: prevBox.scrollLeft }, this._hGeom) : null;
    if (intent) { this._hUser = intent.user; this._hLeftMs = intent.leftMs; }
    this._hDate = v.date;
    this._stale = false;
    const legend = (items) => items.map(([c, t]) => `<span><i class="sw" style="background:${c}"></i>${escHtml(t)}</span>`).join("");
    const modesUsed = Array.from(new Set(v.bands.map((b) => b.mode)));
    this.shadowRoot.innerHTML = `${css}<ha-card>
      <div class="head"><span class="title">Engine v2 history${v.date ? ` · ${escHtml(v.date)}` : ""}</span>${v.kind === "ok" ? chartKeyHtml(["value", "buy", "sell", "energy", "sun", "replan"], this._hidden("history")) : ""}${v.kind === "ok" ? `<span class="chip${v.control === "v2" ? "" : " muted"}">${v.control === "v2" ? `Engine v2 in control${v.profile ? ` · ${escHtml(v.profile)}` : ""}` : v.control === "forecast" ? "Forecast" : v.control === "v1" ? "Preview" : "Control changed"}</span>` : ""}</div>
      ${this._pickerHtml(attrs || {})}
      ${v.kind === "empty" ? `<p class="muted">${escHtml(v.text)}</p>` : `${v.banner ? `<p class="preview-line" role="note">${escHtml(v.banner)}</p>` : ""}
      <div class="hbox" id="hist"><svg id="h-axl" aria-hidden="true"></svg><div class="hscroll" id="h-scroll"><div id="h-plot"></div></div><svg id="h-axt" class="over" aria-hidden="true"></svg></div>
      <p class="readout" id="hread" aria-live="polite">${escHtml(V2_HISTORY_HINT)}</p>
      <div class="legend">${legend(modesUsed.concat(v.forecast ? v.forecast.bands.map((b) => b.mode) : []).filter((k, i, all) => all.indexOf(k) === i).map((k) => [`var(${v2Mode(k).colour})`, v2Mode(k).name]).concat([["var(--v2-battery)", "Battery as it ran"], ["var(--v2-price)", "Import price (dashed: smart slot)"], ["var(--m-export)", "Export price (dotted)"]]).concat(v.forecast ? [["var(--v2-battery)", "From now: dotted battery line (shaded: likely range), paler bands: what the plan expects"]] : []).concat(v.hasSun ? [["var(--v2-sun)", "Sun"], ["var(--v2-house)", "House use (dashed: expected)"]] : []))}</div>
      ${v.partlyPreview || v.previewOnly ? '<p class="muted small">Dimmed bands: engine v2 was only previewing, nothing was sent.</p>' : ""}
      ${v.changes.length ? `<div><h3>Mode changes (newest first)</h3><div class="changes">${v.changes.map((c) => `<span class="num muted">${escHtml(c.at)}</span><span class="mode"><i class="sw" style="background:var(${v2Mode(c.modeKey).colour})"></i>${escHtml(c.mode)}</span><span>${escHtml(c.reason)}</span>`).join("")}</div></div>` : '<p class="muted small">No mode changes recorded for this day.</p>'}
      ${v.note ? `<p class="muted small">${escHtml(v.note)}</p>` : ""}`}
    </ha-card>`;
    this._wirePicker(attrs || {});
    this._wireKey("history");
    if (v.kind === "ok") this._whenLaidOut(() => this._drawChart(this.shadowRoot.getElementById("h-plot"), v), "h-scroll");
  }
  /** The chart in pixels: as wide as its hours need (the hours in view fill the box, what came before lies to the left, reached by
   *  scrolling), with the scales in a fixed strip on the left so they stay in view while it scrolls, as the plan chart does. */
  _drawChart(host, v) {
    const G = { T: 12, bandH: 26 };
    const root = this.shadowRoot, scroller = root.getElementById("h-scroll"), axl = root.getElementById("h-axl"), axt = root.getElementById("h-axt");
    const boxW = Math.max(260, scroller.clientWidth || 620);
    const pph = Math.max(boxW, TIMELINE_MIN_W) / Math.max(1, v.spanH - v.viewH);       // pixels per hour; never so tight the hour labels collide (a phone scrolls)
    const plotW = Math.max(boxW, v.spanH * pph);
    this._drawnWidth = Math.round(this.clientWidth || 0);
    const topY = G.T + G.bandH + 10, levelH = 150, gap = 34, priceTop = topY + levelH + gap, priceH = 110;
    const hid = this._hidden("history");
    const flowBars = v.flows && v.flows.length ? v.flows : null;
    const hasE = (flowBars || v.energy.length > 0) && !hid.has("energy"), enTop = priceTop + priceH + 34, enH = 80;
    const hasS = v.hasSun && !hid.has("sun"), sunTop = (hasE ? enTop + enH : priceTop + priceH) + 34, sunH = 80;
    const H = (hasS ? sunTop + sunH : hasE ? enTop + enH : priceTop + priceH) + 28;
    const svg = svgEl("svg", { width: plotW, height: H, viewBox: `0 0 ${plotW} ${H}`, role: "img", "aria-label": "Engine v2's modes, battery level, prices and the value of a stored kWh over the day" }, host);
    axl.setAttribute("width", 44); axl.setAttribute("height", H);                    // the scales (left) and the panel titles (over the plot) do not scroll
    axt.setAttribute("width", boxW); axt.setAttribute("height", H);
    const x = (hr) => hr * pph;
    const title = (y, text) => svgEl("text", { x: 6, y, "font-size": 11, fill: "var(--secondary-text-color)" }, axt, text);
    const fl = v.floor;
    const yL = (p) => topY + (1 - (Math.max(p, fl) - fl) / (100 - fl)) * levelH;           // the scale starts at the battery's lowest level
    const yP = (p) => priceTop + (1 - Math.min(p, v.priceMax) / v.priceMax) * priceH;
    const muted = "var(--secondary-text-color)";
    // each chart on a lightly shaded ground with a darker base line, so the separate charts read as separate
    const ground = (top, h) => {
      [[svg, plotW], [axl, 44]].forEach(([t, w]) => {
        svgEl("rect", { x: 0, y: top, width: w, height: h, fill: "var(--primary-text-color)", "fill-opacity": 0.045 }, t);
        svgEl("line", { x1: 0, x2: w, y1: top + h, y2: top + h, stroke: "var(--secondary-text-color)", "stroke-opacity": 0.55 }, t);
      });
    };
    ground(topY, levelH); ground(priceTop, priceH);
    if (hasE) ground(enTop, enH);
    if (hasS) ground(sunTop, sunH);
    for (const p of [fl].concat([25, 50, 75, 100].filter((q) => q - fl >= 8))) {
      svgEl("line", { x1: 0, x2: plotW, y1: yL(p), y2: yL(p), stroke: "var(--divider-color)" }, svg);
      svgEl("text", { x: 38, y: yL(p) + 4, "text-anchor": "end", "font-size": 11, fill: muted }, axl, `${Math.round(p)}%`);
    }
    for (let p = 0; p <= v.priceMax; p += v.priceMax <= 20 ? 5 : 10) {
      svgEl("line", { x1: 0, x2: plotW, y1: yP(p), y2: yP(p), stroke: "var(--divider-color)", "stroke-opacity": 0.6 }, svg);
      svgEl("text", { x: 38, y: yP(p) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--v2-price)" }, axl, `${p}p`);
    }
    v.ticks.forEach((t) => {
      const label = v2Hm(new Date(t.ms).toISOString());
      if (x(t.h) > 6 && x(t.h) < plotW - 6) svgEl("text", { x: x(t.h), y: H - 8, "text-anchor": "middle", "font-size": 11, fill: muted }, svg, t.day ? `${new Date(t.ms).toLocaleDateString("en-GB", { weekday: "short" })} ${label}` : label);
      svgEl("line", { x1: x(t.h), x2: x(t.h), y1: topY, y2: topY + levelH, stroke: "var(--divider-color)", "stroke-opacity": 0.5 }, svg);
    });
    v.bands.forEach((b) => {
      const col = `var(${v2Mode(b.mode).colour})`;
      svgEl("rect", { x: x(b.a), y: G.T, width: Math.max(0, x(b.b) - x(b.a) - 1), height: G.bandH, rx: 4, fill: col, "fill-opacity": b.preview ? 0.4 : 1 }, svg);
      const label = bandLabel({ mode: b.mode, until: "" }, x(b.b) - x(b.a)) + (b.preview && x(b.b) - x(b.a) > 120 ? " · Preview" : "");
      if (label) svgEl("text", { x: x(b.a) + 6, y: G.T + 17, "font-size": 11.5, "font-weight": 500, fill: b.preview ? "var(--primary-text-color)" : "#fff" }, svg, label);
    });
    const fcl = v.forecast;
    (fcl ? fcl.bands : []).forEach((b) => {                // what the plan expects: paler, with a dashed edge
      const col = `var(${v2Mode(b.mode).colour})`;
      svgEl("rect", { x: x(b.a), y: G.T, width: Math.max(0, x(b.b) - x(b.a) - 1), height: G.bandH, rx: 4, fill: col, "fill-opacity": 0.3, stroke: col, "stroke-dasharray": "4 3" }, svg);
      const label = bandLabel({ mode: b.mode, until: "" }, x(b.b) - x(b.a));
      if (label) svgEl("text", { x: x(b.a) + 6, y: G.T + 17, "font-size": 11.5, "font-weight": 500, fill: "var(--primary-text-color)" }, svg, label);
    });
    const line = (runs, f, attrs) => runs.forEach((r) => {
      if (r.length > 1) svgEl("polyline", Object.assign({ points: r.map((p) => `${x(p.h)},${f(p.y)}`).join(" "), fill: "none" }, attrs), svg);
      else svgEl("circle", { cx: x(r[0].h), cy: f(r[0].y), r: 2.5, fill: attrs.stroke }, svg);
    });
    if (fcl && fcl.low.length > 1 && fcl.high.length > 1) {   // the likely range from now on
      const edge = fcl.high.map((p) => `${x(p.h)},${yL(p.y)}`).concat(fcl.low.slice().reverse().map((p) => `${x(p.h)},${yL(p.y)}`)).join(" ");
      svgEl("polygon", { points: edge, fill: "var(--v2-battery)", "fill-opacity": 0.14 }, svg);
    }
    if (fcl && fcl.mid.length) line([fcl.mid], yL, { stroke: "var(--v2-battery)", "stroke-width": 2.5, "stroke-dasharray": "2 3" });
    line(v.level, yL, { stroke: "var(--v2-battery)", "stroke-width": 2.5 });
    v.importSteps.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: yP(s.y), y2: yP(s.y), stroke: "var(--v2-price)", "stroke-width": 2, "stroke-dasharray": s.slot ? "5 3" : "none" }, svg));   // a smart slot stays dashed once it has run
    v.exportSteps.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: yP(s.y), y2: yP(s.y), stroke: "var(--m-export)", "stroke-width": 1.5, "stroke-dasharray": "2 3" }, svg));
    const dotted = (steps, col) => steps.forEach((s) => svgEl("line", { x1: x(s.a), x2: x(s.b), y1: yP(s.y), y2: yP(s.y), stroke: col, "stroke-width": 1.3, "stroke-opacity": 0.7, "stroke-dasharray": "1 3" }, svg));
    (fcl ? fcl.steps : []).forEach((q) => {
      svgEl("line", { x1: x(q.a), x2: x(q.b), y1: yP(q.importP), y2: yP(q.importP), stroke: "var(--v2-price)", "stroke-width": 2, "stroke-opacity": 0.7, "stroke-dasharray": q.slot || q.estimated ? "5 3" : "none" }, svg);
      if (q.exportP !== null && q.exportP !== undefined) svgEl("line", { x1: x(q.a), x2: x(q.b), y1: yP(q.exportP), y2: yP(q.exportP), stroke: "var(--m-export)", "stroke-width": 1.5, "stroke-opacity": 0.7, "stroke-dasharray": "2 3" }, svg);
    });
    if (!hid.has("buy")) dotted(v.buySteps, "var(--v2-buy)");
    if (!hid.has("sell")) dotted(v.sellSteps, "var(--v2-sell)");
    if (!hid.has("value")) {
      line(v.value, yP, { stroke: "var(--v2-good)", "stroke-width": 2.2 });
      if (fcl && fcl.value.length) line([fcl.value], yP, { stroke: "var(--v2-good)", "stroke-width": 2.2, "stroke-dasharray": "6 3", "stroke-opacity": 0.8 });
    }
    title(priceTop - 8, "Prices (dashed: smart slot), buy and sell lines and the value of a stored kWh (pence per kWh)");
    const mid = enTop + enH / 2, yE = (k) => (k / v.energyMax) * (enH / 2 - 4);
    const yS = (kw) => sunTop + sunH - (kw / v.sunMax) * sunH;
    if (hasE) {                                          // grid energy per half-hour: import up, export down; the car outlined (it is part of the load)
      svgEl("line", { x1: 0, x2: plotW, y1: mid, y2: mid, stroke: "var(--divider-color)" }, svg);
      svgEl("text", { x: 38, y: mid + 4, "text-anchor": "end", "font-size": 11, fill: muted }, axl, "0");
      svgEl("text", { x: 38, y: mid - yE(v.energyMax) + 4, "text-anchor": "end", "font-size": 11, fill: muted }, axl, `${v.energyMax}`);
      title(enTop - 8, flowBars ? "Grid power as it happened, as kWh per half-hour: import up, export down; grey: the car" : "Grid energy per half-hour (kWh): import up, export down; outline: the car");
      (flowBars || v.energy).forEach((e) => {
        const inset = flowBars ? 0 : 1;
        const bx = x(e.a) + inset, bw = Math.max(1, x(e.b) - x(e.a) - 2 * inset);
        if (e.inK) svgEl("rect", { x: bx, y: mid - yE(e.inK), width: bw, height: yE(e.inK), fill: "var(--v2-price)", "fill-opacity": 0.75 }, svg);
        if (e.outK) svgEl("rect", { x: bx, y: mid, width: bw, height: yE(e.outK), fill: "var(--m-export)", "fill-opacity": 0.75 }, svg);
        if (e.car && flowBars) svgEl("rect", { x: bx, y: mid - yE(e.car), width: bw, height: yE(e.car), fill: "var(--primary-text-color)", "fill-opacity": 0.28 }, svg);
        else if (e.car) svgEl("rect", { x: bx + 1, y: mid - yE(e.car), width: Math.max(1, bw - 2), height: yE(e.car), fill: "none", stroke: "var(--primary-text-color)", "stroke-width": 1.2, "stroke-opacity": 0.8 }, svg);
      });
    }
    if (hasS) {                                          // the sun and the house in kW: as they happened, then as expected (paler, dashed)
      const fs = v.forecast ? v.forecast.sun : null;
      svgEl("line", { x1: 0, x2: plotW, y1: sunTop + sunH, y2: sunTop + sunH, stroke: "var(--divider-color)" }, svg);
      svgEl("text", { x: 38, y: sunTop + sunH + 4, "text-anchor": "end", "font-size": 11, fill: muted }, axl, "0");
      svgEl("text", { x: 38, y: sunTop + 4, "text-anchor": "end", "font-size": 11, fill: muted }, axl, `${v.sunMax} kW`);
      title(sunTop - 8, fs ? "Sun and house (kW): as they happened, then expected (dashed)" : "Sun and house (kW), as they happened");
      (v.sunActual || []).forEach((e) => { if (e.kw > 0) svgEl("rect", { x: x(e.a), y: yS(e.kw), width: Math.max(1, x(e.b) - x(e.a)), height: sunTop + sunH - yS(e.kw), fill: "var(--v2-sun)", "fill-opacity": 0.45 }, svg); });
      const runs = []; let cur = null;
      (v.houseActual || []).forEach((e) => { if (cur && Math.abs(cur[cur.length - 1].b - e.a) < 1e-6) cur.push(e); else { cur = [e]; runs.push(cur); } });
      runs.forEach((r) => svgEl("polyline", { points: r.map((e) => `${x(e.a)},${yS(e.kw)} ${x(e.b)},${yS(e.kw)}`).join(" "), fill: "none", stroke: "var(--v2-house)", "stroke-width": 1.5 }, svg));
      if (fs && fs.mid.length) {
        if (fs.high.length && fs.low.length) svgEl("polygon", { points: fs.high.map((p) => `${x(p.h)},${yS(p.kw)}`).concat(fs.low.slice().reverse().map((p) => `${x(p.h)},${yS(p.kw)}`)).join(" "), fill: "var(--v2-sun)", "fill-opacity": 0.18 }, svg);
        const midPts = fs.mid.map((p) => `${x(p.h)},${yS(p.kw)}`).join(" ");
        svgEl("polygon", { points: `${x(fs.mid[0].h)},${sunTop + sunH} ${midPts} ${x(fs.mid[fs.mid.length - 1].h)},${sunTop + sunH}`, fill: "var(--v2-sun)", "fill-opacity": 0.22 }, svg);
        svgEl("polyline", { points: midPts, fill: "none", stroke: "var(--v2-sun)", "stroke-width": 1.5, "stroke-dasharray": "4 3" }, svg);
        if (fs.house.length) svgEl("polyline", { points: fs.house.map((p) => `${x(p.h)},${yS(p.kw)}`).join(" "), fill: "none", stroke: "var(--v2-house)", "stroke-width": 1.8, "stroke-dasharray": "4 3" }, svg);
      }
    }
    if (v.dayEndH !== null && v.dayEndH < v.spanH - 0.01) {                 // today runs on into tomorrow: mark midnight
      svgEl("line", { x1: x(v.dayEndH), x2: x(v.dayEndH), y1: topY, y2: H - 30, stroke: "var(--divider-color)", "stroke-width": 1.5, "stroke-dasharray": "2 4" }, svg);
    }
    const bottom = H - 30;
    if (v.nowH !== null && v.nowH !== undefined) {         // the present moment
      svgEl("line", { x1: x(v.nowH), x2: x(v.nowH), y1: G.T, y2: bottom, stroke: "var(--error-color, #e53935)", "stroke-width": 1.5 }, svg);
      svgEl("text", { x: x(v.nowH) + 3, y: bottom + 12, "font-size": 11, fill: "var(--error-color, #e53935)" }, svg, "Now");
    }
    const cursor = svgEl("line", { x1: 0, x2: 0, y1: G.T, y2: bottom, stroke: "var(--primary-text-color)", "stroke-width": 1, "stroke-opacity": 0.6, visibility: "hidden", "pointer-events": "none" }, svg);
    const clock = (hr) => v2Hm(new Date(v.t0 + hr * HOUR_MS).toISOString());
    const read = this.shadowRoot.getElementById("hread");
    const probeG = svgEl("g", { "pointer-events": "none" }, svg);
    // a number floating where the cursor meets its line: dot, then the figure on a card-coloured chip (right of the cursor, left near the right edge)
    const tag = (px, py, text, colour, taken) => {
      let ty = py;
      while (taken.some((t) => Math.abs(t - ty) < 14)) ty += 14;
      taken.push(ty);
      const w = text.length * 6.4 + 8, left = px > plotW - w - 16, bx = left ? px - 8 - w : px + 8;
      if (py !== null) svgEl("circle", { cx: px, cy: py, r: 3.5, fill: colour, stroke: "var(--card-background-color)", "stroke-width": 1 }, probeG);
      svgEl("rect", { x: bx, y: ty - 8, width: w, height: 15, rx: 4, fill: "var(--card-background-color)", "fill-opacity": 0.88, stroke: colour, "stroke-opacity": 0.6 }, probeG);
      svgEl("text", { x: bx + 4, y: ty + 3.5, "font-size": 11, "font-weight": 600, fill: colour }, probeG, text);
    };
    const paint = (hr) => {
      cursor.setAttribute("x1", x(hr)); cursor.setAttribute("x2", x(hr)); cursor.setAttribute("visibility", "visible");
      while (probeG.firstChild) probeG.removeChild(probeG.firstChild);
      const lines = v2HistoryReadout(v, hr, clock);
      if (read && lines.length) read.textContent = lines.slice(0, 3).join(" · ");     // the time, the mode and why; the numbers float on the chart
      const q = v2HistoryProbe(v, hr), px = x(hr);
      if (!q) return;
      const pct = (n) => `${Math.round(n)}%`, kw1 = (n) => `${n.toFixed(1)} kW`;
      const time = clock(hr), tw = time.length * 6.4 + 10;
      svgEl("rect", { x: Math.min(Math.max(px - tw / 2, 0), plotW - tw), y: topY - 15, width: tw, height: 14, rx: 4, fill: "var(--primary-text-color)" }, probeG);
      svgEl("text", { x: Math.min(Math.max(px, tw / 2), plotW - tw / 2), y: topY - 5, "text-anchor": "middle", "font-size": 10.5, "font-weight": 600, fill: "var(--card-background-color)" }, probeG, time);
      const lvl = [];
      if (q.battery !== null) tag(px, yL(q.battery), `${pct(q.battery)}${q.low !== null && q.high !== null ? ` (${Math.round(q.low)}–${Math.round(q.high)})` : ""}`, "var(--v2-battery)", lvl);
      const pr = [];
      if (q.importP !== null && q.importP !== undefined) tag(px, yP(q.importP), `${q.importP.toFixed(1)}p`, "var(--v2-price)", pr);
      if (q.value !== null && !hid.has("value")) tag(px, yP(q.value), `value ${q.value.toFixed(1)}p`, "var(--v2-good)", pr);
      if (hasE && q.grid) {
        const g = q.grid, base = g.kind === "export" ? mid + yE(g.kw / 2) : mid - yE(g.kw / 2), en = [];
        tag(px, base, g.kind === "quiet" ? "grid 0" : `${g.kind === "import" ? "import" : "export"} ${kw1(g.kw)}`, g.kind === "export" ? "var(--m-export)" : "var(--v2-price)", en);
        if (q.car) tag(px, mid - yE(q.car / 2), `car ${kw1(q.car)}`, "var(--primary-text-color)", en);
      }
      if (hasS) {
        const su = [];
        if (q.sun !== null) tag(px, yS(q.sun), `sun ${kw1(q.sun)}${q.sunLow !== null && q.sunHigh !== null ? ` (${q.sunLow.toFixed(1)}–${q.sunHigh.toFixed(1)})` : ""}`, "var(--v2-sun)", su);
        if (q.house !== null) tag(px, yS(q.house), `house ${kw1(q.house)}`, "var(--v2-house)", su);
      }
    };
    const at = (ev) => {
      const r = svg.getBoundingClientRect();
      if (!r.width) return;
      const hr = (((ev.clientX - r.left) / r.width) * plotW) / pph;
      if (hr < 0 || hr > v.spanH) return;
      this._sel = { hour: hr, date: v.date };
      paint(hr);
    };
    svg.addEventListener("pointermove", at);
    svg.addEventListener("pointerdown", at);
    if (this._sel && this._sel.date === v.date) paint(this._sel.hour);          // a redraw keeps the time you picked
    (hid.has("replan") ? [] : v.replans).forEach((rh) => svgEl("line", { x1: x(rh), x2: x(rh), y1: H - 26, y2: H - 17, stroke: "var(--primary-text-color)", "stroke-width": 1.5, "stroke-opacity": 0.7 }, svg));
    this._placeScroll(scroller, v, pph, plotW, boxW);
  }
  /** Where the scroll box opens: on the default view (VIEW_PAST_HOURS before now, or the start of a past day) until the person scrolls;
   *  after that where they left it, kept by the time at its left edge so a redraw does not move it. */
  _placeScroll(scroller, v, pph, plotW, boxW) {
    const maxPx = Math.max(0, plotW - boxW), defPx = Math.min(maxPx, v.viewH * pph);
    let px = defPx;
    if (this._hUser && this._hLeftMs !== null && this._hLeftMs !== undefined) px = Math.min(maxPx, Math.max(0, ((this._hLeftMs - v.t0) / HOUR_MS) * pph));
    this._hGeom = { t0: v.t0, pph, defPx };
    const laidOut = scroller.clientWidth > 0;
    scroller.scrollLeft = px;
    scroller._placed = laidOut && Math.abs(scroller.scrollLeft - px) < 2;      // a box with no width ignores the position: that is not the person's choice
    this._hSetPx = scroller.scrollLeft;
    if (!laidOut) this._stale = true;
    scroller.addEventListener("scroll", () => {
      if (!scroller.isConnected || scroller !== this.shadowRoot.getElementById("h-scroll")) return;
      if (Math.abs(scroller.scrollLeft - this._hSetPx) < 2) return;           // our own placing
      this._lastScrollAt = Date.now();
      this._hSetPx = scroller.scrollLeft;
      this._hLeftMs = v.t0 + (scroller.scrollLeft / pph) * HOUR_MS;
      this._hUser = Math.abs(scroller.scrollLeft - defPx) > 8;
      this._armIdle();
    });
    scroller.addEventListener("touchstart", () => { this._touching = true; this._touchAt = Date.now(); }, { passive: true });
    const lift = () => { this._touching = false; this._lastScrollAt = Date.now(); this._armIdle(); };
    scroller.addEventListener("touchend", lift, { passive: true });
    scroller.addEventListener("touchcancel", lift, { passive: true });
  }
}

// ---- engines compared, same day (sensor.pe_cost_engines) ----
const ENGINE_COMPARE_SENSOR = "sensor.pe_cost_engines";
const COMPARE_RECHECK_EVENT = "pe_compare_recheck", COMPARE_RECHECK_RESULT = "pe_compare_recheck_result";
const COMPARE_WAITING = "Waiting for the first comparison. Each night PowerEngine replays yesterday with both engines, using the forecasts as they were. It needs one full day of forecast records first, so the first result comes the morning after the first full day of records.";
const COMPARE_OFF = "The same-day comparison is switched off (the Engine comparison switch on the Configuration page).";
const COMPARE_REASONS = { no_snapshot: "no forecast record yet", incomplete: "records incomplete", failed: "replay failed" };
function gbp(n, signed) {
  const v = toNumber(n);
  if (v === null) return "–";
  const r = Math.round(Math.abs(v) * 100) / 100;
  const sign = v < -0.005 ? "−" : signed && v > 0.005 ? "+" : "";
  return `${sign}£${r.toFixed(2)}`;
}
function compareDayLabel(date) {
  if (typeof date !== "string" || !ISO_DAY.test(date)) return String(date || "");
  const d = new Date(`${date}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? date : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}
function approxNote(d) {
  // Why a compared day is only approximate: part-measured half-hours scaled up, or the repeated hour of a 25-hour day.
  const parts = [];
  if (toNumber(d.scaled)) parts.push(`${toNumber(d.scaled)} part-measured half-hour${toNumber(d.scaled) === 1 ? "" : "s"} scaled up`);
  if (d.note) parts.push(String(d.note));
  return parts.join("; ");
}
function whyView(w) {
  // The opened day: why engine v2 fell short of the best possible (the app's `why`), or null for a day compared before
  // breakdowns were kept.
  if (!w || typeof w !== "object" || !Array.isArray(w.parts)) return null;
  return {
    lines: Array.isArray(w.lines) ? w.lines.map(String) : [],
    parts: w.parts.map((p) => ({ label: String(p.label || p.key || ""), v2: gbp(p.v2), bound: gbp(p.bound), gap: gbp(p.gap, true), gapN: toNumber(p.gap) })),
    end: toNumber(w.end) === null ? null : gbp(w.end, true),
    gap: gbp(w.gap),
  };
}
/** The compare view cut to the page's period: only the days in it, totals worked out again from them. The
 *  comparison covers the last 7 days only, so a longer period says so. Pure. */
function compareScoped(view, range) {
  const rows = view.rows.filter((r) => inRange(r.date, range));
  const ok = rows.filter((r) => r.ok && r.n && r.n.v1 !== null && r.n.v2 !== null);
  const sum = (k) => ok.reduce((a, r) => a + (r.n[k] || 0), 0);
  const totals = ok.length ? { days: ok.length, v1: gbp(sum("v1")), v2: gbp(sum("v2")), bound: gbp(sum("bound")), diff: gbp(sum("v2") - sum("v1"), true), best: compareBest(sum("v1"), sum("v2")) } : null;
  let note = "";
  if (!range.rows.length) note = "No days in this period yet.";
  else if (!rows.length) note = "The comparison covers the last 7 days, and none of them are in this period.";
  else if (view.rows.some((r) => !inRange(r.date, range)) || range.rows.length > view.rows.length) note = `Showing the compared days in this period (the comparison covers the last 7 days).`;
  return { view: Object.assign({}, view, { rows, totals }), note };
}
function compareBest(v1, v2) {
  const a = toNumber(v1), b = toNumber(v2);
  if (a === null || b === null) return null;
  return Math.abs(a - b) < 0.01 ? "tie" : a > b ? "v1" : "v2";
}
function calibrationLine(c) {
  const n = toNumber((c || {}).days), diff = toNumber((c || {}).mean_abs_diff), pct = toNumber((c || {}).mean_abs_pct);
  if (!c || !n || diff === null) return "No day yet where one engine was in control all day and its replay could be checked against the metered cost.";
  const size = diff >= 1 ? gbp(diff) : `${Math.round(diff * 100)}p`;
  return `On ${n === 1 ? "the day" : `${n} days`} engine ${c.engine === "v2" ? "v2" : "v1"} was in control, its replay came within ${size}${pct === null ? "" : ` (${Math.round(pct * 10) / 10}%)`} of the metered cost${n === 1 ? "" : ", on average"}.`;
}
/** The table the compare card draws, from the sensor's attributes (and its state: ok, waiting, running, off, error).
 *  kind: "none" | "off" | "waiting" | "error" | "ok". Pure. */
function engineCompareView(attrs, state) {
  const a = attrs && typeof attrs === "object" ? attrs : null;
  if (!a && !state) return { kind: "none", text: "The engine comparison has not published anything yet." };
  const x = a || {};
  const days = Array.isArray(x.days) ? x.days.filter((d) => d && typeof d.date === "string") : [];
  const lastRun = x.last_run && typeof x.last_run === "object" ? x.last_run : null;
  const common = { note: x.note ? String(x.note) : "", running: state === "running", lastRun: lastRun && lastRun.at ? `Last run: ${lastRun.day || ""}${lastRun.status ? `, ${lastRun.status}` : ""}${lastRun.took_s ? ` (${Math.round(lastRun.took_s / 60)} min)` : ""}`.replace(/^Last run: ,/, "Last run:") : "" };
  if (state === "off") return Object.assign(common, { kind: "off", text: COMPARE_OFF });
  const rows = days.map((d) => {
    const ok = d.status === "ok" || (d.status === undefined && d.v1 !== undefined && d.v1 !== null);
    const best = ok ? (d.best === "v1" || d.best === "v2" || d.best === "tie" ? d.best : compareBest(d.v1, d.v2)) : null;
    const diff = toNumber(d.diff) !== null ? toNumber(d.diff) : (toNumber(d.v1) !== null && toNumber(d.v2) !== null ? toNumber(d.v2) - toNumber(d.v1) : null);
    const control = d.in_control === "v1" || d.in_control === "v2" || d.in_control === "mixed" ? d.in_control : "";
    const c = d.calib && typeof d.calib === "object" ? d.calib : null;
    return {
      date: d.date, label: compareDayLabel(d.date), ok, best, status: d.status || "",
      approx: ok ? approxNote(d) : "",
      why: ok ? whyView(d.why) : null,
      reason: ok ? "" : (d.reason ? String(d.reason) : COMPARE_REASONS[d.status] || "not compared"),
      n: ok ? { v1: toNumber(d.v1), v2: toNumber(d.v2), bound: toNumber(d.bound) } : null,
      v1: gbp(d.v1), v2: gbp(d.v2), bound: gbp(d.bound), diff: diff === null ? "–" : gbp(diff, true), diffN: diff,
      control, controlText: control ? `${control === "mixed" ? "mixed" : `engine ${control}`}${control === "v2" && typeof d.profile === "string" && d.profile ? ` · ${d.profile}` : ""}${d.live === false ? " (passive)" : ""}` : "–",
      calib: c && toNumber(c.diff) !== null ? `Engine ${c.engine}'s replay against the metered cost: ${gbp(c.diff, true)}${toNumber(c.diff_pct) === null ? "" : ` (${Math.round(toNumber(c.diff_pct) * 10) / 10}%)`}` : "",
    };
  });
  const compared = rows.filter((r) => r.ok);
  if (!compared.length) {
    if (state === "error") return Object.assign(common, { kind: "error", text: `The last comparison failed${lastRun && lastRun.message ? `: ${lastRun.message}` : "."}`, rows });
    return Object.assign(common, { kind: "waiting", text: COMPARE_WAITING, rows });
  }
  const t = x.totals && typeof x.totals === "object" ? x.totals : null;
  const totals = t ? { days: toNumber(t.days) || compared.length, v1: gbp(t.v1), v2: gbp(t.v2), bound: gbp(t.bound), diff: gbp(t.diff, true), best: compareBest(t.v1, t.v2) } : null;
  return Object.assign(common, { kind: "ok", rows, totals, calibration: calibrationLine(x.calib), problem: state === "error" && lastRun && lastRun.message ? `The last run failed: ${lastRun.message}` : "" });
}

class PowerEngineEngineCompareCard extends PowerEngineV2Card {
  /** "Compare this day again": ask the app (admins only: HA's fire_event needs admin); its answer comes back as an event. */
  async _recheck(day, states) {
    this._recheckMsg = "Asking…";
    this._render(states);
    try {
      await this._hass.callWS({ type: "fire_event", event_type: COMPARE_RECHECK_EVENT, event_data: { day } });
    } catch (err) {
      this._recheckMsg = "Could not ask: only a Home Assistant administrator can.";
      this._render(states);
    }
  }
  _listenRecheck() {
    const conn = this._hass && this._hass.connection;
    if (this._unsubRe || this._subscribingRe || !conn || !conn.subscribeEvents) return;
    this._subscribingRe = true;
    conn.subscribeEvents((ev) => {
      this._recheckMsg = (ev && ev.data && ev.data.message) || "";
      if (this._lastStates) this._render(this._lastStates);
    }, COMPARE_RECHECK_RESULT).then((unsub) => { this._unsubRe = unsub; }, () => { this._subscribingRe = false; });
  }
  setConfig(config) { super.setConfig(Object.assign({ entity: ENGINE_COMPARE_SENSOR }, config || {})); }
  getCardSize() { return 8; }
  _ids() { return [(this._config && this._config.entity) || ENGINE_COMPARE_SENSOR, "sensor.pe_cost_waterfall"]; }
  connectedCallback() {
    if (!this._config || !this._config.shared) return;
    this._onPeriod = () => { if (this._lastStates) this._render(this._lastStates); };
    window.addEventListener(COSTS_PERIOD_EVENT, this._onPeriod);
  }
  disconnectedCallback() { if (this._onPeriod) window.removeEventListener(COSTS_PERIOD_EVENT, this._onPeriod); }
  _render(states) {
    this._lastStates = states;
    const s = (states || {})[(this._config && this._config.entity) || ENGINE_COMPARE_SENSOR];
    let v = engineCompareView(s && s.attributes, s && s.state !== "unavailable" && s.state !== "unknown" ? s.state : undefined);
    let scopeNote = "";
    if (this._config && this._config.shared && v.kind === "ok") {
      const wf = (states || {})["sensor.pe_cost_waterfall"];
      const range = costsRange(costsPeriodLoad(), (wf && wf.attributes && wf.attributes.daily) || []);
      const scoped = compareScoped(v, range);
      v = scoped.view;
      scopeNote = scoped.note;
    }
    if (v.kind === "none" || v.kind === "off" || v.kind === "error" || v.kind === "waiting") {
      this.shadowRoot.innerHTML = `<style>${V2_CSS}</style><ha-card><div class="head"><span class="title">Engines compared, same day</span>${v.running ? '<span class="chip muted">Running now</span>' : ""}</div>
        <p class="${v.kind === "error" ? "" : "muted"}">${escHtml(v.text)}</p>${v.note && v.kind === "waiting" ? `<p class="muted small">${escHtml(v.note)}</p>` : ""}</ha-card>`;
      return;
    }
    const cell = (txt, best) => `<td class="num${best ? " best" : ""}">${best ? '<span class="mark" aria-label="best">✓</span> ' : ""}${escHtml(txt)}</td>`;
    const open = this._open || (this._open = new Set());
    const canRe = !!(this._hass && this._hass.user && this._hass.user.is_admin);
    this._listenRecheck();
    const detail = (r) => {
      if (!open.has(r.date)) return "";
      const again = canRe ? `<p><button data-recheck="${escHtml(r.date)}">Compare this day again</button></p>` : "";
      if (!r.why) return `<tr class="why"><td colspan="6" class="muted">No breakdown for this day: it was compared before breakdowns were kept.${again}</td></tr>`;
      const rows = r.why.parts.map((p) => `<tr><td>${escHtml(p.label)}</td><td class="num">${escHtml(p.v2)}</td><td class="num">${escHtml(p.bound)}</td><td class="num${p.gapN > 0.049 ? " worse" : ""}">${escHtml(p.gap)}</td></tr>`).join("");
      const end = r.why.end === null ? "" : `<tr><td>Battery level left at the end</td><td class="num">–</td><td class="num">–</td><td class="num">${escHtml(r.why.end)}</td></tr>`;
      return `<tr class="why"><td colspan="6">
        ${r.why.lines.map((l) => `<p>${escHtml(l)}</p>`).join("")}
        <table class="parts"><thead><tr><th>Where the cost fell</th><th>Engine v2</th><th>Best possible</th><th>v2 worse by</th></tr></thead><tbody>${rows}${end}</tbody></table>
        <p class="muted small">Costs in £ for that part of the day (negative is money earned). The last column is v2's cost less the best possible's, so a positive figure is where v2 lost out.</p>${again}</td></tr>`;
    };
    const body = v.rows.map((r) => r.ok
      ? `<tr class="day${open.has(r.date) ? " open" : ""}" data-day="${escHtml(r.date)}" tabindex="0" title="Show why"><th scope="row">${escHtml(r.label)}${r.approx ? ` <span class="approx" title="${escHtml(r.approx)}">≈</span>` : ""}</th>${cell(r.v1, r.best === "v1")}${cell(r.v2, r.best === "v2")}${cell(r.bound)}<td class="num">${escHtml(r.diff)}</td><td title="${escHtml(r.calib)}">${escHtml(r.controlText)}</td></tr>${detail(r)}`
      : `<tr class="skip"><th scope="row">${escHtml(r.label)}</th><td colspan="5" class="muted">Not compared: ${escHtml(lowerFirst(r.reason))}${canRe && r.status !== "pending" && r.status !== "no_snapshot" ? ` <button data-recheck="${escHtml(r.date)}">Try again</button>` : ""}</td></tr>`).join("");
    const foot = v.totals ? `<tfoot><tr><th scope="row">${v.totals.days} day${v.totals.days === 1 ? "" : "s"}</th>${cell(v.totals.v1, v.totals.best === "v1")}${cell(v.totals.v2, v.totals.best === "v2")}${cell(v.totals.bound)}<td class="num">${escHtml(v.totals.diff)}</td><td></td></tr></tfoot>` : "";
    this.shadowRoot.innerHTML = `<style>${V2_CSS}
      .wrap { overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; font-size: 14px; }
      th, td { padding: 6px 10px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--divider-color); }
      thead th { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; vertical-align: bottom; white-space: normal; }
      th:first-child, td:last-child, thead th:last-child { text-align: left; }
      tbody th, tfoot th { font-weight: 500; }
      td.best { font-weight: 700; color: var(--v2-good); }
      .mark { font-weight: 700; }
      .approx { color: var(--secondary-text-color); cursor: help; }
      button { font: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; cursor: pointer; margin-left: 6px;
               border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); }
      tr.day { cursor: pointer; }
      tr.day th::before { content: "▸ "; color: var(--secondary-text-color); }
      tr.day.open th::before { content: "▾ "; }
      tr.why td { text-align: left; white-space: normal; background: var(--secondary-background-color, transparent); }
      tr.why p { margin: 4px 0; }
      table.parts { width: auto; max-width: 100%; margin: 6px 0; }
      table.parts th, table.parts td { padding: 3px 10px 3px 0; border-bottom: 1px solid var(--divider-color); }
      table.parts td.worse { color: var(--v2-warn); font-weight: 600; }
      tr.skip td { text-align: left; font-size: 13px; white-space: normal; }
      tfoot td, tfoot th { border-top: 2px solid var(--divider-color); border-bottom: 0; }
      @media (max-width: 520px) { table { font-size: 12.5px; } th, td { padding: 6px 5px; } thead th { font-size: 10.5px; } ha-card { padding: 12px; } }
    </style>
    <ha-card>
      ${this._config && this._config.shared ? (v.running ? '<div class="head"><span class="chip muted">Running now</span></div>' : "") : `<div class="head"><span class="title">Engines compared, same day</span>${v.running ? '<span class="chip muted">Running now</span>' : ""}</div>`}
      ${v.problem ? `<p class="muted small">${escHtml(v.problem)}</p>` : ""}
      ${scopeNote ? `<p class="muted small">${escHtml(scopeNote)}</p>` : ""}
      ${this._recheckMsg ? `<p class="small"><b>${escHtml(this._recheckMsg)}</b></p>` : ""}
      <p class="muted small">What each engine saved against plain self-use, per day (£). ✓ marks the better engine; best possible is perfect hindsight.${v.rows.some((r) => r.approx) ? " ≈ marks a day that is only approximate (hover or tap it to see why)." : ""}</p>
      <div class="wrap"><table>
        <thead><tr><th>Day</th><th>Engine v1</th><th>Engine v2</th><th>Best possible</th><th>v2 − v1</th><th>In control</th></tr></thead>
        <tbody>${body}</tbody>${foot}</table></div>
      <p class="muted small">Select a compared day to see why engine v2 fell short of the best possible.</p>
      <p>${escHtml(v.calibration)}</p>
      ${v.note ? `<p class="muted small">${escHtml(v.note)}</p>` : ""}
      ${v.lastRun ? `<p class="muted small">${escHtml(v.lastRun)}</p>` : ""}
    </ha-card>`;
    this.shadowRoot.querySelectorAll("button[data-recheck]").forEach((b) => b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this._recheck(b.dataset.recheck, states);
    }));
    this.shadowRoot.querySelectorAll("tr.day").forEach((tr) => {
      const toggle = () => {
        if (open.has(tr.dataset.day)) open.delete(tr.dataset.day); else open.add(tr.dataset.day);
        this._render(states);
      };
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
    });
  }
}

// --- special events by month (sensor.pe_event_months) ---------------------------------------------------------
const EVENT_MONTHS_SENSOR = "sensor.pe_event_months";
function eventMonthLabel(key) {
  if (typeof key !== "string" || !/^\d{4}-\d{2}$/.test(key)) return String(key || "");
  return new Date(`${key}-01T12:00:00Z`).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
}
/** The monthly table from the sensor's attributes, newest month first; each month with days carries them. Pure. */
function eventsView(attrs) {
  const a = attrs && typeof attrs === "object" ? attrs : {};
  const name = (a.names && a.names.event) || "Event";
  const num = (v) => toNumber(v) || 0;
  const months = (Array.isArray(a.months) ? a.months : []).filter((m) => m && m.month).slice().reverse().map((m) => ({
    key: m.month, label: eventMonthLabel(m.month), kwh: num(m.axle_kwh).toFixed(1), gross: gbp(m.axle_gross), energy: gbp(m.axle_energy),
    pay: toNumber(m.axle_pay) === null ? null : gbp(m.axle_pay), onTop: gbp(num(m.axle_gross) - num(m.axle_pay)),
    net: gbp(m.axle_net), free: gbp(m.free_power_net), daily: gbp(num(m.actual) - num(m.standing)), standing: gbp(m.standing),
    days: (Array.isArray(m.ev_days) ? m.ev_days : []).slice().reverse().map((d) => ({
      date: d[0], label: compareDayLabel(d[0]), kwh: num(d[1]).toFixed(1), gross: gbp(d[2]), energy: gbp(d[3]), net: gbp(d[4]),
      freeKwh: num(d[5]).toFixed(1), free: gbp(d[6]), pay: toNumber(d[7]) === null ? null : gbp(d[7]), onTop: gbp(num(d[2]) - num(d[7])) })),
  }));
  // an app from before the event's own pay was kept apart from the export rate has no `axle_pay`: one column, as before
  return { name, months, hasDays: months.some((m) => m.days.length), hasPay: months.some((m) => m.pay !== null) };
}

/** What events brought in over the page's period, from the listed days with events (the last three months). Pure. */
function eventsInRange(attrs, range) {
  const months = (attrs && Array.isArray(attrs.months) ? attrs.months : []).filter((m) => Array.isArray(m.ev_days));
  const listed = months.length ? months[0].month : null;                  // the oldest month that lists its days
  if (!range.from || !listed || range.from.slice(0, 7) < listed) return range.from && listed ? { partial: true } : null;
  const days = months.flatMap((m) => m.ev_days).filter((d) => inRange(d[0], range));
  const sum = (i) => days.reduce((a, d) => a + (toNumber(d[i]) || 0), 0);
  return { days: days.length, kwh: sum(1), gross: sum(2), energy: sum(3), net: sum(4), freeKwh: sum(5), free: sum(6),
    pay: days.some((d) => d.length > 7) ? sum(7) : null };
}

class PowerEngineEventsCard extends PowerEngineV2Card {
  setConfig(config) { super.setConfig(Object.assign({ entity: EVENT_MONTHS_SENSOR }, config || {})); }
  getCardSize() { return 6; }
  _ids() { return [(this._config && this._config.entity) || EVENT_MONTHS_SENSOR, "switch.pe_ui_right_align", "sensor.pe_cost_waterfall"]; }
  connectedCallback() {
    if (!this._config || !this._config.shared) return;
    this._onPeriod = () => { if (this._lastStates) this._render(this._lastStates); };
    window.addEventListener(COSTS_PERIOD_EVENT, this._onPeriod);
  }
  disconnectedCallback() { if (this._onPeriod) window.removeEventListener(COSTS_PERIOD_EVENT, this._onPeriod); }
  _periodLine(states, attrs, name) {
    if (!this._config || !this._config.shared) return "";
    const wf = (states || {})["sensor.pe_cost_waterfall"];
    const range = costsRange(costsPeriodLoad(), (wf && wf.attributes && wf.attributes.daily) || []);
    const t = eventsInRange(attrs, range);
    if (!t) return "";
    if (t.partial) return `<p class="muted small">Days with events are listed for the last three months only, so ${escHtml(range.label.toLowerCase())} is shown in the monthly table below.</p>`;
    if (!t.days) return `<p>No ${escHtml(name)} or free-power events in ${escHtml(range.label.toLowerCase())}.</p>`;
    return `<p><b>${escHtml(range.label)}:</b> ${t.days} day${t.days === 1 ? "" : "s"} with events. ${escHtml(name)} exported ${t.kwh.toFixed(1)} kWh and ${t.pay === null ? `paid you ${gbp(t.gross)}` : `paid ${gbp(t.pay)}, with the export rate adding ${gbp(t.gross - t.pay)}`}, ${gbp(t.net)} net of the ${gbp(t.energy)} of energy it used.${t.free ? ` Free power: ${t.freeKwh.toFixed(1)} kWh, a net ${gbp(t.free)}.` : ""}</p>`;
  }
  _render(states) {
    this._lastStates = states;
    const s = (states || {})[(this._config && this._config.entity) || EVENT_MONTHS_SENSOR];
    const v = eventsView(s && s.attributes);
    const left = (states["switch.pe_ui_right_align"] || {}).state === "off";
    const open = this._open || (this._open = new Set());
    if (!v.months.length) {
      this.shadowRoot.innerHTML = `<style>${V2_CSS}</style><ha-card><p class="muted">No ${escHtml(v.name)} or free-power events recorded yet.</p></ha-card>`;
      return;
    }
    // the event's own pay and the export rate on top in two columns (one, as before, from an older app)
    const money = (x) => (v.hasPay ? `<td class="num">${escHtml(x.pay === null ? x.gross : x.pay)}</td><td class="num">${escHtml(x.pay === null ? "–" : x.onTop)}</td>`
      : `<td class="num">${escHtml(x.gross)}</td>`);
    const body = v.months.map((m) => {
      const can = m.days.length > 0;
      const detail = can && open.has(m.key) ? `<tr class="why"><td colspan="9"><table class="parts"><thead><tr><th>Day</th><th>${escHtml(v.name)} kWh</th>${v.hasPay ? `<th>${escHtml(v.name)} pays</th><th>Export rate on top</th>` : "<th>Paid you</th>"}<th>Energy it used</th><th>Net</th><th>Free power kWh</th><th>Free power</th></tr></thead><tbody>${
        m.days.map((d) => `<tr><td>${escHtml(d.label)}</td><td class="num">${d.kwh}</td>${money(d)}<td class="num">${escHtml(d.energy)}</td><td class="num"><b>${escHtml(d.net)}</b></td><td class="num">${d.freeKwh}</td><td class="num">${escHtml(d.free)}</td></tr>`).join("")}</tbody></table></td></tr>` : "";
      return `<tr class="${can ? "day" : ""}${can && open.has(m.key) ? " open" : ""}" ${can ? `data-month="${escHtml(m.key)}" tabindex="0" title="Show the days"` : ""}><th scope="row">${escHtml(m.label)}</th><td class="num">${m.kwh}</td>${money(m)}<td class="num">${escHtml(m.energy)}</td><td class="num"><b>${escHtml(m.net)}</b></td><td class="num">${escHtml(m.free)}</td><td class="num">${escHtml(m.daily)}</td><td class="num">${escHtml(m.standing)}</td></tr>${detail}`;
    }).join("");
    this.shadowRoot.innerHTML = `<style>${V2_CSS}
      .wrap { overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; font-size: 14px; }
      th, td { padding: 6px 10px; text-align: ${left ? "left" : "right"}; white-space: nowrap; border-bottom: 1px solid var(--divider-color); }
      thead th { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; vertical-align: bottom; white-space: normal; }
      th:first-child, td:first-child { text-align: left; }
      tbody th { font-weight: 500; }
      tr.day { cursor: pointer; }
      tr.day th::before { content: "▸ "; color: var(--secondary-text-color); }
      tr.day.open th::before { content: "▾ "; }
      tr.why td { white-space: normal; text-align: left; background: var(--secondary-background-color, transparent); }
      table.parts { width: auto; max-width: 100%; margin: 4px 0; }
      table.parts th, table.parts td { padding: 3px 10px 3px 0; }
      @media (max-width: 520px) { table { font-size: 12.5px; } th, td { padding: 6px 5px; } thead th { font-size: 10.5px; } ha-card { padding: 12px; } }
    </style>
    <ha-card>
      ${this._periodLine(states, s && s.attributes, v.name)}
      <div class="wrap"><table>
        <thead><tr><th>Month</th><th>${escHtml(v.name)} kWh</th>${v.hasPay ? `<th>${escHtml(v.name)} pays</th><th>Export rate on top</th>` : `<th>${escHtml(v.name)} paid you</th>`}<th>Energy it used</th><th>${escHtml(v.name)} net</th><th>Free power</th><th>Day-to-day energy (metered)</th><th>Standing charge</th></tr></thead>
        <tbody>${body}</tbody></table></div>
      ${v.hasDays ? '<p class="muted small">Select a month to see its days with events (the last three months).</p>' : ""}
    </ha-card>`;
    this.shadowRoot.querySelectorAll("tr.day").forEach((tr) => {
      const toggle = () => {
        if (open.has(tr.dataset.month)) open.delete(tr.dataset.month); else open.add(tr.dataset.month);
        this._render(states);
      };
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
    });
  }
}

[["powerengine-engine-card", PowerEngineEngineCard, "PowerEngine engine v2", "Engine v2's mode, why, the value of a stored kWh against the buy and sell lines, and what ends the mode."],
  ["powerengine-v2-plan-card", PowerEngineV2PlanCard, "PowerEngine engine v2 plan", "Engine v2's expected timeline and the value map."],
  ["powerengine-v2-health-card", PowerEngineV2HealthCard, "PowerEngine engine v2 health", "Engine v2's recalculations, flip-flops, everyday range and learned weights."],
  ["powerengine-engine-badge-card", PowerEngineEngineBadgeCard, "PowerEngine engine badge", "Active, Paused or Passive for one engine, with what that means (engine: v1 or v2)."],
  ["powerengine-v2-history-card", PowerEngineV2HistoryCard, "PowerEngine engine v2 history", "A day of engine v2: level as it ran against the expected level, the modes, prices and value, with a day picker."],
  ["powerengine-events-card", PowerEngineEventsCard, "PowerEngine events by month", "Grid-event and free-power totals by month; select a month for its days."],
  ["powerengine-engine-compare-card", PowerEngineEngineCompareCard, "PowerEngine engines compared", "What engine v1, engine v2 and the best possible would have saved on each of the last days."],
].forEach(([tag, cls, name, description]) => {
  if (typeof customElements !== "undefined" && !customElements.get(tag)) {
    customElements.define(tag, cls);
    window.customCards = window.customCards || [];
    window.customCards.push({ type: tag, name, description });
  }
});

if (typeof module !== "undefined") {
  module.exports = { HOUSE_GROUPS, TOPIC_GROUP, routerSupported, routerDraft, routerFromLegacy, withRouter, routerConfig, routerChannelConfig, routerProblem, routeCells, routesFromCells, notifyKinds, newChannelName, NOTIFY_KINDS, ROUTER_APP_VERSION, whatsappSupported, whatsappTargets, parseWhatsappTargets, withWhatsapp, catalogueAttrs, alexaSpeakerKinds, alexaTestPayload, ALEXA_TEST_EVENT, alexaDraft, parseHoursBefore, alexaSpeakerName, ALEXA_DEFAULT, v2HistoryProbe, v2Forecast, v2ForecastLastDay, v2HistoryReadout, V2_FLOWS, V2_HISTORY_HINT, runRows, REPLAN_EVENT, replanStatus, plannedImportP, PACKAGE_SENSOR, PACKAGE_BANNER_TEXT, PACKAGE_BUTTON, PACKAGE_ASK_ADMIN, INSTALL_GUIDE_URL, packageInfo, packageReloadPayload, packagePromptAfterSave, packageBannerView, packageProblemLine, roleNeed, setupRows, setupSummary, otherControllerValue, handoverVisible, effectiveOtherController, guardRolesShown, otherControllerPrompt, predbatInUse, testsPauseHint, OTHER_UNSET_PROMPT, GUARD_ROLES, HANDOVER_DEFAULTS, v2Preview, previewLabel, previewBanner, V2_PREVIEW_LINE, historyDayPayload, shiftHistoryDay, demoNeedsReload, DEMO_WAIT, asBool, FEATURES, FEATURE_DEFAULTS, parseSignNote, readout, inUseFor, nowText, instantProblem, effectiveRole, suggestEntity, initialDraft, buildConfig, slugify, summariseAttribute, settingProblem, testSummary, dampingNote, configEntities, diagStates, diagFileName, scrubReport, reportFileName, reportIssueUrl, REPORT_TEMPLATE, diagHistoryIds, peRepos, versionLine, MIN_APP_VERSION, parseVersion, versionOlder, versionWarnings, logRows, logWhen, escHtml, findRcEntities, liveLine, TESTS, measuredText, simHistoryPlan, monthRange, handoverRows, topicPlan, roleNeed, matchesSearch, TOPICS, CARD_VERSION, waterfallRows, waterfallCustom, waterfallSteps, costsRange, costsSummary, costsTodayInfo, costsBars, peParts, costsDayLabel, shownValue, sectionOpen, sectionsLoad, SECTIONS_KEY, overnightReadout, waterfallScale, pct, waterfallShortLabel, compactGbp, fillNames, SETUP_REPOS, findHacsRepo, hacsInfoPayload, hacsListPayload, hacsAddPayload, hacsDownloadPayload, addonsPayload, installStep, addonFrom, peRunning, setupRows, setupSummary, demoView, demoEventPayload, configPath, showDemoLink, DEMO_DAYS, NOTIFY_EVENTS, SCREEN, NAME_FALLBACK, SITE_KINDS, SITE_WARNING, SITE_RETEST, siteInfo, siteFirmwareOptions, siteVariant, siteFromSelection, siteChooseInverter, siteNeedsWarning, siteDetectedLine, siteOptionLabel, FREE_LOADS_APP_VERSION, freeLoadsSupported, freeLoadDraft, buildFreeLoads, DEVICES_APP_VERSION, DEVICE_INPUTS, devicesSupported, deviceDraft, deviceNewId, buildDevices, deviceReadout,
  wizardDeviceName, wizardInUse, wizardOthers, wizardUsedEntities, wizardPlantFromDevice, wizardPlantId, EXPORT_FORMAT, EXPORT_VERSION, EXPORT_STATE_MAX, wizardInfo, wizardFacts, wizardMatch, wizardCandidates, wizardRoles, wizardSuggest, wizardPlantGuess, wizardMissing, wizardWatts, wizardSignCheck, wizardBalance, scrubText, buildCandidateExport, candidateFileName, wizardEnergyDevices,
  SYSTEM_DRAFT_KEY, systemKinds, systemItems, systemMissingParts, opsSet, opsRemove, opsUndoRemove, opsTag, opsSummary, applyOps, featuresLeftOut, buildApplyConfig, equipmentOf, systemFingerprint, overlayEquipment, systemImpact, systemDraftLoad, systemDraftSave,
  OVERRIDE_MODES, OVERRIDE_PERIODS, OVERRIDE_MAX_SLOTS, inverterWords, overrideEndOptions, overridePayload, overrideView, overrideSummary,
  v2WithProfiles, V2_PROFILES, uiEngine, profileDraft, profileList, profileBase, profileChanges, profileLoadFields, profileSaveFields, profileDeleteFields,
  ENGINE_SENSOR, V2_NOT_RUNNING, V2_NO_DATA, V2_MODES, v2Engine, v2Supported, v2Gate, valueBarGeometry, modeSubtitle, exitRows, engineCardView, timelineLayout, recentRows, scrollIntent, HISTORY_HOURS, V2_RECENT, sunLayout, levelAtHour, bandLabel, timelineHover,
  heatColour, valueMapGrid, mapCellAt, interpValue, offScaleReason, chartKeyHtml, chartHidden, CHART_KEYS, pathValueSeries, timelineValue, timelineLines, pathLines, mapReadoutText, planCardView, causeBars, causeLabel, comfortSummary, healthCardView,
  ENGINE_ICONS, engineIcon, refreshEngineIcons, engineBadge, BADGE_LINES, V2_HISTORY_DAY_EVENT, v2HistoryDayPayload, v2HistoryView, engineCompareView, approxNote, whyView, eventsView, eventsInRange, compareScoped, calibrationLine, gbp, COMPARE_WAITING,
  V1_SECTIONS, v2Same, engineGrouping, engineInUse, engineConfirm, cleanV2Block, engineFields, v2Problem, v2Contradictions, v2Values, comfortReadout, weightsReadout, floorsReadout };
}
