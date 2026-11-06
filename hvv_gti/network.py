import logging
from datetime import datetime, timezone

from .client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .config import Config, Network
from .influx import line_protocol

log = logging.getLogger("hvv_gti.network")

MEASUREMENT = "hvv_network_stop"
MODES = {"S_BAHN": "S", "U_BAHN": "U", "R_BAHN": "R", "XPRESSBUS": "X"}


def parse_journey(journey: dict, now_ts: int, horizon_s: int, measurement: str = MEASUREMENT) -> list[str]:
    """Kuenftige Halte einer Fahrt (naechste `horizon_s` Sekunden) als Line Protocol.

    Die Segmentzeiten enthalten die Verspaetung bereits, `realtimeDelay` ist in Minuten. Der Zeitstempel ist die
    planmaessige Zeit auf die Minute abgerundet: derselbe Halt ueberschreibt sich bei jedem Zyklus, gespeichert bleibt
    die zuletzt gemeldete Verspaetung vor der Durchfahrt. Bereits passierte Halte werden nicht mehr geschrieben (die
    Antwort enthaelt sie, aber der Echtzeitwert kann danach kippen). Ohne Echtzeitflag der Fahrt fehlt `delay_s`.
    Den Ankunftshalt gibt es nur, wenn das letzte Segment `isLast` ist.
    """
    segments = sorted(journey.get("segments", []), key=lambda s: s["startDateTime"])
    if not segments:
        return []
    line = journey.get("line", {})
    mode = MODES.get(journey.get("vehicleType", ""), (journey.get("vehicleType") or "?").lower())
    realtime = bool(journey.get("realtime"))
    stops = [(s["startStationName"], s["startDateTime"], s.get("realtimeDelay"), "departure") for s in segments]
    last = segments[-1]
    if last.get("isLast"):  # die GTI kappt Fahrten teils (z. B. S5 vor Stade): dann ist das Segmentende kein Endhalt
        stops.append((last["endStationName"], last["endDateTime"], last.get("realtimeDelay"), "arrival"))

    lines = []
    for station, ts, delay, kind in stops:
        if not now_ts <= ts <= now_ts + horizon_s:
            continue
        fields: dict[str, int] = {}
        if realtime and delay is not None:
            fields["delay_s"] = int(delay) * 60
        fields["actual"] = int(ts)
        planned = (int(ts) - int(delay or 0) * 60) // 60 * 60
        tags = {
            "mode": mode,
            "line": line.get("name") or "?",
            "station": station,
            "kind": kind,
            "direction": line.get("direction") or "-",
        }
        lines.append(line_protocol(measurement, tags, fields, planned * 1_000_000_000))
    return lines


def collect_network(client: GtiClient, cfg: Config, now: datetime | None = None, denied: set[str] | None = None) -> list[str]:
    """Ein getVehicleMap-Aufruf fuer das ganze S-/U-Bahn-Netz; Fehler (ausser falschen Zugangsdaten) kosten nur diese Messung."""
    return _collect(client, cfg.network, now, denied, "vehicle_map")


def collect_xbus(client: GtiClient, cfg: Config, now: datetime | None = None, denied: set[str] | None = None) -> list[str]:
    """Wie `collect_network` fuer alle X-Busse, Messung `hvv_xbus_stop`, eigener Aufruf wegen des groesseren Gebiets."""
    return _collect(client, cfg.xbus, now, denied, "vehicle_map_xbus")


def collect_regional(client: GtiClient, cfg: Config, now: datetime | None = None, denied: set[str] | None = None) -> list[str]:
    """Wie `collect_network` fuer alle RE-Linien, Messung `hvv_re_stop` (eigener Aufruf wegen des grossen Gebiets)."""
    return _collect(client, cfg.regional, now, denied, "vehicle_map_regional")


def _collect(client: GtiClient, network: Network | None, now: datetime | None, denied: set[str] | None, key: str) -> list[str]:
    denied = denied if denied is not None else set()
    if network is None or key in denied:
        return []
    now_ts = int((now or datetime.now(timezone.utc)).timestamp())
    lon1, lat1, lon2, lat2 = network.bbox
    try:
        response = client.get_vehicle_map(
            (lon1, lat1),
            (lon2, lat2),
            now_ts,
            now_ts + network.horizon_s,
            vehicle_types=list(network.vehicle_types),
        )
    except GtiAuthError:
        raise
    except GtiPermissionError as exc:
        log.warning("Account darf getVehicleMap nicht abfragen, Netzabfrage wird uebersprungen: %s", exc)
        denied.add(key)
        return []
    except GtiError as exc:
        log.error("getVehicleMap %s: %s", network.name, exc)
        return []
    journeys = response.get("journeys", [])
    lines: list[str] = []
    for journey in journeys:
        if network.line_prefix and not (journey.get("line", {}).get("name") or "").startswith(network.line_prefix):
            continue
        lines.extend(parse_journey(journey, now_ts, network.horizon_s, network.measurement))
    log.info("getVehicleMap %s: %d Fahrten, %d Halte in den naechsten %d s", network.name, len(journeys), len(lines), network.horizon_s)
    return lines
