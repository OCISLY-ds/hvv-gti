// Bahnhof in 3D: MapLibre fill-extrusion, je Ebene eine Hoehe. Daten: /stations/<id>.json (scripts/build_stations.py aus OpenStreetMap).
// Jede Art (Bahnsteig, Gang, Gleis, Raum, Treppenstufe, Aufzug) ist eine eigene Ebene des Stils; "Ebene waehlen" blendet die uebrigen
// gedimmt ein, "Ebenenabstand" zieht das Modell auseinander (Hoehe = (Ebene - tiefste Ebene) * Abstand).
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const C = dark
    ? { ground: "#0f1b1d", walk: "#5d767a", S: "#2fae6a", U: "#3a8ee0", R: "#d9605a", "?": "#8497a0", track: "#070b0c", room: "#35505a", part: "#41555a", step: "#e4a519", esc: "#f08a3c", lift: "#5cc8cf", path: "#ff6b70", casing: "#ffffff", pick: "#ffd24a" }
    : { ground: "#e9eeed", walk: "#9fb2b4", S: "#1f9a5a", U: "#1d6fc0", R: "#c4413b", "?": "#7a8c94", track: "#4a5f63", room: "#b9c9cb", part: "#c3cfcd", step: "#c8870f", esc: "#d96d1c", lift: "#0b6b73", path: "#d4141c", casing: "#ffffff", pick: "#f0a500" };
  const MODE_NAME = { S: "S-Bahn", U: "U-Bahn", R: "Regional/Fern", "?": "Bahnsteig" };
  const MODE_ORDER = { U: 0, S: 1, R: 2, "?": 3 };
  const fmtLevel = (l) => String(l).replace("-", "−");
  const mmss = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s` : `${s} s`);

  let map, model, minL = 0, gapIdx = 2, selLevel = null, current = null, dotTimer = 0, pathPts = [];
  const LH = () => 3.2 * gapIdx; // Meter zwischen zwei Ebenen

  // ---- Ausdruecke ----
  const level = (prop) => ["*", ["-", ["to-number", ["get", prop]], minL], LH()];
  const zl = (prop, off) => ["+", level(prop), off];

  const KINDS = [
    // id, Filter, Basis, Hoehe, Farbe, Deckkraft (an / gedimmt), dimmbar
    { id: "part", f: ["==", ["get", "k"], "part"], base: () => 0, top: () => ["*", ["to-number", ["get", "h"]], 3.2], color: C.part, op: [0.1, 0], dim: false, level: false },
    { id: "track", f: ["==", ["get", "k"], "track"], base: () => zl("l", -0.35), top: () => zl("l", -0.05), color: C.track, op: [1, 0.2], dim: true },
    { id: "walk", f: ["==", ["get", "k"], "walk"], base: () => zl("l", -0.3), top: () => zl("l", 0), color: C.walk, op: [1, 0.2], dim: true },
    { id: "floor", f: ["==", ["get", "k"], "floor"], base: () => zl("l", -0.55), top: () => zl("l", 0.15), color: ["match", ["get", "m"], "S", C.S, "U", C.U, "R", C.R, C["?"]], op: [1, 0.22], dim: true },
    { id: "room", f: ["==", ["get", "k"], "room"], base: () => zl("l", 0), top: () => zl("l", 2.6), color: C.room, op: [0.5, 0.1], dim: true },
    { id: "step", f: ["==", ["get", "k"], "step"], base: () => zl("l", -0.45), top: () => zl("l", 0.05), color: C.step, op: [1, 0], dim: false, near: true },
    { id: "esc", f: ["==", ["get", "k"], "esc"], base: () => zl("l", -0.45), top: () => zl("l", 0.05), color: C.esc, op: [1, 0], dim: false, near: true },
    { id: "lift", f: ["==", ["get", "k"], "lift"], base: () => zl("l", -0.3), top: () => zl("h", 0.25), color: C.lift, op: [0.6, 0], dim: false, span: true },
  ];

  function filters(k) {
    const on = [k.f];
    const dim = [k.f];
    if (selLevel !== null && k.level !== false) {
      if (k.near) on.push([">=", ["get", "l"], selLevel - 1], ["<=", ["get", "l"], selLevel + 1]);
      else if (k.span) on.push(["<=", ["get", "l"], selLevel], [">=", ["get", "h"], selLevel]);
      else on.push(["==", ["get", "l"], selLevel]);
      if (k.dim) dim.push(["!=", ["get", "l"], selLevel]);
    }
    return { on: ["all", ...on], dim: selLevel !== null && k.dim ? ["all", ...dim] : ["==", ["get", "k"], "__keine"] };
  }

  function layerDef(id, source, f, base, top, color, opacity) {
    return { id, type: "fill-extrusion", source, filter: f, paint: { "fill-extrusion-color": color, "fill-extrusion-base": base, "fill-extrusion-height": top, "fill-extrusion-opacity": opacity, "fill-extrusion-vertical-gradient": true } };
  }

  function addLayers() {
    for (const k of KINDS) {
      const f = filters(k);
      map.addLayer(layerDef(`${k.id}-on`, "st", f.on, k.base(), k.top(), k.color, k.op[0]));
      if (k.dim) map.addLayer(layerDef(`${k.id}-dim`, "st", f.dim, k.base(), k.top(), k.color, k.op[1]));
      if (k.id === "floor") map.addLayer(layerDef("pick", "st", ["==", ["get", "p"], "__keiner"], zl("l", -0.55), zl("l", 0.3), C.pick, 1));
    }
    map.addLayer(layerDef("path-casing", "path", ["==", ["get", "k"], "casing"], zl("l", 0.05), zl("l", 0.4), C.casing, 0.9));
    map.addLayer(layerDef("path-on", "path", ["==", ["get", "k"], "path"], zl("l", 0.15), zl("l", 0.7), C.path, 1));
    map.addLayer(layerDef("dot", "dot", ["has", "l"], zl("l", 0.7), zl("l", 2.0), C.path, 1));
  }

  function applyHeights() {
    for (const k of KINDS) {
      for (const suffix of ["on", "dim"]) {
        const id = `${k.id}-${suffix}`;
        if (!map.getLayer(id)) continue;
        map.setPaintProperty(id, "fill-extrusion-base", k.base());
        map.setPaintProperty(id, "fill-extrusion-height", k.top());
      }
    }
    for (const [id, a, b] of [["pick", -0.55, 0.3], ["path-casing", 0.05, 0.4], ["path-on", 0.15, 0.7], ["dot", 0.7, 2.0]]) {
      map.setPaintProperty(id, "fill-extrusion-base", zl("l", a));
      map.setPaintProperty(id, "fill-extrusion-height", zl("l", b));
    }
  }

  function applyFilters() {
    for (const k of KINDS) {
      const f = filters(k);
      map.setFilter(`${k.id}-on`, f.on);
      if (k.dim) map.setFilter(`${k.id}-dim`, f.dim);
    }
  }

  // ---- Weg als Band ----
  function quad(a, b, half, kx, ky) {
    const dx = (b[0] - a[0]) * kx, dy = (b[1] - a[1]) * ky, d = Math.hypot(dx, dy) || 1;
    const ux = dx / d, uy = dy / d, nx = -uy * half, ny = ux * half, ex = ux * half * 0.8, ey = uy * half * 0.8;
    const P = (p, s, e) => [p[0] + (s * nx + e * ex) / kx, p[1] + (s * ny + e * ey) / ky];
    return [P(a, 1, -1), P(b, 1, 1), P(b, -1, 1), P(a, -1, -1), P(a, 1, -1)];
  }

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
    const kx = 111320 * Math.cos((model.center[1] * Math.PI) / 180), ky = 110574;
    const features = [];
    pathPts = [];
    if (path && path.g) {
      pathPts = densify(path.g);
      for (let i = 0; i < pathPts.length - 1; i++) {
        const a = pathPts[i], b = pathPts[i + 1], l = Math.max(a[2], b[2]);
        if (Math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * ky) < 0.05) continue;
        features.push({ type: "Feature", properties: { k: "casing", l }, geometry: { type: "Polygon", coordinates: [quad(a, b, 0.95, kx, ky)] } });
        features.push({ type: "Feature", properties: { k: "path", l }, geometry: { type: "Polygon", coordinates: [quad(a, b, 0.6, kx, ky)] } });
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
      const r = 1.0;
      map.getSource("dot").setData({ type: "FeatureCollection", features: [{ type: "Feature", properties: { l: p[2] }, geometry: { type: "Polygon", coordinates: [[[p[0] - r / kx, p[1] - r / ky], [p[0] + r / kx, p[1] - r / ky], [p[0] + r / kx, p[1] + r / ky], [p[0] - r / kx, p[1] + r / ky], [p[0] - r / kx, p[1] - r / ky]]] } }] });
    }, 66);
  }

  // ---- Oberflaeche ----
  const platformText = (p) => `${MODE_NAME[p.mode] || "Bahnsteig"} · ${p.label} (Ebene ${fmtLevel(p.level)})`;

  function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }

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
    const q = new URLSearchParams(location.search);
    const known = (id) => model.platforms.some((p) => p.id === id);
    if (q.get("id") === model.id && known(q.get("from")) && known(q.get("to"))) { $("from").value = q.get("from"); $("to").value = q.get("to"); }
  }

  function fillLevels() {
    const box = $("levels");
    box.replaceChildren();
    const mk = (label, value) => {
      const b = el("button", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(selLevel === value));
      b.addEventListener("click", () => { selLevel = value; for (const c of box.children) c.setAttribute("aria-pressed", String(c === b)); applyFilters(); });
      box.append(b);
    };
    mk("Alle", null);
    for (const l of [...model.levels].reverse()) mk(fmtLevel(l), l);
  }

  async function load(id) {
    current = id;
    for (const b of $("stations").children) b.setAttribute("aria-selected", String(b.dataset.id === id));
    $("note").textContent = "Lade …";
    const res = await fetch(`stations/${encodeURIComponent(id)}.json`);
    if (!res.ok) { $("note").textContent = "Dieser Bahnhof ist nicht verfügbar."; return; }
    model = await res.json();
    minL = Math.min(...model.levels);
    selLevel = null;
    map.getSource("st").setData(model.features);
    applyHeights();
    applyFilters();
    fillLevels();
    fillSelects();
    renderWay();
    const pf = model.features.features.filter((f) => f.properties.k === "floor").flatMap((f) => f.geometry.coordinates[0]);
    const pts = pf.length ? pf : [model.center];
    const b = pts.reduce((r, p) => [Math.min(r[0], p[0]), Math.min(r[1], p[1]), Math.max(r[2], p[0]), Math.max(r[3], p[1])], [180, 90, -180, -90]);
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: { top: 50, bottom: 50, left: 40, right: 40 }, pitch: 58, bearing: -28, maxZoom: 18.2, duration: reduced ? 0 : 900 });
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
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": C.ground } }], light: { anchor: "map", position: [1.4, 210, 52], intensity: 0.5, color: "#ffffff" } },
      center: index[0].center, zoom: 17, pitch: 58, bearing: -28, maxPitch: 80, minZoom: 14, antialias: true,
      attributionControl: { customAttribution: "© OpenStreetMap-Mitwirkende" },
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.on("load", () => {
      const empty = { type: "FeatureCollection", features: [] };
      for (const id of ["st", "path", "dot"]) map.addSource(id, { type: "geojson", data: empty });
      addLayers();
      $("gap").addEventListener("input", (e) => { gapIdx = Number(e.target.value); applyHeights(); });
      $("bld").addEventListener("change", (e) => { for (const s of ["on"]) map.setLayoutProperty(`part-${s}`, "visibility", e.target.checked ? "visible" : "none"); });
      map.setLayoutProperty("part-on", "visibility", "none");
      $("from").addEventListener("change", renderWay);
      $("to").addEventListener("change", renderWay);
      const wanted = new URLSearchParams(location.search).get("id");
      load(index.some((s) => s.id === wanted) ? wanted : index[0].id);
    });
  }
  start().catch((e) => { $("note").textContent = `Fehler beim Laden: ${e.message}`; });
})();
