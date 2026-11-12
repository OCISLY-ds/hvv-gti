"use strict";
// Bewegung der Zuege: Position aus den Segmentzeiten, ohne Spruenge bei neuen Daten.
//
// Jeder Zug hat eine eigene virtuelle Uhr `tau`. Sie laeuft normalerweise mit der Serverzeit. Aendert ein neuer Abruf den Fahrplan
// (z. B. die Verspaetung waechst um eine Minute), wird `tau` so gesetzt, dass der Zug genau dort bleibt, wo er gerade zu sehen
// ist, und holt den Unterschied danach schneller bzw. langsamer fahrend auf, statt zur neuen Position zu springen.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HvvMotion = api;
})(typeof self !== "undefined" ? self : this, function () {
  const DWELL_S = 25; // Haltezeit an jeder Haltestelle
  const END_DWELL_S = 120; // bei abgeschnittenen Fahrten (Gebiet/Zeitfenster) bleibt der Zug laenger stehen
  const RAMP_S = 20; // Anfahren und Bremsen dauern je so lange (bei kurzen Abschnitten hoechstens die Haelfte)
  const TC = 15; // Zeitkonstante des Aufholens in Sekunden
  const RATE_MIN = 0.3, RATE_MAX = 2.5; // so viel langsamer bzw. schneller als die Realzeit darf ein Zug aufholen
  const SNAP_S = 300; // groesserer Rueckstand: nicht aufholen, sondern neu aufsetzen
  const KEEP_S = 90; // Fahrten, die ein Abruf nicht mehr liefert, laufen nach dem alten Fahrplan so lange weiter
  const TURN_RATE = 8; // Drehung des Pfeils: Anteil pro Sekunde (exponentiell)
  const BLOCK_MODES = new Set(["S"]); // nur hier gilt Blockabstand (Haltsignale aus OSM liegen nur fuer die S-Bahn vor)
  const BLOCK_TIMEOUT_S = 180; // so lange wartet ein Zug hoechstens vor einem besetzten Block (Sicherung gegen Verklemmen)
  const EDGE_EPS = 0.001;
  const TRAIN_LEN_M = 160; // Zuglaenge (S-Bahn-Einheiten 130-200 m)
  const A_BRAKE = 0.9, A_ACC = 0.8; // Bremsen vor besetztem Block bzw. Anfahren danach in m/s^2
  const STOP_MARGIN_M = 10; // so weit vor dem Signal bleibt die Spitze stehen
  const LOOKAHEAD_M = 1800; // so weit voraus wird nach besetzten Bloecken gesucht (Bremsweg aus 120 km/h ~620 m)

  const KX = Math.cos((53.55 * Math.PI) / 180) * 111320, KY = 110540;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // ---- Geometrie ----

  function cumulative(t) {
    if (t.cum) return t.cum;
    const cum = [0];
    for (let i = 2; i < t.p.length; i += 2) cum.push(cum[cum.length - 1] + Math.hypot((t.p[i] - t.p[i - 2]) * KX, (t.p[i + 1] - t.p[i - 1]) * KY));
    return (t.cum = cum);
  }

  // Punkt und Fahrtrichtung (Grad im Uhrzeigersinn von Norden) bei Streckenanteil f (0..1)
  function locate(t, f) {
    const cum = cumulative(t), target = cum[cum.length - 1] * clamp(f, 0, 1);
    let lo = 1, hi = cum.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    const i = lo, span = cum[i] - cum[i - 1] || 1, r = (target - cum[i - 1]) / span, a = (i - 1) * 2, b = i * 2;
    return {
      x: t.p[a] + (t.p[b] - t.p[a]) * r,
      y: t.p[a + 1] + (t.p[b + 1] - t.p[a + 1]) * r,
      rot: (Math.atan2((t.p[b] - t.p[a]) * Math.cos((53.55 * Math.PI) / 180), t.p[b + 1] - t.p[a + 1]) * 180) / Math.PI,
    };
  }

  // ---- Geschwindigkeitsprofil: anfahren, gleichmaessig fahren, bremsen (Trapez), Anteil r der Zeit je Rampe ----

  const rampShare = (duration) => Math.min(0.5, RAMP_S / Math.max(duration, 1));

  function ease(u, r) {
    u = clamp(u, 0, 1);
    if (r <= 0) return u;
    const v = 1 / (1 - r);
    if (u < r) return (v * u * u) / (2 * r);
    if (u > 1 - r) return 1 - (v * (1 - u) * (1 - u)) / (2 * r);
    return v * (r / 2 + (u - r));
  }

  function easeInv(f, r) {
    f = clamp(f, 0, 1);
    if (r <= 0) return f;
    const v = 1 / (1 - r), edge = (v * r) / 2;
    if (f < edge) return Math.sqrt((2 * r * f) / v);
    if (f > 1 - edge) return 1 - Math.sqrt((2 * r * (1 - f)) / v);
    return r + (f - edge) / v;
  }

  // ---- Fahrplan ----

  // Effektive Ankunft `e` je Abschnitt: kennt die API die Abfahrt (Start des naechsten Abschnitts), rollt der Zug DWELL_S
  // vorher ein und steht bis zur Abfahrt; am Ende der Daten steht er DWELL_S nach der gemeldeten Ankunft.
  function prepare(j) {
    j.s.forEach((s, i) => {
      const next = j.s[i + 1];
      s.e = next ? Math.max(s.a + 0.5 * (s.b - s.a), Math.min(s.b, next.a - DWELL_S)) : s.b;
      s.r = rampShare(s.e - s.a);
    });
    return j;
  }

  // Pose zum Zeitpunkt t: Ort, Richtung, Abschnitt `seg`, Streckenanteil `f` darin und ob der Zug am Halt steht (dann am Ende von
  // `seg`, f = 1; vor dem ersten Abschnitt am Anfang, f = 0).
  function poseAt(j, tracks, t) {
    let prev = null;
    for (const s of j.s) {
      if (t < s.a) {
        if (prev && tracks[prev.k]) {
          const here = locate(tracks[prev.k], 1), ahead = tracks[s.k] ? locate(tracks[s.k], 0) : here;
          return { x: here.x, y: here.y, rot: ahead.rot, seg: prev, f: 1, dwell: true };
        }
        if (tracks[s.k]) {
          const here = locate(tracks[s.k], 0);
          return { ...here, seg: s, f: 0, dwell: true };
        }
        return null;
      }
      if (t <= s.e) {
        const tr = tracks[s.k];
        if (!tr) return null;
        const f = ease((t - s.a) / Math.max(1, s.e - s.a), s.r);
        return { ...locate(tr, f), seg: s, f, dwell: false };
      }
      prev = s;
    }
    if (prev && tracks[prev.k] && t - prev.e < (j.end ? DWELL_S : END_DWELL_S)) {
      return { ...locate(tracks[prev.k], 1), seg: prev, f: 1, dwell: true };
    }
    return null;
  }

  // Naechster Halt: Ende des ersten Abschnitts, der zum Zeitpunkt t noch nicht abgeschlossen ist
  function nextStop(j, t) {
    for (const s of j.s) if (t <= s.e) return { name: s.t, at: s.e, due: s.b, eta: s.e - t };
    return null;
  }

  // Der Abschnitt, in dem der Zug gerade zu sehen ist, fehlt im neuen Abruf, wenn die Daten ihn schon als beendet melden (die
  // Abrufe liefern nur Abschnitte ab kurz vor jetzt). Dann wird er mit seinen alten Zeiten vorangestellt (hoechstens bis zum Start
  // des ersten neuen Abschnitts), damit der Zug ihn zu Ende fahren kann, statt an das Ende zu springen.
  function carryOver(pose, j) {
    const seg = pose.seg;
    if (!seg || seg.k == null || j.s.some((s) => s.k === seg.k)) return;
    const first = j.s[0];
    const b = first ? Math.min(seg.b, first.a) : seg.b;
    j.s.unshift({ ...seg, b, a: Math.min(seg.a, b - 15) });
  }

  // Virtuelle Zeit im neuen Fahrplan, zu der der Zug dort steht, wo `pose` ihn gerade zeigt. Steht er am Halt, passt jeder
  // Zeitpunkt der Haltezeit; dann wird der gewaehlt, der `now` am naechsten liegt.
  function anchor(pose, j, now) {
    const segs = j.s, idx = segs.findIndex((s) => s.k != null && s.k === pose.seg.k);
    if (idx < 0) return now;
    const s = segs[idx];
    if (!pose.dwell) return s.a + easeInv(pose.f, s.r) * (s.e - s.a);
    if (pose.f === 0) return Math.min(now, s.a);
    const next = segs[idx + 1];
    return clamp(now, s.e, next ? next.a : s.e + (j.end ? DWELL_S : END_DWELL_S));
  }

  function turn(from, to, k) {
    const d = ((((to - from) % 360) + 540) % 360) - 180;
    return from + d * k;
  }

  // Verspaetungstrend: +1 waechst, -1 wird abgebaut, 0 unbekannt. Er aendert sich, wenn sich die Verspaetung des angezeigten
  // Abschnitts aendert (neuer Abschnitt oder neue Daten), und bleibt bis zur naechsten Aenderung bestehen. Beim ersten Sehen
  // gilt der Vergleich mit dem Abschnitt davor, falls die Daten ihn noch enthalten.
  function trackTrend(e) {
    const seg = e.pose.seg, d = seg.d || 0;
    if (e.dCur === null) {
      const i = e.j.s.indexOf(seg), prev = i > 0 ? e.j.s[i - 1] : null;
      e.trend = prev ? Math.sign(d - (prev.d || 0)) : 0;
    } else if (d !== e.dCur) {
      e.trend = Math.sign(d - e.dCur);
    }
    e.dCur = d;
  }

  // ---- Blockabstand ----
  //
  // Die Haltsignale (`track.s`, Streckenanteile) teilen jeden Abschnitt in Bloecke. Der Block vor dem ersten und hinter dem letzten
  // Signal ist der Bahnhofsblock; er heisst nach der Haltestelle (Schluessel "A>B"), damit der Zug am Bahnsteig im Ende des
  // einen und im Anfang des naechsten Abschnitts denselben Block belegt. Gleise haben je Richtung eigene Haltestellen-Schluessel,
  // Zuege der Gegenrichtung stoeren sich also nicht. Ein Block darf immer nur einen Zug enthalten; ein Zug belegt alle Bloecke
  // von der Spitze bis TRAIN_LEN_M zurueck. Vor einem besetzten Block bremst der Zug mit A_BRAKE und haelt STOP_MARGIN_M vor dem
  // Signal; danach faehrt er hoechstens mit A_ACC an.
  function boundsOf(t) {
    return t.s && t.s.length ? t.s : [EDGE_EPS, 1 - EDGE_EPS];
  }

  function blockIndex(bounds, f) {
    let i = 0;
    while (i < bounds.length && bounds[i] <= f) i++;
    return i;
  }

  function blockId(k, bounds, i) {
    if (i === 0) return "S:" + k.split(">")[0];
    if (i === bounds.length) return "S:" + k.split(">")[1];
    return "T:" + k + ":" + i;
  }

  function blockOf(tracks, k, f) {
    const t = tracks[k];
    if (!t) return null;
    const bounds = boundsOf(t);
    return blockId(k, bounds, blockIndex(bounds, f));
  }

  // Teilstueck [f0, f1] (Streckenanteile) der Linie als Liste von [x, y]
  function slice(t, f0, f1) {
    const cum = cumulative(t), total = cum[cum.length - 1], d0 = f0 * total, d1 = f1 * total, a = locate(t, f0), b = locate(t, f1);
    const out = [[a.x, a.y]];
    for (let i = 1; i < cum.length - 1; i++) if (cum[i] > d0 && cum[i] < d1) out.push([t.p[2 * i], t.p[2 * i + 1]]);
    out.push([b.x, b.y]);
    return out;
  }

  const trackLength = (t) => {
    const cum = cumulative(t);
    return cum[cum.length - 1];
  };

  // Alle Bloecke, die der Zug mit Spitze bei `pose` belegt (Spitze bis TRAIN_LEN_M zurueck, auch ueber Abschnitte hinweg)
  function occupiedBlocks(j, tracks, pose) {
    const out = [];
    let idx = j.s.indexOf(pose.seg), seg = pose.seg, f = pose.f, remaining = TRAIN_LEN_M;
    for (let guard = 0; guard < 6 && seg && seg.k != null; guard++) {
      const t = tracks[seg.k];
      if (!t) break;
      const bounds = boundsOf(t), len = Math.max(trackLength(t), 1), head = f * len;
      const first = blockIndex(bounds, Math.max(0, (head - remaining) / len)), last = blockIndex(bounds, f);
      for (let i = first; i <= last; i++) out.push(blockId(seg.k, bounds, i));
      remaining -= head;
      if (remaining <= 0 || idx <= 0) break;
      seg = j.s[--idx];
      f = 1;
    }
    return out;
  }

  // Entfernung (m) von der Spitze bis zum Signal vor dem ersten Block voraus, den ein anderer Zug belegt; sonst Infinity
  function distanceToOccupied(j, tracks, pose, occupancy, self) {
    const mine = self.blocks || [];
    let idx = j.s.indexOf(pose.seg), seg = pose.seg, f = pose.f, dist = 0;
    for (let guard = 0; guard < 12 && seg && seg.k != null; guard++) {
      const t = tracks[seg.k];
      if (!t) return Infinity;
      const bounds = boundsOf(t), len = Math.max(trackLength(t), 1);
      for (let m = 0; m < bounds.length; m++) {
        if (bounds[m] <= f) continue;
        const d = dist + (bounds[m] - f) * len;
        if (d > LOOKAHEAD_M) return Infinity;
        const id = blockId(seg.k, bounds, m + 1), users = occupancy.get(id);
        if (users && !mine.includes(id) && users.some((u) => u !== self)) return d;
      }
      dist += (1 - f) * len;
      if (dist > LOOKAHEAD_M || idx < 0) return Infinity;
      seg = j.s[++idx];
      f = 0;
    }
    return Infinity;
  }

  // ---- Stapeln ueberdeckter Zuege ----
  //
  // Zuege, die auf dem Bildschirm naeher als `thresholdM` beieinander liegen (zusammen an einer Station, auf Parallelgleisen),
  // werden senkrecht uebereinander gestapelt. Haelt mindestens ein Zug an einer Station (`nearAnchor`), sitzt der Stapel ueber dem
  // Stationspin, damit der Pin frei und anklickbar bleibt. Gruppen ueber `maxGroup` Zuege werden nicht gestapelt (weit
  // herausgezoomt liegt eine ganze Stadt uebereinander). Ergebnis: Versatz je Zug in Pixeln (negativ = nach oben).
  function stackTargets(items, { thresholdM, stepPx, baseUpPx, nearAnchor, maxGroup = 8 }) {
    const out = new Map();
    const parent = items.map((_, i) => i);
    const find = (i) => {
      while (parent[i] !== i) i = parent[i] = parent[parent[i]];
      return i;
    };
    const grid = new Map(), cell = Math.max(thresholdM, 1);
    items.forEach((it, i) => {
      const cx = Math.floor(it.mx / cell), cy = Math.floor(it.my / cell);
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          for (const j of grid.get(`${cx + dx},${cy + dy}`) || []) {
            if (Math.hypot(it.mx - items[j].mx, it.my - items[j].my) < thresholdM) parent[find(i)] = find(j);
          }
        }
      }
      const key = `${cx},${cy}`;
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(i);
    });
    const groups = new Map();
    items.forEach((it, i) => {
      const r = find(i);
      if (!groups.has(r)) groups.set(r, []);
      groups.get(r).push(it);
    });
    for (const group of groups.values()) {
      const anchored = nearAnchor && group.some((it) => it.dwell && nearAnchor(it.mx, it.my));
      if ((group.length === 1 && !anchored) || group.length > maxGroup) {
        for (const it of group) out.set(it.id, 0);
        continue;
      }
      group.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      group.forEach((it, i) => out.set(it.id, anchored ? -(baseUpPx + i * stepPx) : (i - (group.length - 1) / 2) * stepPx));
    }
    return out;
  }

  // ---- Animator ----

  class Animator {
    constructor(getTracks) {
      this.getTracks = getTracks;
      this.entries = new Map();
      this.lastNow = null;
      this.blocking = true;
    }

    // Neue Fahrplandaten uebernehmen. Bekannte Fahrten behalten ihre Position (nur die Uhr wird neu gesetzt).
    ingest(journeys, now) {
      const next = new Map();
      for (const j of journeys) {
        const old = this.entries.get(j.id);
        if (old && old.pose) carryOver(old.pose, j);
        prepare(j);
        let tau = now;
        if (old && old.pose) {
          tau = anchor(old.pose, j, now);
          if (Math.abs(now - tau) > SNAP_S) tau = now;
        }
        next.set(j.id, { id: j.id, j, tau, seenAt: now, pose: old ? old.pose : null, stackPx: old ? old.stackPx : undefined, blocks: old ? old.blocks : null, blockedSince: old ? old.blockedSince : null, braked: old ? old.braked : false, speed: old ? old.speed : 0, rot: old ? old.rot : null, dCur: old ? old.dCur : null, trend: old ? old.trend : 0 });
      }
      for (const [id, e] of this.entries) {
        if (!next.has(id) && e.pose && now - e.seenAt < KEEP_S) next.set(id, e);
      }
      this.entries = next;
    }

    // Uhren weiterdrehen und Posen berechnen; liefert die Eintraege mit gueltiger Pose. Vor einem besetzten Block bremst ein Zug
    // (die Uhr laeuft langsamer), haelt vor dem Signal und faehrt wieder an, sobald der Block frei ist.
    step(now) {
      const dt = this.lastNow === null ? 0 : clamp(now - this.lastNow, 0, 1);
      this.lastNow = now;
      const tracks = this.getTracks(), out = [], occupancy = new Map();
      if (this.blocking) {
        for (const e of this.entries.values()) for (const id of e.blocks || []) (occupancy.get(id) || occupancy.set(id, []).get(id)).push(e);
      }
      for (const e of this.entries.values()) {
        const lag = now - e.tau;
        if (Math.abs(lag) > SNAP_S && !e.blockedSince) e.tau = now;
        const catchUp = clamp(1 + (now - e.tau) / TC, RATE_MIN, RATE_MAX);
        const guarded = this.blocking && BLOCK_MODES.has(e.j.m) && e.pose && e.pose.seg.k != null;
        let brake = 1;
        if (guarded) {
          const gap = distanceToOccupied(e.j, tracks, e.pose, occupancy, e);
          if (gap < Infinity || e.braked) {
            const v0 = Math.max(0.5, this.speedAt(e, tracks) * catchUp);
            if (gap < Infinity) {
              const room = Math.max(0, gap - STOP_MARGIN_M);
              brake = room <= 0.05 ? 0 : clamp(Math.sqrt(2 * A_BRAKE * room) / v0, 0, 1);
            }
            if (e.braked) brake = Math.min(brake, ((e.speed || 0) + A_ACC * dt) / v0);
          }
          if (brake < 0.02) {
            if (e.blockedSince === null) e.blockedSince = now;
            if (now - e.blockedSince > BLOCK_TIMEOUT_S) brake = 1;
          } else {
            e.blockedSince = null;
          }
        } else {
          e.blockedSince = null;
        }
        e.braked = brake < 0.999;
        e.tau += dt * catchUp * brake;
        const before = e.pose;
        e.pose = poseAt(e.j, tracks, e.tau);
        if (!e.pose) {
          e.blocks = null;
          e.speed = 0;
          continue;
        }
        if (before && dt > 1e-3) e.speed = Math.hypot((e.pose.x - before.x) * KX, (e.pose.y - before.y) * KY) / dt;
        e.blocks = guarded || (this.blocking && BLOCK_MODES.has(e.j.m) && e.pose.seg.k != null) ? occupiedBlocks(e.j, tracks, e.pose) : null;
        e.rot = e.rot === null ? e.pose.rot : turn(e.rot, e.pose.rot, 1 - Math.exp(-TURN_RATE * dt));
        trackTrend(e);
        out.push(e);
      }
      return out;
    }

    // Fahrgeschwindigkeit (m/s) nach dem Fahrplan an der aktuellen Uhrzeit des Zuges
    speedAt(e, tracks) {
      const a = poseAt(e.j, tracks, e.tau), b = poseAt(e.j, tracks, e.tau + 1);
      return a && b ? Math.hypot((b.x - a.x) * KX, (b.y - a.y) * KY) : 0;
    }
  }

  return { DWELL_S, KX, KY, Animator, blockOf, blockId, boundsOf, slice, occupiedBlocks, stackTargets, ease, easeInv, locate, poseAt, nextStop, prepare, anchor, carryOver };
});
