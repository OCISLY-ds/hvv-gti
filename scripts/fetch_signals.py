"""Holt die Hauptsignale (Haltsignale) im S-Bahn-Gebiet aus OpenStreetMap (Overpass) nach map/data/signals.json.

Je Signal wird die Fahrtrichtung bestimmt, fuer die es gilt: Richtung des OSM-Weges am Signal, bei
`railway:signal:direction=backward` umgekehrt. Signale ohne Richtung (oder `both`) werden uebersprungen, weil sie auf dem
Nachbargleis landen koennten. Ergebnis: Liste [lon, lat, Kurs in Grad im Uhrzeigersinn von Norden].

Aufruf: python3 scripts/fetch_signals.py   (bbox/Kacheln siehe unten; der Overpass-Server will einen User-Agent)
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
AGENT = "hvv-gti/0.1 (private S-Bahn live map)"
BBOX = (9.44, 53.43, 10.35, 53.68)  # lon1, lat1, lon2, lat2: Gebiet der S-Bahn-Gleise in tracks.json
TILE_LON = 0.15
OUT = Path(__file__).resolve().parent.parent / "map" / "data" / "signals.json"

QUERY = """[out:json][timeout:120];
(node["railway"="signal"]["railway:signal:main"]({b});node["railway"="signal"]["railway:signal:combined"]({b});)->.s;
way(bn.s)["railway"~"^(rail|light_rail)$"];
out body geom;
.s out body;"""


def overpass(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(6):
        server = SERVERS[attempt % len(SERVERS)]
        try:
            req = urllib.request.Request(server, data=data, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # Server ausgelastet (502/504/429), Zeitueberschreitung, kaputte Antwort
            wait = 5 * 2**attempt
            print(f"  {server}: {exc}; neuer Versuch in {wait} s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit("Overpass nicht erreichbar")


def bearing(x1: float, y1: float, x2: float, y2: float) -> float:
    dx = (x2 - x1) * math.cos(math.radians((y1 + y2) / 2))
    return math.degrees(math.atan2(dx, y2 - y1)) % 360


def main() -> None:
    lon1, lat1, lon2, lat2 = BBOX
    signals: dict[int, list] = {}
    skipped = {"keine Richtung": 0, "kein Gleis": 0}
    x = lon1
    while x < lon2:
        box = f"{lat1},{x:.4f},{lat2},{min(x + TILE_LON, lon2):.4f}"
        print("Kachel", box, file=sys.stderr)
        elements = overpass(QUERY.format(b=box))["elements"]
        ways = [e for e in elements if e["type"] == "way"]
        position: dict[int, tuple[dict, int]] = {}
        for way in ways:
            for i, node_id in enumerate(way["nodes"]):
                position.setdefault(node_id, (way, i))
        for e in elements:
            if e["type"] != "node" or e["id"] in signals:
                continue
            tags = e.get("tags", {})
            direction = tags.get("railway:signal:direction")
            if direction not in ("forward", "backward"):
                skipped["keine Richtung"] += 1
                continue
            found = position.get(e["id"])
            if found is None:
                skipped["kein Gleis"] += 1
                continue
            way, i = found
            geom = way["geometry"]
            a, b = geom[max(i - 1, 0)], geom[min(i + 1, len(geom) - 1)]
            heading = bearing(a["lon"], a["lat"], b["lon"], b["lat"])
            if direction == "backward":
                heading = (heading + 180) % 360
            signals[e["id"]] = [round(e["lon"], 6), round(e["lat"], 6), round(heading)]
        x += TILE_LON
        time.sleep(2)
    OUT.write_text(json.dumps(sorted(signals.values()), separators=(",", ":")), "utf-8")
    print(f"{len(signals)} Signale -> {OUT}; uebersprungen: {skipped}")


if __name__ == "__main__":
    main()
