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

  function reliability(rel, minutes) {
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

  function transferRow(tr) {
    const t = minTransfer();
    const catchPct = tr.catchByMin ? tr.catchByMin[String(t)] : undefined;
    const row = node("div", "leg xfer");
    row.append(node("span", "t", hhmm(tr.arr)));
    const mid = node("span", "mid");
    const wait = tr.waitMin <= 0 ? "keine Wartezeit" : `${tr.waitMin} min Zeit`;
    const platforms = tr.arrPlatform && tr.depPlatform && tr.arrPlatform !== tr.depPlatform ? ` · ${tr.arrPlatform} → ${tr.depPlatform}` : tr.depPlatform ? ` · ${tr.depPlatform}` : "";
    mid.append(node("span", "d1 xt", `Umstieg ${tr.station}: ${wait}${tr.walkMin ? ` (davon ${tr.walkMin} min Fußweg)` : ""}${platforms}`));
    if (catchPct !== undefined) {
      const hint = node("span", "d2 " + (catchPct >= 90 ? "xok" : catchPct >= 60 ? "xmid" : "xbad"), `Anschluss wird zu ca. ${pct(catchPct)} erreicht`);
      const reserve = tr.waitMin - Math.max(tr.walkMin || 0, t);
      hint.title = reserve > 0
        ? `Reserve: ${reserve} min (Zeit bis zur Abfahrt abzüglich ${t > (tr.walkMin || 0) ? "gewählter Mindest-Umsteigezeit" : "Fußweg"}). Ein Zubringer, der ${reserve} min oder mehr zu spät kommt, gilt als verpasst. Aus den gemessenen Verspätungen von Zubringer und Anschlusszug.`
        : "Keine Reserve: schon ein pünktlicher Zubringer reicht nicht für die gewählte Umsteigezeit.";
      mid.append(hint);
    }
    row.append(mid);
    return row;
  }

  function legRow(leg) {
    const row = node("div", leg.line ? "leg" : "leg walk");
    if (!leg.line) {
      row.append(node("span", "t", hhmm(leg.dep)), node("span", "walkt", `Fußweg ${leg.walkMin} min`));
      return row;
    }
    row.append(node("span", "t", hhmm(leg.dep)), badge(leg.line, leg.mode));
    const mid = node("span", "mid");
    mid.append(node("span", "d1", `${leg.from.name} → ${leg.to.name}`));
    const sub = [`Richtung ${leg.direction}`];
    if (leg.from.platform) sub.push(`ab ${leg.from.platform}`);
    mid.append(node("span", "d2", sub.join(" · ")));
    const arrive = [`an ${hhmm(leg.arr)}`];
    if (leg.to.platform) arrive.push(leg.to.platform);
    mid.append(node("span", "d2 arr", arrive.join(" · ")));
    if (leg.stats && leg.stats.alight) {
      const a = leg.stats.alight;
      const s = node("span", "d2 stat", `${leg.line}: pünktlich ${pct(a.ontimePct)} · Ø ${String(a.avgMin).replace(".", ",")} min · n=${a.n} (${a.scope})`);
      s.title = `Zeitraum: ${a.window}. Verspätung am Ausstiegshalt, gemessen von dieser Karte.`;
      mid.append(s);
    }
    row.append(mid);
    const tag = leg.cancelled ? node("span", "dl late2", "Ausfall") : delayTag(leg.depDelayMin);
    if (tag) row.append(tag);
    return row;
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
      const card = node("div", "rcard");
      const head = node("div", "rhead");
      const late = r.realArr && r.plannedArr ? Math.round((r.realArr - r.plannedArr) / 60) : 0;
      head.append(node("b", "", `${hhmm(r.plannedDep)} → ${hhmm(r.plannedArr)}`));
      head.append(node("span", "rmin", `${r.minutes} min`));
      const rides = r.legs.filter((l) => l.line).length;
      head.append(node("span", "rmin", rides > 1 ? `${rides - 1} Umstieg${rides > 2 ? "e" : ""}` : "direkt"));
      const tag = delayTag(late);
      if (tag && late) {
        tag.title = "Voraussichtliche Verspätung bei Ankunft";
        head.append(tag);
      }
      card.append(head);
      for (const leg of r.legs) {
        if (!leg.line && leg.from.id === leg.to.id) continue; // Umstieg innerhalb der Station: kommt als Umstiegszeile
        if (leg.transfer) card.append(transferRow(leg.transfer));
        card.append(legRow(leg));
      }
      card.append(reliability(r.reliability, r.minutes));
      out.append(card);
    }
    out.append(node("div", "foot", "Wahrscheinlichkeiten aus den Verspätungen, die diese Karte seit Ende Oktober gemessen hat (Zeitraum und Stichprobe je Linie siehe Tooltip)."));
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
