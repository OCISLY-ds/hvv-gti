"""Baut die Bahnhofsmodelle (3D-Ansicht, Umsteigewege) aus OpenStreetMap nach map/seed/stations/<id>.json.

Pro Bahnhof eine Overpass-Abfrage (Bahnsteige, Fusswege, Treppen, Aufzuege, Eingaenge, Raeume, Gebaeudeteile, Gleise); die
Rohdaten werden in map/data/osm-cache/ abgelegt (gitignored), damit ein erneuter Lauf ohne Netz geht (`--offline`).
Aufruf: python3 scripts/build_stations.py [--offline] [id ...]   (ohne id alle Bahnhoefe unten)
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hvv_gti.stationmap import Station  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "map" / "data" / "osm-cache"
OUT = ROOT / "map" / "seed" / "stations"
SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
AGENT = "hvv-gti/0.1 (private S-Bahn live map)"

# id, Name, (Sued, West, Nord, Ost): grosse Umsteigebahnhoefe; ALIASES: weitere GTI-Haltestellennamen desselben Komplexes
ALIASES = {"hamburg-hbf": ["Hauptbahnhof Nord", "Hauptbahnhof Süd", "Hauptbahnhof/Mönckebergstraße", "Mönckebergstraße", "Hamburg Hauptbahnhof"]}
STATIONS = [
    ("hamburg-hbf", "Hamburg Hbf", (53.5500, 10.0000, 53.5560, 10.0140)),
    ("berliner-tor", "Berliner Tor", (53.5500, 10.0190, 53.5545, 10.0290)),
    ("jungfernstieg", "Jungfernstieg", (53.5515, 9.9855, 53.5555, 9.9955)),
    ("altona", "Altona", (53.5490, 9.9290, 53.5545, 9.9400)),
    ("barmbek", "Barmbek", (53.5845, 10.0395, 53.5895, 10.0490)),
    ("landungsbruecken", "Landungsbrücken", (53.5430, 9.9650, 53.5480, 9.9760)),
    ("ohlsdorf", "Ohlsdorf", (53.6150, 10.0120, 53.6215, 10.0260)),
    ("dammtor", "Dammtor", (53.5585, 9.9850, 53.5630, 9.9930)),
]

QUERY = """[out:json][timeout:90];
(
  way["railway"="platform"]({b});
  way["public_transport"="platform"]({b});
  way["highway"~"^(footway|steps|corridor|elevator|pedestrian|path)$"]({b});
  way["indoor"]({b});
  way["conveying"]({b});
  way["building:part"]({b});
  way["railway"~"^(subway|rail|light_rail)$"]({b});
  node["railway"="subway_entrance"]({b});
  node["public_transport"="entrance"]({b});
  node["highway"="elevator"]({b});
);
out body geom;"""


def fetch(sid: str, bbox: tuple, offline: bool) -> list[dict]:
    cache = CACHE / f"{sid}.json"
    if cache.exists():
        return json.loads(cache.read_text())["elements"]
    if offline:
        raise SystemExit(f"{sid}: kein Cache (--offline)")
    data = urllib.parse.urlencode({"data": QUERY.format(b=",".join(str(x) for x in bbox))}).encode()
    for attempt in range(6):
        server = SERVERS[attempt % len(SERVERS)]
        try:
            req = urllib.request.Request(server, data=data, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = json.loads(resp.read())
            CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(raw))
            time.sleep(3)  # hoeflich zum oeffentlichen Server
            return raw["elements"]
        except Exception as exc:
            wait = 5 * 2**attempt
            print(f"  {server}: {exc}; neuer Versuch in {wait} s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit("Overpass nicht erreichbar")


def _entry(sid: str, name: str, model: dict) -> dict:
    """Eintrag in index.json: Auswahl der 3D-Seite und Zuordnung der GTI-Gleise (hvv_gti/stationmatch.py)."""
    return {"id": sid, "name": name, "names": ALIASES.get(sid, []), "center": model["center"], "levels": model["levels"],
            "platforms": [{k: p[k] for k in ("id", "label", "mode", "level")} for p in model["platforms"]]}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for sid, name, (s, w, n, e) in STATIONS:
        if args and sid not in args:
            continue
        elements = fetch(sid, (s, w, n, e), offline)
        station = Station(sid, name, ((w + e) / 2, (s + n) / 2), elements, bounds=(s, w, n, e))
        model = station.model()
        (OUT / f"{sid}.json").write_text(json.dumps(model, separators=(",", ":"), ensure_ascii=False))
        size = (OUT / f"{sid}.json").stat().st_size // 1024
        index.append(_entry(sid, name, model))
        print(f"{name}: {len(model['platforms'])} Bahnsteige, Ebenen {model['levels']}, {len(model['features']['features'])} Objekte, "
              f"{len(model['entrances'])} Eingaenge, Wege {model['stats']}, {size} KB")
    # Index aus allen vorhandenen Bahnhofsdateien (auch wenn nur einzelne neu gebaut wurden)
    known = {i["id"]: i for i in index}
    for sid, name, _ in STATIONS:
        f = OUT / f"{sid}.json"
        if sid not in known and f.exists():
            m = json.loads(f.read_text())
            known[sid] = _entry(sid, name, m)
    ordered = [known[sid] for sid, _, _ in STATIONS if sid in known]
    (OUT / "index.json").write_text(json.dumps(ordered, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
