"""Daten zwischen den Maschinen: Historie aus InfluxDB exportieren (Pi) und Datenpakete in TimescaleDB einlesen (neue Maschine).

Format ist immer Line-Protocol, gzip-komprimiert (`*.lp.gz`). Der Export liest je Messung und UTC-Tag aus dem Influx-Bucket und schreibt eine
Datei `hist-YYYY-MM-DD.lp.gz`; der Import nimmt solche Dateien und die Live-Pakete des Shippers (spool.py) gleichermassen und merkt sich
in `hvv_import_log`, was schon drin ist. Das Einlesen ueberschreibt gleiche Punkte (siehe tsdb.py), Ueberlappungen sind harmlos.
"""

import csv
import gzip
import io
import json
import logging
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .github import GithubError, Releases
from .influx import line_protocol
from .tsdb import Loader, ensure_schema

log = logging.getLogger("hvv_gti.migrate")
UTC = timezone.utc
SKIP = {"", "result", "table", "_start", "_stop", "_time", "_value", "_field", "_measurement"}


# ---- Export aus InfluxDB (Pi) ----


class InfluxExport:
    def __init__(self, url: str, org: str, bucket: str, token: str, timeout: float = 180.0):
        self.url, self.org, self.bucket, self.token, self.timeout = url.rstrip("/"), org, bucket, token, timeout

    def _rows(self, flux: str) -> Iterator[tuple[dict, dict]]:
        """Zeilen der Antwort als (Spalten, Typen je Spalte); Typen aus der #datatype-Zeile jeder Tabelle."""
        body = json.dumps({"query": flux, "type": "flux", "dialect": {"annotations": ["datatype"], "header": True, "delimiter": ",", "commentPrefix": "#"}}).encode()
        req = urllib.request.Request(f"{self.url}/api/v2/query?org={self.org}", data=body, method="POST", headers={"Authorization": f"Token {self.token}", "Content-Type": "application/json", "Accept": "application/csv"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OSError(f"Influx: {exc}") from exc
        types, header = [], None
        for record in csv.reader(io.StringIO(text)):
            if not record:
                header = None
                continue
            if record[0] == "#datatype":
                types, header = record, None
            elif header is None and "result" in record:
                header = record
            elif header is not None and len(record) == len(header):
                yield dict(zip(header, record)), dict(zip(header, types))

    def measurements(self) -> list[str]:
        flux = f'import "influxdata/influxdb/schema"\nschema.measurements(bucket: "{self.bucket}")'
        return sorted(r["_value"] for r, _ in self._rows(flux) if r["_value"].startswith("hvv_"))

    def day_lines(self, measurement: str, start: datetime, stop: datetime) -> list[str]:
        flux = (f'from(bucket: "{self.bucket}") |> range(start: {start:%Y-%m-%dT%H:%M:%SZ}, stop: {stop:%Y-%m-%dT%H:%M:%SZ}) '
                f'|> filter(fn: (r) => r._measurement == "{measurement}")')
        points: dict[tuple, dict] = {}
        for row, types in self._rows(flux):
            tags = {k: v for k, v in row.items() if k not in SKIP and v != ""}
            kind = types["_value"]
            raw = row["_value"]
            value = int(raw) if kind in ("long", "unsignedLong") else float(raw) if kind == "double" else raw == "true" if kind == "boolean" else raw
            when = datetime.fromisoformat(row["_time"].replace("Z", "+00:00"))
            ns = int(when.timestamp()) * 1_000_000_000 + when.microsecond * 1000
            points.setdefault((tuple(sorted(tags.items())), ns), {})[row["_field"]] = value
        return [line_protocol(measurement, dict(tags), fields, ns) for (tags, ns), fields in sorted(points.items(), key=lambda kv: kv[0][1])]


def export_history(src: InfluxExport, days: int, out_dir: Path, now: datetime | None = None) -> list[Path]:
    """Je UTC-Tag der letzten `days` Tage eine Datei hist-YYYY-MM-DD.lp.gz (nur Tage mit Daten); gibt die Dateien zurueck."""
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    measurements = src.measurements()
    files = []
    for back in range(days, -1, -1):
        start = today - timedelta(days=back)
        stop = min(start + timedelta(days=1), now + timedelta(seconds=1))
        lines = []
        for m in measurements:
            lines += src.day_lines(m, start, stop)
        if not lines:
            continue
        path = out_dir / f"hist-{start:%Y-%m-%d}.lp.gz"
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            fh.write("\n".join(lines) + "\n")
        tmp.replace(path)
        log.info("%s: %d Punkte", path.name, len(lines))
        files.append(path)
    return files


# ---- Einlesen in TimescaleDB (neue Maschine) ----


def lines_of(path: Path) -> Iterator[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        yield from fh


def import_file(conn, path: Path, label: str | None = None) -> dict:
    """Eine Datei einlesen und in hvv_import_log vermerken."""
    counts = Loader(conn).load(lines_of(path))
    with conn.cursor() as cur:
        cur.execute("insert into hvv_import_log (asset, points, skipped) values (%s, %s, %s) on conflict (asset) do update set imported_at = now(), points = excluded.points, skipped = excluded.skipped",
                    (label or path.name, counts["points"], counts["skipped"]))
    conn.commit()
    return counts


def import_pending(conn, github: Releases, tmp: Path | None = None, prefix: str = "data-") -> list[tuple[str, dict]]:
    """Alle Pakete aus den Releases `data-*`, die noch nicht eingelesen sind (in Namensreihenfolge); gibt (Paket, Zahlen) zurueck."""
    with conn.cursor() as cur:
        cur.execute("select asset from hvv_import_log")
        done = {r[0] for r in cur.fetchall()}
    results = []
    with tempfile.TemporaryDirectory(dir=tmp) as work:
        for release in sorted(github.list_releases(prefix), key=lambda r: r["tag_name"]):
            for asset in sorted(github.assets(release), key=lambda a: a["name"]):
                label = f"{release['tag_name']}/{asset['name']}"
                if label in done or not asset["name"].endswith(".lp.gz"):
                    continue
                path = Path(work) / asset["name"]
                try:
                    github.download(asset, path)
                    counts = import_file(conn, path, label)
                except (GithubError, OSError) as exc:
                    log.error("%s: %s (naechster Versuch beim naechsten Lauf)", label, exc)
                    continue
                finally:
                    path.unlink(missing_ok=True)
                log.info("%s: %d Punkte eingelesen, %d uebersprungen", label, counts["points"], counts["skipped"])
                results.append((label, counts))
    return results


def run_importer(dsn: str, github: Releases, stop: threading.Event, interval: float, tmp: Path | None = None) -> None:
    import psycopg

    while not stop.is_set():
        try:
            with psycopg.connect(dsn) as conn:
                ensure_schema(conn)
                import_pending(conn, github, tmp)
        except (psycopg.Error, GithubError, OSError) as exc:
            log.error("Einlesen fehlgeschlagen: %s", exc)
        stop.wait(interval)


# ---- Dateien, die nicht in Git liegen (Kartenkacheln, gelernte Gleise, Logos) ----

ASSET_PATHS = ("map/data/hamburg.pmtiles", "map/data/places.json", "map/data/tracks.json", "map/data/signals.json", "data/icons")


def pack_assets(root: Path, out: Path) -> list[str]:
    """Alles aus ASSET_PATHS (was es gibt) in ein tar.gz packen; gibt die enthaltenen Pfade zurueck."""
    import tarfile

    names = []
    with tarfile.open(out, "w:gz") as tar:
        for rel in ASSET_PATHS:
            path = root / rel
            if path.exists():
                tar.add(path, arcname=rel)
                names.append(rel)
    return names


def unpack_assets(archive: Path, root: Path) -> list[str]:
    """Paket entpacken; nur Pfade unter ASSET_PATHS, nichts ausserhalb von `root` (Schutz vor manipulierten Paketen)."""
    import tarfile

    done = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            ok = any(name == p or name.startswith(p + "/") for p in ASSET_PATHS)
            target = (root / name).resolve()
            if not ok or not member.isfile() and not member.isdir() or root.resolve() not in (target, *target.parents):
                log.warning("Paketinhalt uebersprungen: %s", name)
                continue
            tar.extract(member, root, filter="data")
            if member.isfile():
                done.append(name)
    return done
