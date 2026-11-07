"use strict";
// Ausfuehren: node --test tests/js/locate.test.js
// Prueft addLocateControl() mit einem nachgebauten maplibregl/Browser: Knopf nur im sicheren Kontext, Autostart nur bei erteilter Berechtigung.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "../../map/static/app.js"), "utf8");
const part = src.slice(src.indexOf("function addLocateControl()"), src.indexOf("async function main()"));

function run({ secure, permission }) {
  const log = { options: null, triggered: 0, added: [], button: { disabled: false, title: "" }, handlers: {} };
  class GeolocateControl {
    constructor(o) { log.options = o; }
    on(ev, fn) { log.handlers[ev] = fn; }
    trigger() { log.triggered++; }
  }
  const status = { textContent: "ok" };
  const ctx = {
    window: { isSecureContext: secure },
    navigator: { geolocation: {}, permissions: { query: async () => ({ state: permission }) } },
    document: { querySelector: () => log.button, getElementById: () => status },
    maplibregl: { GeolocateControl },
    map: { addControl: (c, pos) => log.added.push(pos), once: (ev, fn) => fn() },
    setTimeout: () => {},
  };
  vm.runInNewContext(`${part}\naddLocateControl()`, ctx);
  return { log, status };
}

test("tracks the user, follows the position and caps the zoom", async () => {
  const { log } = run({ secure: true, permission: "prompt" });
  assert.equal(log.options.trackUserLocation, true);
  assert.equal(log.options.positionOptions.enableHighAccuracy, true);
  assert.ok(log.options.fitBoundsOptions.maxZoom <= 15);
  assert.deepEqual(log.added, ["top-right"]);
});

test("auto-starts only when the permission was granted before", async () => {
  const granted = run({ secure: true, permission: "granted" });
  await new Promise((r) => setImmediate(r));
  assert.equal(granted.log.triggered, 1);
  const asking = run({ secure: true, permission: "prompt" });
  await new Promise((r) => setImmediate(r));
  assert.equal(asking.log.triggered, 0);
});

test("on plain http the button is disabled with an explanation and never triggers", async () => {
  const { log } = run({ secure: false, permission: "granted" });
  await new Promise((r) => setImmediate(r));
  assert.equal(log.button.disabled, true);
  assert.match(log.button.title, /HTTPS/);
  assert.equal(log.triggered, 0);
});

test("a denied permission shows a message in the status line", () => {
  const { log, status } = run({ secure: true, permission: "prompt" });
  log.handlers.error({ code: 1 });
  assert.match(status.textContent, /verweigert/);
});
