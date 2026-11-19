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
const { ageText, dur } = sandbox.HvvRoute;

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
      ["primary", "surface"], ["primary", "sunken"], ["accent", "surface"], ["on-accent", "accent"], ["good", "good-bg"], ["mid", "mid-bg"], ["bad", "bad-bg"], ["good", "surface"], ["mid", "surface"], ["bad", "surface"],
    ];
    for (const [fg, bg] of pairs) assert.ok(ratio(t[fg], t[bg]) >= 4.5, `${name}: ${fg} auf ${bg} = ${ratio(t[fg], t[bg]).toFixed(2)}`);
  }
});

test("borders of interactive controls reach 3:1 on surface and field fill in light and dark mode", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../map/static/route.css"), "utf8");
  const dark = css.slice(css.indexOf("@media (prefers-color-scheme: dark)"), css.indexOf("*, *::before"));
  const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: dark)")));
  const night = { ...light, ...tokens(dark) };
  for (const [name, t] of [["hell", light], ["dunkel", night]]) {
    for (const bg of ["surface", "sunken"]) assert.ok(ratio(t["line-ui"], t[bg]) >= 3, `${name}: line-ui auf ${bg} = ${ratio(t["line-ui"], t[bg]).toFixed(2)}`);
  }
});

test("the page has no header block and the destination marker is the geo-alt pin", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../map/static/index.html"), "utf8");
  assert.ok(!html.includes('class="top"') && !html.includes('class="band"'));
  assert.match(html, /<svg class="mark to"[^>]*viewBox="0 0 16 16"[^>]*><path d="M8 16s6-5\.686 6-10A6 6 0 0 0 2 6c0 4\.314 6 10 6 10m0-7a3 3 0 1 1 0-6 3 3 0 0 1 0 6"\/><\/svg>/);
});

test("every transport mode has an icon the planner can draw", () => {
  const js = fs.readFileSync(path.join(__dirname, "../../map/static/route.js"), "utf8");
  for (const icon of ["bus", "sbahn", "ubahn", "train", "ship"]) assert.match(js, new RegExp(`\\n    ${icon}: svg\\(`), icon);
  assert.match(js, /const MODES = \{ B: \["bus", "Bus"\], X: \["bus", "XpressBus"\], S: \["sbahn", "S-Bahn"\], U: \["ubahn", "U-Bahn"\], R: \["train", "Regionalzug"\]/);
});

test("line badges keep their aspect ratio", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../map/static/route.css"), "utf8");
  assert.match(css, /\.rt-logo \.lb img \{[^}]*width: auto; max-width: none/);
  assert.match(css, /\.lb img \{[^}]*width: auto; max-width: none/);
});

test("durations over an hour are written as hours and minutes", () => {
  assert.deepEqual([0, 5, 59, 60, 81, 121, 125, 180].map(dur), ["0 min", "5 min", "59 min", "1 h", "1 h 21 min", "2 h 1 min", "2 h 5 min", "3 h"]);
});

test("the cancellation hint appears only when a leg lacks data", () => {
  const js = fs.readFileSync(path.join(__dirname, "../../map/static/route.js"), "utf8");
  assert.match(js, /const hint = missing\.length \? `Gemessen für[^`]*` : "";/);
});

// ---- Anschluss laut Prognose verpasst / Reisestatus ----
const R = sandbox.HvvRoute;
const T0 = Date.parse("2026-11-16T17:00:00+01:00") / 1000;
function leg(line, from, to, dep, arr, extra = {}) {
  return { line, mode: "S", direction: "Ziel", from: { name: from, id: `Master:${from}`, platform: "Gleis 1" }, to: { name: to, id: `Master:${to}`, platform: "Gleis 2" }, dep: T0 + dep * 60, arr: T0 + arr * 60, depDelayMin: 0, arrDelayMin: 0, ...extra };
}
const trip = (feederDelay, extraNext = {}) => ({
  plannedDep: T0, plannedArr: T0 + 90 * 60, realArr: T0 + (90 + feederDelay) * 60, key: "k",
  legs: [leg("S5", "A", "B", 0, 30, { arrDelayMin: feederDelay }), leg("S7", "B", "C", 33, 90, { transfer: { waitMin: 3, walkMin: 0 }, ...extraNext })],
});

test("a connection that the forecast says is missed is found with the reason", () => {
  assert.equal(R.findProblem(trip(0), 0), null);
  assert.equal(R.findProblem(trip(2), 0), null); // 3 min Umstieg, 2 min Verspaetung: knapp, aber erreichbar
  const p = R.findProblem(trip(3), 0);
  assert.deepEqual([p.kind, p.index, p.station], ["missed", 1, "B"]);
  assert.equal(p.notBefore, T0 + 33 * 60 + 0); // Zubringer 30 + 3 min Verspaetung, ohne Mindestumsteigezeit
  assert.match(R.reasonText(p, T0 + 53 * 60), /Anschluss S7 in B laut Prognose verpasst \(S5 3 min zu spät, nur 3 min Reserve\)\. Stattdessen: S7 um \d\d:\d\d\./);
  assert.equal(R.findProblem(trip(1), 3).kind, "missed"); // gewaehlte Mindest-Umsteigezeit zaehlt mit
  const cancelled = trip(0);
  cancelled.legs[1].cancelled = true;
  assert.equal(R.findProblem(cancelled, 0).kind, "cancelled");
  assert.match(R.reasonText(R.findProblem(cancelled, 0), T0 + 60 * 60), /fällt aus/);
});

test("the trip status follows the journey and warns about problems", () => {
  const before = R.tripStatus(trip(0), 0, T0 - 10 * 60);
  assert.equal(before.level, "ok");
  assert.match(before.title, /Als Nächstes: S5 ab A, Gleis 1 um \d\d:\d\d \(in 10 min\)/);
  const riding = R.tripStatus(trip(0), 0, T0 + 10 * 60);
  assert.match(riding.title, /Unterwegs: S5 Richtung Ziel/);
  assert.ok(riding.lines.some((l) => /Aussteigen in B/.test(l)) && riding.lines.some((l) => /Danach: S7 ab B/.test(l)));
  const late = R.tripStatus(trip(3), 0, T0 + 10 * 60);
  assert.equal(late.level, "bad");
  assert.match(late.lines[0], /Anschluss S7 in B laut Prognose verpasst/);
  assert.equal(R.tripStatus(trip(0), 0, T0 + 100 * 60).level, "done");
  R.state.saved = { realArrAtSave: T0 + 90 * 60 };
  const worse = R.tripStatus(trip(6), 0, T0 + 5 * 60);
  assert.ok(worse.lines.some((l) => /Seit dem Merken: Ankunft 6 min später/.test(l)));
  R.state.saved = null;
});

test("a saved trip is found again although the walk to the first stop starts at another time", () => {
  const walk = (start) => ({ line: null, from: { name: null }, to: { name: "A" }, dep: T0 + start * 60, arr: T0 + 5 * 60 });
  const ride = leg("S5", "A", "B", 5, 40);
  const first = { plannedDep: T0 + 1 * 60, legs: [walk(1), ride] };
  const later = { plannedDep: T0 + 3 * 60, legs: [walk(3), ride] };  // GTI rechnet den Start des Fussweges je Abfrage neu
  assert.equal(R.routeKey(first), R.routeKey(later));
  assert.notEqual(R.routeKey(first), R.routeKey({ plannedDep: first.plannedDep, legs: [walk(1), leg("S5", "A", "B", 25, 60)] })); // andere Abfahrt = andere Verbindung
});
