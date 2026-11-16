"use strict";
// Routenplaner fuers Handy: Start/Ziel als Haltestelle, Adresse, Sehenswuerdigkeit oder aktueller Standort, Verbindungen mit
// Echtzeit und gemessener Zuverlaessigkeit. Laeuft ohne Karte und ohne Bibliotheken.
(function (root) {
  const $ = (id) => document.getElementById(id);
  const node = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const num = (v) => String(v).replace(".", ",");
  const pct = (v) => `${num(v)} %`;
  const tone = (v) => (v >= 90 ? "good" : v >= 75 ? "mid" : "bad");

  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* ohne Speicher geht alles weiter */ } },
  };

  async function getJson(url, signal) {
    let response;
    try {
      response = await fetch(url, { cache: "no-store", signal });
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      throw new Error("Keine Verbindung zum Server. Bitte Netz prüfen und erneut versuchen.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Fehler ${response.status}`);
    return data;
  }

  // ---- Symbole (feste Zeichenketten, keine Daten) ----

  const svg = (inner, fill) => `<svg viewBox="0 0 24 24" fill="${fill || "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${inner}</svg>`;
  const ICONS = {
    locate: svg('<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7.5"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5"/>'),
    clear: svg('<circle cx="12" cy="12" r="9.5" fill="currentColor" stroke="none" opacity=".16"/><path d="M8.6 8.6l6.8 6.8M15.4 8.6l-6.8 6.8"/>'),
    swap: svg('<path d="M8 20V5M8 5L4.5 8.5M8 5l3.5 3.5M16 4v15M16 19l-3.5-3.5M16 19l3.5-3.5"/>'),
    station: svg('<rect x="5.5" y="3.5" width="13" height="13" rx="3.5"/><path d="M5.5 11h13M8.5 20.5L10 16.5M15.5 20.5L14 16.5"/><circle cx="9" cy="13.6" r=".6" fill="currentColor"/><circle cx="15" cy="13.6" r=".6" fill="currentColor"/>'),
    address: svg('<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>'),
    poi: svg('<path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>'),
    recent: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
    chevron: svg('<path d="M5.5 9l6.5 6.5L18.5 9"/>'),
    walk: svg('<circle cx="13" cy="4.6" r="1.8" fill="currentColor"/><path d="M9.5 21.5l1.7-6.3-2.3-2.2v-4l-1.8 2.2M13 8.5l1.7 3.2 3 .8M11.2 15.2l3.5 1.6.9 4.7M8.9 9.1l3.6-1.4c.4-.1.9.1 1 .5"/>'),
    swapStop: svg('<path d="M7 7h12M7 7l3-3M7 7l3 3M17 17H5m12 0l-3-3m3 3l-3 3"/>'),
    live: '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M3 11a7 7 0 0 1 7-7M3 7.5A4 4 0 0 1 6.5 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" transform="translate(2 1)"/></svg>',
  };
  const icon = (name, cls) => {
    const n = node("span", cls || "");
    n.innerHTML = ICONS[name];
    return n;
  };
  const KIND = { STATION: ["station", "Haltestelle"], ADDRESS: ["address", "Adresse"], POI: ["poi", "Ort"] };

  // ---- Linien: Farbe und Schild ----

  const COLORS = {};
  const FALLBACK = { U: "#1a6fb5", R: "#5b6785", X: "#a61e8c", S: "#2ca147" };
  function lineColor(leg) {
    const name = leg.line || "";
    const found = COLORS[name] || COLORS[name.replace(/^([SU]\d)\d$/, "$1")];
    if (found && !/^#f{3,6}$/i.test(found)) return found;
    if (leg.mode === "B") return "#d52b1e";
    return FALLBACK[name[0]] || "#5b6785";
  }

  function badge(line, mode) {
    const wrap = node("span", "lb");
    const img = new Image();
    img.alt = line;
    img.onerror = () => {
      wrap.replaceChildren();
      const t = node("span", "lb-t", line);
      const color = lineColor({ line, mode });
      t.style.background = color;
      if (/^R[EB]/.test(line)) t.classList.add("light");
      wrap.append(t);
    };
    img.src = `/icon/${encodeURIComponent(line)}.svg`;
    if (/^R[EB]/.test(line)) img.className = "on-white";
    wrap.append(img);
    return wrap;
  }

  // ---- Orte (Start/Ziel) ----

  const MY_LOCATION = "Mein Standort";
  const MIN_QUERY = 3; // Vorschlaege erst ab 3 Zeichen (spart Abrufe beim GTI-Konto), die Suche selbst loest auch 2 Zeichen auf
  const RECENT_KEY = "hvv.recent";
  const placeKey = (p) => `${p.type}:${p.id || p.label}`;

  function recents() {
    try {
      const list = JSON.parse(store.get(RECENT_KEY) || "[]");
      return Array.isArray(list) ? list.filter((p) => p && p.type && p.label).slice(0, 6) : [];
    } catch (e) {
      return [];
    }
  }
  function remember(place) {
    if (place.type === "COORDINATE") return; // der Standort aendert sich
    const keep = recents().filter((p) => placeKey(p) !== placeKey(place));
    keep.unshift({ type: place.type, id: place.id, name: place.name, city: place.city, label: place.label, x: place.x, y: place.y });
    store.set(RECENT_KEY, JSON.stringify(keep.slice(0, 6)));
  }

  // Abfrageparameter: Haltestellen als nackte ID, alles andere als JSON (der Server prueft es)
  function placeParam(p) {
    if (p.type === "STATION") return p.id;
    return JSON.stringify({ type: p.type, id: p.id || undefined, name: p.name, city: p.city || undefined, x: p.x, y: p.y });
  }

  function locate() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error("Dieses Gerät kann den Standort nicht bestimmen."));
      if (root.isSecureContext === false) return reject(new Error("Der Standort ist nur über eine verschlüsselte Verbindung (https) verfügbar. Bitte Adresse eintippen."));
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ type: "COORDINATE", name: MY_LOCATION, label: MY_LOCATION, x: pos.coords.longitude, y: pos.coords.latitude }),
        (err) => reject(new Error(err.code === 1 ? "Der Standortzugriff ist blockiert. Bitte in den Browser-Einstellungen erlauben oder die Adresse eintippen." : err.code === 3 ? "Der Standort wurde nicht rechtzeitig gefunden. Bitte erneut versuchen." : "Der Standort ist gerade nicht verfügbar.")),
        { enableHighAccuracy: false, timeout: 12000, maximumAge: 60000 },
      );
    });
  }

  // Eingabefeld mit Vorschlagsliste (Combobox nach WAI-ARIA)
  function Field(which, hooks) {
    const el = document.querySelector(`.field[data-which="${which}"]`);
    const input = el.querySelector("input");
    const list = el.querySelector(".sugg");
    const clear = el.querySelector(".clear");
    const locateBtn = el.querySelector(".locate");
    let place = null, items = [], active = -1, seq = 0, timer = null, closeTimer = null, controller = null;

    const field = {
      get place() { return place; },
      get text() { return input.value.trim(); },
      input,
      set(p, text) {
        place = p || null;
        input.value = p ? p.label : text || "";
        sync();
      },
      focus() { input.focus(); },
    };

    function sync() {
      clear.hidden = !input.value;
    }
    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }
    function open() {
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
    function setActive(i) {
      const opts = list.querySelectorAll('[role="option"]');
      active = i;
      opts.forEach((opt, n) => opt.setAttribute("aria-selected", String(n === i)));
      if (i >= 0 && opts[i]) {
        input.setAttribute("aria-activedescendant", opts[i].id);
        opts[i].scrollIntoView({ block: "nearest" });
      } else input.removeAttribute("aria-activedescendant");
    }
    function heading(text) {
      const h = node("li", "head", text);
      h.setAttribute("role", "presentation");
      return h;
    }
    function state(text) {
      list.replaceChildren();
      const li = node("li", "state", text);
      li.setAttribute("role", "presentation");
      list.append(li);
      items = [];
      open();
    }
    function option(entry, n) {
      const li = node("li");
      li.id = `${which}-opt-${n}`;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      const ic = icon(entry.icon, "ic");
      const text = node("span", "tx");
      text.append(node("span", "nm", entry.title));
      if (entry.sub) text.append(node("span", "sub", entry.sub));
      li.append(ic, text);
      li.addEventListener("click", () => entry.run());
      return li;
    }
    function render(entries, title) {
      list.replaceChildren();
      if (title) list.append(heading(title));
      items = entries;
      entries.forEach((entry, n) => list.append(option(entry, n)));
      active = -1;
      if (entries.length) open();
      else close();
    }
    const entryFor = (p, recent) => {
      const [ic, kind] = KIND[p.type] || KIND.STATION;
      return { icon: recent ? "recent" : ic, title: p.name || p.label, sub: [p.city && p.type !== "STATION" ? p.city : "", kind].filter(Boolean).join(" · "), place: p, run: () => choose(p) };
    };

    function choose(p) {
      place = p;
      input.value = p.label;
      sync();
      remember(p);
      close();
      hooks.chosen(which);
    }

    async function useLocation() {
      locateBtn && locateBtn.classList.add("busy");
      try {
        const p = await locate();
        choose(p);
      } catch (e) {
        input.focus();
        state(e.message);
      } finally {
        locateBtn && locateBtn.classList.remove("busy");
      }
    }

    function defaults() {
      const entries = [];
      if (which === "from") entries.push({ icon: "locate", title: MY_LOCATION, sub: "per GPS bestimmen", run: useLocation });
      const last = recents();
      last.forEach((p) => entries.push(entryFor(p, true)));
      render(entries);
      if (last.length) list.insertBefore(heading("Zuletzt gesucht"), list.querySelectorAll('[role="option"]')[entries.length - last.length]);
    }

    async function query(text) {
      const mine = ++seq;
      if (controller) controller.abort();
      controller = new AbortController();
      if (!items.length || list.hidden) state("Suche …");
      try {
        const data = await getJson(`/api/places?q=${encodeURIComponent(text)}`, controller.signal);
        if (mine !== seq) return;
        if (!data.places.length) return state("Nichts gefunden. Bitte Schreibweise prüfen oder Straße mit Hausnummer und Ort eingeben.");
        render(data.places.map((p) => entryFor(p, false)));
      } catch (e) {
        if (mine !== seq || (e && e.name === "AbortError")) return;
        state(e.message);
      }
    }

    input.addEventListener("input", () => {
      place = null;
      sync();
      clearTimeout(timer);
      const text = input.value.trim();
      if (text.length < MIN_QUERY) {
        seq++;
        if (controller) controller.abort();
        return text.length ? close() : defaults();
      }
      timer = setTimeout(() => query(text), 260);
    });
    input.addEventListener("focus", () => {
      clearTimeout(closeTimer);
      if (place) return input.select();
      const text = input.value.trim();
      if (!text) defaults();
      else if (text.length >= MIN_QUERY) query(text);
    });
    input.addEventListener("blur", () => {
      clearTimeout(timer);
      closeTimer = setTimeout(close, 150);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (list.hidden || !items.length) return;
        e.preventDefault();
        setActive((active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length);
      } else if (e.key === "Escape") {
        close();
      } else if (e.key === "Enter") {
        if (!list.hidden && items.length && !place) {
          e.preventDefault();
          items[active >= 0 ? active : 0].run();
        } else if (place || !input.value.trim()) {
          // fertig: weiter zum naechsten Feld bzw. Suche
          e.preventDefault();
          hooks.chosen(which);
        }
      }
    });
    // Tippen auf einen Vorschlag darf das Eingabefeld nicht verlassen (sonst schliesst die Liste vor dem Klick)
    list.addEventListener("pointerdown", (e) => e.preventDefault());
    clear.addEventListener("click", () => {
      place = null;
      input.value = "";
      sync();
      input.focus();
      defaults();
    });
    if (locateBtn) locateBtn.addEventListener("click", useLocation);
    clear.innerHTML = ICONS.clear;
    if (locateBtn) locateBtn.innerHTML = ICONS.locate;
    sync();
    return field;
  }

  // Textfeld ohne gewaehlten Vorschlag: den ersten Treffer der Suche nehmen
  async function resolve(field, signal) {
    if (field.place) return field.place;
    const text = field.text;
    if (text.length < 2) return null;
    const data = await getJson(`/api/places?q=${encodeURIComponent(text)}`, signal);
    const first = data.places[0];
    if (first) {
      field.set(first);
      remember(first);
    }
    return first || null;
  }

  // ---- Ergebnisdarstellung ----

  const delayClass = (min) => (min >= 5 ? "late2" : min >= 1 ? "late1" : "ok");
  const signed = (min) => (min > 0 ? `+${min}` : min < 0 ? `−${-min}` : "±0");
  const known = (v) => v !== null && v !== undefined;

  function timeCell(ts, delayMin, cancelled) {
    const cell = node("div", "tm");
    if (!known(ts)) return cell;
    const live = known(delayMin);
    const big = node("span", "t " + (cancelled ? "late2 gone" : live ? delayClass(delayMin) : ""), hhmm(ts + (live && !cancelled ? delayMin * 60 : 0)));
    if (live && !cancelled) {
      const mark = node("span", "live");
      mark.innerHTML = ICONS.live;
      big.append(mark);
    }
    cell.append(big);
    if (live && delayMin !== 0 && !cancelled) cell.append(node("span", "plan", `Plan ${hhmm(ts)}`), node("b", "dl " + delayClass(delayMin), `${signed(delayMin)} min`));
    return cell;
  }

  function stopRow(kind, ts, delayMin, name, platform, cancelled) {
    const row = node("div", "tl " + kind);
    const body = node("div", "body");
    const head = node("div", "sname");
    head.append(node("b", "", name || "–"));
    if (platform) head.append(node("span", "plat", platform));
    body.append(head);
    row.append(timeCell(ts, delayMin, cancelled), node("div", "rail"), body);
    return row;
  }

  function rideCard(leg) {
    const card = node("div", "leg");
    card.style.setProperty("--c", lineColor(leg));
    card.append(stopRow("dep", leg.dep, leg.depDelayMin, leg.from.name, leg.from.platform, leg.cancelled));

    const mid = node("div", "tl mid");
    const rail = node("div", "rail");
    const logo = node("span", "rt-logo");
    logo.append(badge(leg.line, leg.mode));
    rail.append(logo);
    const body = node("div", "body");
    const dir = node("div", "dir" + (leg.cancelled ? " gone" : ""), `Richtung ${leg.direction || "–"}`);
    body.append(dir);
    if (leg.cancelled) body.append(node("div", "warn", "Diese Fahrt fällt aus."));

    const details = node("div", "details");
    details.hidden = true;
    const hasDetails = (leg.stops && leg.stops.length) || (leg.notices && leg.notices.length) || leg.vehicle || leg.carrier;
    const ride = known(leg.durationMin) ? ` (${leg.durationMin} min)` : "";
    const label = leg.stopCount ? `Fahre ${leg.stopCount} Haltestelle${leg.stopCount === 1 ? "" : "n"}${ride}` : `Fahrt${ride}`;
    const toggle = node(hasDetails ? "button" : "div", "more");
    toggle.append(node("span", "", label));
    if (hasDetails) {
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      toggle.append(icon("chevron"));
      toggle.addEventListener("click", () => {
        details.hidden = !details.hidden;
        toggle.setAttribute("aria-expanded", String(!details.hidden));
        toggle.classList.toggle("open", !details.hidden);
      });
    }
    body.append(toggle);

    const st = leg.stats;
    if (st && (st.alight || known(st.cancelRate))) {
      const bits = [];
      if (st.alight) bits.push(`pünktlich ${pct(st.alight.ontimePct)} · Ø ${num(st.alight.avgMin)} min · n=${st.alight.n}`);
      if (known(st.cancelRate)) bits.push(`Ausfälle ${pct(num(st.cancelRate))}`);
      body.append(node("div", "stat", bits.join(" · ")));
    }

    const info = [...new Set([leg.vehicle, leg.carrier].filter(Boolean))].join(" · ");
    if (info) details.append(node("div", "info", info));
    if (leg.origin) details.append(node("div", "info", `Fahrt ab ${leg.origin}`));
    for (const stop of leg.stops || []) {
      const row = node("div", "stop");
      row.append(node("span", "tm2 " + (known(stop.delayMin) ? delayClass(stop.delayMin) : ""), stop.time ? hhmm(stop.time + (stop.delayMin || 0) * 60) : ""), node("span", "nm2", stop.name));
      if (stop.delayMin) row.append(node("span", "dm " + delayClass(stop.delayMin), signed(stop.delayMin)));
      details.append(row);
    }
    for (const text of leg.notices || []) details.append(node("div", "note", `ⓘ ${text}`));
    body.append(details);
    mid.append(node("div", "tm"), rail, body);
    card.append(mid);
    card.append(stopRow("arr", leg.arr, leg.arrDelayMin, leg.to.name, leg.to.platform, leg.cancelled));
    return card;
  }

  const minLabel = (t) => (t === 0 ? "nur Fahrplan" : `mind. ${t} min`);

  function transferRow(tr, feeder, next, minTransfer) {
    const walk = tr.walkMin || 0;
    const row = node("div", "tl xfer");
    const body = node("div", "body");
    const parts = [];
    if (walk) parts.push(`${walk} min zu Fuß`);
    if (tr.waitMin - walk > 0) parts.push(`${tr.waitMin - walk} min warten`);
    if (!parts.length) parts.push("keine Wartezeit");
    const text = node("div", "xtext");
    text.append(icon("swapStop"), node("span", "", `Steige um (${parts.join(", ")})`));
    body.append(text);
    const chips = node("div", "xchips");
    const catchPct = tr.catchByMin ? tr.catchByMin[String(minTransfer)] : undefined;
    if (catchPct !== undefined) {
      const chip = node("span", "pill " + (catchPct >= 90 ? "good" : catchPct >= 60 ? "mid" : "bad"), `Anschluss ca. ${pct(num(catchPct))}`);
      const reserve = tr.waitMin - Math.max(walk, minTransfer);
      chip.title = reserve > 0 ? `Reserve laut Fahrplan: ${reserve} min. Ein Zubringer, der ${reserve} min oder mehr zu spät kommt, gilt als verpasst.` : "Keine Reserve: schon ein pünktlicher Zubringer reicht nicht für die gewählte Umsteigezeit.";
      chips.append(chip);
    }
    if (feeder && next && known(feeder.arrDelayMin)) {
      const live = tr.waitMin + (next.depDelayMin || 0) - feeder.arrDelayMin - Math.max(walk, minTransfer);
      if (feeder.arrDelayMin || next.depDelayMin || live <= 0) {
        chips.append(node("span", "pill " + (live <= 0 ? "bad" : live <= 1 ? "mid" : "good"), live <= 0 ? "Nach aktueller Prognose verpasst" : `Aktuell ${live} min Reserve`));
      }
    }
    if (chips.childElementCount) body.append(chips);
    row.append(node("div", "tm"), node("div", "rail"), body);
    return row;
  }

  function walkRow(leg) {
    const row = node("div", "tl xfer");
    const body = node("div", "body");
    const text = node("div", "xtext");
    text.append(icon("walk"), node("span", "", `Gehe ${leg.walkMin} min zu Fuß`));
    body.append(text);
    if (leg.from.name !== leg.to.name) body.append(node("div", "xsub", `${leg.from.name} → ${leg.to.name}`));
    row.append(node("div", "tm"), node("div", "rail"), body);
    return row;
  }

  function meter(label, value, hint, invert) {
    const row = node("div", "meter");
    const head = node("div", "mh");
    head.append(node("span", "", label), node("b", "", pct(value)));
    const bar = node("div", "bar");
    const fill = node("span", invert ? tone(100 - value * 5) : tone(value));
    fill.style.width = `${Math.max(2, Math.min(100, value))}%`;
    bar.append(fill);
    row.append(head, bar);
    if (hint) row.append(node("div", "hint", hint));
    return row;
  }

  function reliabilityBox(rel, minTransfer) {
    const box = node("div", "rel");
    const by = (rel.byMinTransfer || {})[String(minTransfer)] || rel;
    if (by.ontimePct !== undefined) {
      box.append(meter("Pünktlich am Ziel (≤ 3 min)", by.ontimePct, "Wahrscheinlichkeit, dass die Fahrt höchstens 3 Minuten verspätet ankommt, einschließlich verpasster Anschlüsse."));
      if (rel.transfers) box.append(meter(`Anschluss${rel.transfers > 1 ? "züge" : ""} erreicht`, by.connectionPct, `${rel.transfers} Umstieg${rel.transfers > 1 ? "e" : ""}, Umsteigezeit: ${minLabel(minTransfer)}.`));
    } else {
      box.append(node("div", "hint", rel.unavailable ? "Messdaten derzeit nicht erreichbar – Verbindung ohne Zuverlässigkeitsangabe." : rel.legs ? `Für ${rel.legsWithData} von ${rel.legs} Abschnitten liegen Messdaten vor – keine Gesamtprognose möglich.` : "Keine Fahrt mit Linie."));
    }
    if (rel.cancelPct !== undefined) box.append(meter("Ausfallrisiko", rel.cancelPct, `Gemessen für ${rel.cancelCoverage}; für die übrigen Linien erfasst der Dienst keine Ausfälle.`, true));
    else if (!rel.unavailable) box.append(node("div", "hint", "Ausfallrisiko: keine Messdaten für diese Linien."));
    return box;
  }

  function tripHead(r, minTransfer) {
    const head = node("button", "trip-head");
    head.type = "button";
    const dep = r.realDep || r.plannedDep, arr = r.realArr || r.plannedArr;
    const top = node("div", "trip-top");
    top.append(node("span", "trip-time", `${hhmm(dep)} → ${hhmm(arr)}`), node("span", "trip-dur", `${r.minutes} min`));
    top.append(icon("chevron", "trip-chev"));
    head.append(top);

    // Verlauf: Fussweg am Anfang/Ende und die Linienschilder in Reihenfolge
    const mid = node("div", "trip-mid");
    const steps = r.legs.filter((leg, i) => leg.line || i === 0 || i === r.legs.length - 1);
    steps.forEach((leg) => {
      if (mid.childElementCount) mid.append(node("span", "sep", "›"));
      if (leg.line) mid.append(badge(leg.line, leg.mode));
      else {
        const w = node("span", "walk-chip");
        w.append(icon("walk"), document.createTextNode(`${leg.walkMin} min`));
        mid.append(w);
      }
    });
    head.append(mid);

    const foot = node("div", "trip-foot");
    const rides = r.legs.filter((l) => l.line).length;
    foot.append(node("span", "pill", rides > 1 ? `${rides - 1} Umstieg${rides > 2 ? "e" : ""}` : "Direkt"));
    const rel = r.reliability || {};
    const by = (rel.byMinTransfer || {})[String(minTransfer)] || rel;
    if (by.ontimePct !== undefined) foot.append(node("span", "pill " + tone(by.ontimePct), `${pct(by.ontimePct)} pünktlich`));
    if (rel.cancelPct !== undefined && rel.cancelPct >= 1) foot.append(node("span", "pill " + (rel.cancelPct >= 5 ? "bad" : "mid"), `Ausfall ${pct(rel.cancelPct)}`));
    if (r.realArr && r.plannedArr && Math.round((r.realArr - r.plannedArr) / 60) >= 1) foot.append(node("span", "pill late1", `Ankunft +${Math.round((r.realArr - r.plannedArr) / 60)} min`));
    head.append(foot);
    return head;
  }

  function tripBody(r, minTransfer) {
    const body = node("div", "trip-body");
    const line = node("div", "line");
    let prev = null;
    r.legs.forEach((leg, i) => {
      if (!leg.line) {
        // Fusswege zwischen zwei Fahrten gehoeren zur Umstiegszeile; nur Wege vor der ersten und nach der letzten Fahrt stehen allein
        const between = prev !== null && r.legs.slice(i + 1).some((l) => l.line);
        if (!between) line.append(walkRow(leg));
        return;
      }
      if (leg.transfer) line.append(transferRow(leg.transfer, prev, leg, minTransfer));
      line.append(rideCard(leg));
      prev = leg;
    });
    body.append(line, reliabilityBox(r.reliability || {}, minTransfer));
    return body;
  }

  // ---- Seite ----

  const state = { data: null, open: new Set([0]), controller: null, busy: false };
  let fields = null;

  const nowLocalInput = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const minTransfer = () => Number($("min").value) || 0;

  function renderResults() {
    const out = $("out");
    out.replaceChildren();
    $("foot").hidden = !state.data || !state.data.routes.length;
    const data = state.data;
    if (!data) return;
    if (data.from && data.to) {
      const s = node("div", "summary");
      s.append("Von ", node("b", "", data.from), " nach ", node("b", "", data.to));
      out.append(s);
    }
    if (!data.routes.length) {
      const m = node("div", "msg");
      m.append(node("strong", "", "Keine Verbindung gefunden"), document.createTextNode("Bitte Zeit oder Start und Ziel ändern."));
      return out.append(m);
    }
    const t = minTransfer();
    data.routes.forEach((r, i) => {
      const trip = node("article", "trip" + (state.open.has(i) ? " open" : ""));
      const head = tripHead(r, t);
      head.setAttribute("aria-expanded", String(state.open.has(i)));
      const body = tripBody(r, t);
      body.hidden = !state.open.has(i);
      head.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        trip.classList.toggle("open", open);
        head.setAttribute("aria-expanded", String(open));
        if (open) state.open.add(i);
        else state.open.delete(i);
      });
      trip.append(head, body);
      out.append(trip);
    });
  }

  function showMessage(title, text, retry) {
    const out = $("out");
    out.replaceChildren();
    $("foot").hidden = true;
    const m = node("div", "msg err");
    m.append(node("strong", "", title));
    if (text) m.append(document.createTextNode(text));
    if (retry) {
      const b = node("button", "retry", "Erneut versuchen");
      b.type = "button";
      b.addEventListener("click", search);
      m.append(document.createElement("br"), b);
    }
    out.append(m);
  }

  function showSkeleton() {
    const out = $("out");
    out.replaceChildren();
    for (let i = 0; i < 2; i++) {
      const s = node("div", "skel");
      s.append(node("i"), node("i"), node("i"));
      out.append(s);
    }
  }

  let whenTouched = false;

  async function search() {
    if (state.busy) return;
    const go = $("go");
    state.busy = true;
    go.disabled = true;
    $("out").setAttribute("aria-busy", "true");
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    const { signal } = state.controller;
    showSkeleton();
    try {
      const [from, to] = [await resolve(fields.from, signal), await resolve(fields.to, signal)];
      for (const [which, place] of [["from", from], ["to", to]]) {
        if (!place) {
          fields[which].focus();
          state.data = null;
          const typed = fields[which].text;
          if (typed) return showMessage(`„${typed}“ nicht gefunden`, "Bitte Schreibweise prüfen oder Straße mit Hausnummer und Ort eingeben.", false);
          return showMessage(which === "from" ? "Start fehlt" : "Ziel fehlt", which === "from" ? "Bitte Adresse oder Haltestelle für den Start eingeben oder den Standort nutzen." : "Bitte Adresse oder Haltestelle für das Ziel eingeben.", false);
        }
      }
      if (!whenTouched || !$("when").value) $("when").value = nowLocalInput();
      const params = new URLSearchParams({ from: placeParam(from), to: placeParam(to), arrive: document.querySelector('.seg [aria-checked="true"]').dataset.arrive });
      params.set("when", $("when").value);
      const data = await getJson(`/api/route?${params}`, signal);
      state.data = { ...data, from: from.label, to: to.label };
      state.open = new Set([0]);
      renderResults();
      if ($("out").getBoundingClientRect().top > innerHeight * 0.55) $("out").scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (e) {
      if (e && e.name === "AbortError") return;
      state.data = null;
      showMessage("Suche fehlgeschlagen", e.message, true);
    } finally {
      state.busy = false;
      go.disabled = false;
      $("out").setAttribute("aria-busy", "false");
    }
  }

  function init() {
    // nach einer Auswahl: vom Start zum Ziel, vom Ziel (bei vorhandenem Start) zur Suche
    const advance = (which) => {
      if (which === "from") return fields.to.place ? search() : fields.to.focus();
      return fields.from.place || fields.from.text ? search() : fields.from.focus();
    };
    fields = { from: Field("from", { chosen: advance }), to: Field("to", { chosen: advance }) };
    $("swap").innerHTML = ICONS.swap;
    $("swap").addEventListener("click", () => {
      const a = { place: fields.from.place, text: fields.from.input.value }, b = { place: fields.to.place, text: fields.to.input.value };
      fields.from.set(b.place, b.text);
      fields.to.set(a.place, a.text);
    });
    $("when").value = nowLocalInput();
    $("when").addEventListener("input", () => (whenTouched = true));
    $("now").addEventListener("click", () => {
      whenTouched = false;
      $("when").value = nowLocalInput();
    });
    const seg = document.querySelectorAll(".seg [role=radio]");
    seg.forEach((btn) => btn.addEventListener("click", () => seg.forEach((b) => b.setAttribute("aria-checked", String(b === btn)))));
    const saved = store.get("hvv.minTransfer");
    if (saved !== null && /^[0-5]$/.test(saved)) $("min").value = saved;
    const showMin = () => ($("min-val").textContent = minLabel(minTransfer()));
    showMin();
    $("min").addEventListener("input", () => {
      showMin();
      store.set("hvv.minTransfer", String(minTransfer()));
      if (state.data) renderResults(); // Anschluesse sofort neu bewerten, kein neuer Abruf noetig
    });
    $("search").addEventListener("submit", (e) => {
      e.preventDefault();
      document.activeElement && document.activeElement.blur && document.activeElement.blur();
      search();
    });
    getJson("/api/colors").then((c) => Object.assign(COLORS, c)).catch(() => {});
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
  root.HvvRoute = { placeParam, lineColor, tone, delayClass, signed, minLabel, recents };
})(typeof self !== "undefined" ? self : this);
