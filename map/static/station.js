// Bahnhof in 3D: ein Diorama aus MapLibre fill-extrusion. Boden mit Strassen, Gebaeuden, Baeumen, Gruen und Wasser liegt auf Ebene 0;
// darunter haengen die Ebenen des Bahnhofs (Bahnsteige, Gaenge, Gleise, Raeume, Treppenstufen, Aufzuege), der Umsteigeweg ist ein Band.
// Modell: /stations/<id>.json (hvv_gti/stationmap.py), Umgebung: /stations/<id>.ctx.json (hvv_gti/stationctx.py), beides aus OpenStreetMap.
// Hoehe einer Ebene = (Ebene - tiefste Ebene) * Ebenenabstand. Schrift (Strassen, Orte, Gleise) sind HTML-Marken, weil MapLibre-Symbole am
// Boden kleben; ihre Hoehe wird beim Projizieren nachgefuehrt.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const query = new URLSearchParams(location.search);
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches || query.has("still"); // ?still: ohne Animation (Screenshots)
  document.documentElement.dataset.scene = dark ? "dark" : "light";

  const C = dark
    ? { ground: "#10131b", slab: "#232733", road: "#6b7088", roadEdge: "#3a3e50", walk: "#4b5066", green: "#2f4d3d", wood: "#27422f", water: "#2a4658", bld: "#1c1f2d", crown: ["#1f3b2b", "#264633", "#1a3325"], trunk: "#3b2f27",
        wp: "#5d767a", S: "#33b873", U: "#3d92e8", R: "#e0645e", "?": "#8497a0", track: "#080a10", room: "#3a5360", step: "#e4a519", esc: "#f08a3c", lift: "#5cc8cf", path: "#ff5a63", casing: "#ffffff", pick: "#ffd24a" }
    : { ground: "#dfe6e6", slab: "#eef2f1", road: "#ffffff", roadEdge: "#c9d3d2", walk: "#e2e8e7", green: "#cfe5cf", wood: "#b9d6bb", water: "#bcd8e6", bld: "#cfd6da", crown: ["#8fbf94", "#7cb083", "#a0cba3"], trunk: "#8a7766",
        wp: "#9fb2b4", S: "#1f9a5a", U: "#1d6fc0", R: "#c4413b", "?": "#7a8c94", track: "#44575a", room: "#b9c9cb", step: "#c8870f", esc: "#d96d1c", lift: "#0b6b73", path: "#d4141c", casing: "#ffffff", pick: "#f0a500" };
  const MODE_NAME = { S: "S-Bahn", U: "U-Bahn", R: "Regional/Fern", "?": "Bahnsteig" };
  const MODE_ORDER = { U: 0, S: 1, R: 2, "?": 3 };
  const fmtLevel = (l) => String(l).replace("-", "−");
  const mmss = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s` : `${s} s`);

  let map, model, ctx = null, minL = 0, gapIdx = 2, selLevel = null, ctxMode = "dim", dotTimer = 0, pathPts = [], labels = [], labelQueued = false;
  const LH = () => 3.2 * gapIdx; // Meter zwischen zwei Ebenen
  const levelZ = (l) => (l - minL) * LH();
  const kxy = () => [111320 * Math.cos((model.center[1] * Math.PI) / 180), 110574];

  // ---- Ausdruecke ----
  const level = (prop) => ["*", ["-", ["to-number", ["get", prop]], minL], LH()];
  const zl = (prop, off) => ["+", level(prop), off];
  const zg = (off) => Math.max(0, levelZ(0) + off); // Hoehe des Bodens (Ebene 0) plus Versatz in Metern (MapLibre verlangt >= 0)

  const KINDS = [
    { id: "track", f: ["==", ["get", "k"], "track"], base: () => zl("l", -0.35), top: () => zl("l", -0.05), color: C.track, op: [1, 0.2], dim: true },
    { id: "walk", f: ["==", ["get", "k"], "walk"], base: () => zl("l", -0.3), top: () => zl("l", 0), color: C.wp, op: [1, 0.2], dim: true },
    { id: "floor", f: ["==", ["get", "k"], "floor"], base: () => zl("l", -0.55), top: () => zl("l", 0.15), color: ["match", ["get", "m"], "S", C.S, "U", C.U, "R", C.R, C["?"]], op: [1, 0.22], dim: true },
    { id: "room", f: ["==", ["get", "k"], "room"], base: () => zl("l", 0), top: () => zl("l", 2.6), color: C.room, op: [0.55, 0.1], dim: true },
    { id: "step", f: ["==", ["get", "k"], "step"], base: () => zl("l", -0.45), top: () => zl("l", 0.05), color: C.step, op: [1, 0], dim: false, near: true },
    { id: "esc", f: ["==", ["get", "k"], "esc"], base: () => zl("l", -0.45), top: () => zl("l", 0.05), color: C.esc, op: [1, 0], dim: false, near: true },
    { id: "lift", f: ["==", ["get", "k"], "lift"], base: () => zl("l", -0.3), top: () => zl("l", 0.4), color: C.lift, op: [0.65, 0], dim: false, span: true },
  ];
  // Umgebung: Quelle, Filter, Basis, Hoehe, Farbe, Deckkraft (voll)
  const CTX = [
    { id: "slab", src: "c-slab", base: () => zg(-7), top: () => zg(0), color: C.slab, op: 1 },
    { id: "water", src: "c-water", base: () => zg(0), top: () => zg(0.05), color: C.water, op: 1 },
    { id: "green", src: "c-green", base: () => zg(0), top: () => zg(0.18), color: ["match", ["get", "k"], "wood", C.wood, C.green], op: 1 },
    { id: "edge", src: "c-road", f: ["==", ["get", "k"], "edge"], base: () => zg(0), top: () => zg(0.3), color: C.roadEdge, op: 1 },
    { id: "road", src: "c-road", f: ["==", ["get", "k"], "road"], base: () => zg(0), top: () => zg(0.42), color: C.road, op: 1 },
    { id: "wpath", src: "c-road", f: ["==", ["get", "k"], "walk"], base: () => zg(0), top: () => zg(0.5), color: C.walk, op: 1 },
    { id: "bld", src: "c-bld", base: () => ["+", zg(0), ["to-number", ["get", "m"], 0]], top: () => ["+", zg(0), ["to-number", ["get", "h"]]], color: C.bld, op: 0.94 },
    { id: "trunk", src: "c-tree", f: ["==", ["get", "k"], "trunk"], base: () => zg(0.3), top: () => zg(2.6), color: C.trunk, op: 1 },
    { id: "crown", src: "c-tree", f: ["==", ["get", "k"], "crown"], base: () => zg(2.2), top: () => ["+", zg(2.2), ["to-number", ["get", "t"]]], color: ["match", ["get", "v"], 0, C.crown[0], 1, C.crown[1], C.crown[2]], op: 1 },
  ];
  const CTX_DIM = 0.3;

  function filters(k) {
    const on = [k.f];
    const dim = [k.f];
    if (selLevel !== null) {
      if (k.near) on.push([">=", ["get", "l"], selLevel - 1], ["<=", ["get", "l"], selLevel + 1]);
      else if (k.span) on.push(["<=", ["get", "l"], selLevel], [">=", ["get", "h"], selLevel]);
      else on.push(["==", ["get", "l"], selLevel]);
      if (k.dim) dim.push(["!=", ["get", "l"], selLevel]);
    }
    return { on: ["all", ...on], dim: selLevel !== null && k.dim ? ["all", ...dim] : ["==", ["get", "k"], "__keine"] };
  }

  function layerDef(id, source, f, base, top, color, opacity) {
    const def = { id, type: "fill-extrusion", source, paint: { "fill-extrusion-color": color, "fill-extrusion-base": base, "fill-extrusion-height": top, "fill-extrusion-opacity": opacity, "fill-extrusion-vertical-gradient": true } };
    if (f) def.filter = f;
    return def;
  }

  function ctxOpacity(c) {
    return ctxMode === "full" ? c.op : ctxMode === "dim" ? (c.id === "slab" ? 0 : Math.min(c.op, CTX_DIM)) : 0; // gedaempft: ohne Bodenplatte, der Untergrund bleibt sichtbar
  }

  function addLayers() {
    for (const k of KINDS) {
      const f = filters(k);
      map.addLayer(layerDef(`${k.id}-on`, "st", f.on, k.base(), k.top(), k.color, k.op[0]));
      if (k.dim) map.addLayer(layerDef(`${k.id}-dim`, "st", f.dim, k.base(), k.top(), k.color, k.op[1]));
      if (k.id === "floor") map.addLayer(layerDef("pick", "st", ["==", ["get", "p"], "__keiner"], zl("l", -0.55), zl("l", 0.3), C.pick, 1));
    }
    map.addLayer(layerDef("path-casing", "path", ["==", ["get", "k"], "casing"], zl("l", 0.2), zl("l", 0.5), C.casing, 0.95));
    map.addLayer(layerDef("path-on", "path", ["==", ["get", "k"], "path"], zl("l", 0.5), zl("l", 0.95), C.path, 1));
    map.addLayer(layerDef("dot", "dot", ["has", "l"], zl("l", 0.95), zl("l", 2.4), C.path, 1));
    // Die Umgebung kommt zuletzt: halbdurchsichtige Flaechen werden dann ueber den Untergrund geblendet statt ihn per Tiefentest zu verdecken
    for (const c of CTX) map.addLayer(layerDef(`c-${c.id}`, c.src, c.f, c.base(), c.top(), c.color, ctxOpacity(c)));
  }

  function applyHeights() {
    for (const c of CTX) {
      map.setPaintProperty(`c-${c.id}`, "fill-extrusion-base", c.base());
      map.setPaintProperty(`c-${c.id}`, "fill-extrusion-height", c.top());
    }
    for (const k of KINDS) {
      for (const suffix of ["on", "dim"]) {
        const id = `${k.id}-${suffix}`;
        if (!map.getLayer(id)) continue;
        map.setPaintProperty(id, "fill-extrusion-base", k.base());
        map.setPaintProperty(id, "fill-extrusion-height", k.top());
      }
    }
    for (const [id, a, b] of [["pick", -0.55, 0.3], ["path-casing", 0.2, 0.5], ["path-on", 0.5, 0.95], ["dot", 0.95, 2.4]]) {
      map.setPaintProperty(id, "fill-extrusion-base", zl("l", a));
      map.setPaintProperty(id, "fill-extrusion-height", zl("l", b));
    }
    queueLabels();
  }

  function applyFilters() {
    for (const k of KINDS) {
      const f = filters(k);
      map.setFilter(`${k.id}-on`, f.on);
      if (k.dim) map.setFilter(`${k.id}-dim`, f.dim);
    }
  }

  function applyCtxMode() {
    for (const c of CTX) {
      map.setPaintProperty(`c-${c.id}`, "fill-extrusion-opacity", ctxOpacity(c));
      map.setLayoutProperty(`c-${c.id}`, "visibility", ctxMode === "off" ? "none" : "visible");
    }
    queueLabels();
  }

  // ---- Geometrie ----
  function quad(a, b, half, kx, ky, ext = 0.8) {
    const dx = (b[0] - a[0]) * kx, dy = (b[1] - a[1]) * ky, d = Math.hypot(dx, dy) || 1;
    const ux = dx / d, uy = dy / d, nx = -uy * half, ny = ux * half, ex = ux * half * ext, ey = uy * half * ext;
    const P = (p, s, e) => [p[0] + (s * nx + e * ex) / kx, p[1] + (s * ny + e * ey) / ky];
    return [P(a, 1, -1), P(b, 1, 1), P(b, -1, 1), P(a, -1, -1), P(a, 1, -1)];
  }

  const ROAD_W = { motorway: 9, trunk: 9, primary: 8, secondary: 7, tertiary: 6.2, unclassified: 5.4, residential: 5, living_street: 4.6, service: 3.2, pedestrian: 4, cycleway: 1.9, footway: 1.7, path: 1.4, steps: 1.8, track: 2.6 };
  const WALKS = new Set(["footway", "path", "steps", "cycleway", "pedestrian"]);
  const poly = (ring, props) => ({ type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [ring] } });

  function roadFeatures(c) {
    const [kx, ky] = kxy(), out = [];
    for (const r of c.roads) {
      const w = ROAD_W[r.c] || ROAD_W[r.c.replace("_link", "")] || 3.2, walk = WALKS.has(r.c);
      for (let i = 0; i < r.g.length - 1; i++) {
        if (!walk) out.push(poly(quad(r.g[i], r.g[i + 1], w / 2 + 0.7, kx, ky), { k: "edge" }));
        out.push(poly(quad(r.g[i], r.g[i + 1], w / 2, kx, ky), { k: walk ? "walk" : "road" }));
      }
    }
    return out;
  }

  function hash(lon, lat) {
    const n = Math.sin(lon * 12989.8 + lat * 78233.1) * 43758.5453;
    return n - Math.floor(n);
  }

  function treeFeatures(c) {
    const [kx, ky] = kxy(), out = [];
    for (const [lon, lat] of c.trees) {
      const h = hash(lon, lat), r = 2.2 + h * 1.5, t = 3.2 + h * 3.4, v = Math.floor(h * 3);
      const ring = [];
      for (let i = 0; i <= 8; i++) ring.push([lon + (Math.cos((i / 8) * 2 * Math.PI) * r) / kx, lat + (Math.sin((i / 8) * 2 * Math.PI) * r) / ky]);
      const s = 0.22;
      out.push(poly([[lon - s / kx, lat - s / ky], [lon + s / kx, lat - s / ky], [lon + s / kx, lat + s / ky], [lon - s / kx, lat + s / ky], [lon - s / kx, lat - s / ky]], { k: "trunk" }));
      out.push(poly(ring, { k: "crown", t, v }));
    }
    return out;
  }

  function bbox() {
    if (model.bbox) return model.bbox;
    const [lon, lat] = model.center;
    return [lon - 0.0055, lat - 0.003, lon + 0.0055, lat + 0.003];
  }

  function setContext() {
    const fc = (features) => ({ type: "FeatureCollection", features });
    const [w, s, e, n] = bbox();
    map.getSource("c-slab").setData(fc([poly([[w, s], [e, s], [e, n], [w, n], [w, s]], {})]));
    if (!ctx) { for (const id of ["c-road", "c-bld", "c-tree", "c-green", "c-water"]) map.getSource(id).setData(fc([])); return; }
    map.getSource("c-road").setData(fc(roadFeatures(ctx)));
    map.getSource("c-bld").setData(fc(ctx.buildings.map((b) => poly(b.g, { h: b.h, m: b.m || 0 }))));
    map.getSource("c-tree").setData(fc(treeFeatures(ctx)));
    map.getSource("c-green").setData(fc(ctx.green.map((g) => poly(g.g, { k: g.k }))));
    map.getSource("c-water").setData(fc(ctx.water.map((g) => poly(g.g, {}))));
  }

  // ---- Weg als Band ----
  // Punkte [lon, lat, ebene], Wechsel der Ebene (Treppe, Aufzug) in kleine Schritte zerlegt, damit das Band ansteigt
  function densify(g) {
    const out = [];
    for (let i = 0; i < g.length - 1; i++) {
      const a = g[i], b = g[i + 1];
      const n = a[2] === b[2] ? 1 : 8;
      for (let j = 0; j < n; j++) out.push([a[0] + ((b[0] - a[0]) * j) / n, a[1] + ((b[1] - a[1]) * j) / n, a[2] + ((b[2] - a[2]) * j) / n]);
    }
    out.push(g[g.length - 1]);
    return out;
  }

  function showPath(path) {
    clearInterval(dotTimer);
    const [kx, ky] = kxy();
    const features = [];
    pathPts = [];
    if (path && path.g) {
      pathPts = densify(path.g);
      for (let i = 0; i < pathPts.length - 1; i++) {
        const a = pathPts[i], b = pathPts[i + 1], l = Math.max(a[2], b[2]);
        if (Math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * ky) < 0.05) continue;
        features.push({ type: "Feature", properties: { k: "casing", l }, geometry: { type: "Polygon", coordinates: [quad(a, b, 1.7, kx, ky)] } });
        features.push({ type: "Feature", properties: { k: "path", l }, geometry: { type: "Polygon", coordinates: [quad(a, b, 1.05, kx, ky)] } });
      }
    }
    map.getSource("path").setData({ type: "FeatureCollection", features });
    map.getSource("dot").setData({ type: "FeatureCollection", features: [] });
    if (pathPts.length > 1 && !reduced) animateDot(kx, ky);
  }

  function animateDot(kx, ky) {
    const lens = [0];
    for (let i = 1; i < pathPts.length; i++) lens.push(lens[i - 1] + Math.hypot((pathPts[i][0] - pathPts[i - 1][0]) * kx, (pathPts[i][1] - pathPts[i - 1][1]) * ky) + Math.abs(pathPts[i][2] - pathPts[i - 1][2]) * 3);
    const total = lens[lens.length - 1] || 1, started = performance.now(), cycle = Math.min(14000, Math.max(5000, total * 70));
    dotTimer = setInterval(() => {
      const f = ((performance.now() - started) % (cycle + 1200)) / cycle;
      if (f > 1) return;
      const d = f * total;
      let i = 1;
      while (i < lens.length - 1 && lens[i] < d) i++;
      const t = (d - lens[i - 1]) / ((lens[i] - lens[i - 1]) || 1);
      const a = pathPts[i - 1], b = pathPts[i];
      const p = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
      const r = 1.1;
      map.getSource("dot").setData({ type: "FeatureCollection", features: [{ type: "Feature", properties: { l: p[2] }, geometry: { type: "Polygon", coordinates: [[[p[0] - r / kx, p[1] - r / ky], [p[0] + r / kx, p[1] - r / ky], [p[0] + r / kx, p[1] + r / ky], [p[0] - r / kx, p[1] + r / ky], [p[0] - r / kx, p[1] - r / ky]]] } }] });
    }, 66);
  }

  // ---- Beschriftung ----
  function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }

  function buildLabels() {
    const box = $("labels");
    box.replaceChildren();
    labels = [];
    const add = (node, o) => { box.append(node); labels.push({ node, ...o }); };
    for (const p of model.platforms) {
      const n = el("div", undefined, `lbl plat m-${p.mode}`);
      n.append(el("span", p.label.split(" · ").pop()));
      add(n, { kind: "plat", prio: 0, lon: p.lon, lat: p.lat, lvl: p.level, lift: 2.4, minZoom: 0 });
    }
    for (const e of model.entrances) {
      const text = e.name || (e.ref && e.ref.length <= 4 ? e.ref : "");
      if (!text) continue; // lange Inventarnummern sind keine Beschriftung
      const n = el("div", undefined, "lbl ent");
      n.append(el("i"), el("span", text));
      add(n, { kind: "ent", prio: 1, lon: e.lon, lat: e.lat, lvl: 0, lift: 1.4, minZoom: 16.6 });
    }
    if (ctx) {
      ctx.pois.forEach((p, i) => {
        const n = el("div", undefined, `lbl poi c-${p.k}`);
        n.append(el("i"));
        if (p.n) n.append(el("span", p.n));
        add(n, { kind: "poi", prio: 2 + i / 100, lon: p.x, lat: p.y, lvl: 0, lift: 1.4, minZoom: p.k === "bus" ? 16.8 : 16 });
      });
      ctx.labels.forEach((s, i) => add(el("div", s.n, "lbl street"), { kind: "street", prio: 5 + s.r + i / 100, lon: s.x, lat: s.y, lvl: 0, lift: 0.6, minZoom: 15.4, bearing: s.a }));
    }
    for (const l of labels) { l.w = l.node.offsetWidth; l.h = l.node.offsetHeight; }
    labels.sort((a, b) => a.prio - b.prio);
    queueLabels();
  }

  function queueLabels() {
    if (labelQueued) return;
    labelQueued = true;
    requestAnimationFrame(() => { labelQueued = false; placeLabels(); });
  }

  const EARTH = 40075016.686;
  function screen(lon, lat, meters) {
    const p = map.project([lon, lat]);
    const ppm = (512 * Math.pow(2, map.getZoom())) / (EARTH * Math.cos((lat * Math.PI) / 180));
    return { x: p.x, y: p.y - meters * ppm * Math.cos((map.getPitch() * Math.PI) / 180) };
  }

  function placeLabels() {
    if (!model || !map) return;
    const z = map.getZoom(), cw = map.getContainer().clientWidth, ch = map.getContainer().clientHeight, placed = [];
    const [kx, ky] = kxy();
    for (const l of labels) {
      let show = z >= l.minZoom && !(ctxMode === "off" && l.kind !== "plat");
      if (show && selLevel !== null && l.kind === "plat" && Math.abs(l.lvl - selLevel) > 0.1) show = false;
      if (show && l.kind === "plat" && ctxMode === "full" && l.lvl < 0) show = false; // unter dem vollen Boden nicht sichtbar
      let pt = null;
      if (show) {
        const meters = l.kind === "plat" ? levelZ(l.lvl) + l.lift : levelZ(0) + l.lift;
        pt = screen(l.lon, l.lat, meters);
        if (pt.x < -80 || pt.x > cw + 80 || pt.y < -30 || pt.y > ch + 30) show = false;
      }
      if (show) {
        const w = l.w + 8, h = l.h + 4, r = [pt.x - w / 2, pt.y - h / 2, pt.x + w / 2, pt.y + h / 2];
        if (placed.some((q) => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1])) show = false;
        else placed.push(r);
      }
      if (!show) { l.node.style.display = "none"; continue; }
      let angle = 0;
      if (l.bearing !== undefined) {
        const b = (l.bearing * Math.PI) / 180, a = screen(l.lon, l.lat, levelZ(0) + l.lift), c = screen(l.lon + (Math.sin(b) * 12) / kx, l.lat + (Math.cos(b) * 12) / ky, levelZ(0) + l.lift);
        angle = (Math.atan2(c.y - a.y, c.x - a.x) * 180) / Math.PI;
        if (angle > 90) angle -= 180;
        if (angle < -90) angle += 180;
      }
      l.node.style.display = "";
      l.node.style.transform = `translate(${pt.x.toFixed(1)}px, ${pt.y.toFixed(1)}px) translate(-50%, -50%) rotate(${angle.toFixed(1)}deg)`;
    }
  }

  // ---- Oberflaeche ----
  const platformText = (p) => `${MODE_NAME[p.mode] || "Bahnsteig"} · ${p.label} (Ebene ${fmtLevel(p.level)})`;

  function renderWay() {
    const box = $("way");
    box.replaceChildren();
    const from = $("from").value, to = $("to").value;
    const path = model.paths.find((p) => p.from === from && p.to === to);
    map.setFilter("pick", ["in", ["get", "p"], ["literal", [from, to]]]);
    if (from === to) { box.append(el("p", "Bitte zwei verschiedene Bahnsteige wählen.", "src")); showPath(null); return; }
    if (!path) { box.append(el("p", "Für diesen Umstieg gibt es keine Daten.", "src")); showPath(null); return; }
    const levels = path.steps ? new Set(path.steps.flatMap((s) => [s.a, s.b])).size : 0;
    const sum = el("div", undefined, "sum");
    sum.append(el("b", mmss(path.s)), el("span", `${path.m} m${levels > 1 ? ` · ${levels} Ebenen` : ""}`));
    box.append(sum, el("p", path.src === "graph" ? "Auf dem Wegenetz aus OpenStreetMap berechnet." : "Geschätzt (Luftlinie): in den Kartendaten fehlt ein durchgehender Weg.", "src"));
    if (path.steps) {
      const ol = el("ol");
      for (const s of path.steps) {
        const li = el("li");
        const up = s.b > s.a;
        const text = { walk: `${s.m} m gehen`, stairs: `Treppe ${up ? "hinauf" : "hinunter"}`, escalator: `Rolltreppe ${up ? "hinauf" : "hinunter"}`, lift: `Aufzug ${up ? "hinauf" : "hinunter"}` }[s.t] || s.t;
        const icon = { walk: "→", stairs: up ? "▲" : "▼", escalator: up ? "▲" : "▼", lift: "↕" }[s.t] || "•";
        li.append(el("i", icon), el("span", text), el("span", s.t === "walk" ? "" : `Ebene ${fmtLevel(s.a)} → ${fmtLevel(s.b)}`));
        ol.append(li);
      }
      box.append(ol);
    }
    showPath(path);
    fitPath(path);
  }

  // Kamera auf den gewaehlten Umstieg (sonst auf den ganzen Bahnhof)
  function fitPath(path) {
    const pts = path && path.g && path.g.length ? path.g : null;
    const [w, s, e, n] = pts ? pts.reduce((r, p) => [Math.min(r[0], p[0]), Math.min(r[1], p[1]), Math.max(r[2], p[0]), Math.max(r[3], p[1])], [180, 90, -180, -90]) : bbox();
    const [kx, ky] = kxy(), pad = pts ? 22 : 0; // Meter Luft um den Weg
    map.fitBounds([[w - pad / kx, s - pad / ky], [e + pad / kx, n + pad / ky]], { padding: { top: 90, bottom: 90, left: 80, right: 80 }, pitch: 58, bearing: -28, maxZoom: 18.1, duration: reduced ? 0 : 900 });
  }

  function fillSelects() {
    const sorted = [...model.platforms].sort((a, b) => (MODE_ORDER[a.mode] ?? 9) - (MODE_ORDER[b.mode] ?? 9) || a.level - b.level || a.label.localeCompare(b.label));
    for (const id of ["from", "to"]) {
      const sel = $(id);
      sel.replaceChildren(...sorted.map((p) => { const o = el("option", platformText(p)); o.value = p.id; return o; }));
    }
    // Vorbelegung: der laengste berechnete Weg unter fuenf Minuten (zeigt die Ebenen am besten)
    const best = model.paths.filter((p) => p.src === "graph" && p.s <= 300).sort((a, b) => b.s - a.s)[0];
    if (best) { $("from").value = best.from; $("to").value = best.to; }
    else if (sorted.length > 1) { $("from").value = sorted[0].id; $("to").value = sorted[1].id; }
    // Aus dem Planer geoeffnet (?from=&to=): genau dieser Umstieg
    const known = (id) => model.platforms.some((p) => p.id === id);
    if (query.get("id") === model.id && known(query.get("from")) && known(query.get("to"))) { $("from").value = query.get("from"); $("to").value = query.get("to"); }
  }

  function fillLevels() {
    const box = $("levels");
    box.replaceChildren();
    const mk = (label, value) => {
      const b = el("button", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(selLevel === value));
      b.addEventListener("click", () => { selLevel = value; for (const c of box.children) c.setAttribute("aria-pressed", String(c === b)); applyFilters(); queueLabels(); });
      box.append(b);
    };
    mk("Alle", null);
    for (const l of [...model.levels].reverse()) mk(fmtLevel(l), l);
  }

  async function load(id) {
    for (const b of $("stations").children) b.setAttribute("aria-selected", String(b.dataset.id === id));
    $("note").textContent = "Lade …";
    const [res, cres] = await Promise.all([fetch(`stations/${encodeURIComponent(id)}.json`), fetch(`stations/${encodeURIComponent(id)}.ctx.json`)]);
    if (!res.ok) { $("note").textContent = "Dieser Bahnhof ist nicht verfügbar."; return; }
    model = await res.json();
    ctx = cres.ok ? await cres.json() : null;
    minL = Math.min(...model.levels);
    selLevel = null;
    map.getSource("st").setData(model.features);
    setContext();
    applyHeights();
    applyFilters();
    fillLevels();
    fillSelects();
    renderWay();
    buildLabels();
    $("note").textContent = `Kartendaten: OpenStreetMap-Mitwirkende (ODbL). ${model.stats.graph_paths} von ${model.stats.graph_paths + model.stats.estimated_paths} Umstiegen sind auf dem Wegenetz berechnet, der Rest ist geschätzt. Lücken in OpenStreetMap (fehlende Bahnsteige, Treppen oder Aufzüge) erscheinen hier als Lücken.`;
  }

  async function start() {
    const index = await (await fetch("stations/index.json")).json();
    const chips = $("stations");
    for (const s of index) {
      const b = el("button", s.name);
      b.type = "button";
      b.dataset.id = s.id;
      b.setAttribute("role", "tab");
      b.addEventListener("click", () => load(s.id));
      chips.append(b);
    }
    map = new maplibregl.Map({
      container: "map",
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": C.ground } }], light: { anchor: "map", position: [1.3, 215, 48], intensity: dark ? 0.55 : 0.45, color: dark ? "#cfd6ff" : "#ffffff" } },
      center: index[0].center, zoom: 17, pitch: 58, bearing: -28, maxPitch: 80, minZoom: 14, antialias: true,
      attributionControl: { customAttribution: "© OpenStreetMap-Mitwirkende" },
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.on("error", (e) => { $("note").textContent = `Kartenfehler: ${(e && e.error && e.error.message) || e.message || e}`; });
    map.on("render", queueLabels);
    map.on("load", () => {
      const empty = { type: "FeatureCollection", features: [] };
      for (const id of ["st", "path", "dot", "c-slab", "c-road", "c-bld", "c-tree", "c-green", "c-water"]) map.addSource(id, { type: "geojson", data: empty });
      addLayers();
      $("gap").addEventListener("input", (e) => { gapIdx = Number(e.target.value); applyHeights(); });
      for (const b of $("ctx").children) b.addEventListener("click", () => { ctxMode = b.dataset.mode; for (const c of $("ctx").children) c.setAttribute("aria-pressed", String(c === b)); applyCtxMode(); });
      $("from").addEventListener("change", renderWay);
      $("to").addEventListener("change", renderWay);
      const wanted = query.get("id");
      load(index.some((s) => s.id === wanted) ? wanted : index[0].id);
    });
  }
  start().catch((e) => { $("note").textContent = `Fehler beim Laden: ${e.message}`; });
  window.addEventListener("error", (e) => { $("note").textContent = `Fehler: ${e.message}`; });
  window.__station = { get map() { return map; }, set ctxMode(v) { ctxMode = v; applyCtxMode(); }, set level(v) { selLevel = v; applyFilters(); queueLabels(); } }; // fuer Tests
})();
