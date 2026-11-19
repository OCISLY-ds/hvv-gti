"""Sammler ohne Datenbank: Messpunkte als Line-Protocol-Dateien auf Platte (Spool) und per GitHub-Release zur Datenbank verschicken.

`SpoolWriter` ist eine Senke fuer den Collector (wie `InfluxWriter`): je UTC-Stunde eine Datei `hvv-YYYYMMDDTHH.lp`. Abgeschlossene
Stunden werden gzip-komprimiert. `Shipper` buendelt die noch nicht verschickten Stunden zu einem Paket `hvv-<erste>_<letzte>.lp.gz`
(aneinandergehaengte gzip-Teile sind gueltiges gzip), laedt es in das Release `data-YYYY-MM` und raeumt nach der Aufbewahrungsfrist auf.
Scheitert der Upload (kein Netz, GitHub gestoert), bleibt alles liegen und wird beim naechsten Lauf nachgeholt.
"""

import gzip
import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .github import GithubError, Releases
from .influx import InfluxError

log = logging.getLogger("hvv_gti.spool")

HOUR = re.compile(r"^hvv-(\d{8}T\d{2})\.lp(\.gz)?$")


def _hour(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H")


class SpoolWriter:
    def __init__(self, directory: str | Path, clock=time.time):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.Lock()

    def write(self, lines: list[str]) -> None:
        if not lines:
            return
        path = self.dir / f"hvv-{_hour(self.clock())}.lp"
        try:
            with self._lock, path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError as exc:
            raise InfluxError(f"Spool nicht beschreibbar: {exc}") from exc

    def close_finished(self) -> int:
        """Stunden vor der aktuellen komprimieren (.lp -> .lp.gz); gibt die Zahl zurueck."""
        now, done = _hour(self.clock()), 0
        for path in sorted(self.dir.glob("hvv-*.lp")):
            match = HOUR.match(path.name)
            if not match or match.group(1) >= now:
                continue
            target = path.with_name(path.name + ".gz")
            tmp = target.with_suffix(".tmp")
            with self._lock, path.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
            tmp.replace(target)
            path.unlink()
            done += 1
        return done


class MultiWriter:
    """Mehrere Senken: jede bekommt die Zeilen, ein Fehler einer Senke blockiert die anderen nicht, wird aber gemeldet."""

    def __init__(self, sinks: list):
        self.sinks = sinks

    def write(self, lines: list[str]) -> None:
        errors = []
        for sink in self.sinks:
            try:
                sink.write(lines)
            except InfluxError as exc:
                errors.append(str(exc))
        if errors:
            raise InfluxError("; ".join(errors))


class Shipper:
    def __init__(self, spool: SpoolWriter, github: Releases, retention_days: float = 7.0, clock=time.time):
        self.spool, self.github, self.retention_days, self.clock = spool, github, retention_days, clock
        self.manifest = spool.dir / "shipped.json"

    def _shipped(self) -> list[str]:
        try:
            return json.loads(self.manifest.read_text())
        except (OSError, ValueError):
            return []

    def pending(self) -> list[Path]:
        shipped = set(self._shipped())
        return [p for p in sorted(self.spool.dir.glob("hvv-*.lp.gz")) if p.name not in shipped]

    def ship_once(self) -> str | None:
        """Ein Paket aus allen offenen Stunden hochladen. Gibt den Paketnamen zurueck (None: nichts zu tun)."""
        self.spool.close_finished()
        files = self.pending()
        if not files:
            self.cleanup()
            return None
        first, last = HOUR.match(files[0].name).group(1), HOUR.match(files[-1].name).group(1)
        name = f"hvv-{first}_{last}.lp.gz"
        bundle = self.spool.dir / f".{name}.tmp"
        with bundle.open("wb") as out:
            for path in files:
                with path.open("rb") as src:
                    shutil.copyfileobj(src, out)
        try:
            release = self.github.release(f"data-{first[:4]}-{first[4:6]}")
            self.github.upload(release, bundle, name)  # gleicher Name schon da (Wiederholung nach Abbruch): nichts doppelt
        finally:
            bundle.unlink(missing_ok=True)
        self.manifest.write_text(json.dumps(self._shipped() + [p.name for p in files]))
        log.info("%d Stunden als %s verschickt (%d Datei(en))", len(files), name, len(files))
        self.cleanup()
        return name

    def cleanup(self) -> None:
        """Verschickte Dateien nach der Aufbewahrungsfrist loeschen (die Datenbank hat sie dann seit Tagen)."""
        limit = self.clock() - self.retention_days * 86400
        shipped = set(self._shipped())
        for path in list(self.spool.dir.glob("hvv-*.lp.gz")):
            if path.name in shipped and path.stat().st_mtime < limit:
                path.unlink()
        left = {p.name for p in self.spool.dir.glob("hvv-*.lp.gz")}
        self.manifest.write_text(json.dumps(sorted(shipped & left)))

    def run(self, stop: threading.Event, interval: float) -> None:
        stop.wait(60)
        while not stop.is_set():
            try:
                self.ship_once()
            except (GithubError, OSError) as exc:
                log.error("Verschicken fehlgeschlagen, naechster Versuch in %.0f min: %s", interval / 60, exc)
            stop.wait(interval)
