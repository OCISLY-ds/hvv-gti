"use strict";
// Ausfuehren: node --test tests/js/route.test.js
// Prueft die Anzeige "Echtzeit-Stand: vor 12 s" des Routenplaners (map/static/route.js, laeuft ohne DOM).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "../../map/static/route.js"), "utf8");
const sandbox = {};
vm.runInNewContext(src, sandbox);
const { ageText } = sandbox.HvvRoute;

test("the age counts seconds, then minutes and seconds", () => {
  assert.equal(ageText(0), "vor 0 s");
  assert.equal(ageText(12), "vor 12 s");
  assert.equal(ageText(59), "vor 59 s");
  assert.equal(ageText(60), "vor 1 min 00 s");
  assert.equal(ageText(125), "vor 2 min 05 s");
});

test("the time row cannot be wider than the phone", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../map/static/route.css"), "utf8");
  // Datum, Abfahrt/Ankunft und "Jetzt" stehen in einem Raster, das nie breiter als die Karte wird
  assert.match(css, /\.when \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto/);
  assert.match(css, /\.when input\[type="datetime-local"\] \{[^}]*max-width: 100%/);
  assert.match(css, /#out \{[^}]*grid-template-columns: minmax\(0, 1fr\)/);
});
