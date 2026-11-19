"""Stationssuche, Abfahrtstafel und Routenplaner mit Zuverlaessigkeit aus den gesammelten Messungen.

Die Zuverlaessigkeit einer Verbindung wird per Monte-Carlo aus den tatsaechlich gemessenen Verspaetungen berechnet
(Messungen `hvv_network_stop`, `hvv_xbus_stop`, `hvv_regional_stop` in InfluxDB): fuer jeden Fahrtabschnitt werden
Verspaetungen am Einstiegs- und Ausstiegshalt der Linie gezogen, ein Anschluss gilt als verpasst, wenn der Zubringer
spaeter ankommt als der Anschlusszug abfaehrt. Ausfaelle gibt es nur fuer die Linien, die der Collector mit
Abfahrtstafeln erfasst (`hvv_stop_event`, Feld `cancelled`); fuer alle anderen bleibt das Ausfallrisiko unbekannt.
"""

import csv
import io
import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .client import GtiClient

log = logging.getLogger("hvv_gti.planner")

TZ = ZoneInfo("Europe/Berlin")
ONTIME_S = 180  # "puenktlich": hoechstens 3 Minuten Verspaetung
MIN_SAMPLES = 25  # darunter wird auf einen groeberen Zeitraum bzw. die ganze Linie zurueckgefallen
SIMULATIONS = 4000
TRANSFER_STEPS = range(0, 6)  # waehlbare Mindest-Umsteigezeit in Minuten (Regler in der Karte); 0 = nur der Fahrplan der GTI
LOOKBACK = "-30d"
CACHE_S = 600

# Messung je Linienart (hvv_stop_event zusaetzlich fuer Ausfaelle)
# Busse (B): Bus 5 hat Halt fuer Halt Messwerte (hvv_bus_stop); alle anderen Buslinien (4, 234, ...) kennt nur die Abfahrtstafel
# ihres Erfassungs-Halts (hvv_stop_event, hvv_sweep_event), dort gelten die Werte dann fuer die ganze Linie.
MEASUREMENTS = {
    "S": ("hvv_network_stop",),
    "U": ("hvv_network_stop",),
    "X": ("hvv_xbus_stop",),
    "R": ("hvv_regional_stop",),
    "B": ("hvv_bus_stop", "hvv_stop_event", "hvv_sweep_event"),
}
CANCEL_MEASUREMENTS = ("hvv_stop_event", "hvv_sweep_event")  # Messpunkte der Linien bzw. Ausfall-Erfassung ueber viele Knoten (sweep.py)
WALK_TYPES = {"CHANGE", "FOOTPATH", "BICYCLE"}  # Elemente der GTI-Route, die keine Fahrt sind


def is_walk(simple_type: str | None) -> bool:
    """Fusswege und Umstiege der GTI: CHANGE, FOOTPATH, BICYCLE sowie Varianten wie CHANGE_SAME_PLATFORM (Umstieg am gleichen Bahnsteig)."""
    return bool(simple_type) and (simple_type in WALK_TYPES or simple_type.startswith(("CHANGE", "FOOT", "BICYCLE")))


def line_mode(name: str | None) -> str | None:
    """S, U, X (Xpressbus) oder R (RE/RB); alles andere hat keine Messreihe."""
    if not name:
        return None
    if re.match(r"^S\d", name):
        return "S"
    if re.match(r"^U\d", name):
        return "U"
    if re.match(r"^X\d", name):
        return "X"
    if re.match(r"^R[EB]\d", name):
        return "R"
    return None


def data_mode(name: str | None) -> str | None:
    """Art der Linie fuer die Messreihen: wie `line_mode`, zusaetzlich B fuer Buslinien (nur Ziffern, z. B. 4, 5, 234)."""
    return line_mode(name) or ("B" if name and re.match(r"^\d{1,3}$", name) else None)


# ---- Influx ----


def flux_str(value: str) -> str:
    """Flux-Zeichenkette; json.dumps waere falsch, weil es Umlaute als \\uXXXX schreibt (Flux kennt das nicht)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${") + '"'


class InfluxReader:
    def __init__(self, url: str, org: str, bucket: str, token: str, timeout: float = 20.0):
        self.url = f"{url.rstrip('/')}/api/v2/query?{urllib.parse.urlencode({'org': org})}"
        self.bucket, self.token, self.timeout = bucket, token, timeout

    def query(self, flux: str) -> list[dict[str, str]]:
        req = urllib.request.Request(
            self.url,
            data=flux.encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Token {self.token}", "Content-Type": "application/vnd.flux", "Accept": "application/csv"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OSError(f"Influx: {exc}") from exc
        rows: list[dict[str, str]] = []
        header: list[str] | None = None
        for record in csv.reader(io.StringIO(text)):
            if not record or record[0].startswith("#"):
                continue
            if "_value" in record and "result" in record:
                header = record
            elif header and len(record) == len(header):
                rows.append(dict(zip(header, record)))
        return rows

    def delays(self, measurement: str, line: str, station: str | None) -> list[tuple[float, float]]:
        """(Zeitstempel, Verspaetung in s) der letzten 30 Tage; ohne `station` ueber die ganze Linie (jede 10. Messung)."""
        filt = f'r._measurement == "{measurement}" and r._field == "delay_s" and r.line == {flux_str(line)}'
        if station:
            filt += f" and r.station == {flux_str(station)}"
        sample = "" if station else "|> group() |> sample(n: 10)"
        flux = (
            f'from(bucket: {flux_str(self.bucket)}) |> range(start: {LOOKBACK}) |> filter(fn: (r) => {filt}) {sample} '
            '|> keep(columns: ["_time", "_value"]) |> limit(n: 6000)'
        )
        out = []
        for row in self.query(flux):
            try:
                out.append((datetime.fromisoformat(row["_time"].replace("Z", "+00:00")).timestamp(), float(row["_value"])))
            except (KeyError, ValueError):
                continue
        return out

    def cancel_rate(self, line: str) -> tuple[int, float] | None:
        measurements = " or ".join(f'r._measurement == "{m}"' for m in CANCEL_MEASUREMENTS)
        flux = (
            f'from(bucket: {flux_str(self.bucket)}) |> range(start: {LOOKBACK}) '
            f'|> filter(fn: (r) => ({measurements}) and r._field == "cancelled" and r.line == {flux_str(line)}) '
            '|> keep(columns: ["_value"]) |> limit(n: 20000)'
        )
        values = [row["_value"].strip().lower() in ("1", "true") for row in self.query(flux) if "_value" in row]
        return (len(values), sum(values) / len(values)) if values else None


# ---- Statistik ----


def pick_window(samples: list[tuple[float, float]], when: datetime) -> tuple[list[float], str]:
    """Verspaetungen zur gleichen Tagesart (Werktag/Wochenende) und Uhrzeit (+-1 h), sonst gleiche Uhrzeit, sonst alle."""
    weekend = when.weekday() >= 5
    hour = when.hour

    def local(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, TZ)

    def near(ts: float) -> bool:
        diff = abs(local(ts).hour - hour)
        return min(diff, 24 - diff) <= 1

    tight = [v for ts, v in samples if near(ts) and (local(ts).weekday() >= 5) == weekend]
    if len(tight) >= MIN_SAMPLES:
        return tight, "gleiche Tagesart und Uhrzeit"
    hourly = [v for ts, v in samples if near(ts)]
    if len(hourly) >= MIN_SAMPLES:
        return hourly, "gleiche Uhrzeit"
    return [v for _, v in samples], "alle Zeiten"


class Reliability:
    """Verspaetungsverteilungen je (Linie, Halt) mit Zwischenspeicher."""

    def __init__(self, reader: InfluxReader | None, rng: random.Random | None = None):
        self.reader = reader
        self.rng = rng or random.Random()
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: tuple, fetch):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_S:
                return hit[1]
        value = fetch()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def distribution(self, line: str, station: str, when: datetime) -> dict | None:
        """{"values": [...], "n": int, "scope": str} der Verspaetungen (s) oder None ohne Messreihe."""
        mode = data_mode(line)
        if self.reader is None or mode is None:
            return None

        def collect(where: str | None) -> list[tuple[float, float]]:
            found: list[tuple[float, float]] = []
            for measurement in MEASUREMENTS[mode]:
                found += self._cached(("d", measurement, line, where), lambda m=measurement: self.reader.delays(m, line, where))
            return found

        samples = collect(station)
        scope = "Halt"
        if len(samples) < MIN_SAMPLES:
            samples = collect(None)
            scope = "ganze Linie"
        if not samples:
            return None
        values, window = pick_window(samples, when)
        return {"values": values, "n": len(values), "scope": scope, "window": window}

    def cancel_rate(self, line: str) -> dict | None:
        if self.reader is None or data_mode(line) is None:
            return None
        found = self._cached(("c", line), lambda: self.reader.cancel_rate(line))
        return {"n": found[0], "rate": found[1]} if found else None

    def assess(self, legs: list[dict], when: datetime) -> dict:
        """Zuverlaessigkeit einer Verbindung aus ihren Fahrtabschnitten (`legs` wie von `normalize_route`)."""
        rides = [leg for leg in legs if leg.get("line")]
        info = []
        for leg in rides:
            board = self.distribution(leg["line"], leg["from"]["name"], when)
            alight = self.distribution(leg["line"], leg["to"]["name"], when)
            cancel = self.cancel_rate(leg["line"])
            info.append((board, alight, cancel))
            leg["stats"] = {
                "board": _summary(board),
                "alight": _summary(alight),
                "cancelRate": None if cancel is None else round(cancel["rate"] * 100, 1),
                "cancelN": None if cancel is None else cancel["n"],
            }
        known = [i for i, (b, a, _) in enumerate(info) if a is not None]
        result: dict = {"legsWithData": len(known), "legs": len(rides)}
        n_transfers = len(rides) - 1
        full = bool(rides) and len(known) == len(rides)
        # Je Mindest-Umsteigezeit T (Minuten): Anteil der Simulationen, in denen alle bzw. der einzelne Anschluss erreicht wird.
        # Alle T teilen sich dieselben Zufallsziehungen, damit die Kurve ueber T gleichmaessig faellt.
        all_ok = {t: 0 for t in TRANSFER_STEPS}
        all_ok_ontime = {t: 0 for t in TRANSFER_STEPS}
        each_ok = {(i, t): 0 for i in range(1, len(rides)) for t in TRANSFER_STEPS}
        slack = {}  # (i, t) -> Reserve in s: Zeit zwischen Ankunft und Abfahrt abzueglich Fussweg bzw. Mindest-Umsteigezeit
        times_known = all(rides[i]["dep"] is not None and rides[i - 1]["arr"] is not None for i in range(1, len(rides)))
        for i in range(1, len(rides) if times_known else 1):
            buffer = rides[i]["dep"] - rides[i - 1]["arr"]
            walk_s = (rides[i].get("transfer") or {}).get("walkMin", 0) * 60
            for t in TRANSFER_STEPS:
                slack[(i, t)] = buffer - max(walk_s, t * 60)
        if rides and times_known and (full or any(info[i - 1][1] is not None for i in range(1, len(rides)))):
            for _ in range(SIMULATIONS):
                board_d = [self.rng.choice(b["values"]) if b else 0.0 for b, _, _ in info]
                alight_d = [self.rng.choice(a["values"]) if a else None for _, a, _ in info]
                for t in TRANSFER_STEPS:
                    ok_all = True
                    for i in range(1, len(rides)):
                        feeder = alight_d[i - 1]
                        # verspaetete Zuege ziehen die Reserve auf: 1 min Reserve und 1 min Verspaetung = verpasst
                        if feeder is not None and feeder - board_d[i] < slack[(i, t)]:
                            each_ok[(i, t)] += 1
                        else:
                            ok_all = False
                    if ok_all and full:
                        all_ok[t] += 1
                        all_ok_ontime[t] += alight_d[-1] <= ONTIME_S
            if full:
                by_min = {str(t): {"connectionPct": round(100 * all_ok[t] / SIMULATIONS, 1), "ontimePct": round(100 * all_ok_ontime[t] / SIMULATIONS, 1)} for t in TRANSFER_STEPS}
                result["byMinTransfer"] = by_min
                result["connectionPct"] = by_min["0"]["connectionPct"]  # ohne zusaetzliche Mindestzeit
                result["ontimePct"] = by_min["0"]["ontimePct"]
                result["transfers"] = n_transfers
            for i in range(1, len(rides)):
                transfer = rides[i].get("transfer")
                if transfer is not None and info[i - 1][1] is not None:
                    transfer["catchByMin"] = {str(t): round(100 * each_ok[(i, t)] / SIMULATIONS, 1) for t in TRANSFER_STEPS}
        cancels = [c for _, _, c in info]
        known_cancel = [c for c in cancels if c is not None]
        if known_cancel:
            survive = 1.0
            for c in known_cancel:
                survive *= 1 - c["rate"]
            result["cancelPct"] = round(100 * (1 - survive), 1)
            result["cancelCoverage"] = f"{len(known_cancel)} von {len(rides)} Abschnitten"
            # Abschnitte ohne Ausfallmessung, damit die Oberflaeche sagen kann, welche es sind
            result["cancelMissing"] = [f"{leg['line']} ({leg['from']['name']} → {leg['to']['name']})" for leg, (_, _, c) in zip(rides, info) if c is None]
        return result


def _summary(dist: dict | None) -> dict | None:
    if dist is None:
        return None
    values = dist["values"]
    late = sum(v > ONTIME_S for v in values)
    return {
        "n": dist["n"],
        "ontimePct": round(100 * (1 - late / len(values)), 1),
        "avgMin": round(sum(values) / len(values) / 60, 1),
        "scope": dist["scope"],
        "window": dist["window"],
    }


# ---- GTI: Stationen, Abfahrten, Routen ----


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def gti_epoch(stamp: dict | None) -> float | None:
    """{"date": "07.11.2026", "time": "13:23"} (Ortszeit) -> Epoche"""
    if not stamp or "date" not in stamp or "time" not in stamp:
        return None
    try:
        return datetime.strptime(f"{stamp['date']} {stamp['time']}", "%d.%m.%Y %H:%M").replace(tzinfo=TZ).timestamp()
    except ValueError:
        return None


def mode_of(line: dict) -> str:
    """S, U, R, X oder Bus/Faehre/... fuer die Anzeige."""
    name = line.get("name") or ""
    found = line_mode(name)
    if found:
        return found
    simple = (line.get("type") or {}).get("simpleType") or ""
    return {"TRAIN": "Z", "BUS": "B", "SHIP": "F"}.get(simple, "?")


STATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
PLACE_TYPES = ("STATION", "ADDRESS", "POI", "COORDINATE")
NEAR_STATION_M = 400  # weiter weg ist eine Haltestelle keine sinnvolle Ortsangabe fuer den Standort
PLACE_CACHE_S = 3600
PLACE_CACHE_MAX = 500
COURSE_TTL_S = 30
LON_RANGE, LAT_RANGE = (5.0, 16.0), (47.0, 56.0)  # Deutschland samt Rand; alles andere kann der HVV-Dienst ohnehin nicht routen


def parse_place(raw: str) -> dict:
    """Start/Ziel aus dem Abfrageparameter: eine Haltestellen-ID oder ein JSON-Ort (Adresse, POI, Standort) wie von `places` geliefert.
    Fuer Adressen verlangt die GTI Name, Ort und Koordinate; eine nackte ID waehlt sonst eine gleichnamige Strasse. Wirft ValueError."""
    raw = (raw or "").strip()
    if STATION_ID.match(raw):
        return {"id": raw, "type": "STATION"}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("ungueltiger Ort") from exc
    if not isinstance(data, dict) or data.get("type") not in PLACE_TYPES:
        raise ValueError("ungueltiger Ort")
    place: dict = {"type": data["type"]}
    for key, limit in (("id", 40), ("name", 120), ("city", 80)):
        value = data.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > limit or (key == "id" and not STATION_ID.match(value)):
            raise ValueError("ungueltiger Ort")
        place[key] = value
    try:
        x, y = float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError) as exc:
        if place["type"] != "STATION":
            raise ValueError("ungueltiger Ort") from exc
    else:
        if not (LON_RANGE[0] <= x <= LON_RANGE[1] and LAT_RANGE[0] <= y <= LAT_RANGE[1]):
            raise ValueError("ungueltiger Ort")
        place["coordinate"] = {"x": x, "y": y}
    if place["type"] == "STATION" and "id" not in place:
        raise ValueError("ungueltiger Ort")
    if place["type"] == "COORDINATE":
        place.pop("id", None)
        place.setdefault("name", "Mein Standort")
    return place


class Planner:
    def __init__(self, client: GtiClient, reliability: Reliability):
        self.client = client
        self.reliability = reliability
        self._departures: dict[str, tuple[float, dict]] = {}
        self._places: dict[str, tuple[float, list[dict]]] = {}
        self._courses: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self.shaper = None  # Callable(leg) -> flache Koordinatenliste entlang der Gleise; setzt der Server, ohne bleibt die Karte gerade

    def _shape(self, legs: list[dict]) -> None:
        """Fahrten mit dem Verlauf fuer die Routenkarte versehen (Schluessel "shape")."""
        for leg in legs:
            if leg.get("shape"):
                continue  # die GTI hat den Verlauf geliefert (withPaths), auch fuer Fusswege
            if self.shaper and leg.get("line"):
                try:
                    shape = self.shaper(leg)
                except Exception as exc:  # die Karte ist Beiwerk, die Verbindung darf daran nicht scheitern
                    log.warning("Streckenverlauf nicht berechnet: %s", exc)
                    shape = []
                if len(shape) >= 4:
                    leg["shape"] = shape

    def places(self, query: str, limit: int = 8) -> list[dict]:
        """Haltestellen, Adressen und Sehenswuerdigkeiten zu einer Eingabe (z. B. "Bergedorfer Str. 5"); das Ergebnis geht unveraendert
        als Start/Ziel zurueck an `route`. Antworten werden eine Stunde gemerkt: Tippen soll das GTI-Konto nicht aufbrauchen."""
        key = " ".join(query.lower().split())
        with self._lock:
            hit = self._places.get(key)
            if hit and time.monotonic() - hit[0] < PLACE_CACHE_S:
                return hit[1]
        out, seen = [], set()
        for item in self.client.check_name(query, limit * 2, kind="UNKNOWN"):
            kind = item.get("type")
            coordinate = item.get("coordinate") or {}
            if kind not in ("STATION", "ADDRESS", "POI") or not item.get("id") or (item.get("id"), kind) in seen:
                continue
            if kind != "STATION" and "x" not in coordinate:
                continue  # ohne Koordinate laesst sich eine Adresse nicht routen
            seen.add((item["id"], kind))
            out.append(
                {
                    "id": item["id"],
                    "type": kind,
                    "name": item.get("name") or item.get("combinedName"),
                    "city": item.get("city"),
                    "label": item.get("combinedName") or item.get("name"),
                    "x": coordinate.get("x"),
                    "y": coordinate.get("y"),
                }
            )
            if len(out) >= limit:
                break
        with self._lock:
            if len(self._places) >= PLACE_CACHE_MAX:
                self._places.clear()
            self._places[key] = (time.monotonic(), out)
        return out

    def reverse(self, x: float, y: float) -> dict | None:
        """Beschriftung zu einer Koordinate: die naechste Adresse (GTI checkName, Typ COORDINATE), sonst "Nahe <Haltestelle>" (Typ STATION
        mit Koordinate liefert die naechsten Haltestellen samt Entfernung); None, wenn beides nichts hergibt. Die Position geht nur an
        die GTI, die den Planer ohnehin bedient, an keinen Dritten."""
        if not (LON_RANGE[0] <= x <= LON_RANGE[1] and LAT_RANGE[0] <= y <= LAT_RANGE[1]):
            raise ValueError("Koordinate")
        where = {"coordinate": {"x": x, "y": y}, "name": ""}
        data = self.client.post("checkName", {"theName": {**where, "type": "COORDINATE"}, "maxList": 1})
        for item in data.get("results") or []:
            name, city = (item.get("name") or "").strip(), (item.get("city") or "").strip()
            if name:
                return {"name": name, "city": city, "kind": "address", "label": f"{name}, {city}" if city and city not in name else name}
        data = self.client.post("checkName", {"theName": {**where, "type": "STATION"}, "maxList": 3})
        near = [i for i in data.get("results") or [] if i.get("name") and (i.get("distance") is None or i["distance"] <= NEAR_STATION_M)]
        if near:
            best = min(near, key=lambda i: i.get("distance", 0))
            name, city = best["name"].strip(), (best.get("city") or "").strip()
            return {"name": name, "city": city, "kind": "station", "label": f"Nahe {name}, {city}" if city and city not in name else f"Nahe {name}"}
        return None

    def stations(self, query: str, limit: int = 8) -> list[dict]:
        out = []
        for item in self.client.check_name(query, limit * 2):
            if item.get("type") != "STATION" or not item.get("id"):
                continue
            out.append({"id": item["id"], "name": item.get("name") or item.get("combinedName"), "city": item.get("city"), "label": item.get("combinedName") or item.get("name")})
            if len(out) >= limit:
                break
        return out

    def departures(self, station_id: str, count: int = 20, window_min: int = 60) -> dict:
        key = f"{station_id}|{count}"
        with self._lock:
            hit = self._departures.get(key)
            if hit and time.monotonic() - hit[0] < 15:
                return hit[1]
        reference = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        response = self.client.departure_list(
            {"id": station_id, "type": "STATION"}, reference, max_list=count + 10, max_time_offset=window_min, departure=True
        )
        out = []
        for dep in response.get("departures", []):
            line = dep.get("line") or {}
            planned = reference.timestamp() + int(dep.get("timeOffset", 0)) * 60
            delay = dep.get("delay")
            out.append(
                {
                    "line": line.get("name") or "?",
                    "mode": mode_of(line),
                    "type": (line.get("type") or {}).get("shortInfo") or "",
                    "direction": line.get("direction") or "",
                    "planned": planned,
                    "delayMin": None if delay is None else round(delay / 60),
                    "cancelled": bool(dep.get("cancelled")),
                    "extra": bool(dep.get("extra")),
                    "platform": (dep.get("stopPoint") or {}).get("name") or "",
                }
            )
        out.sort(key=lambda d: d["planned"] + (d["delayMin"] or 0) * 60)
        data = {
            "now": time.time(),
            "departures": [d for d in out if d["planned"] + (d["delayMin"] or 0) * 60 >= time.time() - 60][:count],
        }
        with self._lock:
            self._departures[key] = (time.monotonic(), data)
        return data

    def _schedules(self, start: str | dict, dest: str | dict, when: datetime, arrive: bool = False, count: int = 3) -> tuple[dict, list[dict]]:
        local = when.astimezone(TZ)
        as_place = lambda p: {"id": p, "type": "STATION"} if isinstance(p, str) else p
        response = self.client.post(
            "getRoute",
            {
                "start": as_place(start),
                "dest": as_place(dest),
                "time": {"date": local.strftime("%d.%m.%Y"), "time": local.strftime("%H:%M")},
                "timeIsDeparture": not arrive,
                "schedulesBefore": count - 1 if arrive else 0,
                "schedulesAfter": 0 if arrive else count - 1,
                "realtime": "REALTIME",
                "intermediateStops": True,
                "withPaths": True,  # Verlauf je Abschnitt (Strassen fuer Fusswege, Gleise/Strassen fuer Fahrten) fuer die Routenkarte
            },
        )
        return response, response.get("realtimeSchedules") or response.get("schedules") or []

    def route(self, start: str | dict, dest: str | dict, when: datetime, arrive: bool = False, count: int = 3) -> dict:
        """`start`/`dest`: Haltestellen-ID oder Ort (`parse_place`)."""
        local = when.astimezone(TZ)
        response, schedules = self._schedules(start, dest, when, arrive, count)
        routes = []
        for schedule in schedules[:count]:
            legs = normalize_route(schedule)
            self._shape(legs)
            dep = parse_iso(schedule.get("plannedDepartureTime"))
            try:
                assessed = self.reliability.assess(legs, dep or local)
            except OSError as exc:  # Verbindungen gibt es auch ohne Messdaten
                log.warning("Zuverlaessigkeit nicht berechnet: %s", exc)
                assessed = {"legsWithData": 0, "legs": sum(1 for leg in legs if leg.get("line")), "unavailable": True}
            real_dep, real_arr = parse_iso(schedule.get("realDepartureTime")), parse_iso(schedule.get("realArrivalTime"))
            arr = parse_iso(schedule.get("plannedArrivalTime"))
            routes.append(
                {
                    "plannedDep": dep.timestamp() if dep else None,
                    "plannedArr": arr.timestamp() if arr else None,
                    "realDep": real_dep.timestamp() if real_dep else None,
                    "realArr": real_arr.timestamp() if real_arr else None,
                    "minutes": schedule.get("time"),
                    "footpathMin": schedule.get("footpathTime"),
                    "tickets": [
                        {"price": t.get("price"), "name": t.get("type"), "level": t.get("level")}
                        for t in (schedule.get("tickets") or [])[:2]
                        if t.get("price") is not None
                    ],
                    "legs": legs,
                    "reliability": assessed,
                }
            )
        return {"routes": routes, "returnCode": response.get("returnCode")}

    # ---- Andere Abfahrt derselben Linie waehlen ("Auch in 28 min und 48 min") ----

    def board(self, station_id: str) -> list[dict]:
        """Abfahrtstafel der naechsten 2 Stunden mit allen Linien, 20 s lang gemerkt (mehrere Fahrtabschnitte teilen sich eine Tafel)."""
        key = f"board|{station_id}"
        with self._lock:
            hit = self._departures.get(key)
            if hit and time.monotonic() - hit[0] < 20:
                return hit[1]["list"]
        reference = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        response = self.client.departure_list({"id": station_id, "type": "STATION"}, reference, max_list=150, max_time_offset=120, departure=True)
        out = []
        for dep in response.get("departures", []):
            line = dep.get("line") or {}
            delay = dep.get("delay")
            out.append(
                {
                    "line": line.get("name") or "?",
                    "direction": line.get("direction") or "",
                    "planned": reference.timestamp() + int(dep.get("timeOffset", 0)) * 60,
                    "delayMin": None if delay is None else round(delay / 60),
                    "cancelled": bool(dep.get("cancelled")),
                    "platform": (dep.get("stopPoint") or {}).get("name") or "",
                }
            )
        with self._lock:
            if len(self._departures) >= PLACE_CACHE_MAX:
                self._departures.clear()
            self._departures[key] = (time.monotonic(), {"list": out})
        return out

    def course(self, line_id: str, station_id: str, when: float, direction: str, service_id: int | None) -> dict:
        """Ganzer Fahrtverlauf (alle Halte von der Starthaltestelle bis zum Ziel der Linie) der Fahrt, die `when` (Plan-Epoche) an `station_id` abfaehrt."""
        key = f"{service_id or ''}|{line_id}|{station_id}|{int(when) // 60}|{direction}"
        with self._lock:
            hit = self._courses.get(key)
        if hit and time.monotonic() - hit[0] < COURSE_TTL_S:
            return hit[1]
        payload: dict = {
            "station": {"id": station_id, "type": "STATION"},
            "time": datetime.fromtimestamp(when, TZ).strftime("%Y-%m-%dT%H:%M:00.000%z"),
            "direction": direction,
            "segments": "ALL",
        }
        payload["lineId" if line_id.startswith("de:") else "lineKey"] = line_id  # DTID (de:hvv:S1:) oder voller Line Key
        if service_id:
            payload["serviceId"] = service_id  # ueberschreibt Zeit und Richtung
        data = normalize_course(self.client.post("departureCourse", payload))
        with self._lock:
            if len(self._courses) >= PLACE_CACHE_MAX:
                self._courses.clear()
            self._courses[key] = (time.monotonic(), data)
        return data

    def alternatives(self, station_id: str, line: str, direction: str, not_before: float, limit: int = 5) -> dict:
        """Weitere Abfahrten derselben Linie in dieselbe Richtung ab `not_before` (Epoche, nach Echtzeit), ohne Ausfaelle."""
        found = []
        for dep in self.board(station_id):
            real = dep["planned"] + (dep["delayMin"] or 0) * 60
            if dep["line"] == line and dep["direction"] == direction and not dep["cancelled"] and real >= not_before - 30:
                found.append({**dep, "real": real})
        found.sort(key=lambda d: d["real"])
        return {"now": time.time(), "alternatives": found[:limit]}

    def reroute(self, prefix: list[dict], leg: dict, planned: float, dest: str | dict, tail: bool) -> dict:
        """Verbindung mit einer anderen Abfahrt des Fahrtabschnitts `leg`: `prefix` (Abschnitte davor) bleibt, die Fahrt wird durch die
        Abfahrt `planned` (Plan-Epoche) ersetzt und der Rest bis `dest` neu gesucht (nur wenn nach `leg` noch etwas kommt: `tail`)."""
        start_id, end_id = (leg.get("from") or {}).get("id"), (leg.get("to") or {}).get("id")
        if not start_id or not end_id or not leg.get("line"):
            raise ValueError("ungueltiger Abschnitt")
        _, schedules = self._schedules(start_id, end_id, datetime.fromtimestamp(planned - 60, TZ), False, 4)
        ride = None
        for schedule in schedules:
            for candidate in normalize_route(schedule):
                if candidate.get("line") == leg["line"] and candidate.get("dep") is not None and abs(candidate["dep"] - planned) <= 90:
                    ride = candidate
                    break
            if ride:
                break
        if ride is None:
            raise LookupError("Diese Abfahrt ist nicht mehr verfügbar.")
        legs = [*prefix, ride]
        if tail:
            arrival = (ride.get("arr") or planned) + (ride.get("arrDelayMin") or 0) * 60 + 120  # 2 min zum Aussteigen und Umsteigen
            _, onward = self._schedules(end_id, dest, datetime.fromtimestamp(arrival, TZ), False, 1)
            if not onward:
                raise LookupError("Für diese Abfahrt gibt es keine Weiterfahrt.")
            legs += normalize_route(onward[0])
        for item in legs:
            item.pop("transfer", None)
            item.pop("stats", None)
        self._shape(legs)
        annotate_transfers(legs)
        first, last = legs[0], legs[-1]
        rides = [item for item in legs if item.get("line")]
        planned_dep, planned_arr = first["dep"], last["arr"]
        real_dep = planned_dep + ((first.get("depDelayMin") or 0) * 60 if first.get("line") else 0)
        real_arr = planned_arr + (((rides[-1].get("arrDelayMin") or 0) * 60) if rides else 0)
        try:
            assessed = self.reliability.assess(legs, datetime.fromtimestamp(planned_dep, TZ))
        except OSError as exc:
            log.warning("Zuverlaessigkeit nicht berechnet: %s", exc)
            assessed = {"legsWithData": 0, "legs": len(rides), "unavailable": True}
        return {
            "plannedDep": planned_dep,
            "plannedArr": planned_arr,
            "realDep": real_dep,
            "realArr": real_arr,
            "minutes": round((real_arr - real_dep) / 60),
            "footpathMin": None,
            "tickets": [],
            "legs": legs,
            "reliability": assessed,
        }


def normalize_course(response: dict) -> dict:
    """departureCourse -> {"stops": [{name, id, arr, arrDelayMin, dep, depDelayMin, platform, cancelled, extra, x, y}]}; Epochen, Verspaetung in Minuten."""
    delay = lambda seconds: None if seconds is None else round(seconds / 60)
    stamp = lambda value: (parse_iso(value).timestamp() if parse_iso(value) else None)
    elements = response.get("courseElements") or []
    stops = []
    for i, element in enumerate(elements):
        here, before = element.get("fromStation") or {}, elements[i - 1] if i else {}
        stops.append(
            {
                "name": here.get("name"),
                "id": here.get("id"),
                "arr": stamp(before.get("arrTime")),
                "arrDelayMin": delay(before.get("arrDelay")),
                "dep": stamp(element.get("depTime")),
                "depDelayMin": delay(element.get("depDelay")),
                "platform": element.get("fromRealtimePlatform") or element.get("fromPlatform") or "",
                "cancelled": bool(element.get("fromCancelled")),
                "extra": bool(element.get("fromExtra")),
                **_xy(here),
            }
        )
    if elements:
        last, end = elements[-1], elements[-1].get("toStation") or {}
        stops.append(
            {
                "name": end.get("name"),
                "id": end.get("id"),
                "arr": stamp(last.get("arrTime")),
                "arrDelayMin": delay(last.get("arrDelay")),
                "dep": None,
                "depDelayMin": None,
                "platform": last.get("toRealtimePlatform") or last.get("toPlatform") or "",
                "cancelled": bool(last.get("toCancelled")),
                "extra": bool(last.get("toExtra")),
                **_xy(end),
            }
        )
    return {"stops": stops}


def _xy(place: dict) -> dict:
    """Laengen-/Breitengrad einer GTI-Haltestelle als {"x", "y"} fuer die Kartenansicht; leer, wenn die GTI keine Koordinate liefert."""
    coordinate = place.get("coordinate") or {}
    if isinstance(coordinate.get("x"), (int, float)) and isinstance(coordinate.get("y"), (int, float)):
        return {"x": coordinate["x"], "y": coordinate["y"]}
    return {}


def _path_shape(element: dict) -> list[float]:
    """Verlauf eines Abschnitts aus den GTI-Pfaden (`paths[].track[]`) als flache Liste [x, y, x, y, ...]; leer, wenn die GTI keine liefert."""
    out: list[float] = []
    for path in element.get("paths") or []:
        for point in path.get("track") or []:
            x, y = point.get("x"), point.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            x, y = round(x, 5), round(y, 5)
            if len(out) >= 2 and out[-2] == x and out[-1] == y:
                continue
            out.extend((x, y))
    return out if len(out) >= 4 else []


def normalize_route(schedule: dict) -> list[dict]:
    legs = []
    for element in schedule.get("scheduleElements", []):
        src, dst = element.get("from") or {}, element.get("to") or {}
        line = element.get("line") or {}
        dep, arr = gti_epoch(src.get("depTime")), gti_epoch(dst.get("arrTime"))
        base = {
            "from": {"name": src.get("name"), "id": src.get("id"), "platform": src.get("realtimePlatform") or src.get("platform") or "", **_xy(src)},
            "to": {"name": dst.get("name"), "id": dst.get("id"), "platform": dst.get("realtimePlatform") or dst.get("platform") or "", **_xy(dst)},
            "dep": dep,
            "arr": arr,
            **({"shape": shape} if (shape := _path_shape(element)) else {}),
        }
        walking = is_walk((line.get("type") or {}).get("simpleType"))  # Umstiegsfussweg u. a. kommen als "Linie" an
        if line.get("name") and not walking:
            legs.append(
                {
                    **base,
                    "line": line["name"],
                    "mode": mode_of(line),
                    "direction": line.get("direction") or "",
                    "depDelayMin": None if src.get("depDelay") is None else round(src["depDelay"] / 60),
                    "arrDelayMin": None if dst.get("arrDelay") is None else round(dst["arrDelay"] / 60),
                    "cancelled": bool(element.get("cancelled")),
                    "serviceId": element.get("serviceId"),  # Fahrt-ID: macht aus Linie + Richtung + Zeit eine eindeutige Fahrt (Fahrtverlauf)
                    "lineId": line.get("dlid") or line.get("id") or "",
                    **ride_details(element, line, dep, arr),
                }
            )
        elif dep is not None and arr is not None:
            legs.append({**base, "line": None, "walkMin": round((arr - dep) / 60)})
    annotate_transfers(legs)
    return legs


def ride_details(element: dict, line: dict, dep: float | None, arr: float | None) -> dict:
    """Zusatzangaben einer Fahrt fuer die Detailansicht: Zwischenhalte, Fahrzeugart, Betreiber, Hinweise."""
    stops = []
    for stop in element.get("intermediateStops") or []:
        when = gti_epoch(stop.get("depTime")) or gti_epoch(stop.get("arrTime"))
        delay = stop.get("depDelay", stop.get("arrDelay"))
        stops.append({"name": stop.get("name"), "time": when, "delayMin": None if delay is None else round(delay / 60), **_xy(stop)})
    kind = line.get("type") or {}
    seen: list[str] = []
    info: list[dict] = []
    ride_stops = [(element.get("from") or {}).get("name"), *[s["name"] for s in stops], (element.get("to") or {}).get("name")]
    for note in element.get("announcements") or []:
        text = (note.get("summary") or "").strip()
        if text and text not in seen and notice_applies(text, ride_stops):
            seen.append(text)
            info.append(notice_info(note, text))
    return {
        "stops": stops,
        "stopCount": len(stops) + 1,
        "durationMin": None if dep is None or arr is None else round((arr - dep) / 60),
        "vehicle": kind.get("longInfo") or kind.get("shortInfo") or "",
        "carrier": line.get("carrierNameShort") or "",
        "origin": line.get("origin") or "",
        "notices": seen[:3],
        "noticeInfo": info[:3],
    }


SEGMENT = re.compile(r"\bzw(?:\.|ischen)\s+(.+?)\s+(?:u\.|und)\s+(.+?)\s*$", re.I)


def _same_stop(name: str | None, wanted: str) -> bool:
    norm = lambda text: re.sub(r"[^\w]", "", (text or "").casefold())
    a, b = norm(name), norm(wanted)
    return len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a))


ARROW = re.compile(r"^(?:[\w/+-]{1,6}:\s*)?(.+?)\s*(?:->|→|=>)\s*(.+)$")


def notice_applies(summary: str, ride_stops: list[str | None]) -> bool:
    """Die GTI haengt Streckenmeldungen an jede Fahrt der Linie. "... zw. A u. B" und "S7: A -> B ..." gelten aber nur,
    wenn die Fahrt beide Orte passiert (bei "->" steht hinter B noch Text, daher zaehlen die ersten 1-3 Woerter)."""
    passes = lambda place: any(_same_stop(stop, place) for stop in ride_stops)
    match = SEGMENT.search(summary)
    if match:
        return all(passes(place) for place in match.groups())
    arrow = ARROW.match(summary)
    if arrow:
        words = arrow.group(2).split()
        return passes(arrow.group(1)) and any(passes(" ".join(words[:n])) for n in (1, 2, 3) if len(words) >= n)
    return True


def notice_info(note: dict, summary: str) -> dict:
    """Eine Meldung der Fahrt mit allem, was der HVV auf seiner Seite unter "Details anzeigen" zeigt."""
    parse = lambda value: (lambda t: None if t is None else t.timestamp())(_iso(value))
    window = (note.get("validities") or [note.get("publication") or {}])[0]
    return {
        "summary": summary,
        "text": (note.get("description") or "").strip(),
        "from": parse(window.get("begin")),
        "to": parse(window.get("end")),
        "modified": parse(note.get("lastModified")),
        "links": [{"label": link.get("label") or link["url"], "url": link["url"]} for link in note.get("links") or [] if str(link.get("url", "")).startswith("https://")][:3],
    }


def _iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def annotate_transfers(legs: list[dict]) -> None:
    """Haengt an jede Fahrt nach der ersten ein `transfer`: Station, Zeit bis zur Abfahrt, Ankunfts- und Abfahrtsgleis, Fussweg."""
    previous = None
    walk_s = 0
    for leg in legs:
        if not leg.get("line"):
            walk_s += (leg["arr"] - leg["dep"]) if leg.get("arr") is not None and leg.get("dep") is not None else 0
            continue
        if previous is not None and previous.get("arr") is not None and leg.get("dep") is not None:
            wait = leg["dep"] - previous["arr"]
            leg["transfer"] = {
                "station": previous["to"]["name"],
                "toStation": leg["from"]["name"],
                "waitMin": round(wait / 60),
                "walkMin": round(walk_s / 60),
                "arrPlatform": previous["to"]["platform"],
                "depPlatform": leg["from"]["platform"],
                "arr": previous["arr"],
            }
        previous, walk_s = leg, 0
