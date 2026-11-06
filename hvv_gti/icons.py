"""Linien-Logos vom Geofox-Icon-Service (https://cloud.geofox.de/icon/line), lokal zwischengespeichert.

Die GTI selbst liefert keine Logos (listLines/departureList kennen nur Name und Typ). Der Icon-Service braucht keinen
Login, wohl aber den `lineKey` aus `listLines`; deshalb baut `IconStore` daraus einen Index Linienname -> lineKey.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .client import GtiClient

log = logging.getLogger("hvv_gti.icons")

ICON_URL = "https://cloud.geofox.de/icon/line"
INDEX_MAX_AGE_S = 7 * 24 * 3600
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")
CONTENT_TYPES = {"svg": "image/svg+xml", "png": "image/png"}
# Gleicher Linienname bei mehreren Betreibern: Schienenverkehr gewinnt (S1, U1, RE5), dann Bus.
TYPE_RANK = {"TRAIN": 0, "BUS": 1}


FILL_RGB = re.compile(r'fill="rgb\((\d+),(\d+),(\d+)\)"')


def badge_color(svg: str) -> str | None:
    """Farbe des Linienschildes (erste Fuellfarbe, die nicht Weiss/Schwarz ist) als #rrggbb; Regionalzuege haben keine."""
    for r, g, b in FILL_RGB.findall(svg):
        rgb = (int(r), int(g), int(b))
        if rgb not in ((255, 255, 255), (0, 0, 0)):
            return "#%02x%02x%02x" % rgb
    return None


def build_index(lines: list[dict]) -> dict[str, str]:
    best: dict[str, tuple[int, str]] = {}
    for line in lines:
        name, key = line.get("name"), line.get("id")
        if not name or not key or not line.get("exists", True):
            continue
        rank = TYPE_RANK.get(line.get("type", {}).get("simpleType"), 2)
        if name not in best or rank < best[name][0]:
            best[name] = (rank, key)
    return {name: key for name, (_, key) in best.items()}


class IconStore:
    def __init__(self, directory: str | Path, client: GtiClient | None = None, timeout: float = 15.0):
        self.dir = Path(directory)
        self.client = client
        self.timeout = timeout
        self._index: dict[str, str] | None = None
        self._lock = threading.Lock()

    def index(self) -> dict[str, str]:
        with self._lock:
            if self._index is not None:
                return self._index
            path = self.dir / "_index.json"
            try:
                if time.time() - path.stat().st_mtime < INDEX_MAX_AGE_S:
                    self._index = json.loads(path.read_text("utf-8"))
                    return self._index
            except (OSError, ValueError):
                pass
            if self.client is None:
                return {}
            self._index = build_index(self.client.post("listLines", {}).get("lines", []))
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._index, ensure_ascii=False), "utf-8")
            tmp.replace(path)
            log.info("Linienindex: %d Linien", len(self._index))
            return self._index

    def _download(self, key: str, fmt: str, height: int) -> bytes | None:
        query = urllib.parse.urlencode({"height": height, "lineKey": key, "fileFormat": fmt.upper()})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(f"{ICON_URL}?{query}", timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(0.5)
        return None

    def colors(self) -> dict[str, str]:
        """Linienfarben aus den lokalen SVG-Icons, damit Karte und Logos dieselben Farben zeigen."""
        out: dict[str, str] = {}
        for path in sorted(self.dir.glob("*.svg")):
            try:
                color = badge_color(path.read_text("utf-8", errors="ignore"))
            except OSError:
                continue
            if color:
                out[path.stem] = color
        return out

    def get(self, name: str, fmt: str = "svg") -> Path | None:
        """Pfad zur Icon-Datei (wird bei Bedarf geladen) oder None, wenn die Linie unbekannt ist."""
        if fmt not in CONTENT_TYPES or not SAFE_NAME.match(name):
            return None
        path = self.dir / f"{name}.{fmt}"
        if path.exists():
            return path
        key = self.index().get(name)
        if key is None:
            return None
        data = self._download(key, fmt, 64 if fmt == "svg" else 48)
        if not data:
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    def prefetch(self, names: list[str] | None = None, formats: tuple[str, ...] = ("svg",), pause: float = 0.2) -> tuple[int, list[str]]:
        """Laedt Icons vor. Ohne `names` alle Schienenlinien (S, U, RE, RB, AKN ...). Rueckgabe: (geladen, fehlend)."""
        index = self.index()
        if names is None:
            lines = self.client.post("listLines", {}).get("lines", []) if self.client else []
            names = sorted({l["name"] for l in lines if l.get("type", {}).get("simpleType") == "TRAIN" and l.get("name")})
        done, missing = 0, []
        for name in names:
            for fmt in formats:
                fresh = not (self.dir / f"{name}.{fmt}").exists()
                if self.get(name, fmt) is None:
                    missing.append(name if name in index else f"{name} (kein lineKey)")
                    break
                if fresh:
                    done += 1
                    time.sleep(pause)
        return done, missing
