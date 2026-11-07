"""Webserver fuer die Live-Karte: statische Seite, Fahrzeugdaten aus getVehicleMap, Basiskarte (PMTiles) und Linien-Logos.

Alle Betrachter teilen sich einen Cache: hoechstens ein getVehicleMap-Aufruf pro `refresh_s`, egal wie viele Browser
offen sind. Die Seite interpoliert die Zugpositionen selbst aus den Segmentzeiten (inkl. Verspaetung) und der Gleisgeometrie.
"""

import gzip
import ipaddress
import json
import logging
import math
import mimetypes
import re
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from .client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .icons import CONTENT_TYPES, IconStore
from .geometry import clean
from .signals import SignalIndex

log = logging.getLogger("hvv_gti.web")

MODES = {"S_BAHN": "S", "U_BAHN": "U", "R_BAHN": "R", "XPRESSBUS": "X"}
VEHICLE_TYPES = {mode: vehicle_type for vehicle_type, mode in MODES.items()}
COURSE_WINDOW_S = 5400  # Halteverlauf: je 90 min zurueck und voraus, das deckt auch den RE5 Cuxhaven - Hamburg ab
COURSE_CACHE_S = 60
TRACKS_SAVE_INTERVAL_S = 300
KX_M = math.cos(math.radians(53.55)) * 111320  # Meter je Grad Laenge/Breite bei Hamburg
KY_M = 110540
FIRST_FILL_WINDOW_S = 3900  # einmalig groesseres Fenster (> Takt des RE5), damit die Linien sofort komplett gezeichnet werden
REGIONAL_LINE = re.compile(r"^R[EB]\d")  # von den Regionalzuegen (R_BAHN) werden alle RE- und RB-Linien angezeigt


class TripIds:
    """Stabile Fahrt-IDs fuer die Karte.

    Die journeyID der GTI zaehlt Fahrten relativ zum Abrufzeitfenster ("Fahrt 0", "sdIndex 3") und wechselt von Abruf zu Abruf, teils
    zwischen hintereinanderfahrenden Zuegen; ausserdem liefert sie manche Fahrten doppelt. Eine Fahrt erkennt man an ihren Abschnitten:
    Linie, Haltepunktpaar und planmaessige Startzeit (Startzeit minus Verspaetung) aendern sich zwischen den Abrufen nicht.
    """

    KEEP_S = 7200

    def __init__(self):
        self._index: dict[tuple, str] = {}
        self._counter = 0

    @staticmethod
    def keys(line: str, segments: list[dict]) -> list[tuple]:
        return [
            (line, f"{s['startStopPointKey']}>{s['endStopPointKey']}", (s["startDateTime"] - 60 * (s.get("realtimeDelay") or 0)) // 60)
            for s in segments
        ]

    def resolve(self, trips: list[tuple[str, list[dict]]], now: int) -> list[str | None]:
        """Eine ID je Fahrt (Linie, Segmente); None fuer Dubletten, die im selben Abruf schon vergeben wurden."""
        index = {k: v for k, v in self._index.items() if k[2] * 60 >= now - self.KEEP_S}
        taken: set[str] = set()
        result: list[str | None] = []
        for line, segments in trips:
            keys = self.keys(line, segments)
            votes: dict[str, int] = {}
            for key in keys:
                uid = index.get(key)
                if uid is not None:
                    votes[uid] = votes.get(uid, 0) + 1
            uid = max(votes, key=votes.get) if votes else None
            if uid in taken:
                result.append(None)  # dieselbe Fahrt unter zweiter journeyID
                continue
            if uid is None:
                self._counter += 1
                uid = f"t{self._counter}"
            taken.add(uid)
            for key in keys:
                index[key] = uid
            result.append(uid)
        self._index = index
        return result

    def match(self, uid: str, line: str, segments: list[dict]) -> int:
        """Wie viele Abschnitte der Fahrt gehoeren zur ID (zum Wiederfinden in einem anderen Abruf)?"""
        return sum(1 for key in self.keys(line, segments) if self._index.get(key) == uid)


def _stop(name: str, arrival: tuple[int, int | None] | None = None, departure: tuple[int, int | None] | None = None) -> dict:
    """Halt mit Ankunft (a, Verspaetung ad) und Abfahrt (t, Verspaetung d); Zeiten enthalten die Verspaetung, Minuten, None = unbekannt."""
    out: dict = {"n": name, "a": None, "ad": None, "t": None, "d": None}
    if arrival:
        out["a"], out["ad"] = int(arrival[0]), None if arrival[1] is None else int(arrival[1])
    if departure:
        out["t"], out["d"] = int(departure[0]), None if departure[1] is None else int(departure[1])
    return out


def build_course(journey: dict) -> dict | None:
    """Halteverlauf einer Fahrt aus den Segmenten: je Halt Ankunft und Abfahrt inkl. Verspaetung in Minuten."""
    segs = sorted(journey.get("segments", []), key=lambda s: s["startDateTime"])
    if not segs:
        return None
    stops: list[dict] = []
    for i, seg in enumerate(segs):
        prev = segs[i - 1] if i else None
        arrival = (prev["endDateTime"], prev.get("realtimeDelay")) if prev else None
        if prev is not None and prev.get("endStationKey") != seg.get("startStationKey"):
            stops.append(_stop(prev["endStationName"], arrival=arrival))  # Luecke in den Segmenten: der Halt dazwischen fehlt
            arrival = None
        stops.append(_stop(seg["startStationName"], arrival=arrival, departure=(seg["startDateTime"], seg.get("realtimeDelay"))))
    last = segs[-1]
    stops.append(_stop(last["endStationName"], arrival=(last["endDateTime"], last.get("realtimeDelay"))))
    line = journey.get("line", {})
    first = segs[0]
    # Planabfahrt am Ursprung nur, wenn die Fahrt dort wirklich beginnt (sonst waere es irgendein Zwischenhalt)
    planned = (first["startDateTime"] - 60 * (first.get("realtimeDelay") or 0)) // 60 * 60 if first.get("isFirst") else None
    return {
        "l": line.get("name") or "?",
        "dir": line.get("direction"),
        "o": line.get("origin") or stops[0]["n"],
        "dep": planned,
        "rt": bool(journey.get("realtime")),
        "stops": stops,
    }


class VehicleFeed:
    def __init__(
        self,
        client: GtiClient,
        data_dir: Path,
        *,
        refresh_s: float = 30.0,
        window_s: int = 300,
        bbox: tuple[float, float, float, float] = (8.45, 53.10, 10.95, 54.05),
        vehicle_types: tuple[str, ...] = ("S_BAHN", "U_BAHN", "R_BAHN", "XPRESSBUS"),
    ):
        self.client = client
        self.refresh_s = refresh_s
        self.window_s = window_s
        self.bbox = bbox
        self.vehicle_types = list(vehicle_types)
        self.tracks_path = data_dir / "tracks.json"
        self.signals = SignalIndex.load(data_dir / "signals.json")
        self.tracks: dict[str, dict] = {}
        self.stations: dict[str, dict] = {}
        self.version = 0
        self._tracks_gz: tuple[int, bytes] | None = None
        self._edges: tuple[int, dict] | None = None
        self._saved_version = 0
        self._saved_at = 0.0
        self._snapshot: dict | None = None
        self._fetched_at = 0.0
        self._fetch_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._course_lock = threading.Lock()
        self.trips = TripIds()
        self._courses: dict[str, tuple[float, list[dict]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.tracks_path.read_text("utf-8"))
            self.tracks, self.stations = data["tracks"], data["stations"]
            self.version = self._saved_version = 1
            for track in self.tracks.values():
                if not track.get("g"):  # aeltere Dateien: Geometrie einmalig bereinigen
                    track["p"], track["g"] = clean(track["p"]), 1
                    self.version = 2
                self._sign(track)
            log.info("Gleisgeometrie geladen: %d Abschnitte, %d Haltestellen", len(self.tracks), len(self.stations))
        except (OSError, ValueError, KeyError):
            pass

    def _sign(self, track: dict) -> None:
        """Signalpositionen ("s", Streckenanteile) am Abschnitt; nicht gespeichert, weil sie aus signals.json abgeleitet sind."""
        track["s"] = self.signals.fractions(track["p"]) if self.signals else []

    def _save(self, force: bool = False) -> None:
        if self.version == self._saved_version or (not force and time.monotonic() - self._saved_at < TRACKS_SAVE_INTERVAL_S):
            return
        try:
            self.tracks_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.tracks_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tracks": {k: {"p": t["p"], "l": t["l"], "g": 1, **({"v": 1} if t.get("v") else {})} for k, t in self.tracks.items()}, "stations": self.stations}, separators=(",", ":")), "utf-8")
            tmp.replace(self.tracks_path)
            self._saved_version, self._saved_at = self.version, time.monotonic()
        except OSError as exc:
            log.warning("Gleisgeometrie nicht gespeichert: %s", exc)

    def _remember(self, key: str, points: list[float], line: str, from_key: str, from_name: str, to_key: str, to_name: str) -> None:
        track = self.tracks.get(key)
        if track is not None and track.get("v"):  # echte Geometrie ersetzt die Behelfslinie
            track["p"], track["g"] = clean(points), 1
            track.pop("v")
            track.pop("cum", None)
            self._sign(track)
            self.version += 1
        if track is None:
            track = self.tracks[key] = {"p": clean(points), "g": 1, "l": []}
            self._sign(track)
            self.version += 1
        if line not in track["l"]:
            track["l"].append(line)
            self.version += 1
        for st_key, name, (x, y) in ((from_key, from_name, points[:2]), (to_key, to_name, points[-2:])):
            station = self.stations.get(st_key)
            if station is None:
                station = self.stations[st_key] = {"n": name, "x": round(x, 5), "y": round(y, 5), "l": []}
                self.version += 1
            if line not in station["l"]:
                station["l"].append(line)
                self.version += 1

    def _edge_index(self) -> dict[tuple[str, str], list[str]]:
        """(Starthalt, Zielhalt) -> Gleisabschnitte dazwischen, ueber die naechste bekannte Haltestelle (<= 150 m) an den Enden."""
        if self._edges is not None and self._edges[0] == self.version:
            return self._edges[1]
        stops = [(s["x"] * KX_M, s["y"] * KY_M, key) for key, s in self.stations.items()]

        def nearest(x: float, y: float) -> str | None:
            best = min(((math.hypot(x * KX_M - sx, y * KY_M - sy), key) for sx, sy, key in stops), default=None)
            return best[1] if best and best[0] <= 150 else None

        index: dict[tuple[str, str], list[str]] = {}
        for key, track in self.tracks.items():
            if track.get("v"):
                continue
            a, b = nearest(track["p"][0], track["p"][1]), nearest(track["p"][-2], track["p"][-1])
            if a and b and a != b:
                index.setdefault((a, b), []).append(key)
        self._edges = (self.version, index)
        return index

    def _bridge(self, line: str, s: dict) -> str | None:
        """Segment ohne Geometrie (die HVV-Daten lassen sie bei manchen Fahrten weg, z. B. S3 Fischbek - Neugraben): die Linie wird
        dem vorhandenen Gleis zwischen denselben Haltestellen zugeschlagen, sonst entsteht eine gerade Behelfsverbindung."""
        a, b = s.get("startStationKey"), s.get("endStationKey")
        if a not in self.stations or b not in self.stations or a == b:
            return None
        found = self._edge_index().get((a, b))
        if found:
            for key in found:
                if line not in self.tracks[key]["l"]:
                    self.tracks[key]["l"].append(line)
                    self.version += 1
            return found[0]
        key = f"{s['startStopPointKey']}>{s['endStopPointKey']}"
        track = self.tracks.get(key)
        if track is None:
            sa, sb = self.stations[a], self.stations[b]
            track = self.tracks[key] = {"p": [sa["x"], sa["y"], sb["x"], sb["y"]], "g": 1, "v": 1, "l": []}
            self._sign(track)
            self.version += 1
        if line not in track["l"]:
            track["l"].append(line)
            self.version += 1
        return key

    def _fetch(self, first: bool) -> dict:
        now = int(time.time())
        window = FIRST_FILL_WINDOW_S if first else self.window_s
        (lon1, lat1, lon2, lat2) = self.bbox
        response = self.client.get_vehicle_map(
            (lon1, lat1), (lon2, lat2), now, now + window, without_coords=False, vehicle_types=self.vehicle_types
        )
        candidates = []
        for j in response.get("journeys", []):
            line = j.get("line", {}).get("name") or "?"
            if j.get("vehicleType") == "R_BAHN" and not REGIONAL_LINE.match(line):
                continue
            segs = sorted(j.get("segments", []), key=lambda s: s["startDateTime"])
            out = []
            for s in segs:
                points = (s.get("track") or {}).get("track") or []
                key = None
                if len(points) >= 4:
                    key = f"{s['startStopPointKey']}>{s['endStopPointKey']}"
                    self._remember(key, points, line, s["startStationKey"], s["startStationName"], s["endStationKey"], s["endStationName"])
                else:
                    key = self._bridge(line, s)
                if s["endDateTime"] < now - 30 or s["startDateTime"] > now + self.window_s:
                    continue
                out.append(
                    {"a": s["startDateTime"], "b": s["endDateTime"], "k": key, "d": s.get("realtimeDelay"), "f": s["startStationName"], "t": s["endStationName"]}
                )
            if out:
                candidates.append((j, line, segs, out))
        ids = self.trips.resolve([(line, segs) for _, line, segs, _ in candidates], now)
        journeys = []
        for uid, (j, line, segs, out) in zip(ids, candidates):
            if uid is None:
                continue
            journeys.append(
                {
                    "id": uid,
                    "l": line,
                    "m": MODES.get(j.get("vehicleType", ""), "?"),
                    "dir": j.get("line", {}).get("direction"),
                    "o": j.get("line", {}).get("origin"),
                    "rt": bool(j.get("realtime")),
                    "s": out,
                    "end": bool(segs and segs[-1].get("isLast")),
                }
            )
        self._save(force=first)
        return {"now": now, "fetched": now, "journeys": journeys}

    def vehicles(self) -> dict | None:
        """Aktueller Stand; veraltete Daten liefert der Cache weiter, waehrend genau ein Thread nachlaedt."""
        with self._state_lock:
            snapshot, age = self._snapshot, time.monotonic() - self._fetched_at
        if snapshot is not None and age < self.refresh_s:
            return snapshot
        if not self._fetch_lock.acquire(blocking=snapshot is None):
            return snapshot
        try:
            with self._state_lock:
                if self._snapshot is not None and time.monotonic() - self._fetched_at < self.refresh_s:
                    return self._snapshot
            try:
                fresh = self._fetch(first=snapshot is None)
            except GtiAuthError:
                raise
            except (GtiPermissionError, GtiError) as exc:
                log.error("getVehicleMap: %s", exc)
                return snapshot
            with self._state_lock:
                self._snapshot, self._fetched_at = fresh, time.monotonic()
                log.info("getVehicleMap: %d Fahrten, %d Gleisabschnitte", len(fresh["journeys"]), len(self.tracks))
            return fresh
        finally:
            self._fetch_lock.release()

    def course(self, journey_id: str, mode: str) -> dict | None:
        """Halteverlauf einer Fahrt. Ein Weitfensterabruf je Fahrzeugart wird kurz gecacht und bedient alle Klicks darauf."""
        vehicle_type = VEHICLE_TYPES.get(mode)
        if vehicle_type not in self.vehicle_types:
            return None
        with self._course_lock:
            cached = self._courses.get(vehicle_type)
            if cached is None or time.monotonic() - cached[0] > COURSE_CACHE_S:
                now = int(time.time())
                (lon1, lat1, lon2, lat2) = self.bbox
                response = self.client.get_vehicle_map(
                    (lon1, lat1), (lon2, lat2), now - COURSE_WINDOW_S, now + COURSE_WINDOW_S, without_coords=True, vehicle_types=[vehicle_type]
                )
                cached = self._courses[vehicle_type] = (time.monotonic(), response.get("journeys", []))
            best, journey = 0, None
            for candidate in cached[1]:
                line = candidate.get("line", {}).get("name") or "?"
                score = self.trips.match(journey_id, line, candidate.get("segments", []))
                if score > best:
                    best, journey = score, candidate
        course = build_course(journey) if journey else None
        if course:
            course["id"] = journey_id
        return course

    def tracks_payload(self) -> tuple[int, bytes]:
        """(Version, gzip-JSON) der Gleisgeometrie; wird je Version nur einmal gepackt."""
        with self._state_lock:
            if self._tracks_gz is None or self._tracks_gz[0] != self.version:
                body = json.dumps({"v": self.version, "tracks": self.tracks, "stations": self.stations}, separators=(",", ":")).encode()
                self._tracks_gz = (self.version, gzip.compress(body, 6))
            return self._tracks_gz


class Handler(BaseHTTPRequestHandler):
    server_version = "hvv-map/0.1"
    protocol_version = "HTTP/1.1"
    app: "MapApp"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/api/vehicles":
                data = self.app.feed.vehicles()
                if data is None:
                    return self._json({"error": "keine Fahrzeugdaten verfuegbar"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return self._json({**data, "server": time.time()})  # "server": Uhrzeit der Antwort, nicht des (gecachten) Abrufs
            if path == "/api/journey":
                query = parse_qs(urlsplit(self.path).query)
                journey_id, mode = (query.get(k, [""])[0] for k in ("id", "m"))
                try:
                    course = self.app.feed.course(journey_id, mode)
                except GtiAuthError:
                    raise
                except GtiError as exc:
                    log.error("Halteverlauf: %s", exc)
                    return self._json({"error": "Halteverlauf derzeit nicht abrufbar"}, HTTPStatus.BAD_GATEWAY)
                if course is None:
                    return self._json({"error": "Fahrt nicht mehr in den Daten"}, HTTPStatus.NOT_FOUND)
                return self._json(course)
            if path == "/api/tracks":
                self.app.feed.vehicles()
                version, body = self.app.feed.tracks_payload()
                etag = f'"{version}"'
                if self.headers.get("If-None-Match") == etag:
                    return self._send(HTTPStatus.NOT_MODIFIED, b"", "application/json", {"ETag": etag})
                return self._send(200, body, "application/json; charset=utf-8", {"Content-Encoding": "gzip", "ETag": etag, "Cache-Control": "no-cache"})
            if path == "/api/config":
                return self._json({"tiles": self.app.tiles_file().exists(), "tilesUrl": "/tiles/hamburg.pmtiles", "refreshS": self.app.feed.refresh_s, "xbus": "XPRESSBUS" in self.app.feed.vehicle_types})
            if path in ("/api/stations", "/api/departures", "/api/route"):
                return self._planner(path)
            if path == "/api/colors":
                return self._json(self.app.icons.colors())
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path.startswith("/icon/"):
                return self._icon(path[len("/icon/"):])
            if path == "/tiles/hamburg.pmtiles":
                return self._file(self.app.tiles_file(), "application/octet-stream", ranged=True)
            return self._static(path)
        except GtiAuthError as exc:
            log.error("GTI-Zugang abgelehnt: %s", exc)
            return self._json({"error": "GTI-Zugang abgelehnt"}, HTTPStatus.BAD_GATEWAY)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _planner(self, path: str) -> None:
        planner = self.app.planner
        if planner is None:
            return self._json({"error": "Routenplaner nicht verfuegbar"}, HTTPStatus.NOT_FOUND)
        if not self.app.limiter.allow(client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))):
            return self._json({"error": "Zu viele Anfragen – bitte kurz warten."}, HTTPStatus.TOO_MANY_REQUESTS)
        query = parse_qs(urlsplit(self.path).query)
        arg = lambda name: (query.get(name, [""])[0] or "").strip()
        station = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
        try:
            if path == "/api/stations":
                text = arg("q")
                return self._json({"stations": planner.stations(text) if len(text) >= 2 else []})
            if path == "/api/departures":
                if not station.match(arg("id")):
                    return self._json({"error": "ungueltige Station"}, HTTPStatus.BAD_REQUEST)
                return self._json(planner.departures(arg("id"), min(40, max(1, int(arg("n") or 20)))))
            if not (station.match(arg("from")) and station.match(arg("to"))):
                return self._json({"error": "ungueltige Station"}, HTTPStatus.BAD_REQUEST)
            when = datetime.now(ZoneInfo("Europe/Berlin"))
            if arg("when"):
                when = datetime.fromisoformat(arg("when")).replace(tzinfo=ZoneInfo("Europe/Berlin"))
            return self._json(planner.route(arg("from"), arg("to"), when, arg("arrive") == "1"))
        except ValueError:
            return self._json({"error": "ungueltige Eingabe"}, HTTPStatus.BAD_REQUEST)
        except GtiAuthError:
            raise
        except GtiError as exc:
            log.error("Planer: %s", exc)
            return self._json({"error": "HVV-Schnittstelle derzeit nicht erreichbar"}, HTTPStatus.BAD_GATEWAY)
        except OSError as exc:
            log.error("Planer (Influx): %s", exc)
            return self._json({"error": "Messdaten derzeit nicht erreichbar"}, HTTPStatus.BAD_GATEWAY)

    def _icon(self, filename: str) -> None:
        name, _, fmt = filename.rpartition(".")
        try:
            path = self.app.icons.get(name, fmt)
        except GtiError as exc:
            log.warning("Linienindex nicht ladbar: %s", exc)
            path = None
        if path is None:
            return self._send(404, b"unbekannte Linie", "text/plain")
        self._file(path, CONTENT_TYPES[fmt], cache="public, max-age=86400")

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        root = self.app.static_dir.resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            return self._send(404, b"not found", "text/plain")
        self._file(target, mimetypes.guess_type(target.name)[0] or "application/octet-stream", cache="no-cache")

    def _file(self, path: Path, content_type: str, *, ranged: bool = False, cache: str = "no-cache") -> None:
        if not path.is_file():
            return self._send(404, b"not found", "text/plain")
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        header = self.headers.get("Range") if ranged else None
        if header and header.startswith("bytes="):
            first, _, last = header[6:].split(",")[0].partition("-")
            try:
                if first == "":
                    start = max(0, size - int(last))
                else:
                    start = int(first)
                    end = min(size - 1, int(last)) if last else size - 1
            except ValueError:
                start, end = 0, size - 1
            if start > end or start >= size:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        if ranged:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


PROXY_NETWORKS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]  # Adressen, hinter denen ein Proxy steht


class RateLimiter:
    """Begrenzt Anfragen je Schluessel (Client-IP) auf `limit` pro `window_s` Sekunden. Schuetzt das gemeinsame GTI-Konto, wenn die
    Karte oeffentlich erreichbar ist: Abfahrten, Routen und Stationssuche loesen je einen GTI-Aufruf aus."""

    def __init__(self, limit: int, window_s: float = 60.0):
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < self.window_s]
            allowed = len(recent) < self.limit
            if allowed:
                recent.append(now)
            if recent:
                self._hits[key] = recent
            else:
                self._hits.pop(key, None)
            if len(self._hits) > 5000:  # Speicher begrenzen
                self._hits = {k: v for k, v in self._hits.items() if now - v[-1] < self.window_s}
            return allowed


def client_ip(peer: str, forwarded: str | None) -> str:
    """Hinter einem Reverse Proxy (privater/lokaler Peer) gilt die erste Adresse aus X-Forwarded-For, sonst die Peer-Adresse."""
    try:
        address = ipaddress.ip_address(peer)
        trusted = address.is_loopback or any(address in net for net in PROXY_NETWORKS if net.version == address.version)
    except ValueError:
        trusted = False
    if trusted and forwarded:
        first = forwarded.split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass
    return peer


class MapApp:
    def __init__(self, feed: VehicleFeed, icons: IconStore, static_dir: Path, data_dir: Path, planner=None, planner_rate: int = 60):
        self.feed, self.icons, self.static_dir, self.data_dir, self.planner = feed, icons, static_dir, data_dir, planner
        self.limiter = RateLimiter(planner_rate)

    def tiles_file(self) -> Path:
        return self.data_dir / "hamburg.pmtiles"


def serve(app: MapApp, host: str, port: int, stop: threading.Event) -> None:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    log.info("Karte laeuft auf http://%s:%d (Daten %s)", host, port, app.data_dir)
    try:
        stop.wait()
    finally:
        httpd.shutdown()
        app.feed._save(force=True)
