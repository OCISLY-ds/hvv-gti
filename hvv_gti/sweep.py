"""Ausfall-Erfassung fuer alle RE/RB, U-/S-Bahnen und viele Buslinien: Abfahrtstafeln einiger Knoten, reihum abgefragt.

Die Netzabfrage (getVehicleMap) kennt keine Ausfaelle, nur die Abfahrtstafel (`cancelled`). Je Zyklus werden `cfg.sweep_per_cycle`
Knoten abgefragt; jeder Knoten kommt so alle paar Zyklen dran (die Tafel schaut `max_time_offset` Minuten voraus, eine Abfahrt wird
also mehrfach gesehen, gespeichert bleibt der letzte Stand: derselbe Zeitstempel ueberschreibt sich). Messung `hvv_sweep_event`
im Schema von `hvv_stop_event`, aber getrennt, damit die Grafana-Dashboards (Variable `route`) nicht mit hunderten Linien volllaufen.
Linien, die ein Messpunkt in `cfg.routes` an derselben Haltestelle schon erfasst, werden hier uebergangen (keine Doppelzaehlung).
"""

import logging
from datetime import datetime, timezone

from .client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .collector import reason_fields
from .config import Config
from .influx import line_protocol
from .planner import data_mode

log = logging.getLogger("hvv_gti.sweep")

MEASUREMENT = "hvv_sweep_event"


def parse_sweep(response: dict, reference: datetime, station: str, skip: set[tuple[str, str]] | frozenset = frozenset()) -> list[str]:
    """Abfahrten einer Tafel als Line Protocol. Nur Linien, die der Planer bewerten kann (S, U, X, RE/RB, Bus); Fernverkehr nicht."""
    lines = []
    for dep in response.get("departures", []):
        service = dep.get("line", {})
        name = service.get("name") or ""
        if data_mode(name) is None or (station, name) in skip:
            continue
        planned = reference.replace(second=0, microsecond=0).timestamp() + int(dep.get("timeOffset", 0)) * 60
        cancelled = bool(dep.get("cancelled"))
        fields: dict[str, int | str] = {"cancelled": 1 if cancelled else 0, "extra": 1 if dep.get("extra") else 0}
        if dep.get("delay") is not None:
            fields["delay_s"] = int(dep["delay"])
        elif cancelled:
            fields["delay_s"] = 0
        if dep.get("serviceId") is not None:
            fields["service_id"] = int(dep["serviceId"])
        fields.update(reason_fields(dep))
        direction = service.get("direction") or "-"
        tags = {"route": name, "line": name, "station": station, "kind": "departure", "direction": direction, "destination": direction, "origin": service.get("origin") or "-"}
        lines.append(line_protocol(MEASUREMENT, tags, fields, int(planned) * 1_000_000_000))
    return lines


class Sweeper:
    """Merkt sich, welcher Knoten als naechster dran ist."""

    def __init__(self) -> None:
        self.cursor = 0

    def batch(self, cfg: Config) -> list:
        stations = list(cfg.sweep)
        if cfg.sweep_per_cycle <= 0 or not stations:
            return []
        count = min(cfg.sweep_per_cycle, len(stations))
        chosen = [stations[(self.cursor + i) % len(stations)] for i in range(count)]
        self.cursor = (self.cursor + count) % len(stations)
        return chosen

    def collect(self, client: GtiClient, cfg: Config, now: datetime | None = None, denied: set[str] | None = None) -> list[str]:
        denied = denied if denied is not None else set()
        if "sweep" in denied:
            return []
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
        skip = {(route.station, route.line) for route in cfg.routes}
        out: list[str] = []
        for node in self.batch(cfg):
            try:
                response = client.departure_list(
                    {"id": node.station_id, "type": "STATION"},
                    reference,
                    max_list=node.max_list,
                    max_time_offset=cfg.max_time_offset,
                    service_types=list(node.service_types),
                )
            except GtiAuthError:
                raise
            except GtiPermissionError as exc:
                log.warning("Account darf die Abfahrtstafeln der Ausfall-Erfassung nicht abfragen, wird uebersprungen: %s", exc)
                denied.add("sweep")
                return out
            except GtiError as exc:
                log.error("Ausfall-Erfassung %s %s: %s", node.name, "/".join(node.service_types), exc)
                continue
            events = parse_sweep(response, reference, node.name, skip)
            total = len(response.get("departures", []))
            log.info("Ausfall-Erfassung %s %s: %d Eintraege, %d gespeichert", node.name, "/".join(node.service_types), total, len(events))
            if total >= node.max_list:
                log.warning("Ausfall-Erfassung %s %s: Liste voll (%d Eintraege), Fahrten koennen fehlen", node.name, "/".join(node.service_types), total)
            out.extend(events)
        return out
