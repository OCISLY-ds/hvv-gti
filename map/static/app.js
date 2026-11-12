"use strict";

// Linienfarben kommen vom Server aus den offiziellen Geofox-Icons (/api/colors), damit Karte und Logos uebereinstimmen; bis sie
// geladen sind und fuer Linien ohne eigenes Icon (S11, S21, S31 = Farbe der Grundlinie) gelten diese Werte
const COLORS = {};
const FALLBACK = { U: "#1a6fb5", R: "#c8102e", X: "#a61e8c", S: "#2ca147" };
const colorOf = (line) => COLORS[line] || COLORS[line.replace(/^([SU]\d)\d$/, "$1")] || (/^R[EB]/.test(line) ? "#ffffff" : FALLBACK[line[0]] || FALLBACK.S);

const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const HAMBURG = [10.0, 53.55];

let tracks = {}, stations = {}, tracksVersion = null;
let journeys = [], serverOffset = null, refreshS = 30;
const motion = window.HvvMotion;
const animator = new motion.Animator(() => tracks);
const hidden = new Set();
const isX = (line) => /^X/.test(line); // X-Busse (Xpressbus)
let busLines = new Set(); // einzelne Metrobuslinien (Bus 5), die der Server zusaetzlich liefert (/api/config)
const isBus = (line) => isX(line) || busLines.has(line); // alles, was als Bus (Haltestellen ab Zoom 12,5, magenta/Logofarbe) gezeichnet wird
// Kategorien, die der Nutzer komplett ein- und ausblenden kann (Haken im Panel)
const CATEGORIES = [["S", "S-Bahn"], ["U", "U-Bahn"], ["RE", "RE"], ["RB", "RB"], ["X", "X-Busse"], ["B", "Metrobus"], ["O", "Sonstige"]];
const catOf = (line) => (/^S\d/.test(line) ? "S" : /^U\d/.test(line) ? "U" : /^RE\d/.test(line) ? "RE" : /^RB\d/.test(line) ? "RB" : isX(line) ? "X" : busLines.has(line) ? "B" : "O");
const SYMBOLS = { S: "S", U: "U", RE: "RE", RB: "RB", X: "X", B: "B", O: "…" }; // Beschriftung der Kategorie-Schalter
const COLLAPSED = new Set(["RE", "RB"]); // in der Liste standardmaessig eingeklappt
const showCat = Object.fromEntries(CATEGORIES.map(([c]) => [c, true]));
const isShown = (line) => showCat[catOf(line)];
let geometrySig = "";
let corridorCache = null, lastFetched = null, showBlocks = false, busyKey = "";
const storage = {
  get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* privater Modus */ } },
};
const iconState = new Map();
let map, popup, selectedId = null;

function vectorStyle(url) {
  const src = "hh";
  const L = (id, type, layer, paint, extra) => ({ id, type, source: src, "source-layer": layer, paint, ...extra });
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: { hh: { type: "vector", url: `pmtiles://${url}`, attribution: OSM_ATTR } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#1b2636" } },
      L("landuse", "fill", "landuse", { "fill-color": ["match", ["get", "class"], ["park", "wood", "grass", "scrub", "national_park"], "#1f3a33", ["industrial", "parking"], "#212c3b", "#222f40"] }),
      L("water", "fill", "water", { "fill-color": "#0f2a44" }),
      L("waterway", "line", "waterway", { "line-color": "#0f2a44", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.8, 14, 4] }),
      L("building", "fill", "building", { "fill-color": "#26344a" }),
      L("road", "line", "road", {
        "line-color": ["match", ["get", "class"], ["motorway", "motorway_link"], "#5b6b85", "main", "#43526b", "#33415a"],
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.4, 12, 1, 16, 3],
      }, { filter: ["!=", ["get", "class"], "path"] }),
    ],
  };
}

// Hintergrundkarte ohne Schluessel, wenn keine lokale hamburg.pmtiles vorliegt: OpenFreeMap (Vektorkacheln im OpenMapTiles-Schema,
// https://openfreemap.org). Die frueheren CARTO-Rasterkacheln verlangen inzwischen einen API-Schluessel.
function openFreeMapStyle() {
  const src = "ofm";
  const L = (id, type, layer, paint, extra) => ({ id, type, source: src, "source-layer": layer, paint, ...extra });
  const zoomWidth = (z0, w0, z1, w1) => ["interpolate", ["linear"], ["zoom"], z0, w0, z1, w1];
  const label = (id, classes, minzoom, size) => ({
    id, type: "symbol", source: src, "source-layer": "place", minzoom, filter: ["in", ["get", "class"], ["literal", classes]],
    layout: { "text-field": ["coalesce", ["get", "name:de"], ["get", "name"]], "text-font": ["Noto Sans Regular"], "text-size": size },
    paint: { "text-color": "#7f8da3", "text-halo-color": "#16202e", "text-halo-width": 1.2 },
  });
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: {
      [src]: {
        type: "vector", url: "https://tiles.openfreemap.org/planet",
        attribution: `<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank">OpenMapTiles</a> ${OSM_ATTR}`,
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#1b2636" } },
      L("landcover", "fill", "landcover", { "fill-color": ["match", ["get", "class"], ["wood", "grass"], "#1f3a33", "#222f40"] }),
      L("landuse", "fill", "landuse", { "fill-color": ["match", ["get", "class"], ["park", "cemetery", "playground", "pitch"], "#1f3a33", ["industrial", "commercial", "retail"], "#212c3b", "#222f40"] }),
      L("park", "fill", "park", { "fill-color": "#1f3a33", "fill-opacity": 0.8 }),
      L("water", "fill", "water", { "fill-color": "#0f2a44" }),
      L("waterway", "line", "waterway", { "line-color": "#0f2a44", "line-width": zoomWidth(8, 0.8, 14, 4) }),
      L("building", "fill", "building", { "fill-color": "#26344a" }, { minzoom: 13 }),
      L("road", "line", "transportation", {
        "line-color": ["match", ["get", "class"], ["motorway", "trunk"], "#5b6b85", ["primary", "secondary"], "#43526b", "#33415a"],
        "line-width": zoomWidth(8, 0.4, 16, 3),
      }, { filter: ["!", ["in", ["get", "class"], ["literal", ["path", "rail", "transit", "pedestrian", "track", "raceway", "ferry"]]]] }),
      label("place-large", ["city", "town"], 6, zoomWidth(6, 11, 13, 16)),
      label("place-small", ["suburb", "village", "quarter"], 11, zoomWidth(11, 10, 15, 14)),
    ],
  };
}

async function loadJson(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// Gleisabschnitte, die (auch in Gegenrichtung) auf ganzer Laenge hoechstens MERGE_DIST_M auseinanderliegen, werden zu einem
// Korridor zusammengefasst und nur einmal gezeichnet; jede Linie darauf bekommt eine eigene Spur (Versatz in Linienbreiten),
// sodass z.B. U2 und U4 oder S3 und S5 wie auf dem Netzplan nebeneinander laufen. Erst ab groesserem Abstand
// (getrennte Gleise, Umfahrungen) bleiben die Abschnitte getrennt.
const MERGE_DIST_M = 35;
const MERGE_SHARE = 0.9;
const KX = Math.cos((53.55 * Math.PI) / 180) * 111320, KY = 110540;

// Linie mit Segmentraster (Zellen von CELL_M): fuer Abstaende bis CELL_M muessen nur die Segmente der Nachbarzellen geprueft
// werden statt der ganzen Linie; bei Abstaenden darueber liefert distToPoly Infinity
function makePoly(P) {
  const cells = new Map();
  const add = (key, seg) => {
    const list = cells.get(key);
    if (!list) cells.set(key, [seg]);
    else if (list[list.length - 1] !== seg) list.push(seg);
  };
  for (let i = 2, seg = 0; i < P.length; i += 2, seg++) {
    const ax = P[i - 2], ay = P[i - 1], dx = P[i] - ax, dy = P[i + 1] - ay, len = Math.hypot(dx, dy), steps = Math.max(1, Math.ceil(len / 20));
    for (let k = 0; k <= steps; k++) add(`${Math.floor((ax + (dx * k) / steps) / CELL_M)},${Math.floor((ay + (dy * k) / steps) / CELL_M)}`, seg);
  }
  return { P, cells };
}

function distToPoly(x, y, poly) {
  const P = poly.P, cx = Math.floor(x / CELL_M), cy = Math.floor(y / CELL_M);
  let best = Infinity;
  for (let gx = -1; gx <= 1; gx++) {
    for (let gy = -1; gy <= 1; gy++) {
      for (const seg of poly.cells.get(`${cx + gx},${cy + gy}`) || []) {
        const i = seg * 2 + 2, ax = P[i - 2], ay = P[i - 1], dx = P[i] - ax, dy = P[i + 1] - ay, l = dx * dx + dy * dy;
        const u = l ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / l)) : 0;
        const d = Math.hypot(x - ax - u * dx, y - ay - u * dy);
        if (d < best) best = d;
      }
    }
  }
  return best;
}

function sampleEvery(P, step) {
  const out = [P[0], P[1]];
  for (let i = 2; i < P.length; i += 2) {
    const ax = P[i - 2], ay = P[i - 1], dx = P[i] - ax, dy = P[i + 1] - ay, len = Math.hypot(dx, dy);
    for (let d = step; d <= len; d += step) out.push(ax + (dx * d) / len, ay + (dy * d) / len);
  }
  out.push(P[P.length - 2], P[P.length - 1]);
  return out;
}

// Anteil der Stuetzpunkte von S, die hoechstens MERGE_DIST_M von der Linie P entfernt liegen; bricht ab, sobald MERGE_SHARE
// nicht mehr erreichbar ist (liefert dann 0), das spart bei den vielen ungleichen Paaren fast die ganze Rechenzeit
function shareWithin(S, poly) {
  const n = S.length / 2, allowedMisses = Math.floor(n * (1 - MERGE_SHARE));
  let miss = 0;
  for (let i = 0; i < S.length; i += 2) {
    if (distToPoly(S[i], S[i + 1], poly) > MERGE_DIST_M && ++miss > allowedMisses) return 0;
  }
  return 1 - miss / n;
}

// Raster fuer die Vorauswahl: zwei Punkte, die hoechstens MERGE_DIST_M plus halber Stuetzpunktabstand (7,5 m) auseinanderliegen,
// liegen in Zellen, die sich um hoechstens 1 unterscheiden, solange die Zelle mindestens so gross ist
const CELL_M = 50;

function geometrySignature() {
  let points = 0, bus = 0, n = 0;
  for (const t of Object.values(tracks)) {
    n++;
    points += t.p.length;
    if (t.l.every(isBus)) bus++;
  }
  return `${n}:${points}:${bus}`;
}

function corridors() {
  const items = Object.entries(tracks).filter(([, t]) => t.p.length >= 4).map(([key, t]) => {
    const P = [];
    for (let i = 0; i + 1 < t.p.length; i += 2) P.push(t.p[i] * KX, t.p[i + 1] * KY);
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (let i = 0; i < P.length; i += 2) {
      box[0] = Math.min(box[0], P[i]); box[1] = Math.min(box[1], P[i + 1]);
      box[2] = Math.max(box[2], P[i]); box[3] = Math.max(box[3], P[i + 1]);
    }
    return { key, t, P, poly: makePoly(P), S: sampleEvery(P, 15), box, bus: t.l.every(isBus), root: null };
  });
  const find = (x) => { while (x.root) x = x.root; return x; };
  // Vorauswahl ueber ein Zellraster: ein Paar kommt nur in Frage, wenn von jedem Abschnitt mindestens MERGE_SHARE der Stuetzpunkte
  // in einer Zelle liegen, die der andere (samt Nachbarzellen) beruehrt; das ist eine notwendige Bedingung des genauen Tests
  const cellKey = (x, y) => `${Math.floor(x / CELL_M)},${Math.floor(y / CELL_M)}`;
  const reach = items.map((it) => {
    const cells = new Set();
    for (let i = 0; i < it.S.length; i += 2) {
      const cx = Math.floor(it.S[i] / CELL_M), cy = Math.floor(it.S[i + 1] / CELL_M);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) cells.add(`${cx + dx},${cy + dy}`);
    }
    return cells;
  });
  const owners = new Map();
  reach.forEach((cells, i) => { for (const c of cells) (owners.get(c) || owners.set(c, []).get(c)).push(i); });
  // covered[i] -> Map(j -> Anzahl Stuetzpunkte von i in der Reichweite von j)
  const covered = items.map((it, i) => {
    const counts = new Map();
    for (let k = 0; k < it.S.length; k += 2) for (const j of owners.get(cellKey(it.S[k], it.S[k + 1])) || []) if (j !== i) counts.set(j, (counts.get(j) || 0) + 1);
    return counts;
  });
  for (let i = 0; i < items.length; i++) {
    for (const [j, countIJ] of covered[i]) {
      if (j <= i) continue;
      const A = items[i], B = items[j];
      if (A.bus !== B.bus) continue; // Bus und Schiene werden nie zusammengelegt
      if (countIJ < MERGE_SHARE * (A.S.length / 2) || (covered[j].get(i) || 0) < MERGE_SHARE * (B.S.length / 2)) continue;
      const ra = find(A), rb = find(B);
      if (ra === rb) continue;
      if (shareWithin(A.S, B.poly) >= MERGE_SHARE && shareWithin(B.S, A.poly) >= MERGE_SHARE) ra.root = rb;
    }
  }
  const groups = new Map();
  for (const it of items) {
    const r = find(it);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r).push(it);
  }
  // Gezeichnet wird die Geometrie des laengsten Abschnitts je Korridor. Was ein anderer Abschnitt der Gruppe ausserhalb davon
  // fuehrt (z. B. das Stueck zum anderen Bahnsteig, eine Umfahrung), wird als eigenes Teilstueck seiner Linien ergaenzt,
  // sonst haette die Linie dort ein offenes Ende.
  return [...groups.values()].map((members) => {
    const lead = members.reduce((a, b) => (b.S.length > a.S.length ? b : a));
    const extras = [];
    for (const m of members) if (m !== lead) for (const run of uncovered(m, lead)) extras.push({ run, key: m.key });
    return { members, lead, extras };
  });
}

const COVER_M = 12;

// Teilstuecke (flache Listen in Metern) von `it`, die mehr als COVER_M von der Linie `lead` entfernt liegen, je mit einem
// Stützpunkt Ueberlappung zu beiden Seiten
function uncovered(it, lead) {
  const S = it.S, n = S.length / 2, runs = [];
  let start = -1;
  for (let i = 0; i <= n; i++) {
    const far = i < n && distToPoly(S[2 * i], S[2 * i + 1], lead.poly) > COVER_M;
    if (far && start < 0) start = i;
    if (!far && start >= 0) {
      if (i - start >= 2) runs.push(S.slice(2 * Math.max(0, start - 1), 2 * Math.min(n - 1, i) + 2));
      start = -1;
    }
  }
  return runs;
}

// ---- Teilstuecke ausrichten und Spuren fliessend verbinden ----
//
// Jedes Teilstueck (Korridor-Geometrie oder Zusatzstueck) wird so gerichtet, dass entlang zusammenhaengender Strecken alle in
// dieselbe Richtung zeigen. Das Spurversatz-Vorzeichen (rechts/links der Linienrichtung) bleibt damit entlang der Strecke
// dieselbe physische Seite; bisher drehte jedes Stueck nach seiner eigenen West-Ost-Regel und die Linie sprang seitlich.
// An den Verbindungen wird der Versatz jeder Linie auf den Mittelwert der Nachbarn gelegt und in den letzten TRANS_M Metern
// stufenlos (in kleinen Teilstuecken) dorthin ueberblendet.
const LINK_M = 80; // Enden bis zu diesem Abstand mit gemeinsamer Linie gelten fuer die Spurseite als verbunden
const JOIN_M = 30, TRANS_M = 120, TRANS_STEP_M = 30, TANGENT_M = 12;

function metresOf(pts) {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot((pts[i][0] - pts[i - 1][0]) * KX, (pts[i][1] - pts[i - 1][1]) * KY));
  return cum;
}

// Richtung (Einheitsvektor in Metern) vom Ende `end` ("s" Anfang, "e" Ende) ins Teilstueck hinein
function awayTangent(piece, end) {
  const pts = piece.pts, cum = piece.cum, total = cum[cum.length - 1];
  const from = end === "s" ? 0 : pts.length - 1, step = end === "s" ? 1 : -1;
  const dist = (k) => (end === "s" ? cum[k] : total - cum[k]);
  let j = from;
  while (j + step >= 0 && j + step < pts.length && dist(j) < TANGENT_M) j += step;
  const dx = (pts[j][0] - pts[from][0]) * KX, dy = (pts[j][1] - pts[from][1]) * KY, l = Math.hypot(dx, dy) || 1;
  return [dx / l, dy / l];
}

function reversePiece(piece) {
  piece.pts.reverse();
  piece.cum = metresOf(piece.pts);
  [piece.nodeS, piece.nodeE] = [piece.nodeE, piece.nodeS];
}

function layoutPieces(groups) {
  const pieces = [];
  for (const { members, lead, extras } of groups) {
    const pts = [];
    for (let i = 0; i + 1 < lead.t.p.length; i += 2) pts.push([lead.t.p[i], lead.t.p[i + 1]]);
    pieces.push({ pts, keys: members.map((m) => m.key) });
    for (const { run, key } of extras) {
      const part = [];
      for (let i = 0; i + 1 < run.length; i += 2) part.push([run[i] / KX, run[i + 1] / KY]);
      pieces.push({ pts: part, keys: [key] });
    }
  }
  for (const p of pieces) p.cum = metresOf(p.pts);
  // Verbindungsknoten: Enden, die hoechstens JOIN_M auseinanderliegen
  const ends = [];
  pieces.forEach((p, i) => {
    p.id = i;
    ends.push({ p, end: "s", x: p.pts[0][0] * KX, y: p.pts[0][1] * KY, node: -1 });
    ends.push({ p, end: "e", x: p.pts[p.pts.length - 1][0] * KX, y: p.pts[p.pts.length - 1][1] * KY, node: -1 });
  });
  const grid = new Map(), cell = (v) => Math.floor(v / JOIN_M);
  for (const e of ends) {
    const key = `${cell(e.x)},${cell(e.y)}`;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(e);
  }
  const nodes = [];
  for (const e of ends) {
    if (e.node >= 0) continue;
    const id = nodes.length, members = [], stack = [e];
    e.node = id;
    while (stack.length) {
      const c = stack.pop();
      members.push(c);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const o of grid.get(`${cell(c.x) + dx},${cell(c.y) + dy}`) || []) {
          if (o.node < 0 && Math.hypot(o.x - c.x, o.y - c.y) <= JOIN_M) { o.node = id; stack.push(o); }
        }
      }
    }
    nodes.push(members);
  }
  for (const e of ends) e.p[e.end === "s" ? "nodeS" : "nodeE"] = e.node;

  // Richtung: vom laengsten Stueck aus durch die Knoten propagieren
  const seen = new Set();
  const order = [...pieces].sort((a, b) => b.cum[b.cum.length - 1] - a.cum[a.cum.length - 1]);
  for (const seed of order) {
    if (seen.has(seed.id)) continue;
    const dx = (seed.pts[seed.pts.length - 1][0] - seed.pts[0][0]) * KX, dy = (seed.pts[seed.pts.length - 1][1] - seed.pts[0][1]) * KY;
    if (Math.abs(dx) >= Math.abs(dy) ? dx < 0 : dy < 0) reversePiece(seed); // Ausgangsrichtung: West nach Ost (steil: Sued nach Nord)
    seen.add(seed.id);
    const queue = [seed];
    while (queue.length) {
      const c = queue.shift();
      for (const end of ["s", "e"]) {
        const node = end === "s" ? c.nodeS : c.nodeE, aC = awayTangent(c, end);
        for (const o of nodes[node]) {
          const q = o.p;
          if (seen.has(q.id)) continue;
          // `o.end` ist das Ende von q in dessen Ausgangsrichtung (q wurde noch nicht gedreht)
          const aQ = awayTangent(q, o.end), dot = aC[0] * aQ[0] + aC[1] * aQ[1];
          const leaves = (end === "e") === dot < 0; // q beginnt am Knoten (verlaesst ihn) statt dort zu enden
          const reversed = (o.end === "s") !== leaves;
          if (reversed) reversePiece(q);
          seen.add(q.id);
          queue.push(q);
        }
      }
    }
  }
  // Knoten nach dem Drehen neu aufbauen: je Knoten die Enden mit ihrer endgueltigen Lage (Anfang/Ende)
  const finalNodes = nodes.map(() => []);
  for (const p of pieces) {
    finalNodes[p.nodeS].push({ p, end: "s" });
    finalNodes[p.nodeE].push({ p, end: "e" });
  }
  assignSigma(pieces, finalNodes);
  return { pieces, nodes: finalNodes };
}

// sigma (+1/-1) je Stueck: auf welcher Seite des Stuecks die Spuren im gemeinsamen Bezugssystem liegen. Laufen zwei Stuecke
// am Knoten entgegengesetzt (frontal aufeinander zu oder beide vom Knoten weg), ist "rechts" des einen das "links" des anderen;
// die Spurreihenfolge wird dann gespiegelt, damit jede Linie auf derselben Seite des Gleises bleibt statt am Knoten zu kreuzen.
// Die Beziehungen werden nach Eindeutigkeit (parallele Tangenten zuerst, fast rechtwinklige Abzweige zuletzt) in eine
// Vorzeichen-Union-Find-Struktur aufgenommen; widerspruechliche Kanten in Schleifen verlieren gegen die eindeutigeren.
function assignSigma(pieces, nodes) {
  // Nur Stuecke mit gemeinsamer Linie haengen bei der Spurseite voneinander ab; an grossen Knoten (Hauptbahnhof: 21 Gleisenden)
  // wuerden sonst fremde Linien falsche Zwangsbedingungen erzeugen und die Seiten anderswo kippen lassen.
  const lineSet = pieces.map((p) => {
    const set = new Set();
    for (const k of p.keys) for (const l of tracks[k]?.l || []) set.add(l);
    return set;
  });
  const shareLine = (a, b) => [...lineSet[a.id]].some((l) => lineSet[b.id].has(l));
  const edges = [];
  for (const node of nodes) {
    for (let i = 0; i < node.length; i++) {
      for (let j = i + 1; j < node.length; j++) {
        const a = node[i], b = node[j];
        if (a.p === b.p || !shareLine(a.p, b.p)) continue;
        const ta = travelTangent(a.p, a.end), tb = travelTangent(b.p, b.end), dot = ta[0] * tb[0] + ta[1] * tb[1];
        edges.push({ a: a.p.id, b: b.p.id, w: Math.abs(dot), s: dot >= 0 ? 1 : -1 });
      }
    }
  }
  // Zusaetzliche, schwaechere Verknuepfungen: Enden, die zu weit fuer einen Knoten auseinanderliegen (Bahnsteigvarianten, Luecken
  // in den HVV-Daten), aber dieselbe Linie fuehren. Ohne sie bekaeme ein Stueck, das nur ueber so eine Luecke anschliesst, seine
  // Spurseite zufaellig und die Linien wuerden dort die Seiten tauschen.
  const ends = [], grid = new Map(), cell = (v) => Math.floor(v / LINK_M);
  for (const p of pieces) {
    for (const end of ["s", "e"]) {
      const q = end === "s" ? p.pts[0] : p.pts[p.pts.length - 1];
      ends.push({ p, end, x: q[0] * KX, y: q[1] * KY, node: end === "s" ? p.nodeS : p.nodeE });
    }
  }
  for (const e of ends) {
    const key = `${cell(e.x)},${cell(e.y)}`;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(e);
  }
  for (const e of ends) {
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      for (const o of grid.get(`${cell(e.x) + dx},${cell(e.y) + dy}`) || []) {
        if (o.p.id <= e.p.id || o.node === e.node) continue;
        const dist = Math.hypot(e.x - o.x, e.y - o.y);
        if (dist > LINK_M || !shareLine(e.p, o.p)) continue;
        const ta = travelTangent(e.p, e.end), tb = travelTangent(o.p, o.end), dot = ta[0] * tb[0] + ta[1] * tb[1];
        edges.push({ a: e.p.id, b: o.p.id, w: 0.5 * Math.abs(dot) * (1 - dist / (2 * LINK_M)), s: dot >= 0 ? 1 : -1 });
      }
    }
  }
  edges.sort((x, y) => y.w - x.w);
  const parent = pieces.map((_, i) => i), parity = pieces.map(() => 1); // parity: sigma des Stuecks relativ zu seinem Elternteil
  const find = (x) => {
    let par = 1;
    while (parent[x] !== x) { par *= parity[x]; x = parent[x]; }
    return [x, par];
  };
  for (const e of edges) {
    const [ra, pa] = find(e.a), [rb, pb] = find(e.b);
    if (ra === rb) continue;
    parent[rb] = ra;
    parity[rb] = e.s * pa * pb;
  }
  for (const p of pieces) p.sigma = find(p.id)[1];
}

// Fahrtrichtung des Teilstuecks am Knoten (am Anfang vom Knoten weg, am Ende zum Knoten hin)
function travelTangent(piece, end) {
  const a = awayTangent(piece, end);
  return end === "s" ? a : [-a[0], -a[1]];
}

// Teilstueck der Linie zwischen den Streckenlaengen d0 und d1 (Meter)
function slicePoints(pts, cum, d0, d1) {
  const at = (d) => {
    let i = 1;
    while (i < cum.length - 1 && cum[i] < d) i++;
    const span = cum[i] - cum[i - 1] || 1, r = Math.min(1, Math.max(0, (d - cum[i - 1]) / span));
    return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * r, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * r];
  };
  const out = [at(d0)];
  for (let i = 1; i < pts.length - 1; i++) if (cum[i] > d0 && cum[i] < d1) out.push(pts[i]);
  out.push(at(d1));
  return out;
}

function trackGeoJson() {
  const features = [];
  corridorCache ||= layoutPieces(corridors()); // teuer und unabhaengig vom Schalter fuer die X-Busse
  const { pieces, nodes } = corridorCache;
  // Linien eines Stuecks: aus den aktuellen Gleisen (die Linienlisten aendern sich, die Geometrie meist nicht)
  const sortedLines = (p) => {
    const lines = new Set();
    for (const k of p.keys) for (const l of tracks[k]?.l || []) lines.add(l);
    return [...lines].filter(isShown).sort((x, y) => x.localeCompare(y, "de", { numeric: true }));
  };
  const laneOf = new Map(); // Stueck -> {Linie: Versatz in Spuren}
  for (const p of pieces) {
    const ls = sortedLines(p), lanes = {};
    ls.forEach((l, i) => (lanes[l] = i - (ls.length - 1) / 2));
    laneOf.set(p, lanes);
  }
  // Spurwert am Knoten im Bezugssystem (ohne sigma): Mittelwert der Stuecke, die die Linie dort haben, damit alle denselben
  // Punkt ansteuern; gezeichnet wird er mit dem sigma des jeweiligen Stuecks
  const meanAt = new Map();
  const target = (p, end, l) => {
    const node = end === "s" ? p.nodeS : p.nodeE, key = `${node}|${l}`;
    let m = meanAt.get(key);
    if (m === undefined) {
      const users = nodes[node].filter((o) => laneOf.get(o.p)[l] !== undefined);
      m = users.reduce((sum, o) => sum + laneOf.get(o.p)[l], 0) / users.length;
      meanAt.set(key, m);
    }
    return m;
  };
  for (const p of pieces) {
    const lines = sortedLines(p);
    if (!lines.length) continue;
    const total = p.cum[p.cum.length - 1], lanes = laneOf.get(p);
    features.push({ type: "Feature", properties: { k: 0, n: lines.length }, geometry: { type: "LineString", coordinates: p.pts } });
    const lt = Math.min(TRANS_M, total / 2);
    for (const l of lines) {
      const own = p.sigma * lanes[l], t0 = p.sigma * target(p, "s", l), t1 = p.sigma * target(p, "e", l), color = colorOf(l);
      const emit = (o, c) => features.push({ type: "Feature", properties: { k: 1, c: color, o }, geometry: { type: "LineString", coordinates: c } });
      if ((Math.abs(t0 - own) < 0.05 && Math.abs(t1 - own) < 0.05) || lt < 8) { emit(own, p.pts); continue; }
      const steps = Math.max(1, Math.round(lt / TRANS_STEP_M)), cuts = [0];
      for (let i = 1; i <= steps; i++) cuts.push((lt * i) / steps);
      cuts.push(total - lt);
      for (let i = steps - 1; i >= 0; i--) cuts.push(total - (lt * i) / steps);
      for (let i = 0; i + 1 < cuts.length; i++) {
        if (cuts[i + 1] - cuts[i] < 0.5) continue;
        const mid = (cuts[i] + cuts[i + 1]) / 2;
        const w0 = Math.max(0, 1 - mid / lt), w1 = Math.max(0, 1 - (total - mid) / lt);
        emit(own + (t0 - own) * w0 + (t1 - own) * w1, slicePoints(p.pts, p.cum, cuts[i], cuts[i + 1]));
      }
    }
  }
  return { type: "FeatureCollection", features };
}

// Signalbloecke der S-Bahn-Gleise: je Block ein Stueck der Linie (abwechselnde Farben), dazu die Signale als Punkte. Die Bloecke
// am Anfang und Ende eines Abschnitts sind Bahnhofsbloecke und haben dieselbe ID wie der Anschlussabschnitt.
function blockGeoJson() {
  const blocks = [], signals = [];
  for (const [k, t] of Object.entries(tracks)) {
    if (!t.s || !t.s.length || t.p.length < 4 || !t.l.some((l) => /^S\d/.test(l) && isShown(l))) continue;
    const edges = [0, ...t.s, 1];
    for (let i = 0; i + 1 < edges.length; i++) {
      blocks.push({
        type: "Feature",
        properties: { id: motion.blockId(k, t.s, i), alt: i % 3 },
        geometry: { type: "LineString", coordinates: motion.slice(t, edges[i], edges[i + 1]) },
      });
    }
    for (const f of t.s) {
      const q = motion.locate(t, f);
      signals.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [q.x, q.y] } });
    }
  }
  return { blocks: { type: "FeatureCollection", features: blocks }, signals: { type: "FeatureCollection", features: signals } };
}

function rebuildBlocks() {
  const empty = { type: "FeatureCollection", features: [] };
  const data = showBlocks ? blockGeoJson() : { blocks: empty, signals: empty };
  map.getSource("blocks").setData(data.blocks);
  map.getSource("signals").setData(data.signals);
  for (const id of ["blocks", "blocks-busy", "signals"]) map.setLayoutProperty(id, "visibility", showBlocks ? "visible" : "none");
  busyKey = "";
}

// Belegte Bloecke rot einfaerben (hoechstens zweimal pro Sekunde, nur bei Aenderung)
let busyAt = 0;
function updateBusyBlocks(entries) {
  if (!showBlocks || performance.now() - busyAt < 500) return;
  busyAt = performance.now();
  const ids = new Set();
  for (const e of entries) for (const id of e.blocks || []) ids.add(id);
  const key = [...ids].sort().join("|");
  if (key === busyKey) return;
  busyKey = key;
  map.setFilter("blocks-busy", ["in", ["get", "id"], ["literal", [...ids]]]);
}

function stationGeoJson() {
  return {
    type: "FeatureCollection",
    features: Object.entries(stations)
      .filter(([, s]) => s.l.some(isShown))
      .map(([k, s]) => ({ type: "Feature", properties: { k, n: s.n, b: s.l.every(isBus) ? 1 : 0 }, geometry: { type: "Point", coordinates: [s.x, s.y] } })),
  };
}

async function refreshTracks() {
  const data = await loadJson("/api/tracks");
  if (data.v === tracksVersion) return;
  tracksVersion = data.v;
  tracks = data.tracks;
  stations = data.stations;
  buildStationGrid();
  for (const t of Object.values(tracks)) t.cum = null;
  // Die Korridor-Zusammenfassung ist teuer: nur neu berechnen, wenn sich die Geometrie aendert, nicht bei neuen Linien auf alten Gleisen
  const sig = geometrySignature();
  if (sig !== geometrySig) corridorCache = null;
  geometrySig = sig;
  rebuildTracks();
}

function rebuildTracks() {
  map.getSource("tracks").setData(trackGeoJson());
  map.getSource("stations").setData(stationGeoJson());
  if (showBlocks) rebuildBlocks();
}

function fmtEta(sec) {
  const t = Math.max(0, Math.floor(sec));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

// Pfeil (Spitze oben, Norden) im Abstand zum Zugsymbol; wird per icon-rotate in Fahrtrichtung gedreht
function arrowImage() {
  const size = 96, c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  g.beginPath();
  g.moveTo(48, 2);
  g.lineTo(62, 28);
  g.lineTo(48, 21);
  g.lineTo(34, 28);
  g.closePath();
  g.lineJoin = "round";
  g.lineWidth = 4;
  g.strokeStyle = "#0b121c";
  g.stroke();
  g.fillStyle = "#ffffff";
  g.fill();
  return g.getImageData(0, 0, size, size);
}

function serverNow() {
  return Date.now() / 1000 + (serverOffset || 0);
}

const ICON_H = 28; // Hoehe der Zugsymbole in CSS-Pixeln bei icon-size 1
const onWhite = (line) => /^R[EB]/.test(line); // helle/rote Regionalzug-Logos brauchen weissen Grund

function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

function loadIcon(line) {
  if (iconState.has(line)) return;
  iconState.set(line, "loading");
  const img = new Image();
  img.onload = () => {
    try {
      let source = img, ratio = img.naturalHeight / ICON_H;
      if (onWhite(line)) {
        const pad = Math.round(img.naturalHeight * 0.12), c = document.createElement("canvas");
        c.width = img.naturalWidth + 2 * pad;
        c.height = img.naturalHeight + 2 * pad;
        const g = c.getContext("2d");
        g.fillStyle = "#ffffff";
        roundRect(g, 0, 0, c.width, c.height, c.height * 0.22);
        g.fill();
        g.drawImage(img, pad, pad);
        source = g.getImageData(0, 0, c.width, c.height);
        ratio = c.height / ICON_H;
      }
      if (!map.hasImage(`ic-${line}`)) map.addImage(`ic-${line}`, source, { pixelRatio: ratio });
      iconState.set(line, "ok");
    } catch (e) {
      iconState.set(line, "fail");
    }
  };
  img.onerror = () => iconState.set(line, "fail");
  img.src = `/icon/${encodeURIComponent(line)}.png`;
}

// Verspaetungsplakette: rote Zahl auf weissem Grund, je Minutenwert einmal erzeugt
function delayBadge(minutes, trend) {
  const name = `dl-${minutes}-${trend}`;
  if (map.hasImage(name)) return name;
  const text = `+${minutes}`, ratio = 2, h = 20, font = `bold ${13 * ratio}px system-ui, sans-serif`;
  const probe = document.createElement("canvas").getContext("2d");
  probe.font = font;
  const textW = Math.ceil(probe.measureText(text).width / ratio);
  const w = textW + 10 + (trend ? 13 : 0);
  const c = document.createElement("canvas");
  c.width = w * ratio;
  c.height = h * ratio;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff";
  roundRect(g, 1, 1, c.width - 2, c.height - 2, 8 * ratio);
  g.fill();
  g.lineWidth = ratio;
  g.strokeStyle = "#0b121c";
  g.stroke();
  g.font = font;
  g.fillStyle = "#d0021b";
  g.textAlign = "left";
  g.textBaseline = "middle";
  g.fillText(text, 5 * ratio, c.height / 2 + ratio);
  if (trend) {
    // Pfeil nach oben rechts (rot): Verspaetung waechst; nach unten links (gruen): wird abgebaut
    const cx = (5 + textW + 7) * ratio, cy = c.height / 2, r = 4 * ratio, s = trend > 0 ? 1 : -1;
    g.strokeStyle = trend > 0 ? "#d0021b" : "#1a9c3e";
    g.lineWidth = 2 * ratio;
    g.lineCap = g.lineJoin = "round";
    g.beginPath();
    g.moveTo(cx - s * r, cy + s * r);
    g.lineTo(cx + s * r, cy - s * r);
    g.moveTo(cx + s * r - s * 1.6 * r, cy - s * r);
    g.lineTo(cx + s * r, cy - s * r);
    g.lineTo(cx + s * r, cy - s * r + s * 1.6 * r);
    g.stroke();
  }
  map.addImage(name, g.getImageData(0, 0, c.width, c.height), { pixelRatio: ratio });
  return name;
}

// Bildwiederholrate: jedes Bild (hoechstens ~65 fps); wird das Neuzeichnen teuer, faellt sie auf 30 bzw. 15 fps zurueck
const FRAME_MS = [15, 33, 66];
let frameTier = 0, lastFrame = 0, frameCost = 0;

function frame(ts) {
  requestAnimationFrame(frame);
  if (ts - lastFrame < FRAME_MS[frameTier] - 2) return;
  lastFrame = ts;
  const t0 = performance.now();
  renderTrains();
  frameCost = frameCost * 0.9 + (performance.now() - t0) * 0.1;
  frameTier = frameCost > 14 ? 2 : frameCost > 7 ? 1 : 0;
}

// Symbolgroessen in Pixeln je Zoomstufe; muessen zu den Kartenebenen "trains-dot" und "stations" passen
const lerpZoom = (z, pts) => {
  if (z <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    if (z <= pts[i][0]) return pts[i - 1][1] + ((pts[i][1] - pts[i - 1][1]) * (z - pts[i - 1][0])) / (pts[i][0] - pts[i - 1][0]);
  }
  return pts[pts.length - 1][1];
};
const dotRadius = (z) => lerpZoom(z, [[9, 4], [15, 9]]);
const pinRadius = (z) => lerpZoom(z, [[9, 5], [12, 8], [16, 12]]);
const metresPerPixel = (z, lat) => (78271.517 * Math.cos((lat * Math.PI) / 180)) / 2 ** z;

// Haltestellen als Raster in Metern, um Zuege am Bahnsteig schnell zu erkennen
let stationGrid = new Map();
function buildStationGrid() {
  stationGrid = new Map();
  for (const s of Object.values(stations)) {
    const key = `${Math.floor((s.x * KX) / 150)},${Math.floor((s.y * KY) / 150)}`;
    if (!stationGrid.has(key)) stationGrid.set(key, []);
    stationGrid.get(key).push([s.x * KX, s.y * KY]);
  }
}
function nearStation(mx, my, radiusM) {
  const cx = Math.floor(mx / 150), cy = Math.floor(my / 150), reach = Math.ceil(radiusM / 150);
  for (let dx = -reach; dx <= reach; dx++) {
    for (let dy = -reach; dy <= reach; dy++) {
      for (const [sx, sy] of stationGrid.get(`${cx + dx},${cy + dy}`) || []) if (Math.hypot(mx - sx, my - sy) <= radiusM) return true;
    }
  }
  return false;
}

// Ueberdeckte Zuege senkrecht stapeln; das Ergebnis (e.sx, e.sy) ist die gezeichnete Position, weich nachgefuehrt
const STACK_MIN_ZOOM = 12.5;
let lastStackAt = 0;
function stackOnScreen(items) {
  const z = map.getZoom(), mpp = metresPerPixel(z, map.getCenter().lat), dot = dotRadius(z), pin = pinRadius(z);
  const stepPx = 2 * dot + 3, nowMs = performance.now(), dt = Math.min(0.1, (nowMs - lastStackAt) / 1000);
  lastStackAt = nowMs;
  // Stapeln lohnt erst im Stadtteil-Massstab; weiter herausgezoomt liegen ohnehin ganze Strecken uebereinander
  const targets = z < STACK_MIN_ZOOM ? new Map() : motion.stackTargets(
    items.map((e) => ({ id: e.id, mx: e.pose.x * KX, my: e.pose.y * KY, dwell: e.pose.dwell })),
    { thresholdM: Math.max(2 * dot, 14) * mpp, stepPx, baseUpPx: pin + dot + 3, nearAnchor: (mx, my) => nearStation(mx, my, Math.max(pin + dot, 12) * mpp) }
  );
  const k = 1 - Math.exp(-10 * dt);
  for (const e of items) {
    const target = targets.get(e.id) || 0;
    e.stackPx = e.stackPx === undefined ? target : e.stackPx + (target - e.stackPx) * k;
    e.sx = e.pose.x;
    e.sy = e.pose.y - (e.stackPx * mpp) / KY; // Bildschirm oben = Norden
  }
}

function renderTrains() {
  const now = serverNow(), entries = animator.step(now), features = [];
  updateBusyBlocks(entries);
  const bounds = map.getBounds(), padX = (bounds.getEast() - bounds.getWest()) * 0.15, padY = (bounds.getNorth() - bounds.getSouth()) * 0.15;
  const west = bounds.getWest() - padX, east = bounds.getEast() + padX, south = bounds.getSouth() - padY, north = bounds.getNorth() + padY;
  const visible = [];
  for (const e of entries) {
    const { j, pose: p } = e;
    if (hidden.has(j.l) || !isShown(j.l)) continue;
    if (e.id !== selectedId && (p.x < west || p.x > east || p.y < south || p.y > north)) continue; // ausserhalb des Kartenausschnitts
    visible.push(e);
  }
  stackOnScreen(visible);
  for (const e of visible) {
    const { j, pose: p } = e;
    if (e.id === selectedId) {
      if (popup && popup.isOpen() && !(sel && sel.sheet)) popup.setLngLat([e.sx, e.sy]);
      // Handy: der Zug bleibt sichtbar, solange die Karte nicht selbst verschoben wird
      if (sel && sel.sheet && !sel.userMoved && performance.now() - (sel.lastPan || 0) > 4000 && sel.lastPan !== undefined) {
        sel.lastPan = performance.now();
        map.easeTo({ center: [e.sx, e.sy], duration: 3000, easing: (x) => x });
      }
      updatePopup(e, now);
    }
    loadIcon(j.l);
    const next = motion.nextStop(j, e.tau); // Abschnitt nach der angezeigten Zugzeit, passend zur Position
    const late = Math.round(p.seg.d || 0);
    features.push({
      type: "Feature",
      id: features.length,
      properties: {
        id: j.id, l: j.l, c: colorOf(j.l), sel: e.id === selectedId ? 1 : 0, icon: iconState.get(j.l) === "ok" ? `ic-${j.l}` : "",
        rot: e.rot || 0, eta: next ? fmtEta(next.at - now) : "", nxt: next ? next.name : "", dl: late >= 1 ? delayBadge(late, e.trend) : "",
      },
      geometry: { type: "Point", coordinates: [e.sx, e.sy] },
    });
  }
  map.getSource("trains").setData({ type: "FeatureCollection", features });
}

function renderLegend() {
  const counts = new Map(), perCat = {};
  for (const j of journeys) {
    perCat[catOf(j.l)] = (perCat[catOf(j.l)] || 0) + 1;
    if (isShown(j.l)) counts.set(j.l, (counts.get(j.l) || 0) + 1);
  }
  for (const [c] of CATEGORIES) {
    const n = document.getElementById("catn-" + c);
    if (n) n.textContent = String(perCat[c] || 0);
    const chip = document.getElementById("cat-" + c);
    if (chip && c === "O") chip.hidden = !perCat.O && showCat.O;
  }
  const order = [...counts.keys()].sort((a, b) => a[0].localeCompare(b[0]) || a.localeCompare(b, "de", { numeric: true }));
  const box = document.getElementById("legend");
  box.replaceChildren();
  // Linien je Kategorie; RE und RB sind eingeklappt, bis der Nutzer sie oeffnet (der Browser merkt sich die Wahl)
  const groups = new Map();
  for (const [c, name] of CATEGORIES) groups.set(c, { name, lines: [], el: null });
  for (const line of order) groups.get(catOf(line)).lines.push(line);
  for (const [c, g] of groups) {
    if (!g.lines.length) continue;
    const open = (storage.get("legend" + c) ?? (COLLAPSED.has(c) ? "0" : "1")) === "1";
    const head = document.createElement("div");
    head.className = "ghead" + (open ? " open" : "");
    const total = g.lines.reduce((a, l) => a + counts.get(l), 0);
    head.textContent = `${g.name} · ${total}`;
    head.onclick = () => {
      storage.set("legend" + c, open ? "0" : "1");
      renderLegend();
    };
    box.append(head);
    if (open) for (const line of g.lines) box.append(legendRow(line, counts.get(line)));
  }
}

function legendRow(line, count) {
  const row = document.createElement("div");
  row.className = "row" + (hidden.has(line) ? " off" : "");
  const img = document.createElement("img");
  img.src = `/icon/${encodeURIComponent(line)}.svg`;
  img.alt = line;
  if (onWhite(line)) img.className = "on-white";
  const n = document.createElement("span");
  n.className = "n";
  n.textContent = count;
  row.append(img, n);
  row.onclick = () => {
    hidden.has(line) ? hidden.delete(line) : hidden.add(line);
    renderLegend();
    renderTrains();
  };
  return row;
}

async function refreshVehicles() {
  const status = document.getElementById("status");
  try {
    const sent = Date.now() / 1000;
    const data = await loadJson("/api/vehicles");
    const received = Date.now() / 1000;
    // Serverzeit zur Mitte der Laufzeit; leicht geglaettet, damit Netzwerkschwankungen die Zuege nicht verschieben
    const offset = data.server - (sent + received) / 2;
    serverOffset = serverOffset === null || Math.abs(offset - serverOffset) > 2 ? offset : serverOffset + 0.2 * (offset - serverOffset);
    await refreshTracks();
    if (data.fetched !== lastFetched) {
      lastFetched = data.fetched;
      journeys = data.journeys;
      animator.ingest(journeys, serverNow());
      renderLegend();
    }
    status.textContent = `${journeys.length} Fahrten, Stand ${new Date(lastFetched * 1000).toLocaleTimeString("de-DE")}`;
  } catch (e) {
    status.textContent = "Keine Daten: " + e.message;
  }
}

// ---- Zug-Popup: eindeutige Fahrt-Bezeichnung und Halteverlauf mit Verspaetung ----

const timeFmt = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
const hhmm = (ts) => timeFmt.format(new Date(ts * 1000));
const COURSE_REFRESH_MS = 60000;
let sel = null; // { id, mode, course, box, rows, ... } der angeklickte Zug

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// Halte aus den Abschnitten der Karte (nur die naechsten Minuten); gilt, bis der vollstaendige Verlauf geladen ist
function provisionalStops(j) {
  const stops = [];
  j.s.forEach((s, i) => {
    const prev = i ? j.s[i - 1] : null;
    let arrival = prev ? { a: prev.b, ad: prev.d } : { a: null, ad: null };
    if (prev && prev.t !== s.f) {
      stops.push({ n: prev.t, ...arrival, t: null, d: null });
      arrival = { a: null, ad: null };
    }
    stops.push({ n: s.f, ...arrival, t: s.a, d: s.d });
  });
  const last = j.s[j.s.length - 1];
  stops.push({ n: last.t, a: last.b, ad: last.d, t: null, d: null });
  return stops;
}

function idLabel(j, course) {
  const origin = (course && course.o) || j.o || j.s[0].f;
  const dep = course && course.dep;
  return `Fahrt ${dep ? hhmm(dep) + " Uhr " : ""}ab ${origin}`;
}

function openPopup(e, lngLat) {
  const j = e.j;
  const box = el("div", "popup");
  const head = el("div", "head");
  const img = document.createElement("img");
  img.src = `/icon/${encodeURIComponent(j.l)}.svg`;
  img.alt = j.l;
  if (onWhite(j.l)) img.className = "on-white";
  head.append(img, el("b", "", j.dir ? `nach ${j.dir}` : j.l));
  const id = el("div", "jid", idLabel(j));
  id.title = j.id; // technische ID der GTI nur als Tooltip
  const status = el("div", "status"), next = el("div", "next"), note = el("div", "note", "Halteverlauf wird geladen …");
  const list = el("div", "stops");
  box.append(head, id, status, next, list, note);
  // Je Klick ein neues Popup: eine wiederverwendete Instanz wuerde von ihrem eigenen closeOnClick-Handler gleich wieder geschlossen
  if (popup) popup.remove();
  const mine = { id: j.id, mode: j.m, j, course: null, box, idLine: id, status, next, note, list, rows: [], lastUi: 0, firstFill: true, timer: null };
  sel = mine;
  selectedId = j.id;
  mine.timer = setInterval(() => loadCourse(mine), COURSE_REFRESH_MS);
  fillStops(mine, provisionalStops(j));
  const sheet = compact();
  mine.sheet = sheet;
  mine.lastPan = undefined;
  popup = new maplibregl.Popup({ closeButton: true, maxWidth: "360px", closeOnClick: !sheet, className: "train-popup" + (sheet ? " sheet" + (sideSheet() ? " side" : "") : "") });
  popup.on("close", () => {
    clearInterval(mine.timer);
    if (mine.observer) mine.observer.disconnect();
    if (sel === mine) {
      sel = null;
      selectedId = null;
      if (sheet) {
        document.body.classList.remove("sheet-side");
        document.body.style.removeProperty("--sheet-h");
        map.easeTo({ padding: { top: 0, bottom: 0, left: 0, right: 0 }, duration: 250 });
      }
    }
  });
  popup.setLngLat(lngLat).setDOMContent(box).addTo(map);
  if (sheet) {
    // Karte so verschieben, dass der Zug neben bzw. ueber der Karte sichtbar bleibt
    document.getElementById("panel").classList.add("collapsed");
    document.getElementById("ptoggle").setAttribute("aria-expanded", "false");
    requestAnimationFrame(() => {
      const r = popup.getElement().querySelector(".maplibregl-popup-content").getBoundingClientRect();
      document.body.classList.toggle("sheet-side", sideSheet());
      if (!sideSheet()) {
        // Quellenangabe ueber die Karte schieben; die Hoehe aendert sich, sobald Halteverlauf und Schrift geladen sind
        const content = popup.getElement().querySelector(".maplibregl-popup-content");
        mine.observer = new ResizeObserver(() => document.body.style.setProperty("--sheet-h", `${Math.round(content.getBoundingClientRect().height)}px`));
        mine.observer.observe(content);
      }
      const padding = sideSheet() ? { top: 0, bottom: 0, left: Math.round(r.right + 8), right: 0 } : { top: 0, bottom: Math.round(r.height + 8), left: 0, right: 0 };
      map.easeTo({ center: [e.pose.x, e.pose.y], zoom: Math.max(map.getZoom(), 12.5), padding, duration: 450 });
      mine.lastPan = performance.now() + 500;
    });
  }
  loadCourse(mine);
}

async function loadCourse(s) {
  try {
    const course = await loadJson(`/api/journey?id=${encodeURIComponent(s.id)}&m=${s.mode}`);
    if (sel !== s) return;
    if (!s.course) s.firstFill = true; // die Liste wird ersetzt: einmal zum naechsten Halt scrollen
    s.course = course;
    s.idLine.textContent = idLabel(s.j, course);
    s.note.textContent = course.rt ? "" : "Keine Echtzeitdaten: Fahrplanzeiten";
    fillStops(s, course.stops);
  } catch (err) {
    if (sel === s && !s.course) s.note.textContent = "Vollständiger Halteverlauf derzeit nicht verfügbar";
  }
}

function fillStops(s, stops) {
  const keep = s.list.scrollTop;
  const rt = s.course ? s.course.rt : s.j.rt;
  s.rows = stops.map((stop) => {
    // Hauptzeit ist die Ankunft (am Ursprung die Abfahrt); bleibt der Zug laenger stehen, steht die Abfahrt beim Halt
    const main = stop.a != null ? { t: stop.a, d: stop.ad } : { t: stop.t, d: stop.d };
    const d = main.d || 0;
    const row = el("div", "stop");
    const name = el("span", "nm", stop.n);
    name.title = stop.n;
    if (stop.a != null && stop.t != null && stop.t - stop.a >= 60) name.append(el("small", "", ` · ab ${hhmm(stop.t)}`));
    // Ist und Verspaetung stehen erst, wenn der Zug den Halt erreicht hat; bei kommenden Halten bleiben sie leer (nur Soll)
    const ist = el("span", "ist"), dl = el("span", "dl" + (d >= 5 ? " late2" : d >= 1 ? " late1" : ""));
    row.append(name, el("span", "pl", hhmm(main.t - 60 * d)), ist, dl);
    return {
      row, ist, dl, name: stop.n, t: stop.t != null ? stop.t : stop.a, arrival: main.t, arrived: null,
      istText: rt ? hhmm(main.t) : "–", dlText: rt && main.d != null ? (d > 0 ? `+${d}` : d < 0 ? `${d}` : "±0") : "",
    };
  });
  const head = el("div", "stop hd");
  head.append(el("span", "nm", "Halt"), el("span", "pl", "Plan"), el("span", "ist", "Ist"), el("span", "dl", ""));
  s.list.replaceChildren(head, ...s.rows.map((r) => r.row));
  s.list.scrollTop = keep;
  s.lastUi = 0;
  const e = animator.entries.get(s.id);
  if (e && e.pose) updatePopup(e, serverNow(), true);
}

// Zustand (Strecke, Naechster Halt, Markierung der Halte) bis zu zweimal pro Sekunde auffrischen
function updatePopup(e, now, force) {
  if (!sel || sel.id !== e.id) return;
  const t = performance.now();
  if (!force && t - sel.lastUi < 500) return;
  sel.lastUi = t;
  const p = e.pose, seg = p.seg, d = seg.d;
  const where = p.dwell ? `Halt ${p.f === 0 ? seg.f : seg.t}` : `${seg.f} → ${seg.t}`;
  sel.status.textContent = where + (d ? `, ${d > 0 ? "+" : ""}${d} min` : "") + (d > 0 && e.trend ? (e.trend > 0 ? " ↗ wächst" : " ↙ wird abgebaut") : "") + (e.blockedSince ? ", wartet vor Signal (Block belegt)" : "");
  const next = motion.nextStop(e.j, e.tau);
  sel.next.textContent = next ? `Nächster Halt: ${next.name}, an ${hhmm(next.due)} (in ${fmtEta(next.at - now)})` : "";
  const here = p.dwell ? (p.f === 0 ? seg.f : seg.t) : null; // Halt, an dem der Zug gerade steht
  let marked = false;
  for (const r of sel.rows) {
    const passed = r.t <= e.tau;
    const current = !passed && !marked;
    const cls = "stop" + (passed ? " passed" : current ? " next" : "");
    if (!passed) marked = true;
    if (r.row.className !== cls) r.row.className = cls;
    // erreicht: Ankunftszeit vorbei oder der Zug steht gerade dort (er rollt vor der gemeldeten Ankunft ein)
    const arrived = r.arrival <= e.tau || (current && r.name === here);
    if (arrived !== r.arrived) {
      r.arrived = arrived;
      r.ist.textContent = arrived ? r.istText : "";
      r.dl.textContent = arrived ? r.dlText : "";
    }
  }
  if (sel.firstFill) {
    const target = sel.list.querySelector(".next");
    if (target) {
      sel.firstFill = false;
      sel.list.scrollTop = Math.max(0, target.offsetTop - sel.list.clientHeight / 3);
    }
  }
}

function showPopup(id, lngLat) {
  if (sel && sel.id === id && popup && popup.isOpen()) return;
  const e = animator.entries.get(id);
  if (e && e.pose) openPopup(e, lngLat);
}

// Kleine Bildschirme (Handy hoch, Handy quer): Popup als Karte unten bzw. links, Panel eingeklappt
const compact = () => matchMedia("(max-width: 700px)").matches || matchMedia("(max-height: 500px)").matches;
const sideSheet = () => matchMedia("(max-height: 500px) and (min-width: 560px)").matches;

function setupPanel() {
  const panel = document.getElementById("panel"), btn = document.getElementById("ptoggle");
  const set = (collapsed, remember) => {
    panel.classList.toggle("collapsed", collapsed);
    btn.setAttribute("aria-expanded", String(!collapsed));
    if (remember) storage.set("panelCollapsed", collapsed ? "1" : "0");
  };
  const saved = storage.get("panelCollapsed");
  set(saved === null ? compact() : saved === "1", false);
  btn.onclick = () => set(!panel.classList.contains("collapsed"), true);
  document.getElementById("phead").onclick = (ev) => { if (ev.target !== btn && !btn.contains(ev.target) && compact()) btn.click(); };
}

// Haken je Kategorie im Panel; die Wahl merkt sich der Browser (X-Busse: auch die alte Einstellung "showXbus")
function setupCategories(cfg) {
  const box = document.getElementById("cats");
  busLines = new Set(cfg.bus || []);
  for (const [c, name] of CATEGORIES) {
    if (c === "X" && !cfg.xbus) {
      showCat.X = false;
      continue;
    }
    if (c === "B" && !busLines.size) {
      showCat.B = false;
      continue;
    }
    const saved = storage.get("cat" + c) ?? (c === "X" ? storage.get("showXbus") : null);
    showCat[c] = saved !== "0";
    const chip = document.createElement("button"), sym = document.createElement("span"), count = document.createElement("span");
    chip.type = "button";
    chip.className = "chip" + (showCat[c] ? "" : " off");
    chip.id = "cat-" + c;
    chip.title = name;
    chip.setAttribute("aria-label", name);
    chip.setAttribute("aria-pressed", String(showCat[c]));
    if (c === "O") chip.hidden = true; // erst sichtbar, wenn es solche Linien gibt
    sym.className = "sym sym-" + c;
    sym.textContent = SYMBOLS[c];
    count.className = "cn";
    count.id = "catn-" + c;
    chip.onclick = () => {
      showCat[c] = !showCat[c];
      chip.classList.toggle("off", !showCat[c]);
      chip.setAttribute("aria-pressed", String(showCat[c]));
      storage.set("cat" + c, showCat[c] ? "1" : "0");
      if (selectedId !== null && !isShown(journeys.find((x) => x.id === selectedId)?.l || "S1")) popup?.remove();
      rebuildTracks();
      renderLegend();
      renderTrains();
    };
    chip.append(sym, count);
    box.append(chip);
  }
}

// Eigener Standort per GPS: blauer Punkt mit Genauigkeitskreis (und Blickrichtung, falls das Geraet sie liefert).
// Der Knopf unter dem Zoom-Schalter zentriert die Karte auf den Standort und folgt ihm, bis man die Karte
// verschiebt oder einen Zug antippt (dessen Verfolgung bewegt die Karte selbst, MapLibre schaltet das Folgen dann ab).
// Das Browser-API gibt es nur in sicheren Kontexten (HTTPS oder localhost): ueber http://<LAN-IP> bleibt der Knopf aus.
function addLocateControl() {
  const secure = window.isSecureContext && "geolocation" in navigator;
  const ctrl = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 },
    fitBoundsOptions: { maxZoom: 15 }, // beim Zentrieren nie naeher als Stadtteil-Massstab, auch bei sehr genauem Signal
    trackUserLocation: true,
    showUserHeading: true,
    showAccuracyCircle: true,
  });
  map.addControl(ctrl, "top-right");
  if (!secure) {
    const btn = document.querySelector(".maplibregl-ctrl-geolocate");
    if (btn) {
      btn.disabled = true;
      btn.title = "Standort nur über HTTPS verfügbar";
    }
    return;
  }
  ctrl.on("error", (e) => {
    // 1 = Zugriff verweigert, 2 = kein Signal, 3 = Zeitueberschreitung
    const text = { 1: "Standortzugriff verweigert", 2: "Standort nicht verfügbar", 3: "Standort: Zeitüberschreitung" }[e.code] || "Standort nicht verfügbar";
    const st = document.getElementById("status");
    const old = st.textContent;
    st.textContent = text;
    setTimeout(() => { if (st.textContent === text) st.textContent = old; }, 5000);
  });
  // Wurde der Zugriff schon einmal erlaubt, startet die Ortung beim Laden von selbst (kein erneuter Dialog);
  // sonst erst auf Tipp, damit der Browser nicht ungefragt die Berechtigung abfragt.
  navigator.permissions?.query({ name: "geolocation" }).then((p) => {
    if (p.state === "granted") map.once("idle", () => ctrl.trigger());
  }).catch(() => {});
}

async function main() {
  setupPanel();
  const cfg = await loadJson("/api/config");
  try { Object.assign(COLORS, await loadJson("/api/colors")); } catch (e) { /* Standardfarben */ }
  refreshS = cfg.refreshS;
  setupCategories(cfg);
  const blocksToggle = document.getElementById("blocks");
  showBlocks = storage.get("showBlocks") === "1";
  blocksToggle.checked = showBlocks;
  blocksToggle.onchange = () => {
    showBlocks = blocksToggle.checked;
    storage.set("showBlocks", showBlocks ? "1" : "0");
    rebuildBlocks();
  };
  let style;
  if (cfg.tiles) {
    maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
    style = vectorStyle(new URL(cfg.tilesUrl, location.href).href);
  } else {
    style = openFreeMapStyle();
  }
  map = new maplibregl.Map({ container: "map", style, center: HAMBURG, zoom: 10.5, minZoom: 8, attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  addLocateControl();
  window.hvvMap = map;
  window.hvvColorOf = colorOf;

  await new Promise((r) => map.on("load", r));
  const empty = { type: "FeatureCollection", features: [] };
  map.addSource("tracks", { type: "geojson", data: empty });
  map.addSource("stations", { type: "geojson", data: empty });
  map.addSource("blocks", { type: "geojson", data: empty });
  map.addSource("signals", { type: "geojson", data: empty });
  map.addSource("trains", { type: "geojson", data: empty });
  const widthAt = (z9, z15) => ["interpolate", ["linear"], ["zoom"], 9, z9, 15, z15];
  map.addLayer({
    id: "tracks-casing", type: "line", source: "tracks", filter: ["==", ["get", "k"], 0],
    paint: { "line-color": "#0b121c", "line-width": widthAt(["+", 2.5, ["*", 1.2, ["-", ["get", "n"], 1]]], ["+", 8, ["*", 4, ["-", ["get", "n"], 1]]]), "line-opacity": 0.8 },
  });
  map.addLayer({
    id: "tracks", type: "line", source: "tracks", filter: ["==", ["get", "k"], 1],
    paint: { "line-color": ["get", "c"], "line-width": widthAt(1.2, 4), "line-offset": widthAt(["*", ["get", "o"], 1.2], ["*", ["get", "o"], 4]) },
  });
  // Signalbloecke: je Fahrtrichtung rechts neben dem Gleis (Rechtsverkehr), belegte Bloecke rot
  const hidden0 = { visibility: "none" };
  const side = ["interpolate", ["linear"], ["zoom"], 11, 2, 16, 7];
  map.addLayer({
    id: "blocks", type: "line", source: "blocks", minzoom: 11, layout: hidden0,
    paint: { "line-color": ["match", ["get", "alt"], 0, "#35c4ff", 1, "#ffd23f", "#ff6fb5"], "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.5, 16, 4], "line-offset": side, "line-opacity": 0.9 },
  });
  map.addLayer({
    id: "blocks-busy", type: "line", source: "blocks", minzoom: 11, layout: hidden0, filter: ["in", ["get", "id"], ["literal", []]],
    paint: { "line-color": "#ff3b30", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 16, 6], "line-offset": side },
  });
  map.addLayer({
    id: "signals", type: "circle", source: "signals", minzoom: 12.5, layout: hidden0,
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12.5, 1.5, 16, 3.5], "circle-color": "#fff", "circle-stroke-color": "#ff3b30", "circle-stroke-width": 1.2 },
  });
  const bus = ["==", ["get", "b"], 1];
  // Stationspins: gross und ab Zoom 9 (Bus ab 12,5) sichtbar, damit sich jede Station anklicken laesst (Abfahrten)
  map.addLayer({
    id: "stations", type: "circle", source: "stations", minzoom: 9, filter: ["!", bus],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 5, 12, 8, 16, 12],
      "circle-color": "#fff", "circle-stroke-color": "#0b121c", "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 1.5, 16, 3],
    },
  });
  map.addLayer({
    id: "stations-bus", type: "circle", source: "stations", minzoom: 12.5, filter: bus,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 12.5, 4, 16, 8],
      "circle-color": "#e8c6e0", "circle-stroke-color": "#0b121c", "circle-stroke-width": 2,
    },
  });
  // Bahnhoefe ab Zoom 12,5, Bushaltestellen erst ab 14 (sonst zu dicht)
  for (const [id, minzoom, filter] of [["station-labels", 12.5, ["!", bus]], ["station-labels-bus", 14, bus]]) {
    map.addLayer({
      id, type: "symbol", source: "stations", minzoom, filter,
      layout: { "text-field": ["get", "n"], "text-size": 11, "text-offset": [0, 1], "text-anchor": "top", "text-font": ["Noto Sans Regular"] },
      paint: { "text-color": "#cdd6e4", "text-halo-color": "#0b121c", "text-halo-width": 1.2 },
    });
  }
  map.addLayer({ id: "trains-dot", type: "circle", source: "trains", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 15, 9], "circle-color": ["get", "c"], "circle-stroke-color": ["case", ["==", ["get", "sel"], 1], "#fff", "#0b121c"], "circle-stroke-width": 1.5 } });
  map.addLayer({
    id: "trains-icon", type: "symbol", source: "trains", minzoom: 11, filter: ["!=", ["get", "icon"], ""],
    layout: { "icon-image": ["get", "icon"], "icon-size": ["interpolate", ["linear"], ["zoom"], 11, 0.45, 16, 0.8], "icon-allow-overlap": true, "icon-ignore-placement": true },
  });

  map.addImage("arrow", arrowImage(), { pixelRatio: 2 });
  const hasNext = ["!=", ["get", "eta"], ""];
  map.addLayer({
    id: "trains-arrow", type: "symbol", source: "trains", minzoom: 10, filter: hasNext,
    layout: {
      "icon-image": "arrow", "icon-rotate": ["get", "rot"], "icon-rotation-alignment": "map", "icon-pitch-alignment": "map",
      "icon-size": ["interpolate", ["linear"], ["zoom"], 10, 0.45, 12, 0.7, 16, 1.1], "icon-allow-overlap": true, "icon-ignore-placement": true,
    },
  });
  const label = (id, field, minzoom, anchor, offset, size) => map.addLayer({
    id, type: "symbol", source: "trains", minzoom, filter: hasNext,
    layout: { "text-field": ["get", field], "text-size": size, "text-anchor": anchor, "text-offset": offset, "text-font": ["Noto Sans Regular"], "text-allow-overlap": true, "text-ignore-placement": true },
    paint: { "text-color": "#ffffff", "text-halo-color": "#0b121c", "text-halo-width": 1.5 },
  });
  map.addLayer({
    id: "trains-delay", type: "symbol", source: "trains", minzoom: 10, filter: ["!=", ["get", "dl"], ""],
    layout: {
      "icon-image": ["get", "dl"], "icon-anchor": "left", "icon-allow-overlap": true, "icon-ignore-placement": true,
      "icon-offset": ["interpolate", ["linear"], ["zoom"], 10, ["literal", [10, -9]], 16, ["literal", [22, -16]]],
    },
  });
  // auf kleinen Bildschirmen erst bei staerkerem Zoom, sonst ueberlagern sich die Beschriftungen
  label("trains-eta", "eta", compact() ? 12.5 : 11.5, "top", [0, 2.4], 12);
  label("trains-next", "nxt", compact() ? 14 : 13, "bottom", [0, -2.4], 11);

  const trainLayers = ["trains-dot", "trains-icon", "trains-arrow", "trains-delay"];
  // Ein Klick trifft oft mehrere Ebenen (und mehrere Zuege): der oberste Treffer gilt
  map.on("click", (ev) => {
    // Finger treffen ungenauer als eine Maus; Trefferflaeche = Symbolradius + Rand, der Pin ist stets anklickbar
    const coarse = matchMedia("(pointer: coarse)").matches, z = map.getZoom();
    const trainR = coarse ? 18 : dotRadius(z) + 3, stopR = coarse ? Math.max(24, pinRadius(z) + 10) : pinRadius(z) + 4;
    const around = (r) => [[ev.point.x - r, ev.point.y - r], [ev.point.x + r, ev.point.y + r]];
    const nearest = (features) => {
      let best = null, bestD = Infinity;
      for (const f of features) {
        const q = map.project(f.geometry.coordinates), d = Math.hypot(q.x - ev.point.x, q.y - ev.point.y);
        if (d < bestD) [best, bestD] = [f, d];
      }
      return best && { f: best, d: bestD };
    };
    const train = nearest(map.queryRenderedFeatures(around(trainR), { layers: trainLayers }));
    const stop = nearest(map.queryRenderedFeatures(around(stopR), { layers: ["stations", "stations-bus"] }));
    // Treffer im Zugsymbol selbst gehoert dem Zug, sonst gewinnt der (groessere) Stationspin
    if (train && (!stop || train.d <= dotRadius(z) + 2)) return showPopup(train.f.properties.id, ev.lngLat);
    if (stop && stop.f.properties.k) {
      if (popup && popup.isOpen()) popup.remove();
      return HvvPlanner.showDepartures(stop.f.geometry.coordinates.slice(), stop.f.properties);
    }
    if (sel && sel.sheet) popup.remove(); // die Karte schliesst sich nicht selbst per Tipp (closeOnClick aus)
  });
  map.on("dragstart", () => { if (sel) sel.userMoved = true; }); // wer die Karte selbst verschiebt, wird nicht mehr zentriert
  for (const id of ["stations", "stations-bus"]) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
  for (const id of trainLayers) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }

  if (showBlocks) rebuildBlocks();
  await refreshVehicles();
  // Der Server cached den Abruf; haeufigeres Nachfragen holt neue Verspaetungen schneller auf die Karte, ohne die GTI zu belasten
  setInterval(refreshVehicles, Math.max(5, refreshS / 3) * 1000);
  requestAnimationFrame(frame);
}

main().catch((e) => {
  document.getElementById("status").textContent = "Fehler: " + e.message;
});
