const test = require("node:test");
const assert = require("node:assert");
const h = require("../ha-powerengine-card.js");

const st = (state, extra) => ({ [h.PACKAGE_SENSOR]: { state, attributes: Object.assign({ files: [], reload_needed: state === "reload_needed", message: "" }, extra) } });

test("packageInfo: none without the sensor or when unavailable", () => {
  assert.strictEqual(h.packageInfo({}), null);
  assert.strictEqual(h.packageInfo({ [h.PACKAGE_SENSOR]: { state: "unavailable", attributes: {} } }), null);
  assert.strictEqual(h.packageInfo(st("reload_needed")).reloadNeeded, true);
  assert.strictEqual(h.packageInfo(st("ok")).reloadNeeded, false);
});

test("banner: shown for reload_needed, button for admin, ask text for non-admin", () => {
  const info = h.packageInfo(st("reload_needed"));
  const a = h.packageBannerView({ info, isAdmin: true });
  assert.ok(a.show); assert.strictEqual(a.text, h.PACKAGE_BANNER_TEXT); assert.strictEqual(a.button, h.PACKAGE_BUTTON);
  const n = h.packageBannerView({ info, isAdmin: false });
  assert.ok(n.show); assert.strictEqual(n.button, null); assert.ok(n.text.includes(h.PACKAGE_ASK_ADMIN));
});

test("banner: hidden for ok, problems and no sensor", () => {
  ["ok", "no_packages_dir", "unmanaged", "error"].forEach((s) => assert.strictEqual(h.packageBannerView({ info: h.packageInfo(st(s)), isAdmin: true }).show, false, s));
  assert.strictEqual(h.packageBannerView({ info: null, isAdmin: true, forced: true }).show, false);
});

test("banner: loading, loaded, error, and it leaves when the app reports ok", () => {
  const need = h.packageInfo(st("reload_needed"));
  assert.strictEqual(h.packageBannerView({ info: need, isAdmin: true, status: "loading" }).button, "Loading…");
  assert.strictEqual(h.packageBannerView({ info: need, isAdmin: true, status: "loaded" }).button, "Loaded");
  assert.strictEqual(h.packageBannerView({ info: need, isAdmin: true, status: "error", error: "boom" }).error, "boom");
  assert.strictEqual(h.packageBannerView({ info: h.packageInfo(st("ok")), isAdmin: true, forced: true, status: "loaded" }).show, false);
});

test("forced (after save) shows the banner before the app reports reload_needed", () => {
  const ok = h.packageInfo(st("ok"));
  assert.ok(h.packageBannerView({ info: ok, isAdmin: true, forced: true }).show);
  assert.strictEqual(h.packageBannerView({ info: h.packageInfo(st("error")), isAdmin: true, forced: true }).show, false);
});

test("button payload is homeassistant.reload_all", () => {
  assert.deepStrictEqual(h.packageReloadPayload(), { domain: "homeassistant", service: "reload_all", data: {} });
});

test("post-save prompt: only with the sensor and a changed other_controller", () => {
  const info = h.packageInfo(st("ok"));
  assert.strictEqual(h.packagePromptAfterSave(info, "none", "predbat"), true);
  assert.strictEqual(h.packagePromptAfterSave(info, "predbat", "predbat"), false);
  assert.strictEqual(h.packagePromptAfterSave(info, undefined, "predbat"), false);
  assert.strictEqual(h.packagePromptAfterSave(null, "none", "predbat"), false);
  assert.strictEqual(h.packagePromptAfterSave(info, "predbat", "unset"), true);
});

test("problem line: message and install guide for the three states only", () => {
  const l = h.packageProblemLine(h.packageInfo(st("unmanaged", { message: "Package edited by hand." })));
  assert.strictEqual(l.text, "Package edited by hand."); assert.strictEqual(l.href, h.INSTALL_GUIDE_URL);
  assert.ok(h.packageProblemLine(h.packageInfo(st("error"))).text.length > 0);
  assert.strictEqual(h.packageProblemLine(h.packageInfo(st("ok"))), null);
  assert.strictEqual(h.packageProblemLine(h.packageInfo(st("reload_needed"))), null);
  assert.strictEqual(h.packageProblemLine(null), null);
});
