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

// Kontraste der Textfarben auf ihren Flaechen (WCAG AA: 4,5 : 1), Hell- und Dunkelmodus
function tokens(block) {
  const out = {};
  for (const m of block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})/gi)) out[m[1]] = m[2];
  return out;
}
function lum(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

test("text colours keep AA contrast on their surfaces in light and dark mode", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../map/static/route.css"), "utf8");
  const dark = css.slice(css.indexOf("@media (prefers-color-scheme: dark)"), css.indexOf("*, *::before"));
  const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: dark)")));
  const night = { ...light, ...tokens(dark) };
  for (const [name, t] of [["hell", light], ["dunkel", night]]) {
    const pairs = [
      ["ink", "surface"], ["ink", "bg"], ["mute", "surface"], ["mute", "sunken"], ["mute", "bg"], ["on-primary", "primary"],
      ["primary", "surface"], ["good", "good-bg"], ["mid", "mid-bg"], ["bad", "bad-bg"], ["good", "surface"], ["mid", "surface"], ["bad", "surface"],
    ];
    for (const [fg, bg] of pairs) assert.ok(ratio(t[fg], t[bg]) >= 4.5, `${name}: ${fg} auf ${bg} = ${ratio(t[fg], t[bg]).toFixed(2)}`);
  }
});

test("the Hamburg band and bow are part of the page and carry no text", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../map/static/index.html"), "utf8");
  assert.match(html, /<div class="band" aria-hidden="true"><span class="bow"><\/span><\/div>/);
});
