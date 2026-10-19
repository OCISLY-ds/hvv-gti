import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .config import Config, Route
from .influx import InfluxError, InfluxWriter, line_protocol

log = logging.getLogger("hvv_gti.collector")

MEASUREMENT = "hvv_stop_event"
MAX_BACKOFF = 600.0


@dataclass(frozen=True)
class Stop:
    label: str
    sd_name: dict


def resolve_station(client: GtiClient, name: str, station_id: str | None) -> Stop:
    if station_id:
        return Stop(name, {"id": station_id, "type": "STATION", "name": name, "combinedName": name})
    stations = [r for r in client.check_name(name) if r.get("type", "STATION") == "STATION"]
    if not stations:
        raise GtiError(f"Keine Haltestelle fuer '{name}' gefunden (python -m hvv_gti check-name '{name}')")
    wanted = name.casefold()
    exact = [r for r in stations if wanted in (r.get("name", "").casefold(), r.get("combinedName", "").casefold())]
    chosen = (exact or stations)[0]
    log.info(
        "Station '%s' -> %s (%s); Kandidaten: %s",
        name,
        chosen.get("id"),
        chosen.get("combinedName"),
        ", ".join(f"{r.get('id')}={r.get('combinedName')}" for r in stations),
    )
    keys = ("id", "type", "name", "city", "combinedName")
    return Stop(name, {k: chosen[k] for k in keys if k in chosen})


def match_route(routes: list[Route], direction: str) -> Route | None:
    folded = direction.casefold()
    for route in routes:
        if not route.directions or any(d.casefold() in folded for d in route.directions):
            return route
    return None


def parse_events(
    response: dict,
    reference: datetime,
    stop: Stop,
    kind: str,
    routes: tuple[Route, ...] | list[Route],
    unmatched: set[tuple[str, str, str]] | None = None,
) -> list[str]:
    """`reference` ist die (auf die Minute gerundete) Anfragezeit; timeOffset ist planmaessig, delay separat.

    Fahrten von Linien ohne Route werden ignoriert. Passt bei einer bekannten Linie keine Richtung, wird die
    Fahrt trotzdem gespeichert (route = "<Linie> <Haltestelle> -> <Richtung>") und einmalig gewarnt.
    Fehlt `delay` (kein Echtzeitwert), fehlt auch das Feld `delay_s`; nur Ausfaelle bekommen 0.
    """
    by_line: dict[str, list[Route]] = {}
    for route in routes:
        by_line.setdefault(route.line, []).append(route)
    lines = []
    for dep in response.get("departures", []):
        service = dep.get("line", {})
        candidates = by_line.get(service.get("name"))
        if not candidates:
            continue
        direction = service.get("direction") or "-"
        route = match_route(candidates, direction)
        if route:
            route_name = route.name
        else:
            route_name = f"{service['name']} {stop.label} → {direction}"
            key = (service["name"], stop.label, direction)
            if unmatched is not None and key not in unmatched:
                unmatched.add(key)
                log.warning("Keine Strecke fuer %s ab %s Richtung '%s', gespeichert als '%s'", *key, route_name)
        planned = reference + timedelta(minutes=int(dep.get("timeOffset", 0)))
        cancelled = bool(dep.get("cancelled"))
        fields: dict[str, int] = {"cancelled": 1 if cancelled else 0, "extra": 1 if dep.get("extra") else 0}
        if dep.get("delay") is not None:
            fields["delay_s"] = int(dep["delay"])
        elif cancelled:
            fields["delay_s"] = 0
        if dep.get("serviceId") is not None:
            fields["service_id"] = int(dep["serviceId"])
        tags = {
            "route": route_name,
            "line": service["name"],
            "station": stop.label,
            "kind": kind,
            "direction": direction,
            "origin": service.get("origin") or "-",
        }
        lines.append(line_protocol(MEASUREMENT, tags, fields, int(planned.timestamp()) * 1_000_000_000))
    return lines


def group_routes(routes: tuple[Route, ...]) -> list[tuple[tuple[str, str, str], list[Route]]]:
    """Gruppiert nach (Haltestelle, Haltestellen-ID, Verkehrsmittel): pro Gruppe ein departureList-Aufruf."""
    groups: dict[tuple[str, str, str], list[Route]] = {}
    for route in routes:
        groups.setdefault((route.station, route.station_id, route.service_type), []).append(route)
    return list(groups.items())


def resolve_stops(client: GtiClient, routes: tuple[Route, ...]) -> dict[str, Stop]:
    stations = dict.fromkeys((r.station, r.station_id) for r in routes)
    return {name: resolve_station(client, name, sid) for name, sid in stations}


def collect_once(
    client: GtiClient,
    stops: dict[str, Stop],
    cfg: Config,
    now: datetime | None = None,
    denied: set[str] | None = None,
    unmatched: set[tuple[str, str, str]] | None = None,
) -> list[str]:
    """`denied` sammelt Arten (departure/arrival), die der Account nicht abfragen darf; sie werden danach uebersprungen."""
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    denied = denied if denied is not None else set()
    kinds = [("departure", True)] + ([("arrival", False)] if cfg.collect_arrivals else [])
    if all(kind in denied for kind, _ in kinds):
        raise GtiPermissionError("Der Account darf keine der konfigurierten Abfragen ausfuehren")
    lines: list[str] = []
    errors: list[GtiError] = []
    ok = 0
    for (station, _, service_type), routes in group_routes(cfg.routes):
        stop = stops[station]
        max_list = max(r.max_list for r in routes)
        wanted = ", ".join(sorted({r.line for r in routes}))
        for kind, is_departure in kinds:
            if kind in denied:
                continue
            try:
                response = client.departure_list(
                    stop.sd_name,
                    reference,
                    max_list=max_list,
                    max_time_offset=cfg.max_time_offset,
                    departure=is_departure,
                    service_types=[service_type],
                )
            except GtiAuthError:
                raise
            except GtiPermissionError as exc:
                log.warning("Account darf '%s' nicht abfragen, wird uebersprungen: %s", kind, exc)
                denied.add(kind)
                errors.append(exc)
                continue
            except GtiError as exc:
                log.error("%s %s %s: %s", stop.label, service_type, kind, exc)
                errors.append(exc)
                continue
            ok += 1
            events = parse_events(response, reference, stop, kind, routes, unmatched)
            total = len(response.get("departures", []))
            log.info("%s %s %s: %d Eintraege, %d davon Linie %s", stop.label, service_type, kind, total, len(events), wanted)
            if total >= max_list:
                log.warning("%s %s %s: Liste voll (%d Eintraege), Fahrten koennen fehlen", stop.label, service_type, kind, total)
            lines.extend(events)
    if errors and not ok:
        raise errors[-1]
    return lines


def run(
    cfg: Config,
    client: GtiClient,
    writer: InfluxWriter | None,
    stop_event: threading.Event,
    *,
    once: bool = False,
) -> int:
    stops: dict[str, Stop] | None = None
    denied: set[str] = set()
    unmatched: set[tuple[str, str, str]] = set()
    failures = 0
    while not stop_event.is_set():
        try:
            if stops is None:
                stops = resolve_stops(client, cfg.routes)
            lines = collect_once(client, stops, cfg, denied=denied, unmatched=unmatched)
            if writer is None:
                print("\n".join(lines), flush=True)
            else:
                writer.write(lines)
                log.info("%d Datenpunkte geschrieben", len(lines))
            failures = 0
        except (GtiError, InfluxError) as exc:
            failures += 1
            log.error("Zyklus fehlgeschlagen (%d in Folge): %s", failures, exc)
        if once:
            return 1 if failures else 0
        wait = cfg.poll_interval if not failures else min(cfg.poll_interval * 2**failures, MAX_BACKOFF)
        stop_event.wait(wait)
    return 0
