"""Einmalig: hvv_stop_event vom alten Schema (route = "S5 Stade → Neugraben") auf das neue umschreiben.

Neu: Tag `route` = Linie ("S5"), `direction` = Fahrtrichtung der Strecke ("Neugraben"), `destination` = Ziel laut GTI.
Der Lauf ist idempotent: nur Punkte ohne Tag `destination` werden umgeschrieben und danach geloescht. Nach dem Neustart
des Collectors mit neuem Image noch einmal ausfuehren, falls der alte Container zwischendurch weitergeschrieben hat.

    python3 scripts/migrate_route_tags.py [--dry-run]
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hvv_gti.config import load_dotenv  # noqa: E402
from hvv_gti.influx import InfluxWriter, line_protocol  # noqa: E402

MEASUREMENT = "hvv_stop_event"
TAGS = ("route", "line", "station", "kind", "direction", "origin")
FIELDS = ("delay_s", "cancelled", "extra", "service_id")
HEADING_RENAMES = {("RE5", "Hamburg Hbf"): "Hamburg"}


def new_tags(old: dict) -> dict:
    old_route = old["route"]
    destination = old["direction"]
    if " → " in old_route:
        route = old_route.split(" ", 1)[0]
        heading = old_route.split(" → ", 1)[1]
        heading = HEADING_RENAMES.get((route, heading), heading)
    else:  # Testpunkte aus der Anfangszeit ohne route-Tag
        route, heading = old["line"], destination
    return {
        "route": route,
        "line": old["line"],
        "station": old["station"],
        "kind": old["kind"],
        "direction": heading,
        "destination": destination,
        "origin": old["origin"],
    }


def query(url: str, org: str, token: str, flux: str) -> list[dict]:
    req = urllib.request.Request(
        f"{url}/api/v2/query?org={urllib.parse.quote(org)}",
        data=json.dumps({"query": flux, "type": "flux"}).encode(),
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json", "Accept": "application/csv"},
    )
    try:
        text = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Influx HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
    rows, header = [], None
    for raw in text.splitlines():
        if not raw.strip():
            header = None
            continue
        cells = next(csv.reader(io.StringIO(raw)))
        if header is None:
            header = cells
        else:
            rows.append(dict(zip(header, cells)))
    return rows


def delete(url: str, org: str, bucket: str, token: str, start: str, stop: str, predicate: str) -> None:
    req = urllib.request.Request(
        f"{url}/api/v2/delete?org={urllib.parse.quote(org)}&bucket={urllib.parse.quote(bucket)}",
        data=json.dumps({"start": start, "stop": stop, "predicate": predicate}).encode(),
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=60)


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    env = os.environ
    url, org, bucket = env.get("INFLUX_URL", "http://localhost:8086").rstrip("/"), env["INFLUX_ORG"], env.get("INFLUX_BUCKET", "hvv")
    write_token = env["INFLUX_TOKEN"]
    read_token = env.get("INFLUX_READ_TOKEN") or write_token

    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -800d, stop: 30d)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT}")
  |> filter(fn: (r) => not exists r.destination)
  |> map(fn: (r) => ({{r with {", ".join(f'{t}: if exists r.{t} then r.{t} else ""' for t in TAGS)}}}))
  |> pivot(rowKey: ["_time", {", ".join(json.dumps(t) for t in TAGS)}], columnKey: ["_field"], valueColumn: "_value")
  |> group()
"""
    rows = query(url, org, read_token, flux)
    lines, old_routes, orphans = [], set(), []
    for row in rows:
        if not row.get("_time"):
            continue
        old = {t: row.get(t, "") for t in TAGS}
        fields = {}
        for name in FIELDS:
            if row.get(name, "") != "":
                fields[name] = int(float(row[name]))
        ts = datetime.fromisoformat(row["_time"].replace("Z", "+00:00"))
        ts_ns = int(ts.timestamp()) * 1_000_000_000
        lines.append(line_protocol(MEASUREMENT, new_tags(old), fields, ts_ns))
        if old["route"]:
            old_routes.add(old["route"])
        else:
            orphans.append((old, row["_time"]))
    print(f"{len(lines)} Punkte im alten Schema, {len(old_routes)} alte Streckennamen, {len(orphans)} ohne route-Tag")
    if args.dry_run or not lines:
        return 0

    InfluxWriter(url, org, bucket, write_token).write(lines)
    for route in sorted(old_routes):
        delete(url, org, bucket, write_token, "1970-01-01T00:00:00Z", "2100-01-01T00:00:00Z", f'_measurement="{MEASUREMENT}" AND route={quote(route)}')
    for old, iso in orphans:
        start = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        stop = datetime.fromtimestamp(start.timestamp() + 1, start.tzinfo)
        predicate = f'_measurement="{MEASUREMENT}" AND ' + " AND ".join(f"{t}={quote(old[t])}" for t in ("line", "station", "kind", "direction", "origin"))
        delete(url, org, bucket, write_token, start.isoformat(), stop.isoformat(), predicate)
    print("migriert und alte Punkte geloescht")
    return 0


if __name__ == "__main__":
    sys.exit(main())
