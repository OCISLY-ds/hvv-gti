#!/usr/bin/env python3
"""Ortsnamen (Stadt, Stadtteil, Viertel, Dorf) fuer die Routenkarte aus OSM-Extrakten (.osm.pbf) lesen -> map/data/places.json.

Reiner Python-Leser ohne Zusatzpakete, liest Block fuer Block (wenig Speicher) und ueberspringt Bloecke ohne place-Werte.
Aufruf: python3 scripts/extract_places.py map/tiles/work/*.osm.pbf  [-o map/data/places.json]
Ausgabe: [[Name, Laenge, Breite, Klasse], ...], Klasse c(ity) t(own) s(uburb) q(uarter) n(eighbourhood) v(illage) h(amlet) a(irport).
"""
import argparse
import json
import struct
import sys
import zlib

BBOX = (8.6, 53.1, 10.65, 53.95)  # lon_min, lat_min, lon_max, lat_max wie die Kacheln
PLACES = {b"city": "c", b"town": "t", b"suburb": "s", b"quarter": "q", b"neighbourhood": "n", b"village": "v", b"hamlet": "h"}
AERODROME = b"aerodrome"  # aeroway=aerodrome als Knoten (Flughaefen sind meist Flaechen, ohne Knoten fehlen sie, siehe EXTRA)
# Flughaefen im Kartengebiet, die in OSM nur als Flaeche stehen (Name, Laenge, Breite, "a")
EXTRA = [["Hamburg Airport", 9.9882, 53.6304, "a"], ["Airbus Finkenwerder", 9.8356, 53.5353, "a"]]


def varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, i
        shift += 7


def fields(buf):
    """Protobuf-Nachricht -> Liste (Feldnummer, Wert); Wert = int oder bytes."""
    i, out = 0, []
    while i < len(buf):
        key, i = varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = varint(buf, i)
        elif wire == 2:
            n, i = varint(buf, i)
            v, i = buf[i : i + n], i + n
        elif wire == 1:
            v, i = buf[i : i + 8], i + 8
        elif wire == 5:
            v, i = buf[i : i + 4], i + 4
        else:
            raise ValueError("Wire-Typ %d" % wire)
        out.append((num, v))
    return out


def packed(buf):
    i, out = 0, []
    while i < len(buf):
        v, i = varint(buf, i)
        out.append(v)
    return out


zig = lambda v: (v >> 1) ^ -(v & 1)


def blobs(path):
    with open(path, "rb") as f:
        while True:
            head = f.read(4)
            if len(head) < 4:
                return
            header = dict(fields(f.read(struct.unpack(">I", head)[0])))
            blob = dict(fields(f.read(header[3])))
            data = zlib.decompress(blob[3]) if 3 in blob else blob.get(1, b"")
            if header[1] == b"OSMData":
                yield data


def read(path):
    for data in blobs(path):
        block = fields(data)
        strings = [v for n, v in fields(next(v for n, v in block if n == 1)) if n == 1]
        has_place = b"place" in strings and any(s in PLACES for s in strings)
        has_air = b"aeroway" in strings and AERODROME in strings
        if not has_place and not has_air:
            continue  # Block ohne place=... gar nicht erst entschluesseln
        key = strings.index(b"place") if has_place else -1
        akey = strings.index(b"aeroway") if has_air else -1
        gran = next((v for n, v in block if n == 17), 100)
        lat0 = zig(next((v for n, v in block if n == 19), 0))
        lon0 = zig(next((v for n, v in block if n == 20), 0))
        for n, group in block:
            if n != 2:
                continue
            for gn, dense in fields(group):
                if gn != 2:
                    continue
                d = {k: v for k, v in fields(dense)}
                lats, lons, kv = (packed(d.get(k, b"")) for k in (8, 9, 10))
                lat = lon = 0
                j = 0
                for la, lo in zip(lats, lons):
                    lat += zig(la)
                    lon += zig(lo)
                    tags = {}
                    while j < len(kv) and kv[j] != 0:
                        tags[kv[j]] = kv[j + 1]
                        j += 2
                    j += 1
                    kind = PLACES.get(strings[tags[key]]) if key in tags else None
                    if kind is None and akey in tags and strings[tags[akey]] == AERODROME:
                        kind = "a"
                    if kind:
                        names = {strings[k]: strings[v] for k, v in tags.items()}
                        name = names.get(b"name:de") or names.get(b"name")
                        x, y = 1e-9 * (lon0 + gran * lon), 1e-9 * (lat0 + gran * lat)
                        if name and BBOX[0] <= x <= BBOX[2] and BBOX[1] <= y <= BBOX[3]:
                            yield [name.decode(), round(x, 5), round(y, 5), kind]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default="map/data/places.json")
    args = ap.parse_args()
    seen, out = set(), [list(p) for p in EXTRA]
    for path in args.files:
        n = len(out)
        for p in read(path):
            k = (p[0], round(p[1], 1), round(p[2], 1)) if p[3] == "a" else (p[0], round(p[1], 3), round(p[2], 3))
            near_airport = p[3] == "a" and any(o[3] == "a" and abs(o[1] - p[1]) < 0.08 and abs(o[2] - p[2]) < 0.08 for o in out)
            if k not in seen and not near_airport:
                seen.add(k)
                out.append(p)
        print(f"{path}: {len(out) - n} Orte", file=sys.stderr)
    out.sort(key=lambda p: (p[3], p[0]))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(out)} Orte -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
