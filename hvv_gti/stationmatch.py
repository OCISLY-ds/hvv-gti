"""Ordnet die Gleisangaben der GTI ("Gleis 1 (S-Bahn)", "Gleis 6A-C") den Bahnsteigen eines 3D-Bahnhofsmodells zu.

Das Modell (hvv_gti/stationmap.py, map/seed/stations/index.json) kennt je Bahnsteig Gleisnummern, Verkehrsmittel und Ebene. Eine Zuordnung
gibt es nur, wenn sie eindeutig ist: gleicher Bahnhof (Name oder Alias), gleiche Gleisnummer, gleiches Verkehrsmittel. Fehlt ein
Bahnsteig in OpenStreetMap oder passen mehrere, bekommt der Umstieg keinen 3D-Link statt eines falschen.
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("hvv_gti.stationmatch")


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold().replace("hbf", "hauptbahnhof")).strip()


def _gti_number(text: str | None) -> int | None:
    match = re.search(r"\d+", text or "")
    return int(match.group()) if match else None


def _refs(label: str) -> set[int]:
    """Gleisnummern eines Bahnsteigs aus "Hamburg Hbf · Gleis 5/6" -> {5, 6}."""
    tail = label.rsplit("Gleis", 1)[-1] if "Gleis" in label else ""
    return {int(n) for n in re.findall(r"\d+", tail)}


class StationModels:
    def __init__(self, entries: list[dict]):
        self.entries = entries

    @classmethod
    def load(cls, directory: Path) -> "StationModels":
        try:
            return cls(json.loads((Path(directory) / "index.json").read_text()))
        except (OSError, ValueError) as exc:
            log.warning("Keine 3D-Bahnhofsmodelle (%s)", exc)
            return cls([])

    def _station(self, names: list[str]) -> dict | None:
        wanted = {_norm(n) for n in names if n}
        for entry in self.entries:
            if wanted & {_norm(n) for n in [entry["name"], *entry.get("names", [])]}:
                return entry
        return None

    @staticmethod
    def _platform(entry: dict, text: str, mode: str | None, names: list[str]) -> dict | None:
        number = _gti_number(text)
        if number is None or mode not in ("S", "U", "R"):
            return None
        found = [p for p in entry.get("platforms", []) if p.get("mode") == mode and number in _refs(p["label"])]
        if len(found) > 1:  # z. B. U-Bahn "Gleis 1" an Hauptbahnhof Nord und Moenckebergstrasse: der Stationsname entscheidet
            hinted = [p for p in found if any(_norm(n) and _norm(n) in _norm(p["label"]) for n in names)]
            found = hinted if len(hinted) == 1 else []
        return found[0] if len(found) == 1 else None

    def match(self, transfer: dict, arriving_line_mode: str | None, leaving_line_mode: str | None) -> dict | None:
        names = [transfer.get("station"), transfer.get("toStation")]
        entry = self._station(names)
        if entry is None:
            return None
        arrive = self._platform(entry, transfer.get("arrPlatform", ""), arriving_line_mode, names)
        leave = self._platform(entry, transfer.get("depPlatform", ""), leaving_line_mode, names)
        if arrive is None or leave is None or arrive["id"] == leave["id"]:
            return None
        return {"id": entry["id"], "from": arrive["id"], "to": leave["id"]}
