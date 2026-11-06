#!/usr/bin/env python3
"""Traegt Grenzen und Kartenmitte in den PMTiles-Header ein, wenn tilemaker sie leer gelassen hat.

Das passiert, weil `osmium merge` den Bbox-Header der Eingabe nicht uebernimmt; MapLibre laedt dann nur Kacheln innerhalb
der Grenzen 0/0/0/0, die Karte bleibt leer. Aufruf: fix_header.py DATEI lon1,lat1,lon2,lat2
"""
import struct
import sys

path, bbox = sys.argv[1], [float(x) for x in sys.argv[2].split(",")]
lon1, lat1, lon2, lat2 = bbox
with open(path, "r+b") as f:
    head = f.read(127)
    if head[:7] != b"PMTiles" or head[7] != 3:
        sys.exit(f"{path}: kein PMTiles v3")
    if any(struct.unpack("<4i", head[102:118])):
        print("Header hat bereits Grenzen")
        sys.exit(0)
    e7 = lambda v: round(v * 1e7)
    f.seek(102)
    f.write(struct.pack("<4i", e7(lon1), e7(lat1), e7(lon2), e7(lat2)))
    f.write(struct.pack("<Bii", min(max(head[100], 10), head[101]), e7((lon1 + lon2) / 2), e7((lat1 + lat2) / 2)))
print(f"Header: Grenzen {bbox}")
