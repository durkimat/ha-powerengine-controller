const test = require("node:test");
const assert = require("node:assert");
const c = require("../ha-powerengine-card.js");
const GROUP = "120363085203994232@g.us";

test("WhatsApp announcements need app 0.9.193 or newer", () => {
  assert.strictEqual(c.whatsappSupported("0.9.192"), false);
  assert.strictEqual(c.whatsappSupported("0.9.193"), true);
  assert.strictEqual(c.whatsappSupported(undefined), false);
});

test("whatsappTargets and parseWhatsappTargets accept group ids and phone numbers only", () => {
  assert.deepStrictEqual(c.whatsappTargets([GROUP, ` ${GROUP} `, "447700900123", "Durkin Chat", 5]), [GROUP, "447700900123"]);
  assert.deepStrictEqual(c.whatsappTargets("x"), []);
  assert.deepStrictEqual(c.parseWhatsappTargets(`${GROUP}, 447700900123 ${GROUP}`), [GROUP, "447700900123"]);
  assert.strictEqual(c.parseWhatsappTargets(`${GROUP}, nonsense`), null);
  assert.deepStrictEqual(c.parseWhatsappTargets(""), []);
});

test("alexaDraft carries the WhatsApp settings only when present", () => {
  assert.strictEqual("whatsapp" in c.alexaDraft({}), false);
  const d = c.alexaDraft({ whatsapp: true, whatsapp_targets: [GROUP, "bad"] });
  assert.strictEqual(d.whatsapp, true);
  assert.deepStrictEqual(d.whatsapp_targets, [GROUP]);
});

test("withWhatsapp adds the settings (off) for a new app and removes them for an old one", () => {
  const mk = () => ({ notifications: { alexa: c.alexaDraft({}) } });
  assert.deepStrictEqual([c.withWhatsapp(mk(), true).notifications.alexa.whatsapp, c.withWhatsapp(mk(), true).notifications.alexa.whatsapp_targets], [false, []]);
  const old = mk(); old.notifications.alexa.whatsapp = true; old.notifications.alexa.whatsapp_targets = [GROUP];
  const out = c.withWhatsapp(old, false).notifications.alexa;
  assert.strictEqual("whatsapp" in out || "whatsapp_targets" in out, false);
});

test("saved and unsaved WhatsApp settings compare equal until one is changed, and the config carries them", () => {
  const saved = { notifications: { alexa: { whatsapp: true, whatsapp_targets: [GROUP] } } };
  const a = c.withWhatsapp(c.initialDraft(saved, [], [], {}).draft, true);
  const b = c.withWhatsapp(c.initialDraft(saved, [], [], {}).draft, true);
  assert.strictEqual(JSON.stringify(c.buildConfig(a)), JSON.stringify(c.buildConfig(b)));
  assert.deepStrictEqual(c.buildConfig(a).notifications.alexa.whatsapp_targets, [GROUP]);
  const none = c.withWhatsapp(c.initialDraft({}, [], [], {}).draft, false);
  assert.strictEqual("whatsapp" in c.buildConfig(none).notifications.alexa, false);
});

test("the test announcement carries the WhatsApp chats only when it is switched on", () => {
  const on = { notifications: { alexa: c.alexaDraft({ services: [], whatsapp: true, whatsapp_targets: [GROUP] }) } };
  assert.deepStrictEqual(c.alexaTestPayload(on).whatsapp, [GROUP]);
  const off = { notifications: { alexa: c.alexaDraft({ whatsapp: false, whatsapp_targets: [GROUP] }) } };
  assert.strictEqual("whatsapp" in c.alexaTestPayload(off), false);
});
