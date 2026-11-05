const test = require("node:test");
const assert = require("node:assert");
const c = require("../ha-powerengine-card.js");
const GROUP = "120363085203994232@g.us";
const LIVE = { service: "persistent_notification", events: { health: true, inputs: true, axle: true, free_power: true, daily: true, alexa: true, simulator: true },
  alexa: { services: ["notify.alexa_media_daddy_dot", "notify.alexa_media_grandpa_dot"], earliest_weekday: "07:30", earliest_weekend: "08:00", latest: "21:00", intro: "Daddy Bot here.", whatsapp: true, whatsapp_targets: [GROUP] } };

test("the router needs app 0.9.195 or newer", () => {
  assert.strictEqual(c.routerSupported("0.9.194"), false);
  assert.strictEqual(c.routerSupported("0.9.195"), true);
  assert.strictEqual(c.routerSupported(undefined), false);
});

test("the older settings become the same channels and rules the app makes", () => {
  const r = c.routerFromLegacy({ ...LIVE, alexa: c.alexaDraft(LIVE.alexa) });
  assert.deepStrictEqual(Object.keys(r.channels), ["bell", "alexa", "whatsapp"]);
  assert.strictEqual(r.channels.alexa.intro, "Daddy Bot here.");
  assert.deepStrictEqual(r.channels.alexa.hours, { weekday_from: "07:30", weekend_from: "08:00", until: "21:00" });
  assert.deepStrictEqual(r.channels.whatsapp.targets, [GROUP]);
  assert.deepStrictEqual(r.rules, [{ events: ["health", "inputs", "axle", "free_power", "daily", "simulator"], to: ["bell"] },
    { events: ["grid_reminder", "grid_summary"], to: ["alexa", "whatsapp"] }]);
  assert.deepStrictEqual(r.reminder_hours, [8, 5, 2, 1, 0]);
  const phone = c.routerFromLegacy({ service: "notify.mobile_app_pixel", events: { health: true }, alexa: c.alexaDraft({ finish: false }) });
  assert.deepStrictEqual(phone.channels.phone, { type: "notify", label: "Phone", intro: "", hours: null, outside_hours: "hold", service: "notify.mobile_app_pixel" });
  assert.deepStrictEqual(phone.rules, [{ events: ["health"], to: ["phone"] }]);          // alexa is off: no rule for it
  const off = c.routerFromLegacy({ service: "off", events: {}, alexa: c.alexaDraft({}) });
  assert.deepStrictEqual(off.rules, []);
});

test("withRouter converts only for an app that supports it and leaves the new form alone", () => {
  const mk = () => ({ notifications: { ...LIVE, alexa: c.alexaDraft(LIVE.alexa) } });
  assert.ok(c.withRouter(mk(), true).notifications.channels);
  assert.ok(c.withRouter(mk(), false).notifications.service);
  const once = c.withRouter(mk(), true);
  assert.deepStrictEqual(c.withRouter(JSON.parse(JSON.stringify(once)), true), once);
});

test("a saved channels-and-rules config is kept by initialDraft and saved back by buildConfig", () => {
  const saved = { notifications: { channels: { bell: { type: "bell" }, family: { type: "whatsapp", targets: [GROUP, "bad"], outside_hours: "skip", hours: { weekday_from: "8:00", until: "21:00" } } },
    rules: [{ events: ["health", "health"], to: ["bell", "nowhere"] }, { events: ["bins"], to: ["family"] }], reminder_hours: [1, 8] } };
  const { draft } = c.initialDraft(saved, [], [], {});
  const n = draft.notifications;
  assert.deepStrictEqual(n.channels.family.hours, { weekday_from: "08:00", weekend_from: "08:00", until: "21:00" });
  assert.deepStrictEqual(n.channels.family.targets, [GROUP]);
  assert.deepStrictEqual(n.rules, [{ events: ["health"], to: ["bell"] }, { events: ["bins"], to: ["family"] }]);
  assert.deepStrictEqual(n.reminder_hours, [8, 1]);
  const out = c.buildConfig(draft).notifications;
  assert.deepStrictEqual(out.channels.family, { type: "whatsapp", label: "family", hours: { weekday_from: "08:00", weekend_from: "08:00", until: "21:00" }, outside_hours: "skip", targets: [GROUP] });
  assert.deepStrictEqual(out.channels.bell, { type: "bell", label: "bell" });
  assert.strictEqual("service" in out, false);
});

test("an unchanged page is not dirty: the draft and the saved base convert the same way", () => {
  const mk = () => c.withRouter(c.initialDraft({ notifications: LIVE }, [], [], {}).draft, true);
  assert.strictEqual(JSON.stringify(c.buildConfig(mk())), JSON.stringify(c.buildConfig(mk())));
  const edited = mk(); edited.notifications.channels.alexa.intro = "Hi.";
  assert.notStrictEqual(JSON.stringify(c.buildConfig(edited)), JSON.stringify(c.buildConfig(mk())));
});

test("the table's ticks round-trip to rules, one rule per channel", () => {
  const rules = [{ events: ["health", "daily"], to: ["bell"] }, { events: ["grid_reminder"], to: ["alexa", "whatsapp"] }];
  const cells = c.routeCells(rules);
  assert.ok(cells.has("health|bell") && cells.has("grid_reminder|whatsapp") && !cells.has("health|alexa"));
  cells.add("health|alexa"); cells.delete("daily|bell");
  const kinds = ["health", "daily", "grid_reminder"];
  assert.deepStrictEqual(c.routesFromCells(cells, ["bell", "alexa", "whatsapp"], kinds), [
    { events: ["health"], to: ["bell"] }, { events: ["health", "grid_reminder"], to: ["alexa"] }, { events: ["grid_reminder"], to: ["whatsapp"] }]);
  assert.deepStrictEqual(c.routesFromCells(new Set(), ["bell"], kinds), []);
});

test("the table lists PowerEngine's kinds, then ones seen elsewhere, then ones only a rule names", () => {
  const k = c.notifyKinds([{ kind: "bins", label: "Bin collections", source: "family" }, { kind: "health", label: "x", source: "powerengine" }], [{ events: ["old_kind"], to: ["a"] }]);
  assert.deepStrictEqual(k.slice(0, 8).map((x) => x.kind), c.NOTIFY_KINDS.map((x) => x[0]));
  assert.deepStrictEqual(k.slice(8).map((x) => [x.kind, x.label, x.source]), [["bins", "Bin collections", "family"], ["old_kind", "old_kind", "external"]]);
  assert.strictEqual(c.notifyKinds(null, []).length, c.NOTIFY_KINDS.length);
});

test("a channel that the app would refuse is caught before saving", () => {
  const ch = (type, extra) => ({ channels: { x: { type, label: "X", intro: "", hours: null, outside_hours: "hold", ...extra } } });
  assert.match(c.routerProblem(ch("notify", { service: "" })), /notify service/);
  assert.match(c.routerProblem(ch("alexa", { services: [] })), /speaker/);
  assert.match(c.routerProblem(ch("whatsapp", { targets: [] })), /WhatsApp/);
  assert.match(c.routerProblem(ch("bell", { hours: { weekday_from: "08:00", weekend_from: "09:00", until: "07:00" } })), /after they start/);
  assert.strictEqual(c.routerProblem(ch("bell", { hours: { weekday_from: "08:00", weekend_from: "09:00", until: "21:00" } })), "");
  assert.strictEqual(c.routerProblem(ch("alexa", { services: ["notify.alexa_media_a"] })), "");
});

test("new channels get a free name", () => {
  assert.strictEqual(c.newChannelName("alexa", {}), "alexa");
  assert.strictEqual(c.newChannelName("alexa", { alexa: 1, alexa_2: 1 }), "alexa_3");
});
