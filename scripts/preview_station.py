"""Zeichnet ein Bahnhofsmodell (map/seed/stations/<id>.json) als gestapelte Isometrie nach PNG, ohne Zusatzbibliotheken.

Zur Kontrolle der Daten ohne Browser: jede Ebene schwebt in eigener Hoehe, Treppen/Aufzuege verbinden sie, ein Umsteigeweg
(--from/--to, Teil des Bahnsteignamens) wird rot gezeichnet.
Aufruf: python3 scripts/preview_station.py hamburg-hbf out.png [--from "Gleis 3/4" --to "Süd · Gleis 3"] [--level -1]
"""

import json
import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W, H = 1500, 1000
COL = {"floor": (122, 135, 160), "room": (60, 66, 82), "part": (46, 50, 62), "walk": (150, 160, 175), "track": (60, 70, 76),
       "step": (226, 168, 54), "esc": (240, 138, 60), "lift": (84, 190, 214)}
MODE_COL = {"S": (47, 174, 106), "U": (58, 142, 224), "R": (217, 96, 90), "?": (132, 151, 160)}
FILL_ORDER = ["part", "room", "walk", "floor", "track"]


class Canvas:
    def __init__(self, w, h, bg=(18, 20, 26)):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def set(self, x, y, c, a=1.0):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            if a >= 1:
                self.px[i:i + 3] = bytes(c)
            else:
                for k in range(3):
                    self.px[i + k] = int(self.px[i + k] * (1 - a) + c[k] * a)

    def poly(self, pts, c, a=1.0):
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        for y in range(max(int(min(ys)), 0), min(int(max(ys)) + 1, self.h)):
            xs = []
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                if (y1 <= y < y2) or (y2 <= y < y1):
                    xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                for x in range(max(int(xs[i]), 0), min(int(xs[i + 1]) + 1, self.w)):
                    self.set(x, y, c, a)

    def line(self, p, q, c, width=1, a=1.0):
        n = int(max(abs(q[0] - p[0]), abs(q[1] - p[1]))) + 1
        r = width // 2
        for i in range(n + 1):
            x = p[0] + (q[0] - p[0]) * i / n
            y = p[1] + (q[1] - p[1]) * i / n
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    self.set(int(x) + dx, int(y) + dy, c, a)

    def png(self, path):
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main():
    sid, out = sys.argv[1], sys.argv[2]
    opts = dict(zip(sys.argv[3::2], sys.argv[4::2]))
    m = json.loads((ROOT / "map" / "seed" / "stations" / f"{sid}.json").read_text())
    lon0, lat0 = m["center"]
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110574
    LH = 12.0  # m zwischen den Ebenen (auseinandergezogen)
    only = float(opts["--level"]) if "--level" in opts else None
    pts = [(c[0], c[1]) for f in m["features"]["features"] for c in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "Point" else f["geometry"]["coordinates"] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"][0])]
    xs = [(p[0] - lon0) * kx for p in pts]; ys = [(p[1] - lat0) * ky for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1
    s = min(W, H) * 0.95 / (span * 1.15)
    if only is None:
        s *= 0.8
    def proj(lon, lat, level):
        x, y = (lon - lon0) * kx, (lat - lat0) * ky
        sx = (x - y) * math.cos(math.radians(30)) * s * (0.85 if only is None else 1)
        sy = (x + y) * math.sin(math.radians(30)) * s * (0.85 if only is None else 1)
        return W / 2 + sx, H * (0.72 if only is None else 0.55) + sy * (1 if only is None else 1) - level * LH * s * (0.85 if only is None else 0)
    cv = Canvas(W, H)
    feats = m["features"]["features"]
    levels = sorted({f["properties"]["l"] for f in feats if f["properties"]["k"] in ("room", "walk", "floor", "track")})
    if only is not None:
        levels = [only]
    def draw(f, level_for, color, thick=3, alpha=1.0):
        ring = [proj(c[0], c[1], level_for) for c in f["geometry"]["coordinates"][0]]
        if thick:
            cv.poly([(x, y + thick) for x, y in ring], tuple(int(v * 0.55) for v in color))
        cv.poly(ring, color, alpha)

    for level in levels:
        for kind in FILL_ORDER:
            for f in feats:
                p = f["properties"]
                if p["k"] != kind or f["geometry"]["type"] != "Polygon":
                    continue
                if kind == "part":
                    if only is None and level == levels[0]:
                        draw(f, 0.0, COL["part"], 0, 0.4)
                    continue
                if p["l"] != level:
                    continue
                draw(f, level, MODE_COL.get(p.get("m"), COL["floor"]) if kind == "floor" else COL[kind], 3 if kind in ("floor", "walk") else 0)
    for kind in ("step", "esc"):
        for f in sorted((f for f in feats if f["properties"]["k"] == kind), key=lambda f: f["properties"]["l"]):
            l = f["properties"]["l"]
            if only is None or abs(l - only) <= 1:
                draw(f, l, COL[kind], 2)
    for f in feats:
        p = f["properties"]
        if p["k"] == "lift" and (only is None or p["l"] <= only <= p["h"]):
            ring0 = [proj(c[0], c[1], p["l"]) for c in f["geometry"]["coordinates"][0]]
            ring1 = [proj(c[0], c[1], p["h"]) for c in f["geometry"]["coordinates"][0]]
            for a, b in zip(ring0, ring1):
                cv.line(a, b, COL["lift"], 3)
            cv.poly(ring1, COL["lift"])
    if "--from" in opts and "--to" in opts:
        by = {p["id"]: p["label"] for p in m["platforms"]}
        for path in m["paths"]:
            if opts["--from"] in by[path["from"]] and opts["--to"] in by[path["to"]] and path.get("g"):
                print("Weg:", by[path["from"]], "->", by[path["to"]], path["s"], "s", path["m"], "m", [(x["t"], x["a"], x["b"], x["m"]) for x in path["steps"]])
                pl = [proj(c[0], c[1], c[2]) for c in path["g"]]
                for a, b in zip(pl, pl[1:]):
                    cv.line(a, b, (255, 70, 70), 4)
                break
    cv.png(out)


main()
