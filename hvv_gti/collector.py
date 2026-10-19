import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .config import Config
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


def parse_events(response: dict, reference: datetime, stop: Stop, kind: str, line: str) -> list[str]:
    """`reference` ist die (auf die Minute gerundete) Anfragezeit; timeOffset ist planmaessig, delay separat."""
    lines = []
    for dep in response.get("departures", []):
        service = dep.get("line", {})
        if service.get("name") != line:
            continue
        planned = reference + timedelta(minutes=int(dep.get("timeOffset", 0)))
        fields = {
            "delay_s": int(dep.get("delay") or 0),
            "cancelled": 1 if dep.get("cancelled") else 0,
            "extra": 1 if dep.get("extra") else 0,
        }
        if dep.get("serviceId") is not None:
            fields["service_id"] = int(dep["serviceId"])
        tags = {
            "line": line,
            "station": stop.label,
            "kind": kind,
            "direction": service.get("direction") or "-",
            "origin": service.get("origin") or "-",
        }
        lines.append(line_protocol(MEASUREMENT, tags, fields, int(planned.timestamp()) * 1_000_000_000))
    return lines


def collect_once(
    client: GtiClient,
    stops: list[Stop],
    cfg: Config,
    now: datetime | None = None,
    denied: set[str] | None = None,
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
    for stop in stops:
        for kind, is_departure in kinds:
            if kind in denied:
                continue
            try:
                response = client.departure_list(
                    stop.sd_name,
                    reference,
                    max_list=cfg.max_list,
                    max_time_offset=cfg.max_time_offset,
                    departure=is_departure,
                    service_types=list(cfg.service_types),
                )
            except GtiAuthError:
                raise
            except GtiPermissionError as exc:
                log.warning("Account darf '%s' nicht abfragen, wird uebersprungen: %s", kind, exc)
                denied.add(kind)
                errors.append(exc)
                continue
            except GtiError as exc:
                log.error("%s %s: %s", stop.label, kind, exc)
                errors.append(exc)
                continue
            ok += 1
            events = parse_events(response, reference, stop, kind, cfg.line)
            total = len(response.get("departures", []))
            log.info("%s %s: %d Eintraege, %d davon Linie %s", stop.label, kind, total, len(events), cfg.line)
            if total and not events:
                log.warning("%s %s: keine Fahrten der Linie %s in der Antwort", stop.label, kind, cfg.line)
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
    stops: list[Stop] | None = None
    denied: set[str] = set()
    failures = 0
    while not stop_event.is_set():
        try:
            if stops is None:
                stops = [resolve_station(client, name, sid) for name, sid in cfg.stations]
            lines = collect_once(client, stops, cfg, denied=denied)
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
