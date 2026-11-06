import hashlib
import logging
from datetime import datetime, timezone

from .client import TZ, GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .config import Config, Corridor
from .influx import line_protocol

log = logging.getLogger("hvv_gti.trains")

STOP_MEASUREMENT = "hvv_train_stop"
POSITION_MEASUREMENT = "hvv_train_pos"
# Ein Zug gilt kurz vor der Abfahrt am ersten Halt schon als unterwegs, nach der Ankunft am letzten nicht mehr.
DEPARTURE_GRACE_S = 120


def station_indices(corridor: Corridor) -> dict[str, int]:
    indices: dict[str, int] = {}
    for idx, station in enumerate(corridor.stations):
        for name in (station.name, *station.aliases):
            indices[name.casefold()] = idx
    return indices


def _delay_min(segment: dict) -> int | None:
    value = segment.get("realtimeDelay")
    return None if value is None else int(value)


def journey_key(journey_id: str) -> str:
    return hashlib.sha1(journey_id.encode("utf-8")).hexdigest()[:10]


def parse_journey(journey: dict, corridor: Corridor, now_ts: int) -> list[str]:
    """Haltepunkte und aktuelle Position einer Fahrt im Korridor als Line Protocol.

    Die Zeiten der Segmente enthalten die Verspaetung bereits (Vergleich mit der Abfahrtstafel am 20.10.2026),
    `realtimeDelay` ist in Minuten. Ein Halt-Punkt traegt als Zeitstempel die planmaessige Zeit und ueberschreibt sich
    deshalb bei jeder Abfrage: gespeichert bleibt die zuletzt gemeldete Verspaetung vor der Durchfahrt.
    """
    indices = station_indices(corridor)
    segments = sorted(journey.get("segments", []), key=lambda s: s["startDateTime"])
    if not segments:
        return []

    def idx_of(name: str | None) -> int | None:
        return indices.get((name or "").casefold())

    # Halte in Fahrtreihenfolge: Start jedes Segments, dazu das Ende des letzten (Ankunft).
    stops: list[dict] = []
    for seg in segments:
        stops.append({"name": seg["startStationName"], "ts": seg["startDateTime"], "delay": _delay_min(seg), "arrival": False})
    last = segments[-1]
    stops.append({"name": last["endStationName"], "ts": last["endDateTime"], "delay": _delay_min(last), "arrival": True})
    for stop in stops:
        stop["idx"] = idx_of(stop["name"])
    in_corridor = [s for s in stops if s["idx"] is not None]
    if len(in_corridor) < 2:
        return []

    line = journey["line"]["name"]
    direction = "Hamburg" if in_corridor[-1]["idx"] > in_corridor[0]["idx"] else "Stade"
    key = journey_key(journey.get("journeyID", ""))
    first = in_corridor[0]
    first_planned = first["ts"] - (first["delay"] or 0) * 60
    label = f"{line} {datetime.fromtimestamp(first_planned, TZ):%H:%M} ab {corridor.stations[first['idx']].name}"

    lines = []
    for stop in in_corridor:
        planned = (stop["ts"] - (stop["delay"] or 0) * 60) // 60 * 60
        fields: dict = {"actual": int(stop["ts"]), "idx": stop["idx"], "arrival": 1 if stop["arrival"] else 0, "train": label}
        if stop["delay"] is not None:
            fields["delay_min"] = stop["delay"]
        tags = {
            "journey": key,
            "line": line,
            "direction": direction,
            "station": corridor.stations[stop["idx"]].name,
        }
        lines.append(line_protocol(STOP_MEASUREMENT, tags, fields, planned * 1_000_000_000))

    position = _position(segments, corridor, indices, now_ts)
    if position:
        fields = {**position, "train": label}
        tags = {"journey": key, "line": line, "direction": direction}
        lines.append(line_protocol(POSITION_MEASUREMENT, tags, fields, now_ts * 1_000_000_000))
    return lines


def _position(segments: list[dict], corridor: Corridor, indices: dict[str, int], now_ts: int) -> dict | None:
    def idx_of(name: str | None) -> int | None:
        return indices.get((name or "").casefold())

    def result(pos: float, state: str, where: str, seg: dict) -> dict:
        out = {"pos": float(pos), "state": state, "where": where, "next": seg["endStationName"], "next_eta": int(seg["endDateTime"])}
        delay = _delay_min(seg)
        if delay is not None:
            out["delay_min"] = delay
        return out

    if now_ts < segments[0]["startDateTime"] - DEPARTURE_GRACE_S or now_ts >= segments[-1]["endDateTime"]:
        return None
    for i, seg in enumerate(segments):
        start_idx, end_idx = idx_of(seg["startStationName"]), idx_of(seg["endStationName"])
        if seg["startDateTime"] <= now_ts < seg["endDateTime"]:
            if start_idx is None or end_idx is None:
                return None
            share = (now_ts - seg["startDateTime"]) / (seg["endDateTime"] - seg["startDateTime"])
            where = f"{seg['startStationName']} → {seg['endStationName']}"
            return result(start_idx + share * (end_idx - start_idx), "fährt", where, seg)
        if now_ts < seg["startDateTime"]:  # vor der Abfahrt: Halt am Start dieses Segments
            if start_idx is None:
                return None
            state = "wartet" if i == 0 else "hält"
            return result(start_idx, state, f"in {seg['startStationName']}", seg)
    return None


def collect_trains(
    client: GtiClient,
    cfg: Config,
    now: datetime | None = None,
    denied: set[str] | None = None,
) -> list[str]:
    """Ein getVehicleMap-Aufruf je Fahrzeugtyp; Fehler (ausser falschen Zugangsdaten) kosten nur diese Messung."""
    corridor = cfg.corridor
    if corridor is None:
        return []
    denied = denied if denied is not None else set()
    now_ts = int((now or datetime.now(timezone.utc)).timestamp())
    lines: list[str] = []
    by_type: dict[str, set[str]] = {}
    for line, vehicle_type in corridor.lines:
        by_type.setdefault(vehicle_type, set()).add(line)
    for vehicle_type, wanted in by_type.items():
        if "vehicle_map" in denied:
            break
        lon1, lat1, lon2, lat2 = corridor.bbox
        try:
            response = client.get_vehicle_map(
                (lon1, lat1),
                (lon2, lat2),
                now_ts - corridor.lookback_s,
                now_ts + corridor.lookahead_s,
                vehicle_types=[vehicle_type],
            )
        except GtiAuthError:
            raise
        except GtiPermissionError as exc:
            log.warning("Account darf getVehicleMap nicht abfragen, Zuglauf wird uebersprungen: %s", exc)
            denied.add("vehicle_map")
            break
        except GtiError as exc:
            log.error("getVehicleMap %s: %s", vehicle_type, exc)
            continue
        journeys = [j for j in response.get("journeys", []) if j.get("line", {}).get("name") in wanted]
        parsed = [parse_journey(j, corridor, now_ts) for j in journeys]
        log.info("getVehicleMap %s: %d Fahrten, %d davon im Korridor %s", vehicle_type, len(journeys), sum(1 for p in parsed if p), corridor.name)
        for chunk in parsed:
            lines.extend(chunk)
    return lines
