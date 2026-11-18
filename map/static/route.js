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
  // Dauer: 81 min -> "1 h 21 min", 120 min -> "2 h"
  const dur = (min) => {
    const m = Math.round(min);
    if (m < 60) return `${m} min`;
    return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
  };
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

  async function postJson(url, body) {
    let response;
    try {
      response = await fetch(url, { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (e) {
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
    bus: svg('<rect x="4.5" y="3.5" width="15" height="14" rx="3"/><path d="M4.5 10.5h15M7.5 20.5v-3M16.5 20.5v-3"/><circle cx="8.3" cy="14" r=".9" fill="currentColor" stroke="none"/><circle cx="15.7" cy="14" r=".9" fill="currentColor" stroke="none"/>'),
    sbahn: svg('<circle cx="12" cy="12" r="8.6"/><path d="M15.2 9.3c-.6-1-1.7-1.5-3.1-1.5-1.7 0-2.8.8-2.8 2 0 3.1 6 1.4 6 4.4 0 1.3-1.3 2.1-3 2.1-1.4 0-2.5-.5-3.2-1.6"/>'),
    ubahn: svg('<rect x="3.6" y="3.6" width="16.8" height="16.8" rx="3.8"/><path d="M8.6 7.8v5.2a3.4 3.4 0 0 0 6.8 0V7.8"/>'),
    train: svg('<path d="M9.5 2.8h5M12 2.8v2"/><rect x="5.5" y="4.8" width="13" height="12.7" rx="3"/><path d="M5.5 11.2h13M8.3 21l1.6-3.5M15.7 21l-1.6-3.5"/><circle cx="9" cy="14.3" r=".8" fill="currentColor" stroke="none"/><circle cx="15" cy="14.3" r=".8" fill="currentColor" stroke="none"/>'),
    ship: svg('<path d="M4 15.5l1.8 3.7h12.4l1.8-3.7zM7.5 15V9.5h9V15M12 9.5V5.5M9.5 5.5h5"/>'),
    refresh: svg('<path d="M20 11a8 8 0 0 0-14.5-4.2M4 4.5v3.3h3.3M4 13a8 8 0 0 0 14.5 4.2M20 19.5v-3.3h-3.3"/>'),
    chevron: svg('<path d="M5.5 9l6.5 6.5L18.5 9"/>'),
    walk: svg('<circle cx="13" cy="4.6" r="1.8" fill="currentColor"/><path d="M9.5 21.5l1.7-6.3-2.3-2.2v-4l-1.8 2.2M13 8.5l1.7 3.2 3 .8M11.2 15.2l3.5 1.6.9 4.7M8.9 9.1l3.6-1.4c.4-.1.9.1 1 .5"/>'),
    swapStop: svg('<path d="M7 7h12M7 7l3-3M7 7l3 3M17 17H5m12 0l-3-3m3 3l-3 3"/>'),
    // Funksymbol fuer Echtzeit: Punkt und zwei Bogen, die nacheinander aufleuchten (CSS .live)
    live: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path class="w1" d="M3 8.2a4.8 4.8 0 0 1 4.8 4.8"/><path class="w2" d="M3 3.2A9.8 9.8 0 0 1 12.8 13"/></g><circle cx="3.6" cy="12.4" r="1.7" fill="currentColor"/></svg>',
  };
  const icon = (name, cls) => {
    const n = node("span", cls || "");
    n.innerHTML = ICONS[name];
    return n;
  };
  // Verkehrsmittel: Bus, S-Bahn, U-Bahn, Regionalzug, Faehre (mode kommt vom Server: B, X, S, U, R, Z, F)
  const MODES = { B: ["bus", "Bus"], X: ["bus", "XpressBus"], S: ["sbahn", "S-Bahn"], U: ["ubahn", "U-Bahn"], R: ["train", "Regionalzug"], Z: ["train", "Zug"], F: ["ship", "Fähre"] };
  const modeOf = (leg) => MODES[leg.mode] || (/^\d/.test(leg.line || "") ? MODES.B : MODES.Z);
  function modeIcon(leg) {
    const [name, label] = modeOf(leg);
    const n = icon(name, "mode");
    n.setAttribute("role", "img");
    n.setAttribute("aria-label", label);
    n.title = label;
    n.style.color = lineColor(leg);
    return n;
  }
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
        // Adresse zur Position: "Weidenbaumsweg 14, Hamburg" statt "Mein Standort" (Routing nutzt weiter die genaue Koordinate)
        try {
          const a = await Promise.race([getJson(`/api/reverse?x=${p.x}&y=${p.y}`), new Promise((_, no) => setTimeout(no, 4000))]);
          if (a.place && a.place.label) p.label = a.place.label;
        } catch (e) { /* ohne Adresse bleibt "Mein Standort" */ }
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

  const stamp = (ts) => new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) + " Uhr";

  // Meldung zur Fahrt (Bauarbeiten, Fahrplanaenderung ...): Titel immer sichtbar, Text und Gueltigkeit auf Tipp
  function noticeBox(info) {
    const box = node("div", "notice");
    box.append(node("div", "ntitle", info.summary));
    const more = node("div", "ndetail");
    more.hidden = true;
    if (info.from && info.to) more.append(node("div", "nmeta", `Gültigkeit: ${stamp(info.from)} – ${stamp(info.to)}`));
    if (info.text) more.append(node("p", "", info.text));
    for (const link of info.links || []) {
      const a = node("a", "", link.label);
      a.href = link.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      more.append(a);
    }
    if (info.modified) more.append(node("div", "nmeta", `Letzte Änderung: ${stamp(info.modified)}`));
    if (more.childNodes.length) {
      const btn = node("button", "link", "Details anzeigen");
      btn.type = "button";
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", () => {
        more.hidden = !more.hidden;
        btn.textContent = more.hidden ? "Details anzeigen" : "Details ausblenden";
        btn.setAttribute("aria-expanded", String(!more.hidden));
      });
      box.append(btn, more);
    }
    return box;
  }

  function rideCard(leg, legIndex, ctx) {
    const card = node("div", "leg");
    card.style.setProperty("--c", lineColor(leg));
    card.append(stopRow("dep", leg.dep, leg.depDelayMin, leg.from.name, leg.from.platform, leg.cancelled));

    const mid = node("div", "tl mid");
    const rail = node("div", "rail");
    const logo = node("span", "rt-logo");
    logo.append(badge(leg.line, leg.mode));
    rail.append(logo);
    const body = node("div", "body");
    const dir = node("div", "dir" + (leg.cancelled ? " gone" : ""));
    dir.append(modeIcon(leg), node("span", "", `Richtung ${leg.direction || "–"}`));
    body.append(dir);
    if (ctx && leg.from.id && !leg.cancelled) {
      const alts = node("div", "alts");
      alts.hidden = true;
      body.append(alts);
      ctx.alts.push({ el: alts, leg, index: legIndex });
    }
    if (leg.cancelled) body.append(node("div", "warn", "Diese Fahrt fällt aus."));
    for (const info of leg.noticeInfo || []) body.append(noticeBox(info));

    const details = node("div", "details");
    const stopsKey = `${ctx && ctx.route ? ctx.route.key : ""}:${legIndex}`;
    details.hidden = !state.stopsOpen.has(stopsKey); // bleibt beim Neuladen der Liste so, wie du es gelassen hast
    const hasDetails = (leg.stops && leg.stops.length) || leg.vehicle || leg.carrier;
    const ride = known(leg.durationMin) ? ` (${dur(leg.durationMin)})` : "";
    const label = leg.stopCount ? `Fahre ${leg.stopCount} Haltestelle${leg.stopCount === 1 ? "" : "n"}${ride}` : `Fahrt${ride}`;
    const toggle = node(hasDetails ? "button" : "div", "more");
    toggle.append(node("span", "", label));
    if (hasDetails) {
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(!details.hidden));
      toggle.classList.toggle("open", !details.hidden);
      toggle.append(icon("chevron"));
      toggle.addEventListener("click", () => {
        details.hidden = !details.hidden;
        toggle.setAttribute("aria-expanded", String(!details.hidden));
        toggle.classList.toggle("open", !details.hidden);
        if (details.hidden) state.stopsOpen.delete(stopsKey);
        else state.stopsOpen.add(stopsKey);
      });
    }
    body.append(toggle);

    const st = leg.stats;
    if (st && (st.alight || known(st.cancelRate))) {
      const bits = [];
      if (st.alight) bits.push(`pünktlich ${pct(st.alight.ontimePct)} · Ø ${num(st.alight.avgMin)} min · n=${st.alight.n}${st.alight.scope === "ganze Linie" ? " (Linie gesamt)" : ""}`);
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
    body.append(details);
    mid.append(node("div", "tm"), rail, body);
    card.append(mid);
    card.append(stopRow("arr", leg.arr, leg.arrDelayMin, leg.to.name, leg.to.platform, leg.cancelled));
    return card;
  }

  const minLabel = (t) => (t === 0 ? "nur Fahrplan" : `mind. ${t} min`);

  function transferRow(tr, feeder, next, minTransfer, ctx) {
    const walk = tr.walkMin || 0;
    const row = node("div", "tl xfer");
    const body = node("div", "body");
    const parts = [];
    if (walk) parts.push(`${dur(walk)} zu Fuß`);
    if (tr.waitMin - walk > 0) parts.push(`${dur(tr.waitMin - walk)} warten`);
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
        chips.append(node("span", "pill " + (live <= 0 ? "bad" : live <= 1 ? "mid" : "good"), live <= 0 ? "Nach aktueller Prognose verpasst" : `Aktuell ${dur(live)} Reserve`));
        if (live <= 0 && ctx && state.ignore.has(ctx.route.key)) {
          const again = node("button", "link", "Alternative anzeigen");
          again.type = "button";
          again.addEventListener("click", () => showAlternative(ctx.ri));
          chips.append(again);
        }
      }
    }
    if (chips.childElementCount) body.append(chips);
    row.append(node("div", "tm"), node("div", "rail"), body);
    return row;
  }

  // Start/Ziel ohne Haltestelle (Adresse, Standort): die GTI liefert dort keinen Namen, dann steht die Eingabe aus der Suche
  function walkRow(leg, isFirst) {
    const row = node("div", "tl xfer");
    const body = node("div", "body");
    const text = node("div", "xtext");
    text.append(icon("walk"), node("span", "", `Gehe ${dur(leg.walkMin)} zu Fuß`));
    body.append(text);
    const searched = state.last || {};
    const from = leg.from.name || (isFirst ? searched.from : searched.to) || "Start";
    const to = leg.to.name || (isFirst ? searched.from : searched.to) || "Ziel";
    if (from !== to) body.append(node("div", "xsub", `${from} → ${to}`));
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
    if (rel.cancelPct !== undefined) {
      const missing = rel.cancelMissing || [];
      // Hinweis nur, wenn Abschnitte fehlen; bei "3 von 3" waere er ueberfluessig
      const hint = missing.length ? `Gemessen für ${rel.cancelCoverage}. Für ${missing.join(", ")} erfasst der Dienst keine Ausfälle.` : "";
      box.append(meter("Ausfallrisiko", rel.cancelPct, hint, true));
    }
    else if (!rel.unavailable) box.append(node("div", "hint", "Ausfallrisiko: keine Messdaten für diese Linien."));
    return box;
  }

  function tripHead(r, minTransfer) {
    const head = node("button", "trip-head");
    head.type = "button";
    const dep = r.realDep || r.plannedDep, arr = r.realArr || r.plannedArr;
    const top = node("div", "trip-top");
    top.append(node("span", "trip-time", `${hhmm(dep)} → ${hhmm(arr)}`), node("span", "trip-dur", dur(r.minutes)));
    top.append(icon("chevron", "trip-chev"));
    head.append(top);

    // Verlauf: Fussweg am Anfang/Ende und die Linienschilder in Reihenfolge
    const mid = node("div", "trip-mid");
    const steps = r.legs.filter((leg, i) => leg.line || i === 0 || i === r.legs.length - 1);
    steps.forEach((leg) => {
      if (mid.childElementCount) mid.append(node("span", "sep", "›"));
      if (leg.line) mid.append(modeIcon(leg), badge(leg.line, leg.mode));
      else {
        const w = node("span", "walk-chip");
        w.append(icon("walk"), document.createTextNode(dur(leg.walkMin)));
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
    if (r.custom && r.custom.picks.some((pick) => pick.auto)) foot.append(node("span", "pill custom", "Alternative"));
    else if (r.custom) foot.append(node("span", "pill custom", "Eigene Abfahrt"));
    head.append(foot);
    return head;
  }

  function tripBody(r, minTransfer, ctx) {
    const body = node("div", "trip-body");
    const saved = isSaved(r);
    const save = node("div", "save-row");
    const saveBtn = node("button", "link", saved ? "★ Gemerkt – vergessen" : "☆ Diese Reise merken");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", () => (saved ? forgetSaved() : saveRoute(ctx.ri)));
    save.append(saveBtn);
    if (!saved) save.append(node("span", "hint", "Du siehst sie beim nächsten Besuch wieder, mit aktuellen Zeiten."));
    body.append(save);
    if (r.custom) {
      const autos = r.custom.picks.some((pick) => pick.auto), manual = r.custom.picks.some((pick) => !pick.auto);
      if (autos) {
        const note = node("div", "custom-note auto");
        note.append(node("span", "", (r.custom.reasons || []).join(" ")));
        const ignore = node("button", "link", "Prognose ignorieren");
        ignore.type = "button";
        ignore.addEventListener("click", () => ignoreForecast(ctx.ri));
        note.append(ignore);
        body.append(note);
      }
      if (manual) {
        const note = node("div", "custom-note");
        note.append(node("span", "", "Du hast eine andere Abfahrt gewählt, der Rest der Route wurde neu berechnet."));
        const undo = node("button", "link", "Zurücksetzen");
        undo.type = "button";
        undo.addEventListener("click", () => resetRoute(ctx.ri));
        note.append(undo);
        body.append(note);
      }
    }
    if (state.altError && state.altError.ri === ctx.ri) body.append(node("div", "note err", state.altError.text));
    const line = node("div", "line");
    let prev = null;
    r.legs.forEach((leg, i) => {
      if (!leg.line) {
        // Fusswege zwischen zwei Fahrten gehoeren zur Umstiegszeile; nur Wege vor der ersten und nach der letzten Fahrt stehen allein
        const between = prev !== null && r.legs.slice(i + 1).some((l) => l.line);
        if (!between) line.append(walkRow(leg, prev === null));
        return;
      }
      if (leg.transfer) line.append(transferRow(leg.transfer, prev, leg, minTransfer, ctx));
      line.append(rideCard(leg, i, ctx));
      prev = leg;
    });
    body.append(line, reliabilityBox(r.reliability || {}, minTransfer));
    body._alts = ctx.alts;
    return body;
  }

  // ---- Seite ----

  const state = { data: null, open: new Set([0]), controller: null, busy: false, fetchedAt: 0, lastTry: 0, last: null, refreshError: "", altOpen: new Set(), stopsOpen: new Set(), ignore: new Set(), saved: null, lastLevel: "", sig: "", altError: null, rerouting: false };
  let fields = null;

  const nowLocalInput = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const minTransfer = () => Number($("min").value) || 0;

  // ---- Andere Abfahrt derselben Linie ("Auch in 28 min und 48 min") ----

  const ALT_TTL = 20000;
  const altCache = new Map(); // Schluessel -> { at, list, pending }
  // Schluessel einer Verbindung: Plan-Abfahrt der ERSTEN FAHRT und die Linien. Die Startzeit der ganzen Verbindung taugt nicht: Beginnt sie mit
  // einem Fussweg (Adresse, Standort), rechnet die GTI sie bei jeder Abfrage neu, und die gemerkte Reise wuerde nie wiedergefunden.
  const routeKey = (r) => {
    const rides = r.legs.filter((l) => l.line);
    return `${rides.length ? rides[0].dep : r.plannedDep}|${rides.map((l) => l.line).join(",")}`;
  };
  const minutesFromNow = (real) => Math.max(0, Math.round((real * 1000 - Date.now()) / 60000));
  const joinDe = (items) => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} und ${items[items.length - 1]}`);

  function notBefore(route, index) {
    const leg = route.legs[index], prev = route.legs[index - 1];
    if (prev && known(prev.arr)) return prev.arr + (prev.arrDelayMin || 0) * 60;
    return Math.max(Date.now() / 1000, (leg.dep || 0) - 15 * 60); // nichts anbieten, was lange vor der gesuchten Zeit liegt
  }

  function loadAlts(leg, after) {
    const key = `${leg.from.id}|${leg.line}|${leg.direction}|${Math.floor(after / 60)}`;
    const hit = altCache.get(key);
    if (hit && hit.list && Date.now() - hit.at < ALT_TTL) return Promise.resolve({ list: hit.list, fresh: true });
    if (hit && hit.pending) return hit.pending;
    const params = new URLSearchParams({ station: leg.from.id, line: leg.line, dir: leg.direction || "", after: String(Math.floor(after)), n: "6" });
    const pending = getJson(`/api/alternatives?${params}`)
      .then((d) => {
        altCache.set(key, { at: Date.now(), list: d.alternatives });
        return { list: d.alternatives, fresh: true };
      })
      .catch(() => {
        altCache.delete(key);
        return { list: hit && hit.list ? hit.list : [], fresh: false };
      });
    altCache.set(key, { ...(hit || {}), pending });
    pending.finally(() => { const c = altCache.get(key); if (c && c.pending === pending) delete c.pending; });
    return pending;
  }

  function showAlts(item, ri, route, list) {
    const { el, leg, index } = item;
    if (!el.isConnected) return;
    const others = list.filter((a) => Math.abs(a.planned - leg.dep) > 45).slice(0, 3);
    el.replaceChildren();
    if (!others.length) return (el.hidden = true);
    el.hidden = false;
    const key = `${route.key}:${index}`;
    const open = state.altOpen.has(key);
    const toggle = node("button", "more alts-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(open));
    toggle.classList.toggle("open", open);
    toggle.append(node("span", "", `Auch in ${joinDe(others.map((a) => dur(minutesFromNow(a.real))))}`), icon("chevron"));
    const rows = node("div", "alt-list");
    rows.hidden = !open;
    toggle.addEventListener("click", () => {
      rows.hidden = !rows.hidden;
      toggle.setAttribute("aria-expanded", String(!rows.hidden));
      toggle.classList.toggle("open", !rows.hidden);
      if (rows.hidden) state.altOpen.delete(key);
      else state.altOpen.add(key);
    });
    for (const alt of others) {
      const b = node("button", "alt-item");
      b.type = "button";
      const t = node("span", "alt-t", hhmm(alt.real));
      const meta = node("span", "alt-m", alt.delayMin ? `Plan ${hhmm(alt.planned)} · ${signed(alt.delayMin)} min` : `in ${dur(minutesFromNow(alt.real))}`);
      const go = node("span", "alt-go", "Wählen");
      b.append(t, meta, go);
      b.setAttribute("aria-label", `Abfahrt um ${hhmm(alt.real)} Uhr wählen`);
      b.addEventListener("click", () => chooseAlt(ri, index, alt, b));
      rows.append(b);
    }
    el.append(toggle, rows);
  }

  async function hydrateAlts(body, ri, route) {
    if (!body._alts || body._hydrated) return;
    body._hydrated = true;
    for (const item of body._alts) {
      const after = notBefore(route, item.index);
      const key = `${item.leg.from.id}|${item.leg.line}|${item.leg.direction}|${Math.floor(after / 60)}`;
      const cached = altCache.get(key);
      if (cached && cached.list) showAlts(item, ri, route, cached.list); // alte Werte sofort zeigen, damit nichts flackert
      const { list } = await loadAlts(item.leg, after);
      showAlts(item, ri, route, list);
    }
  }

  async function rerouteCall(route, index, planned) {
    const res = await postJson("/api/reroute", {
      prefix: route.legs.slice(0, index),
      leg: route.legs[index],
      planned,
      to: new URLSearchParams(state.last.params).get("to"),
      tail: route.legs.length > index + 1,
    });
    return res.route;
  }

  async function chooseAlt(ri, index, alt, button) {
    if (state.rerouting) return;
    const route = state.data.routes[ri];
    state.rerouting = true;
    state.altError = null;
    document.querySelectorAll(".alt-item").forEach((b) => (b.disabled = true));
    button.querySelector(".alt-go").textContent = "Berechne …";
    try {
      const next = await rerouteCall(route, index, alt.planned);
      next.key = route.key;
      next.custom = { orig: route.custom ? route.custom.orig : route, picks: [...(route.custom ? route.custom.picks : []), { index, planned: alt.planned }] };
      state.data.routes[ri] = next;
      state.open.add(ri);
      syncSaved(next);
    } catch (e) {
      state.altError = { ri, text: e.message };
    } finally {
      state.rerouting = false;
      renderResults();
    }
  }

  function resetRoute(ri) {
    const route = state.data.routes[ri];
    if (route.custom) {
      if (route.custom.picks.some((pick) => pick.auto)) state.ignore.add(route.key); // sonst wuerde die Alternative sofort wiederkommen
      state.data.routes[ri] = route.custom.orig;
      syncSaved(route.custom.orig);
    }
    renderResults();
  }

  // Gewaehlte Abfahrten nacheinander auf eine Verbindung anwenden (jede neu berechnet den Rest)
  async function applyPicks(route, picks) {
    let current = route;
    const done = [];
    for (const pick of picks) {
      const next = await rerouteCall(current, pick.index, pick.planned);
      done.push(pick);
      next.key = route.key;
      next.custom = { orig: current.custom ? current.custom.orig : current, picks: [...done] };
      current = next;
    }
    return current;
  }

  // Nach einer neuen Abfrage selbst gewaehlte Abfahrten erneut anwenden (die neue Antwort kennt sie nicht); automatische Alternativen
  // werden dagegen jedes Mal neu bewertet
  async function reapplyPicks(previous, fresh) {
    for (const old of previous.filter((r) => r.custom && r.custom.picks.some((pick) => !pick.auto))) {
      const at = fresh.routes.findIndex((r) => r.key === old.key);
      if (at < 0) continue;
      try {
        fresh.routes[at] = await applyPicks(fresh.routes[at], old.custom.picks.filter((pick) => !pick.auto));
      } catch (e) {
        state.refreshError = "Die gewählte Abfahrt ist nicht mehr verfügbar, die ursprüngliche Verbindung wird angezeigt.";
      }
    }
  }

  // ---- Anschluss laut Prognose verpasst: direkt eine Alternative zeigen (mit Begruendung, ignorierbar) ----

  // Erster Abschnitt, der laut Prognose nicht zu schaffen ist: verpasster Anschluss oder ausfallende Fahrt
  function findProblem(route, minT) {
    const t = minT === undefined ? minTransfer() : minT;
    let feeder = null;
    for (let i = 0; i < route.legs.length; i++) {
      const leg = route.legs[i];
      if (!leg.line) continue;
      const tr = leg.transfer;
      if (feeder && tr && known(feeder.arrDelayMin) && !feeder.cancelled && known(feeder.arr)) {
        const walk = tr.walkMin || 0;
        const base = tr.waitMin - Math.max(walk, t);
        if (base + (leg.depDelayMin || 0) - feeder.arrDelayMin <= 0) {
          return { kind: "missed", index: i, leg, feeder, base, station: leg.from.name, notBefore: feeder.arr + feeder.arrDelayMin * 60 + Math.max(walk, t) * 60 };
        }
      }
      if (leg.cancelled) return { kind: "cancelled", index: i, leg, feeder, notBefore: leg.dep };
      feeder = leg;
    }
    return null;
  }

  function reasonText(p, depart) {
    if (p.kind === "cancelled") return `Die ${p.leg.line} um ${hhmm(p.leg.dep)} ab ${p.leg.from.name} fällt aus. Stattdessen: ${p.leg.line} um ${hhmm(depart)}.`;
    return `Anschluss ${p.leg.line} in ${p.station} laut Prognose verpasst (${p.feeder.line} ${dur(p.feeder.arrDelayMin)} zu spät, nur ${dur(Math.max(0, p.base))} Reserve). Stattdessen: ${p.leg.line} um ${hhmm(depart)}.`;
  }

  async function fixRoute(route) {
    const p = findProblem(route);
    if (!p) return null;
    const { list } = await loadAlts(p.leg, p.notBefore - 60);
    const candidate = list.find((a) => a.real >= p.notBefore && Math.abs(a.planned - p.leg.dep) > 45);
    if (!candidate) return null;
    const next = await rerouteCall(route, p.index, candidate.planned);
    const prev = route.custom || { orig: route, picks: [], reasons: [] };
    next.key = route.key;
    next.custom = {
      orig: prev.orig,
      base: prev.base || route, // Stand vor der ersten automatischen Alternative: dorthin geht "Prognose ignorieren"
      picks: [...prev.picks, { index: p.index, planned: candidate.planned, auto: true }],
      reasons: [...(prev.reasons || []), reasonText(p, candidate.real)],
    };
    return next;
  }

  async function autoFixAll(data) {
    for (let i = 0; i < data.routes.length; i++) {
      if (state.ignore.has(data.routes[i].key)) continue;
      let current = data.routes[i];
      try {
        for (let n = 0; n < 3; n++) {
          const next = await fixRoute(current);
          if (!next) break;
          current = next;
        }
      } catch (e) { /* ohne Alternative bleibt die Verbindung, wie sie ist */ }
      data.routes[i] = current;
    }
  }

  function ignoreForecast(ri) {
    const route = state.data.routes[ri];
    state.ignore.add(route.key);
    if (route.custom && route.custom.base) state.data.routes[ri] = route.custom.base;
    renderResults();
  }

  async function showAlternative(ri) {
    const route = state.data.routes[ri];
    state.ignore.delete(route.key);
    const holder = { routes: [route] };
    try {
      await autoFixAll(holder);
    } finally {
      state.data.routes[ri] = holder.routes[0];
      renderResults();
    }
  }

  // ---- Reise merken: beim naechsten Besuch wieder da, mit aktuellen Zeiten und Hinweisen ----

  const SAVED_KEY = "hvv.saved";
  const isSaved = (r) => !!state.saved && r.key === state.saved.key;
  const localInputOf = (epoch) => new Date(epoch * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);

  function loadSaved() {
    try {
      const v = JSON.parse(store.get(SAVED_KEY) || "null");
      if (!(v && v.key && v.from && v.to && v.snapshot && Array.isArray(v.picks))) return null;
      if (v.v !== 2) v.key = routeKey(v.snapshot); // alter Schluessel (Startzeit der Verbindung) passt nicht mehr
      return v;
    } catch (e) {
      return null;
    }
  }
  const persistSaved = () => store.set(SAVED_KEY, state.saved ? JSON.stringify(state.saved) : "");

  function saveRoute(ri) {
    const r = state.data.routes[ri];
    state.saved = {
      v: 2,
      key: r.key,
      from: state.last.fromPlace,
      to: state.last.toPlace,
      plannedDep: r.plannedDep,
      plannedArr: r.plannedArr,
      realArrAtSave: r.realArr || r.plannedArr,
      picks: r.custom ? r.custom.picks.filter((pick) => !pick.auto) : [],
      snapshot: JSON.parse(JSON.stringify({ ...r, custom: undefined })),
      savedAt: Date.now(),
    };
    state.lastLevel = "";
    persistSaved();
    renderResults();
  }

  function forgetSaved() {
    state.saved = null;
    persistSaved();
    renderResults();
  }

  // Aendert der Nutzer die Abfahrt der gemerkten Reise, merkt sich der Speicher die neue Wahl
  function syncSaved(route) {
    if (!isSaved(route)) return;
    state.saved.picks = route.custom ? route.custom.picks.filter((pick) => !pick.auto) : [];
    persistSaved();
  }

  const sameTrip = (sv) => {
    const q = new URLSearchParams(state.last.params);
    return q.get("from") === placeParam(sv.from) && q.get("to") === placeParam(sv.to);
  };

  // Gemerkte Reise in neue Ergebnisse einsetzen: nach oben holen; ist sie nicht mehr dabei, zeigt der gemerkte Stand
  async function adoptSaved(data) {
    const sv = state.saved;
    if (!sv || !sameTrip(sv)) return;
    const at = data.routes.findIndex((r) => r.key === sv.key);
    if (at < 0) {
      const old = JSON.parse(JSON.stringify(sv.snapshot));
      old.key = sv.key;
      old.stale = true;
      data.routes.unshift(old);
      return;
    }
    let r = data.routes[at];
    if (!r.custom && sv.picks.length) {
      try {
        r = await applyPicks(r, sv.picks);
      } catch (e) { /* die gewaehlte Abfahrt gibt es nicht mehr: Originalverbindung */ }
    }
    data.routes.splice(at, 1);
    data.routes.unshift(r);
  }

  // Wo bist du gerade auf der Reise und was ist zu beachten?
  function tripStatus(r, minT, nowS) {
    const now = nowS === undefined ? Date.now() / 1000 : nowS;
    const steps = [];
    let shift = 0;
    for (const leg of r.legs) {
      if (!known(leg.dep) || !known(leg.arr)) continue;
      if (leg.line) {
        steps.push({ leg, dep: leg.dep + (leg.depDelayMin || 0) * 60, arr: leg.arr + (leg.arrDelayMin || 0) * 60 });
        shift = (leg.arrDelayMin || 0) * 60;
      } else steps.push({ leg, dep: leg.dep + shift, arr: leg.arr + shift });
    }
    if (!steps.length) return null;
    const last = steps[steps.length - 1];
    const lines = [];
    let level = "ok", title;
    const delay = r.realArr && r.plannedArr ? Math.round((r.realArr - r.plannedArr) / 60) : 0;
    const where = (leg) => (leg.from.platform ? `${leg.from.name}, ${leg.from.platform}` : leg.from.name);
    const upcoming = steps.filter((s) => s.leg.line && s.dep > now);
    const active = steps.find((s) => now >= s.dep && now < s.arr);

    if (now >= last.arr) return { level: "done", title: `Reise beendet – Ankunft ${hhmm(last.arr)}`, lines: ["Du kannst sie vergessen."] };
    if (now < steps[0].dep || !active) {
      const next = upcoming[0];
      title = next ? `Als Nächstes: ${next.leg.line} ab ${where(next.leg)} um ${hhmm(next.dep)} (in ${dur(Math.max(0, Math.round((next.dep - now) / 60)))})` : "Unterwegs";
    } else if (active.leg.line) {
      title = `Unterwegs: ${active.leg.line} Richtung ${active.leg.direction || "–"}`;
      lines.push(`Aussteigen in ${active.leg.to.name} um ${hhmm(active.arr)}${active.leg.arrDelayMin ? ` (${signed(active.leg.arrDelayMin)} min)` : ""}`);
      if (upcoming[0]) lines.push(`Danach: ${upcoming[0].leg.line} ab ${where(upcoming[0].leg)} um ${hhmm(upcoming[0].dep)}`);
    } else {
      title = `Zu Fuß: ${active.leg.from.name || (state.last && state.last.from) || "Start"} → ${active.leg.to.name || (state.last && state.last.to) || "Ziel"}`;
      lines.push(`Noch ${dur(Math.max(1, Math.round((active.arr - now) / 60)))}`);
      if (upcoming[0]) lines.push(`Danach: ${upcoming[0].leg.line} ab ${where(upcoming[0].leg)} um ${hhmm(upcoming[0].dep)}`);
    }
    lines.push(delay >= 1 ? `Ankunft ${hhmm(last.arr)} (+${dur(delay)})` : `Ankunft ${hhmm(last.arr)}, pünktlich`);
    const sv = state.saved;
    if (sv && known(sv.realArrAtSave) && r.realArr && Math.abs(Math.round((r.realArr - sv.realArrAtSave) / 60)) >= 2) {
      const diff = Math.round((r.realArr - sv.realArrAtSave) / 60);
      lines.push(`Seit dem Merken: Ankunft ${diff > 0 ? `${dur(diff)} später` : `${dur(-diff)} früher`}`);
    }
    if (delay >= 5) level = "warn";
    if (r.custom && r.custom.picks.some((pick) => pick.auto)) {
      level = "warn";
      lines.unshift(`Route angepasst: ${r.custom.reasons[r.custom.reasons.length - 1]}`);
    }
    const problem = findProblem(r, minT);
    if (problem) {
      level = "bad";
      lines.unshift(problem.kind === "cancelled" ? `Die ${problem.leg.line} fällt aus.` : `Anschluss ${problem.leg.line} in ${problem.station} laut Prognose verpasst.`);
    }
    if (r.stale) {
      level = level === "bad" ? "bad" : "warn";
      lines.push("Diese Verbindung ist in der aktuellen Abfrage nicht mehr enthalten, gezeigt wird der gemerkte Stand.");
    }
    return { level, title, lines };
  }

  const LEVEL_RANK = { ok: 0, done: 0, warn: 1, bad: 2 };
  function statusBanner(r) {
    const st = tripStatus(r, minTransfer());
    if (!st) return null;
    const box = node("div", "status " + st.level);
    box.append(node("strong", "", st.title));
    st.lines.forEach((text) => box.append(node("div", "", text)));
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      const ask = node("button", "link", "Bei Störungen benachrichtigen");
      ask.type = "button";
      ask.addEventListener("click", () => Notification.requestPermission().then(() => renderResults()));
      box.append(ask, node("span", "hint", " (nur solange die Seite geöffnet ist)"));
    }
    if (LEVEL_RANK[st.level] > LEVEL_RANK[state.lastLevel || "ok"] && document.hidden && typeof Notification !== "undefined" && Notification.permission === "granted") {
      try {
        new Notification(`HVV: ${st.title}`, { body: st.lines.slice(0, 2).join(" · "), tag: "hvv-saved" });
      } catch (e) { /* Benachrichtigungen nicht moeglich */ }
    }
    state.lastLevel = st.level;
    return box;
  }

  function updateSavedBar() {
    const bar = $("savedbar");
    const shown = state.data && state.data.routes.some((r) => isSaved(r));
    bar.hidden = !state.saved || !!shown;
    if (bar.hidden) return;
    const sv = state.saved;
    bar.replaceChildren();
    const open = node("button", "link", `★ Gemerkte Reise anzeigen: ${sv.from.label} → ${sv.to.label}, ${hhmm(sv.plannedDep)} Uhr`);
    open.type = "button";
    open.addEventListener("click", () => restoreSaved(sv));
    const drop = node("button", "link", "Vergessen");
    drop.type = "button";
    drop.addEventListener("click", forgetSaved);
    bar.append(open, drop);
  }

  function restoreSaved(sv) {
    fields.from.set(sv.from);
    fields.to.set(sv.to);
    whenTouched = true;
    $("when").value = localInputOf(sv.plannedDep);
    document.querySelectorAll(".seg [role=radio]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.arrive === "0")));
    return search();
  }

  // ---- Alter der Daten: "vor 12 s", jede Sekunde neu gezaehlt ----

  // Fingerabdruck der Verbindungen ohne die Zuverlaessigkeit (die wird bei jedem Abruf neu gewuerfelt und schwankt um Zehntelprozent)
  const sigOf = (routes) => JSON.stringify(routes, (k, v) => (k === "reliability" || k === "catchByMin" ? undefined : v));
  const AUTO_S = 30; // alle 30 s von selbst neu abrufen, solange die Seite sichtbar ist (die GTI-Abrufe sind begrenzt)
  const ageText = (s) => `vor ${s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`}`;
  const ageSeconds = () => Math.max(0, Math.floor((Date.now() - state.fetchedAt) / 1000));
  function tickAge() {
    const val = document.querySelector(".age-val");
    if (!val || !state.fetchedAt) return;
    val.textContent = ageText(ageSeconds());
  }

  // Automatisch aktualisieren: nur bei sichtbarer Seite, nie parallel und nach einem Fehler erst wieder nach AUTO_S
  function autoRefresh() {
    if (!state.data || state.busy || document.visibilityState === "hidden") return;
    if (Date.now() - Math.max(state.fetchedAt, state.lastTry) >= AUTO_S * 1000) search(true);
  }

  function ageRow() {
    const row = node("div", "age");
    row.setAttribute("aria-live", "off"); // der Zaehler darf nicht jede Sekunde vorgelesen werden
    const text = node("span", "age-text");
    text.append("Echtzeit-Stand: ", node("b", "age-val", ""));
    const btn = node("button", "refresh");
    btn.type = "button";
    btn.append(icon("refresh"), node("span", "", "Aktualisieren"));
    btn.addEventListener("click", () => search(true));
    row.append(text, btn);
    if (state.refreshError) row.append(node("div", "err", state.refreshError));
    return row;
  }

  function renderResults() {
    const out = $("out");
    out.replaceChildren();
    $("foot").hidden = !state.data || !state.data.routes.length;
    const data = state.data;
    updateSavedBar();
    if (!data) return;
    if (state.fetchedAt) {
      out.append(ageRow());
      tickAge();
    }
    if (!data.routes.length) {
      const m = node("div", "msg");
      m.append(node("strong", "", "Keine Verbindung gefunden"), document.createTextNode("Bitte Zeit oder Start und Ziel ändern."));
      return out.append(m);
    }
    const t = minTransfer();
    data.routes.forEach((r, i) => {
      const trip = node("article", "trip" + (state.open.has(i) ? " open" : "") + (isSaved(r) ? " saved" : ""));
      const banner = isSaved(r) ? statusBanner(r) : null;
      if (banner) trip.append(banner);
      const head = tripHead(r, t);
      head.setAttribute("aria-expanded", String(state.open.has(i)));
      const body = tripBody(r, t, { ri: i, route: r, alts: [] });
      body.hidden = !state.open.has(i);
      head.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        trip.classList.toggle("open", open);
        head.setAttribute("aria-expanded", String(open));
        if (open) {
          state.open.add(i);
          hydrateAlts(body, i, r);
        } else state.open.delete(i);
      });
      trip.append(head, body);
      out.append(trip);
      if (state.open.has(i)) hydrateAlts(body, i, r);
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
      b.addEventListener("click", () => search());
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

  // refresh = true: dieselbe Suche noch einmal (Start/Ziel/Zeitbezug wie zuletzt), Ergebnisse bleiben sichtbar und aufgeklappt
  async function search(refresh) {
    if (state.busy) return;
    const again = refresh === true && state.last && state.data;
    state.lastTry = Date.now();
    const go = $("go");
    state.busy = true;
    go.disabled = true;
    $("out").setAttribute("aria-busy", "true");
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    const { signal } = state.controller;
    if (again) {
      state.refreshError = "";
      const btn = document.querySelector(".age .refresh");
      if (btn) {
        btn.disabled = true;
        btn.classList.add("busy");
      }
    } else showSkeleton();
    try {
      if (again) {
        const params = new URLSearchParams(state.last.params);
        if (!whenTouched) {
          $("when").value = nowLocalInput(); // "Jetzt" bleibt jetzt, auch beim Aktualisieren
          params.set("when", $("when").value);
        }
        const data = await getJson(`/api/route?${params}`, signal);
        const fresh = { ...data, from: state.last.from, to: state.last.to };
        fresh.routes.forEach((r) => (r.key = routeKey(r)));
        const previous = state.data.routes;
        const sig = sigOf(fresh.routes);
        state.last.params = params.toString();
        if (sig === state.sig && !previous.some((r) => r.custom || isSaved(r)) && !state.refreshError) {
          // nichts hat sich geaendert: Liste stehen lassen (kein Zuklappen oder Springen), nur der Stand wird neu
          state.fetchedAt = Date.now();
          const btn = document.querySelector(".age .refresh");
          if (btn) {
            btn.disabled = false;
            btn.classList.remove("busy");
          }
          return tickAge();
        }
        state.sig = sig;
        await reapplyPicks(previous, fresh);
        await adoptSaved(fresh);
        await autoFixAll(fresh);
        state.data = fresh;
        state.fetchedAt = Date.now();
        return renderResults();
      }
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
      state.data.routes.forEach((r) => (r.key = routeKey(r)));
      state.sig = sigOf(state.data.routes);
      state.altOpen.clear();
      state.stopsOpen.clear();
      state.altError = null;
      state.last = { params: params.toString(), from: from.label, to: to.label, fromPlace: from, toPlace: to };
      await adoptSaved(state.data);
      await autoFixAll(state.data);
      state.fetchedAt = Date.now();
      state.refreshError = "";
      state.open = new Set([0]);
      renderResults();
      if ($("out").getBoundingClientRect().top > innerHeight * 0.55) $("out").scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (e) {
      if (e && e.name === "AbortError") return;
      if (again) {
        state.refreshError = `Aktualisieren fehlgeschlagen: ${e.message}`; // alte Ergebnisse bleiben stehen, ihr Alter laeuft weiter
        return renderResults();
      }
      state.data = null;
      state.fetchedAt = 0;
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
    setInterval(() => {
      tickAge();
      autoRefresh();
    }, 1000);
    document.addEventListener("visibilitychange", () => {
      tickAge();
      autoRefresh(); // zurueck im Tab: sofort neu abrufen, wenn der Stand alt ist
    });
    state.saved = loadSaved();
    if (state.saved) {
      const sv = state.saved;
      if (Date.now() / 1000 > Math.max(sv.plannedArr || 0, sv.realArrAtSave || 0) + 30 * 60) forgetSaved(); // Reise ist laengst vorbei
      else restoreSaved(sv);
    }
    getJson("/api/colors").then((c) => Object.assign(COLORS, c)).catch(() => {});
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
  root.HvvRoute = { placeParam, lineColor, tone, delayClass, signed, minLabel, recents, ageText, dur, findProblem, tripStatus, reasonText, state, routeKey };
})(typeof self !== "undefined" ? self : this);
