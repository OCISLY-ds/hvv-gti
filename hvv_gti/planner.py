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
LOOKBACK = "-30d"
CACHE_S = 600

# Messung je Linienart (hvv_stop_event zusaetzlich fuer Ausfaelle)
MEASUREMENTS = {"S": "hvv_network_stop", "U": "hvv_network_stop", "X": "hvv_xbus_stop", "R": "hvv_regional_stop"}
CANCEL_MEASUREMENT = "hvv_stop_event"
WALK_TYPES = {"CHANGE", "FOOTPATH", "BICYCLE"}  # Elemente der GTI-Route, die keine Fahrt sind


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
        flux = (
            f'from(bucket: {flux_str(self.bucket)}) |> range(start: {LOOKBACK}) '
            f'|> filter(fn: (r) => r._measurement == "{CANCEL_MEASUREMENT}" and r._field == "cancelled" and r.line == {flux_str(line)}) '
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
        mode = line_mode(line)
        if self.reader is None or mode is None:
            return None
        measurement = MEASUREMENTS[mode]
        samples = self._cached(("d", measurement, line, station), lambda: self.reader.delays(measurement, line, station))
        scope = "Halt"
        if len(samples) < MIN_SAMPLES:
            samples = self._cached(("d", measurement, line, None), lambda: self.reader.delays(measurement, line, None))
            scope = "ganze Linie"
        if not samples:
            return None
        values, window = pick_window(samples, when)
        return {"values": values, "n": len(values), "scope": scope, "window": window}

    def cancel_rate(self, line: str) -> dict | None:
        if self.reader is None or line_mode(line) is None:
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
        if rides and len(known) == len(rides):
            ok_conn = ok_late = 0
            for _ in range(SIMULATIONS):
                missed = False
                arrival = 0.0
                for i, leg in enumerate(rides):
                    board, alight, _ = info[i]
                    dep_delay = self.rng.choice(board["values"]) if board else 0.0
                    if i > 0:
                        buffer = leg["dep"] - rides[i - 1]["arr"]
                        if arrival - dep_delay > buffer:
                            missed = True
                            break
                    arrival = self.rng.choice(alight["values"])
                if not missed:
                    ok_conn += 1
                    ok_late += arrival <= ONTIME_S
            result["connectionPct"] = round(100 * ok_conn / SIMULATIONS, 1)
            result["ontimePct"] = round(100 * ok_late / SIMULATIONS, 1)
            result["transfers"] = len(rides) - 1
        cancels = [c for _, _, c in info]
        known_cancel = [c for c in cancels if c is not None]
        if known_cancel:
            survive = 1.0
            for c in known_cancel:
                survive *= 1 - c["rate"]
            result["cancelPct"] = round(100 * (1 - survive), 1)
            result["cancelCoverage"] = f"{len(known_cancel)} von {len(rides)} Abschnitten"
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


class Planner:
    def __init__(self, client: GtiClient, reliability: Reliability):
        self.client = client
        self.reliability = reliability
        self._departures: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

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

    def route(self, start_id: str, dest_id: str, when: datetime, arrive: bool = False, count: int = 3) -> dict:
        local = when.astimezone(TZ)
        response = self.client.post(
            "getRoute",
            {
                "start": {"id": start_id, "type": "STATION"},
                "dest": {"id": dest_id, "type": "STATION"},
                "time": {"date": local.strftime("%d.%m.%Y"), "time": local.strftime("%H:%M")},
                "timeIsDeparture": not arrive,
                "schedulesBefore": count - 1 if arrive else 0,
                "schedulesAfter": 0 if arrive else count - 1,
                "realtime": "REALTIME",
            },
        )
        schedules = response.get("realtimeSchedules") or response.get("schedules") or []
        routes = []
        for schedule in schedules[:count]:
            legs = normalize_route(schedule)
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
                    "legs": legs,
                    "reliability": assessed,
                }
            )
        return {"routes": routes, "returnCode": response.get("returnCode")}


def normalize_route(schedule: dict) -> list[dict]:
    legs = []
    for element in schedule.get("scheduleElements", []):
        src, dst = element.get("from") or {}, element.get("to") or {}
        line = element.get("line") or {}
        dep, arr = gti_epoch(src.get("depTime")), gti_epoch(dst.get("arrTime"))
        base = {
            "from": {"name": src.get("name"), "id": src.get("id"), "platform": src.get("realtimePlatform") or src.get("platform") or ""},
            "to": {"name": dst.get("name"), "id": dst.get("id"), "platform": dst.get("realtimePlatform") or dst.get("platform") or ""},
            "dep": dep,
            "arr": arr,
        }
        walking = (line.get("type") or {}).get("simpleType") in WALK_TYPES  # Umstiegsfussweg u. a. kommen als "Linie" an
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
                }
            )
        elif dep is not None and arr is not None:
            legs.append({**base, "line": None, "walkMin": round((arr - dep) / 60)})
    return legs
