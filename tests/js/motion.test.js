"use strict";
// Ausfuehren: node --test tests/js/motion.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { Animator, ease, easeInv, locate, prepare } = require("../../map/static/motion.js");

const { KX } = require("../../map/static/motion.js");
const tracks = {
  AB: { p: [10.0, 53.5, 10.05, 53.5, 10.1, 53.5] },
  BC: { p: [10.1, 53.5, 10.1, 53.55] },
};
const seg = (k, a, b, d = 0, f = "A", t = "B") => ({ k, a, b, d, f, t });
const journey = (segments, id = "j") => ({ id, l: "S1", m: "S", end: false, s: segments });
const metres = (p, q) => Math.hypot((p.x - q.x) * KX, (p.y - q.y) * 110540);

test("ease and easeInv are inverse and monotonic", () => {
  for (const r of [0, 0.1, 0.25, 0.5]) {
    let last = -1;
    for (let u = 0; u <= 1.0001; u += 0.01) {
      const f = ease(u, r);
      assert.ok(f >= last - 1e-12);
      last = f;
      assert.ok(Math.abs(easeInv(f, r) - Math.min(1, u)) < 1e-6, `r=${r} u=${u}`);
    }
    assert.equal(ease(0, r), 0);
    assert.ok(Math.abs(ease(1, r) - 1) < 1e-12);
  }
});

test("locate interpolates along the polyline and gives the heading", () => {
  const mid = locate(tracks.AB, 0.5);
  assert.ok(Math.abs(mid.x - 10.05) < 1e-6);
  assert.ok(Math.abs(mid.rot - 90) < 0.5);
  assert.ok(Math.abs(locate(tracks.BC, 0.5).rot) < 0.5);
});

// Fahrt jede Sekunde im Takt von 60 fps simulieren; liefert Positionen und maximalen Schritt je Bild
function run(animator, from, to, positions, id = "j") {
  let worst = 0, prev = null;
  for (let now = from; now <= to; now += 1 / 60) {
    const e = animator.step(now).find((x) => x.id === id);
    if (e) {
      if (prev) worst = Math.max(worst, metres(prev, e.pose));
      prev = e.pose;
      positions.push({ now, x: e.pose.x, tau: e.tau });
    }
  }
  return worst;
}

for (const shift of [60, 120, -60]) {
  test(`a schedule shift of ${shift} s does not make the train jump or run backwards`, () => {
    const a = new Animator(() => tracks);
    a.ingest([journey([seg("AB", 1000, 1300)])], 1000);
    const pos = [];
    run(a, 1000, 1100, pos);
    const before = a.entries.get("j").pose;
    a.ingest([journey([seg("AB", 1000 + shift, 1300 + shift, 1)])], 1100);
    const after = a.step(1100 + 1 / 60).find((x) => x.id === "j").pose;
    assert.ok(metres(before, after) < 15, `Sprung ${metres(before, after)} m`);
    const more = [];
    const worst = run(a, 1100 + 1 / 60, 1500, more);
    assert.ok(worst < 3.5 * 25 / 60 + 1, `Schritt ${worst} m`); // hoechstens RATE_MAX * 25 m/s
    for (let i = 1; i < more.length; i++) assert.ok(more[i].x >= more[i - 1].x - 1e-9, "rueckwaerts");
    const end = more[more.length - 1];
    assert.ok(Math.abs(end.now - end.tau) < 0.5, `Rueckstand ${end.now - end.tau}`);
  });
}

test("a dwelling train stays put when the departure moves", () => {
  const a = new Animator(() => tracks);
  const day = [seg("AB", 1000, 1200), seg("BC", 1260, 1400)];
  a.ingest([journey(day)], 1000);
  const pos = [];
  run(a, 1000, 1230, pos); // steht seit ~1175 in B (DWELL_S vor der Abfahrt um 1260 eingerollt)
  const before = a.entries.get("j").pose;
  assert.equal(before.dwell, true);
  a.ingest([journey([seg("AB", 1000, 1200), seg("BC", 1320, 1460)])], 1230);
  const after = a.step(1230 + 1 / 60).find((x) => x.id === "j").pose;
  assert.ok(metres(before, after) < 1);
  assert.equal(after.dwell, true);
});

test("new journeys appear at their scheduled position and vanished ones keep running for a while", () => {
  const a = new Animator(() => tracks);
  a.ingest([journey([seg("AB", 1000, 1300)])], 1100);
  const e = a.step(1100).find((x) => x.id === "j");
  assert.ok(Math.abs(e.pose.f - ease(100 / 300, e.j.s[0].r)) < 1e-9);
  a.ingest([], 1120);
  assert.equal(a.step(1121).length, 1); // bleibt nach dem alten Fahrplan sichtbar
  a.ingest([], 1100 + 200);
  assert.equal(a.step(1300).length, 0); // zu lange nicht gesehen
});

test("a train keeps running its segment when the new data already dropped it", () => {
  const a = new Animator(() => tracks);
  a.ingest([journey([seg("AB", 1000, 1300, 0, "A", "B"), seg("BC", 1330, 1500, 0, "B", "C")])], 1000);
  const pos = [];
  run(a, 1000, 1100, pos);
  const before = a.entries.get("j").pose;
  assert.equal(before.seg.k, "AB");
  // der neue Abruf kennt nur noch BC, und zwar frueher als gedacht (Verspaetung abgebaut)
  a.ingest([journey([seg("BC", 1130, 1300, 0, "B", "C")])], 1100);
  const after = a.step(1100 + 1 / 60).find((x) => x.id === "j").pose;
  assert.ok(metres(before, after) < 15, `Sprung ${metres(before, after)} m`);
  const more = [];
  const worst = run(a, 1100 + 1 / 60, 1400, more);
  assert.ok(worst < 4, `Schritt ${worst} m`); // die verkuerzte Fahrzeit (6,6 km in 105 s) wird schnell, aber stetig nachgeholt
  for (let i = 1; i < more.length; i++) assert.ok(more[i].x >= more[i - 1].x - 1e-9, "rueckwaerts");
});

test("delay trend follows changes of the displayed delay", () => {
  const a = new Animator(() => tracks);
  a.ingest([journey([seg("AB", 1000, 1300, 2)])], 1000);
  assert.equal(a.step(1001)[0].trend, 0); // erstes Sehen, kein Vergleich moeglich
  a.ingest([journey([seg("AB", 1060, 1360, 4)])], 1002);
  assert.equal(a.step(1003)[0].trend, 1); // 2 -> 4 min: waechst
  a.step(1100);
  assert.equal(a.step(1101)[0].trend, 1); // bleibt bis zur naechsten Aenderung
  a.ingest([journey([seg("AB", 1000 + 60, 1360, 3)])], 1102);
  assert.equal(a.step(1103)[0].trend, -1); // 4 -> 3 min: wird abgebaut
  // neuer Zug mit bekanntem Vorgaengerabschnitt
  const b = new Animator(() => tracks);
  b.ingest([journey([seg("AB", 1000, 1100, 1), seg("BC", 1130, 1300, 3, "B", "C")])], 1150);
  assert.equal(b.step(1150)[0].trend, 1);
});

test("prepare computes effective arrival and ramp", () => {
  const j = prepare(journey([seg("AB", 1000, 1200), seg("BC", 1210, 1400)]));
  assert.equal(j.s[0].e, 1185); // 25 s vor der Abfahrt, aber nie vor der halben Fahrzeit
  assert.equal(j.s[1].e, 1400);
});

// ---- Blockabstand ----
const { blockOf, occupiedBlocks, stackTargets } = require("../../map/static/motion.js");
const blockTracks = {
  "A>B": { p: [10.0, 53.5, 10.1, 53.5], s: [0.5] }, // ein Signal in der Mitte: Blocke S:A, T:A>B:1, S:B (Bahnhof B)
  "B>C": { p: [10.1, 53.5, 10.2, 53.5], s: [0.5] },
};

test("blockOf names station blocks after the stop, shared by both neighbouring sections", () => {
  assert.equal(blockOf(blockTracks, "A>B", 0), "S:A");
  assert.equal(blockOf(blockTracks, "A>B", 0.7), "S:B");
  assert.equal(blockOf(blockTracks, "B>C", 0), "S:B");
  assert.equal(blockOf(blockTracks, "A>B", 0.5), "S:B"); // genau am Signal: schon im Block dahinter (hier der letzte)
  assert.equal(blockOf({ X: { p: [0, 0, 1, 1] } }, "X", 0.5), "T:X:1"); // ohne Signal: ganzer Abschnitt ein Block
});

function followerRun(extra) {
  const tr = { "A>B": { p: [10.0, 53.5, 10.1, 53.5], s: [0.3, 0.6] }, "B>C": blockTracks["B>C"] };
  const leader = journey([seg("A>B", 800, 1300), seg("B>C", 1310, 1400)], "lead");
  const follower = journey([seg("A>B", 1000, 1400), seg("B>C", 1500, 1900)], "follow");
  const animator = new Animator(() => tr);
  animator.ingest([leader, follower], 1000);
  const frames = [];
  for (let now = 1000; now <= 1900; now += 1 / 20) {
    const out = animator.step(now);
    const lead = out.find((e) => e.id === "lead"), fol = out.find((e) => e.id === "follow");
    frames.push({ now, lead, fol, folPose: fol && { ...fol.pose }, waiting: fol && !!fol.blockedSince, braked: fol && fol.braked });
  }
  return frames;
}

test("a follower never shares a block with the leader, waits at the signal and follows", () => {
  const frames = followerRun();
  let waited = false;
  for (const { now, lead, fol, waiting } of frames) {
    if (!lead || !fol) continue;
    const shared = (fol.blocks || []).filter((b) => (lead.blocks || []).includes(b));
    assert.equal(shared.length, 0, `gemeinsamer Block ${shared} bei ${now}`);
    if (waiting) waited = true;
  }
  assert.ok(waited, "der Folgezug haette warten muessen");
  assert.ok(frames[frames.length - 1].fol, "der Folgezug faehrt am Ende weiter");
});

test("the follower brakes and starts off smoothly (at most about 1 m/s^2) because of the signal", () => {
  const frames = followerRun();
  let prev = null, worst = 0, stopped = false, limited = 0;
  for (const { now, folPose, waiting, braked } of frames) {
    if (!folPose) continue;
    if (waiting) stopped = true;
    const v = prev ? metres(prev.pose, folPose) / (now - prev.now) : null;
    if (prev && prev.v > 0.5 && prev.braked && braked) {
      worst = Math.max(worst, Math.abs((v - prev.v) / (now - prev.now)));
      limited++;
    }
    prev = { pose: folPose, now, v, braked };
  }
  assert.ok(stopped);
  assert.ok(limited > 100, `nur ${limited} Bilder gebremst`);
  assert.ok(worst < 1.2, `Beschleunigung/Verzoegerung ${worst} m/s^2`);
});

test("a train occupies every block over its length of about 160 m", () => {
  const tr = { "A>B": { p: [10.0, 53.5, 10.0045, 53.5], s: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] } }; // ~300 m, Bloecke je 30 m
  const pose = { seg: { k: "A>B" }, f: 0.5 };
  const blocks = occupiedBlocks({ s: [pose.seg] }, tr, pose);
  assert.ok(blocks.length >= 5 && blocks.length <= 7, blocks.join());
  assert.ok(blocks.includes(blockOf(tr, "A>B", 0.5)));
});

// ---- Stapeln ----
const stack = (items, opts = {}) => stackTargets(items, { thresholdM: 20, stepPx: 20, baseUpPx: 30, nearAnchor: null, ...opts });

test("trains far apart are not moved", () => {
  const out = stack([{ id: "a", mx: 0, my: 0 }, { id: "b", mx: 100, my: 0 }]);
  assert.deepEqual([out.get("a"), out.get("b")], [0, 0]);
});

test("overlapping trains are stacked vertically, centered and in a stable order", () => {
  const items = [{ id: "c", mx: 0, my: 0 }, { id: "a", mx: 3, my: 1 }, { id: "b", mx: 6, my: 0 }];
  const out = stack(items);
  assert.deepEqual([out.get("a"), out.get("b"), out.get("c")], [-20, 0, 20]); // nach id sortiert, um 0 zentriert
  assert.deepEqual([...stack([...items].reverse()).entries()].sort().map((e) => e[1]), [-20, 0, 20]);
});

test("a chain of close trains forms one stack", () => {
  const out = stack([{ id: "a", mx: 0, my: 0 }, { id: "b", mx: 15, my: 0 }, { id: "c", mx: 30, my: 0 }]);
  assert.equal(new Set([...out.values()]).size, 3);
});

test("trains dwelling at a station are stacked above the pin, even a single one", () => {
  const near = (mx, my) => Math.hypot(mx, my) < 25;
  const single = stack([{ id: "a", mx: 2, my: 0, dwell: true }], { nearAnchor: near });
  assert.equal(single.get("a"), -30); // ueber dem Pin
  const two = stack([{ id: "a", mx: 2, my: 0, dwell: true }, { id: "b", mx: 4, my: 0, dwell: true }], { nearAnchor: near });
  assert.deepEqual([two.get("a"), two.get("b")], [-30, -50]); // Pin bleibt frei
  const passing = stack([{ id: "a", mx: 2, my: 0, dwell: false }], { nearAnchor: near });
  assert.equal(passing.get("a"), 0); // durchfahrende Zuege hopsen nicht
});

test("a huge crowd of trains is not stacked into a column across the screen", () => {
  const crowd = Array.from({ length: 200 }, (_, i) => ({ id: `t${String(i).padStart(3, "0")}`, mx: i * 5, my: (i % 7) * 3, dwell: false }));
  const out = stack(crowd);
  assert.equal(Math.max(...[...out.values()].map(Math.abs)), 0);
  const few = stack(crowd.slice(0, 5)); // wenige ueberlappende Zuege werden weiterhin gestapelt
  assert.ok(new Set([...few.values()]).size > 1);
});
