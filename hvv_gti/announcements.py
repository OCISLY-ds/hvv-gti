import logging
from datetime import datetime, timezone

from .client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from .config import Config
from .influx import line_protocol

log = logging.getLogger("hvv_gti.announcements")

MEASUREMENT = "hvv_announcement"


def tracked_lines(cfg: Config) -> set[str]:
    lines = {route.line for route in cfg.routes}
    if cfg.corridor:
        lines |= {line for line, _ in cfg.corridor.lines}
    return lines


def _parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def is_active(announcement: dict, now: datetime) -> bool:
    """Gilt jetzt mindestens eines der Gueltigkeitsfenster (ohne Angabe: der Veroeffentlichungszeitraum)?"""
    windows = announcement.get("validities") or [announcement.get("publication") or {}]
    for window in windows:
        begin, end = _parse_time(window.get("begin")), _parse_time(window.get("end"))
        if (begin is None or begin <= now) and (end is None or now < end):
            return True
    return False


def affected_lines(announcement: dict, wanted: set[str]) -> list[str]:
    names = {(loc.get("line") or {}).get("name") for loc in announcement.get("locations") or []}
    return sorted(names & wanted)


def parse_announcements(response: dict, wanted: set[str], now: datetime) -> list[str]:
    """Pro aktiver Meldung und betroffener Linie ein Messpunkt zum Abfragezeitpunkt (auf die Minute gerundet).

    `reason` ist die GTI-Ursache (ROADWORKS, ACCIDENT, VEHICLE_ON_THE_LINE, STAFF_ABSENCE ...). Die Meldung gilt fuer
    die Linie, nicht fuer eine einzelne Fahrt: ein Zusammenhang mit einer bestimmten Verspaetung ist nur zeitlich.
    """
    timestamp_ns = int(now.replace(second=0, microsecond=0).timestamp()) * 1_000_000_000
    lines = []
    for announcement in response.get("announcements", []):
        if not is_active(announcement, now):
            continue
        for line in affected_lines(announcement, wanted):
            tags = {
                "line": line,
                "reason": announcement.get("reason") or "UNKNOWN",
                "planned": str(bool(announcement["planned"])).lower() if "planned" in announcement else None,
                "announcement_id": announcement.get("id"),
            }
            fields = {"summary": announcement.get("summary") or "", "version": int(announcement.get("version") or 0)}
            lines.append(line_protocol(MEASUREMENT, tags, fields, timestamp_ns))
    return lines


def collect_announcements(
    client: GtiClient,
    cfg: Config,
    now: datetime | None = None,
    denied: set[str] | None = None,
) -> list[str]:
    """Ein getAnnouncements-Aufruf; Fehler (ausser falschen Zugangsdaten) kosten nur diese Messung."""
    denied = denied if denied is not None else set()
    if "announcements" in denied:
        return []
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wanted = tracked_lines(cfg)
    try:
        response = client.get_announcements(sorted(wanted))
    except GtiAuthError:
        raise
    except GtiPermissionError as exc:
        log.warning("Account darf getAnnouncements nicht abfragen, Stoerungsmeldungen werden uebersprungen: %s", exc)
        denied.add("announcements")
        return []
    except GtiError as exc:
        log.error("getAnnouncements: %s", exc)
        return []
    lines = parse_announcements(response, wanted, now)
    log.info("getAnnouncements: %d Meldungen, %d Messpunkte fuer aktive Meldungen", len(response.get("announcements", [])), len(lines))
    return lines
