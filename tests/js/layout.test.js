"use strict";
// Ausfuehren: node --test tests/js/layout.test.js
// Laedt Teile von app.js (Korridore, Ausrichtung, Spurseite) ohne Browser und prueft sie an kleinen synthetischen Netzen.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "../../map/static/app.js"), "utf8");
const part = src.slice(src.indexOf("const MERGE_DIST_M"), src.indexOf("// Signalbloecke der S-Bahn-Gleise"));
const KX = Math.cos((53.55 * Math.PI) / 180) * 111320, KY = 110540;

function load(tracks) {
  const code = `let tracks = T, corridorCache = null;\nconst isX = (l) => /^X/.test(l), isShown = () => true, colorOf = (l) => l;\n${part}\n;({ corridors, layoutPieces, trackGeoJson, setCache: (c) => { corridorCache = c; } })`;
  return vm.runInNewContext(code, { T: tracks, Math, Object, Map, Set });
}
const deg = (m, lat0 = 53.55) => m / (111320 * Math.cos((lat0 * Math.PI) / 180));
const line = (...pts) => pts.flatMap(([x, y]) => [10 + deg(x), 53.55 + y / KY]);

// Abstand eines Punktes zu den Segmenten einer Linie (Meter) und deren Richtung am naechsten Segment
function onLine(c, at) {
  let best = { d: Infinity };
  for (let i = 1; i < c.length; i++) {
    const ax = (c[i - 1][0] - at[0]) * KX, ay = (c[i - 1][1] - at[1]) * KY, bx = (c[i][0] - at[0]) * KX, by = (c[i][1] - at[1]) * KY;
    const dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy, u = l ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / l)) : 0;
    const d = Math.hypot(ax + u * dx, ay + u * dy);
    if (d < best.d) best = { d, dx, dy };
  }
  return best;
}

// Physische Lage (Ost, Nord) des Versatzes einer Linie an einem Punkt: Versatz o nach rechts der Zeichenrichtung
function sideAt(features, color, at) {
  for (const f of features) {
    if (f.properties.k !== 1 || f.properties.c !== color) continue;
    const hit = onLine(f.geometry.coordinates, at);
    if (hit.d < 12) return [hit.dy * f.properties.o, -hit.dx * f.properties.o];
  }
  throw new Error(`keine ${color}-Linie bei ${at}`);
}

test("two pieces that meet head-on keep S3 and S5 on the same physical side", () => {
  // West-Stueck (Ost-Richtung gezeichnet) und Ost-Stueck, dessen Gleis in Gegenrichtung (Ost nach West) gespeichert ist
  const tracks = {
    A: { p: line([-1000, 0], [0, 0]), l: ["S3", "S5"] },
    B: { p: line([1000, 5], [0, 5]), l: ["S3", "S5"] },
  };
  const { corridors, layoutPieces, trackGeoJson, setCache } = load(tracks);
  setCache(layoutPieces(corridors()));
  const fc = trackGeoJson().features;
  const west = [10 + deg(-300), 53.55], east = [10 + deg(300), 53.55 + 5 / KY];
  const s3West = sideAt(fc, "S3", west), s3East = sideAt(fc, "S3", east);
  const s5West = sideAt(fc, "S5", west), s5East = sideAt(fc, "S5", east);
  // S3 liegt auf beiden Seiten der Station auf derselben Seite (nach Norden bzw. Sueden), S5 gegenueber
  assert.ok(Math.sign(s3West[1]) === Math.sign(s3East[1]), `S3 wechselt die Seite: ${s3West} / ${s3East}`);
  assert.ok(Math.sign(s5West[1]) === Math.sign(s5East[1]), `S5 wechselt die Seite: ${s5West} / ${s5East}`);
  assert.notEqual(Math.sign(s3West[1]), Math.sign(s5West[1]));
});

test("a piece of an unrelated line at the same node does not decide the side", () => {
  const tracks = {
    A: { p: line([-1000, 0], [0, 0]), l: ["S3", "S5"] },
    B: { p: line([0, 0], [1000, 0]), l: ["S3", "S5"] },
    // viele fremde Gleisenden am selben Knoten, teils gegenlaeufig: duerfen die Spurseite von S3/S5 nicht verdrehen
    U1: { p: line([1000, 3], [0, 3]), l: ["U1"] },
    U2: { p: line([0, 6], [-1000, 6]), l: ["U2"] },
  };
  const { corridors, layoutPieces, trackGeoJson, setCache } = load(tracks);
  const layout = layoutPieces(corridors());
  const bySet = (keys) => layout.pieces.find((p) => keys.every((k) => p.keys.includes(k)));
  assert.equal(bySet(["A"]).sigma * bySet(["B"]).sigma, 1);
});

test("corridor detection merges parallel tracks and keeps different ones apart", () => {
  const tracks = {
    A: { p: line([0, 0], [2000, 0]), l: ["S1"] },
    B: { p: line([0, 4], [2000, 4]), l: ["S11"] }, // 4 m daneben: derselbe Korridor
    C: { p: line([0, 400], [2000, 400]), l: ["U1"] }, // weit weg
  };
  const { corridors } = load(tracks);
  const groups = corridors().map((g) => g.members.map((m) => m.key).sort().join("+")).sort();
  assert.equal(JSON.stringify(groups), JSON.stringify(["A+B", "C"]));
});
