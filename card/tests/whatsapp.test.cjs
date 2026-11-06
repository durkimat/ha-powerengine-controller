const test = require("node:test");
const assert = require("node:assert");
const c = require("../ha-powerengine-card.js");
const GROUP = "120363085203994232@g.us";

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

test("withWhatsapp adds the settings (off, no chats) and keeps saved ones", () => {
  const mk = () => ({ notifications: { alexa: c.alexaDraft({}) } });
  assert.deepStrictEqual([c.withWhatsapp(mk()).notifications.alexa.whatsapp, c.withWhatsapp(mk()).notifications.alexa.whatsapp_targets], [false, []]);
  const saved = mk(); saved.notifications.alexa.whatsapp = true; saved.notifications.alexa.whatsapp_targets = [GROUP];
  const out = c.withWhatsapp(saved).notifications.alexa;
  assert.deepStrictEqual([out.whatsapp, out.whatsapp_targets], [true, [GROUP]]);
});

test("saved and unsaved WhatsApp settings compare equal until one is changed, and the config carries them", () => {
  const saved = { notifications: { alexa: { whatsapp: true, whatsapp_targets: [GROUP] } } };
  const a = c.withWhatsapp(c.initialDraft(saved, [], [], {}).draft);
  const b = c.withWhatsapp(c.initialDraft(saved, [], [], {}).draft);
  assert.strictEqual(JSON.stringify(c.buildConfig(a)), JSON.stringify(c.buildConfig(b)));
  assert.deepStrictEqual(c.buildConfig(a).notifications.alexa.whatsapp_targets, [GROUP]);
  const fresh = c.withWhatsapp(c.initialDraft({}, [], [], {}).draft);
  assert.strictEqual(c.buildConfig(fresh).notifications.alexa.whatsapp, false);
});

test("the test announcement carries the WhatsApp chats only when it is switched on", () => {
  const on = { notifications: { alexa: c.alexaDraft({ services: [], whatsapp: true, whatsapp_targets: [GROUP] }) } };
  assert.deepStrictEqual(c.alexaTestPayload(on).whatsapp, [GROUP]);
  const off = { notifications: { alexa: c.alexaDraft({ whatsapp: false, whatsapp_targets: [GROUP] }) } };
  assert.strictEqual("whatsapp" in c.alexaTestPayload(off), false);
});
