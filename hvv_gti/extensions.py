"""Streckenabschnitte, die die GTI in getVehicleMap abschneidet, fuer die Karte aus dem Fahrplan ergaenzen.

Die S5 fehlt zwischen Dollern und Stade (gemessen 07.11.2026): Zuege nach Neugraben beginnen in Dollern (`isFirst` falsch, `line.origin`
ist trotzdem Stade), Zuege nach Stade enden dort (`isLast` falsch). Auch mit groesserer Bounding-Box oder `withoutCoords=false`
aendert das nichts. Die Karte ergaenzt den Abschnitt deshalb: Haltefolge und Fahrzeiten stammen aus den Abfahrtstafeln
(Stade ab :36 -> Agathenburg ab :41 -> Dollern ab :44), die Gleisgeometrie aus frueher von der GTI gelieferten Abschnitten
(`map/seed/extensions.json`). Die Verspaetung wird vom Nachbarabschnitt uebernommen, weil die GTI fuer den Abschnitt keine meldet.

Nur die Karte nutzt das. Die Messreihen in InfluxDB bekommen keine errechneten Zeiten, sonst waeren es erfundene Messwerte.
"""

import json
from dataclasses import dataclass
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "map" / "seed" / "extensions.json"
DWELL_S = 30  # Aufenthalt an einem Zwischenhalt; die Fahrzeit von Abfahrt zu Abfahrt enthaelt ihn


@dataclass(frozen=True)
class Hop:
    """Ein Abschnitt zwischen zwei Halten: Haltepunkte und Gleisschluessel stammen aus den GTI-Daten."""

    from_station: str
    to_station: str
    from_stop: str
    to_stop: str
    minutes: int  # Abfahrt zu Abfahrt (am letzten Halt: Fahrzeit)


@dataclass(frozen=True)
class Extension:
    line: str
    anchor: str  # Station, an der die GTI die Fahrt abschneidet
    # Halte vom Anker aus nach aussen (Fahrtrichtung zum Streckenende) und von aussen zum Anker
    outward: tuple[Hop, ...]
    inward: tuple[Hop, ...]
    window_s: int  # so weit ausserhalb des GTI-Zeitfensters reichen die ergaenzten Abschnitte (Summe der Fahrzeiten + Reserve)


DOLLERN, AGATHENBURG, STADE = "Master:8001493", "Master:8000434", "Master:8000089"

EXTENSIONS = (
    Extension(
        line="S5",
        anchor=DOLLERN,
        outward=(
            Hop(DOLLERN, AGATHENBURG, "SBH:1115001:1", "SBH:1116001:1", 3),
            Hop(AGATHENBURG, STADE, "SBH:1116001:1", "SBH:1117003:2", 5),
        ),
        inward=(
            Hop(STADE, AGATHENBURG, "SBH:1117003:1", "SBH:1116002:1", 5),
            Hop(AGATHENBURG, DOLLERN, "SBH:1116002:1", "SBH:1115002:1", 3),
        ),
        window_s=900,
    ),
)


class Seed:
    """Haltestellen und Gleise der ergaenzten Abschnitte."""

    def __init__(self, path: Path = SEED):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        self.stations: dict[str, dict] = data.get("stations", {})
        self.tracks: dict[str, list[float]] = data.get("tracks", {})

    def segment(self, hop: Hop, start: int, end: int, delay, *, first: bool, last: bool, destination: str) -> dict | None:
        points = self.tracks.get(f"{hop.from_stop}>{hop.to_stop}")
        a, b = self.stations.get(hop.from_station), self.stations.get(hop.to_station)
        if not points or not a or not b:
            return None
        return {
            "startStopPointKey": hop.from_stop,
            "endStopPointKey": hop.to_stop,
            "startStationName": a["n"],
            "startStationKey": hop.from_station,
            "startDateTime": start,
            "endStationName": b["n"],
            "endStationKey": hop.to_station,
            "endDateTime": end,
            "destination": destination,
            "realtimeDelay": delay,
            "isFirst": first,
            "isLast": last,
            "track": {"track": points},
        }


def extend(journey: dict, seed: Seed, extensions: tuple[Extension, ...] = EXTENSIONS) -> bool:
    """Haengt an eine abgeschnittene Fahrt die fehlenden Abschnitte an (veraendert `journey`); True, wenn etwas ergaenzt wurde."""
    line = journey.get("line", {}).get("name")
    segments = journey.get("segments")
    if not segments:
        return False
    segments.sort(key=lambda s: s["startDateTime"])
    for ext in extensions:
        if ext.line != line:
            continue
        first, last = segments[0], segments[-1]
        destination = journey.get("line", {}).get("direction") or ""
        if first.get("startStationKey") == ext.anchor and not first.get("isFirst") and ext.inward:
            # Zug nach aussen: er ist zuvor von den ergaenzten Halten zum Anker gefahren
            t = first["startDateTime"]  # Abfahrt am Anker
            built = []
            for hop in reversed(ext.inward):
                arrive = t - DWELL_S
                depart = arrive - hop.minutes * 60 + DWELL_S
                built.append((hop, depart, arrive))
                t = depart
            prefix = [
                seed.segment(hop, depart, arrive, first.get("realtimeDelay"), first=(i == len(built) - 1), last=False, destination=destination)
                for i, (hop, depart, arrive) in enumerate(built)
            ]
            if all(prefix):
                segments[:0] = prefix[::-1]
                segments.sort(key=lambda s: s["startDateTime"])
                return True
        elif last.get("endStationKey") == ext.anchor and not last.get("isLast") and ext.outward:
            t = last["endDateTime"] + DWELL_S  # Abfahrt am Anker
            suffix = []
            for i, hop in enumerate(ext.outward):
                depart = t
                arrive = depart + hop.minutes * 60 - (DWELL_S if i + 1 < len(ext.outward) else 0)
                suffix.append(seed.segment(hop, depart, arrive, last.get("realtimeDelay"), first=False, last=i + 1 == len(ext.outward), destination=destination))
                t = arrive + DWELL_S
            if all(suffix):
                segments.extend(suffix)
                return True
    return False
