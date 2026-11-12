"use strict";
// Abfahrtstafel einer Station (Klick auf eine Haltestelle) und Routenplaner mit Puenktlichkeit/Ausfallrisiko.
// Nutzt von app.js ueber window.hvvMap (Karte) und window.hvvColorOf (Linienfarbe), jeweils erst zur Laufzeit.
(function (root) {
  const $ = (id) => document.getElementById(id);
  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const node = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const getJson = async (url) => {
    const r = await fetch(url, { cache: "no-store" });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Fehler ${r.status}`);
    return data;
  };
  const MODE_NAME = { S: "S-Bahn", U: "U-Bahn", R: "Regionalzug", X: "XpressBus", B: "Bus", F: "Fähre", Z: "Zug" };

  // Linienschild: offizielles Icon, sonst farbiges Textschild
  function badge(line, mode) {
    const wrap = node("span", "lb");
    const img = new Image();
    img.alt = line;
    img.onerror = () => {
      wrap.replaceChildren();
      const t = node("span", "lb-t", line);
      t.style.background = mode === "B" ? "#6b7385" : root.hvvColorOf ? root.hvvColorOf(line) : "#6b7385";
      if (/^R[EB]/.test(line)) t.style.color = "#1b2636";
      wrap.append(t);
    };
    img.src = `/icon/${encodeURIComponent(line)}.svg`;
    if (/^R[EB]/.test(line)) img.className = "on-white";
    wrap.append(img);
    return wrap;
  }

  function delayTag(min) {
    if (min === null || min === undefined) return null;
    const t = node("span", "dl " + (min >= 5 ? "late2" : min >= 1 ? "late1" : "ok"), min > 0 ? `+${min}` : "±0");
    t.title = "Verspätung in Minuten";
    return t;
  }

  // ---- Abfahrtstafel ----

  let depPopup = null, depTimer = null;

  function renderDepartures(box, data) {
    box.replaceChildren();
    if (!data.departures.length) {
      box.append(node("div", "empty", "Keine Abfahrten in der nächsten Stunde."));
      return;
    }
    for (const d of data.departures) {
      const row = node("div", "dep" + (d.cancelled ? " cancelled" : ""));
      const time = node("span", "t", hhmm(d.planned));
      const dir = node("span", "dir");
      dir.append(node("span", "d1", d.direction || "–"));
      if (d.platform) dir.append(node("span", "d2", d.platform));
      row.append(time, badge(d.line, d.mode), dir);
      if (d.cancelled) row.append(node("span", "dl late2", "Ausfall"));
      else {
        const tag = delayTag(d.delayMin);
        if (tag) row.append(tag);
      }
      box.append(row);
    }
  }

  function showDepartures(lngLat, station) {
    if (depPopup) depPopup.remove();
    clearInterval(depTimer);
    const content = node("div", "popup deps");
    const head = node("div", "dhead");
    head.append(node("b", "", station.n));
    const buttons = node("div", "dbtn");
    const from = node("button", "mini", "Route von hier"), to = node("button", "mini", "Route hierher");
    from.onclick = () => openRoute({ from: { id: station.k, label: station.n } });
    to.onclick = () => openRoute({ to: { id: station.k, label: station.n } });
    buttons.append(from, to);
    const list = node("div", "dlist");
    list.append(node("div", "empty", "Abfahrten werden geladen …"));
    content.append(head, buttons, list);
    depPopup = new maplibregl.Popup({ closeButton: true, maxWidth: "380px", className: "dep-popup" }).setLngLat(lngLat).setDOMContent(content).addTo(root.hvvMap);
    const load = async () => {
      try {
        const data = await getJson(`/api/departures?id=${encodeURIComponent(station.k)}&n=20`);
        renderDepartures(list, data);
      } catch (e) {
        list.replaceChildren(node("div", "empty err", e.message));
      }
    };
    load();
    depTimer = setInterval(load, 30000);
    depPopup.on("close", () => clearInterval(depTimer));
  }

  // ---- Routenplaner ----

  const ends = { from: null, to: null };
  const labels = { from: new Map(), to: new Map() };

  function nowLocalInput() {
    const d = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
    return d.toISOString().slice(0, 16);
  }

  function setEnd(which, value) {
    ends[which] = value;
    const input = $(`pl-${which}`);
    input.value = value ? value.label : "";
  }

  function openRoute(preset) {
    $("route").hidden = false;
    if (preset.from) setEnd("from", preset.from);
    if (preset.to) setEnd("to", preset.to);
    if (!$("pl-when").value) $("pl-when").value = nowLocalInput();
    if (ends.from && ends.to) search();
    else $(ends.from ? "pl-to" : "pl-from").focus();
  }

  function wireSuggest(which) {
    const input = $(`pl-${which}`), list = $(`pl-${which}-list`);
    let timer = null;
    input.addEventListener("input", () => {
      ends[which] = null;
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) return;
      timer = setTimeout(async () => {
        try {
          const data = await getJson(`/api/stations?q=${encodeURIComponent(q)}`);
          labels[which].clear();
          list.replaceChildren();
          for (const s of data.stations) {
            labels[which].set(s.label, { id: s.id, label: s.label });
            const o = document.createElement("option");
            o.value = s.label;
            list.append(o);
          }
        } catch (e) { /* keine Vorschlaege */ }
      }, 250);
    });
    const pick = () => {
      const v = labels[which].get(input.value.trim());
      if (v) ends[which] = v;
    };
    input.addEventListener("change", pick);
    input.addEventListener("blur", pick);
  }

  async function resolveEnd(which) {
    if (ends[which]) return ends[which];
    const q = $(`pl-${which}`).value.trim();
    if (q.length < 2) return null;
    const data = await getJson(`/api/stations?q=${encodeURIComponent(q)}`);
    const first = data.stations[0];
    if (!first) return null;
    setEnd(which, { id: first.id, label: first.label });
    return ends[which];
  }

  const pct = (v) => `${String(v).replace(".", ",")} %`;
  const tone = (v) => (v >= 90 ? "good" : v >= 75 ? "mid" : "bad");
  const num = (v) => String(v).replace(".", ",");

  function meter(label, value, hint, invert) {
    const row = node("div", "meter");
    const head = node("div", "mh");
    head.append(node("span", "", label), node("b", "", pct(value)));
    const bar = node("div", "bar"), fill = node("span", invert ? tone(100 - value * 5) : tone(value));
    fill.style.width = `${Math.max(2, Math.min(100, value))}%`;
    bar.append(fill);
    row.append(head, bar);
    if (hint) row.append(node("div", "hint", hint));
    return row;
  }

  let lastData = null;
  const minTransfer = () => Number($("pl-min").value) || 0;
  const minLabel = (t) => (t === 0 ? "nur Fahrplan" : `mind. ${t} min`);

  function reliability(rel) {
    const box = node("div", "rel");
    const by = (rel.byMinTransfer || {})[String(minTransfer())] || rel; // Werte zur gewählten Mindest-Umsteigezeit
    if (by.ontimePct !== undefined) {
      box.append(meter("Pünktlich am Ziel (≤ 3 min)", by.ontimePct, "Wahrscheinlichkeit, dass die Fahrt höchstens 3 Minuten verspätet ankommt, einschließlich verpasster Anschlüsse."));
      if (rel.transfers) box.append(meter(`Anschluss${rel.transfers > 1 ? "züge" : ""} erreicht`, by.connectionPct, `${rel.transfers} Umstieg${rel.transfers > 1 ? "e" : ""}, Umsteigezeit: ${minLabel(minTransfer())}.`));
    } else {
      box.append(node("div", "hint", rel.unavailable ? "Messdaten derzeit nicht erreichbar – Verbindung ohne Zuverlässigkeitsangabe." : rel.legs ? `Für ${rel.legsWithData} von ${rel.legs} Abschnitten liegen Messdaten vor – keine Gesamtprognose möglich.` : "Keine Fahrt mit Linie."));
    }
    if (rel.cancelPct !== undefined) box.append(meter("Ausfallrisiko", rel.cancelPct, `Gemessen für ${rel.cancelCoverage}; für die übrigen Linien erfasst die Karte keine Ausfälle.`, true));
    else if (!rel.unavailable) box.append(node("div", "hint", "Ausfallrisiko: keine Messdaten für diese Linien."));
    return box;
  }

  // ---- Zeitleiste im Stil der HVV-App ----

  const SVG = {
    walk: '<svg viewBox="0 0 24 24" width="20" height="20"><circle cx="13" cy="4.5" r="2.2" fill="currentColor"/><path d="M10.5 22l1.6-7.2-2.4-2.3v-4.2l-1.9 2.3-1.9-1.2 3.4-4.4c.6-.7 1.3-1 2.2-.9 1.5.1 2.3 1 3 2.6.7 1.6 1.9 2.6 3.6 2.9l-.4 2c-2.2-.2-3.8-1.1-4.9-2.7V13l2.6 2.4 1 6.6-2 .3-.9-4.7-1.1 4.6z" fill="currentColor"/></svg>',
    swap: '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M7 7h12M7 7l3-3M7 7l3 3M17 17H5m12 0l-3-3m3 3l-3 3" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M5 9l7 7 7-7" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    live: '<svg viewBox="0 0 16 16" width="11" height="11"><path d="M3 11a7 7 0 0 1 7-7M3 7.5A4 4 0 0 1 6.5 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" transform="translate(2 1)"/></svg>',
  };
  const icon = (name, cls) => {
    const n = node("span", cls || "rt-ico");
    n.innerHTML = SVG[name]; // nur feste Zeichenketten aus SVG, keine Daten
    return n;
  };

  const delayClass = (min) => (min >= 5 ? "rt-late2" : min >= 1 ? "rt-late1" : "rt-ok");
  const signed = (min) => (min > 0 ? `+${min}` : min < 0 ? `−${-min}` : "±0");

  function lineColor(leg) {
    if (leg.mode === "B") return "#d52b1e";
    return root.hvvColorOf ? root.hvvColorOf(leg.line) : "#5b6785";
  }

  // Symbol auf der Linie: Bus = roter Rhombus, S/U = Kreis, sonst abgerundetes Schild
  function railIcon(leg) {
    const mode = leg.mode;
    const box = node("span", "rt-mode m-" + mode);
    box.style.setProperty("--m", mode === "S" ? "#3a9b3a" : mode === "U" ? "#0b5cab" : lineColor(leg));
    box.append(node("span", "", mode === "B" ? "BUS" : mode === "X" ? "X" : mode === "R" ? "R" : mode === "F" ? "F" : mode));
    return box;
  }

  function timeCell(ts, delayMin, cancelled) {
    const cell = node("div", "rt-time");
    if (ts === null || ts === undefined) return cell;
    const known = delayMin !== null && delayMin !== undefined;
    const big = node("span", "rt-t " + (cancelled ? "rt-late2 gone" : known ? delayClass(delayMin) : ""), hhmm(ts + (known && !cancelled ? delayMin * 60 : 0)));
    if (known && !cancelled) big.append(icon("live", "rt-live"));
    cell.append(big);
    if (known && delayMin !== 0 && !cancelled) {
      const small = node("span", "rt-plan");
      small.append(`Plan ${hhmm(ts)} `, node("b", delayClass(delayMin), `${signed(delayMin)} min`));
      cell.append(small);
    }
    return cell;
  }

  function stopRow(kind, ts, delayMin, name, platform, cancelled) {
    const row = node("div", "tl " + kind);
    const rail = node("div", "rail");
    const body = node("div", "body");
    const head = node("div", "sname");
    head.append(node("b", "", name || "–"));
    if (platform) head.append(node("span", "plat", platform));
    body.append(head);
    row.append(timeCell(ts, delayMin, cancelled), rail, body);
    return row;
  }

  function rideCard(leg) {
    const card = node("div", "rt-card");
    card.style.setProperty("--c", lineColor(leg));
    card.append(stopRow("dep", leg.dep, leg.depDelayMin, leg.from.name, leg.from.platform, leg.cancelled));

    const mid = node("div", "tl mid");
    const rail = node("div", "rail");
    rail.append(railIcon(leg));
    const body = node("div", "body");
    const pill = node("div", "rt-pill" + (leg.cancelled ? " gone" : ""));
    pill.append(badge(leg.line, leg.mode), node("span", "", `Richtung ${leg.direction || "–"}`));
    body.append(pill);
    if (leg.cancelled) body.append(node("div", "rt-warn", "Diese Fahrt fällt aus."));

    const facts = [];
    if (leg.stopCount) facts.push(`Fahre ${leg.stopCount} Haltestelle${leg.stopCount === 1 ? "" : "n"}`);
    const ride = leg.durationMin !== null && leg.durationMin !== undefined ? ` (${leg.durationMin} min)` : "";
    const details = node("div", "rt-details");
    details.hidden = true;
    const hasDetails = (leg.stops && leg.stops.length) || (leg.notices && leg.notices.length) || leg.vehicle || leg.carrier;
    const toggle = node(hasDetails ? "button" : "div", "rt-ride");
    toggle.append(node("span", "", `${facts[0] || "Fahrt"}${ride}`));
    if (hasDetails) {
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      toggle.append(icon("chevron", "rt-chev"));
      toggle.onclick = () => {
        details.hidden = !details.hidden;
        toggle.setAttribute("aria-expanded", String(!details.hidden));
        toggle.classList.toggle("open", !details.hidden);
      };
    }
    body.append(toggle);

    if (leg.stats && (leg.stats.alight || leg.stats.cancelRate !== null && leg.stats.cancelRate !== undefined)) {
      const st = leg.stats, bits = [];
      if (st.alight) bits.push(`${leg.line} pünktlich ${pct(st.alight.ontimePct)} · Ø ${num(st.alight.avgMin)} min · n=${st.alight.n}`);
      if (st.cancelRate !== null && st.cancelRate !== undefined) bits.push(`Ausfälle ${pct(num(st.cancelRate))}`);
      const line = node("div", "rt-stat", bits.join(" · "));
      if (st.alight) line.title = `Zeitraum: ${st.alight.window}, Bezug: ${st.alight.scope}. Verspätung am Ausstiegshalt, gemessen von dieser Karte.`;
      body.append(line);
    }

    const info = [...new Set([leg.vehicle, leg.carrier].filter(Boolean))].join(" · ");
    if (info) details.append(node("div", "rt-info", info));
    if (leg.origin) details.append(node("div", "rt-info", `Fahrt ab ${leg.origin}`));
    for (const stop of leg.stops || []) {
      const row = node("div", "rt-stop");
      row.append(node("span", "tm " + (stop.delayMin === null || stop.delayMin === undefined ? "" : delayClass(stop.delayMin)), stop.time ? hhmm(stop.time + (stop.delayMin || 0) * 60) : ""), node("span", "nm", stop.name));
      if (stop.delayMin) row.append(node("span", "dm " + delayClass(stop.delayMin), signed(stop.delayMin)));
      details.append(row);
    }
    for (const text of leg.notices || []) details.append(node("div", "rt-note", `ⓘ ${text}`));
    body.append(details);
    mid.append(node("div", "t"), rail, body);
    card.append(mid);

    card.append(stopRow("arr", leg.arr, leg.arrDelayMin, leg.to.name, leg.to.platform, leg.cancelled));
    return card;
  }

  // Umstieg im Stil der HVV-App ("Steige um (3 min zu Fuß, 1 min warten)") mit Anschluss-Wahrscheinlichkeit und aktueller Reserve
  function transferRow(tr, feeder, next) {
    const t = minTransfer();
    const walk = tr.walkMin || 0;
    const row = node("div", "tl xfer");
    const body = node("div", "body");
    const parts = [];
    if (walk) parts.push(`${walk} min zu Fuß`);
    if (tr.waitMin - walk > 0) parts.push(`${tr.waitMin - walk} min warten`);
    if (!parts.length) parts.push("keine Wartezeit");
    const text = node("div", "xtext");
    text.append(icon("swap", "rt-ico"), node("span", "", `Steige um (${parts.join(", ")})`));
    body.append(text);

    const platforms = [tr.toStation && tr.toStation !== tr.station ? `${tr.station} → ${tr.toStation}` : tr.station];
    if (tr.arrPlatform && tr.depPlatform) platforms.push(tr.arrPlatform === tr.depPlatform ? tr.arrPlatform : `${tr.arrPlatform} → ${tr.depPlatform}`);
    body.append(node("div", "xsub", platforms.join(" · ")));

    const catchPct = tr.catchByMin ? tr.catchByMin[String(t)] : undefined;
    const chips = node("div", "xchips");
    if (catchPct !== undefined) {
      const chip = node("span", "chip " + (catchPct >= 90 ? "good" : catchPct >= 60 ? "mid" : "bad"), `Anschluss ca. ${pct(num(catchPct))}`);
      const reserve = tr.waitMin - Math.max(walk, t);
      chip.title = reserve > 0
        ? `Reserve laut Fahrplan: ${reserve} min (Zeit bis zur Abfahrt abzüglich ${t > walk ? "gewählter Mindest-Umsteigezeit" : "Fußweg"}). Ein Zubringer, der ${reserve} min oder mehr zu spät kommt, gilt als verpasst. Aus den gemessenen Verspätungen beider Linien.`
        : "Keine Reserve: schon ein pünktlicher Zubringer reicht nicht für die gewählte Umsteigezeit.";
      chips.append(chip);
    }
    // aktuelle Prognose: echte Verspätungen von Zubringer und Anschlusszug einrechnen
    if (feeder && next && feeder.arrDelayMin !== null && feeder.arrDelayMin !== undefined) {
      const live = tr.waitMin + (next.depDelayMin || 0) - feeder.arrDelayMin - Math.max(walk, t);
      const cls = live <= 0 ? "bad" : live <= 1 ? "mid" : "good";
      const label = live <= 0 ? "Nach aktueller Prognose verpasst" : `Aktuell ${live} min Reserve`;
      if (feeder.arrDelayMin || next.depDelayMin || live <= 0) chips.append(node("span", "chip " + cls, label));
    }
    if (chips.childElementCount) body.append(chips);
    row.append(node("div", "t"), node("div", "rail"), body);
    return row;
  }

  function walkRow(leg) {
    const row = node("div", "tl xfer");
    const body = node("div", "body");
    const text = node("div", "xtext");
    text.append(icon("walk", "rt-ico"), node("span", "", `Gehe ${leg.walkMin} min zu Fuß`));
    body.append(text);
    if (leg.from.name !== leg.to.name) body.append(node("div", "xsub", `${leg.from.name} → ${leg.to.name}`));
    row.append(node("div", "t"), node("div", "rail"), body);
    return row;
  }

  function routeHeader(r) {
    const head = node("div", "rt-head");
    const dep = r.realDep || r.plannedDep, arr = r.realArr || r.plannedArr;
    const late = r.realArr && r.plannedArr ? Math.round((r.realArr - r.plannedArr) / 60) : 0;
    const top = node("div", "rt-top");
    top.append(node("b", "rt-span", `${hhmm(dep)} → ${hhmm(arr)}`), node("span", "rt-dur", `${r.minutes} min`));
    head.append(top);
    const sub = node("div", "rt-sub");
    const rides = r.legs.filter((l) => l.line);
    const seq = node("span", "rt-seq");
    for (const l of rides) seq.append(badge(l.line, l.mode));
    sub.append(seq, node("span", "", rides.length > 1 ? `${rides.length - 1} Umstieg${rides.length > 2 ? "e" : ""}` : "direkt"));
    if (late) sub.append(node("span", "chip " + (late >= 5 ? "bad" : "mid"), `${signed(late)} min am Ziel`));
    const ticket = (r.tickets || [])[0];
    if (ticket) sub.append(node("span", "rt-fare", `${num(ticket.price.toFixed(2))} €${ticket.level ? ` · ${ticket.level}` : ""}`));
    head.append(sub);
    return head;
  }

  function renderRoutes(data) {
    lastData = data;
    const out = $("pl-result");
    out.replaceChildren();
    if (!data.routes.length) {
      out.append(node("div", "empty", "Keine Verbindung gefunden."));
      return;
    }
    for (const r of data.routes) {
      const card = node("section", "rt");
      card.append(routeHeader(r));
      const line = node("div", "rt-line");
      let prev = null;
      r.legs.forEach((leg, i) => {
        if (!leg.line) {
          // Fußwege zwischen zwei Fahrten gehören zur Umstiegszeile; nur Wege vor der ersten und nach der letzten Fahrt stehen allein
          const between = prev !== null && r.legs.slice(i + 1).some((l) => l.line);
          if (!between) line.append(walkRow(leg));
          return;
        }
        if (leg.transfer) line.append(transferRow(leg.transfer, prev, leg));
        line.append(rideCard(leg));
        prev = leg;
      });
      card.append(line, reliability(r.reliability));
      out.append(card);
    }
    out.append(node("div", "foot", "Wahrscheinlichkeiten aus den Verspätungen, die diese Karte seit Ende Oktober gemessen hat (Zeitraum und Stichprobe je Linie siehe Tooltip). Zeiten mit Funksymbol sind Echtzeitprognosen."));
  }

  async function search() {
    const out = $("pl-result"), btn = $("pl-go");
    btn.disabled = true;
    out.replaceChildren(node("div", "empty", "Suche läuft …"));
    try {
      const [from, to] = [await resolveEnd("from"), await resolveEnd("to")];
      if (!from || !to) throw new Error("Bitte Start und Ziel angeben.");
      const params = new URLSearchParams({ from: from.id, to: to.id, arrive: $("pl-mode").value });
      if ($("pl-when").value) params.set("when", $("pl-when").value);
      renderRoutes(await getJson(`/api/route?${params}`));
    } catch (e) {
      out.replaceChildren(node("div", "empty err", e.message));
    } finally {
      btn.disabled = false;
    }
  }

  function init() {
    wireSuggest("from");
    wireSuggest("to");
    $("pl-when").value = nowLocalInput();
    $("pl-go").onclick = search;
    try {
      const saved = localStorage.getItem("hvv.minTransfer");
      if (saved !== null) $("pl-min").value = saved;
    } catch (e) { /* ohne Speicher gilt der Standard */ }
    const showMin = () => ($("pl-min-val").textContent = minLabel(minTransfer()));
    showMin();
    $("pl-min").addEventListener("input", () => {
      showMin();
      try { localStorage.setItem("hvv.minTransfer", String(minTransfer())); } catch (e) { /* egal */ }
      if (lastData) renderRoutes(lastData); // Anschlüsse sofort neu bewerten, kein neuer Abruf nötig
    });
    $("pl-swap").onclick = () => {
      const a = ends.from, b = ends.to;
      setEnd("from", b);
      setEnd("to", a);
    };
    $("pl-close").onclick = () => ($("route").hidden = true);
    $("open-route").onclick = () => ($("route").hidden = !$("route").hidden);
    for (const id of ["pl-from", "pl-to", "pl-when"]) $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  }

  root.HvvPlanner = { init, showDepartures, openRoute };
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", init);
})(typeof self !== "undefined" ? self : this);
