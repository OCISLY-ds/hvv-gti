"""Gleislinien der GTI glaetten: Hin-und-zurueck-Spitzen entfernen und Ecken abrunden.

Die GTI-Geometrie hat Spitzen (Stichstuecke bis ~50 m am Bahnsteigende, 1-m-Zacken) und viele Knicke aus kurzen Segmenten.
Beides wird beim Speichern bereinigt, damit Karte, Signalpositionen und Zugbewegung auf derselben Linie arbeiten. Anfangs- und
Endpunkt (die Haltestelle) bleiben unveraendert.
"""

import math

KX = math.cos(math.radians(53.55)) * 111320
KY = 110540

MIN_STEP_M = 0.5  # naehere aufeinanderfolgende Punkte gelten als einer
SPIKE_TURN_DEG = 140.0  # Umkehr um mehr als das ...
SPIKE_LEG_M = 80.0  # ... mit mindestens einem so kurzen Schenkel ist eine Spitze
ROUND_MIN_DEG = 12.0
ROUND_MIN_LEG_M = 6.0  # an kuerzeren Schenkeln wird nicht gerundet (sonst entstehen Mini-Zacken)
ROUND_MAX_CUT_M = 12.0
ROUND_SHARE = 0.3


def _turn(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> tuple[float, float, float]:
    """Drehwinkel in b (Grad) und Laengen der beiden Schenkel."""
    ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
    lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
    if lu == 0 or lv == 0:
        return 0.0, lu, lv
    cos = max(-1.0, min(1.0, (ux * vx + uy * vy) / (lu * lv)))
    return math.degrees(math.acos(cos)), lu, lv


def clean(points: list[float]) -> list[float]:
    """[x0, y0, x1, y1, ...] (lon, lat) -> bereinigte Liste, auf 5 Nachkommastellen gerundet."""
    pts = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
    if len(pts) < 3:
        return [round(v, 5) for p in pts for v in p]
    m = lambda p: (p[0] * KX, p[1] * KY)

    def dedupe(seq: list[tuple[float, float]]) -> list[tuple[float, float]]:
        kept = [seq[0]]
        for p in seq[1:]:
            a, b = m(kept[-1]), m(p)
            if math.hypot(a[0] - b[0], a[1] - b[1]) >= MIN_STEP_M:
                kept.append(p)
        if len(kept) >= 2 and kept[-1] != seq[-1]:
            kept[-1] = seq[-1]  # der Endpunkt (Haltestelle) bleibt
        return kept

    pts = dedupe(pts)

    i = 1
    while i < len(pts) - 1:
        angle, lu, lv = _turn(m(pts[i - 1]), m(pts[i]), m(pts[i + 1]))
        if angle > SPIKE_TURN_DEG and min(lu, lv) <= SPIKE_LEG_M:
            del pts[i]
            i = max(1, i - 1)
        else:
            i += 1
    pts = dedupe(pts)

    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = m(pts[i - 1]), m(pts[i]), m(pts[i + 1])
        angle, lu, lv = _turn(a, b, c)
        if angle < ROUND_MIN_DEG or min(lu, lv) < ROUND_MIN_LEG_M:
            out.append(pts[i])
            continue
        # zwei Schnittpunkte in Fahrtrichtung: auf dem Vorgaenger- und auf dem Nachfolgerschenkel
        for neighbour, leg in ((pts[i - 1], lu), (pts[i + 1], lv)):
            share = min(ROUND_SHARE, ROUND_MAX_CUT_M / leg) if leg else 0
            out.append((pts[i][0] + (neighbour[0] - pts[i][0]) * share, pts[i][1] + (neighbour[1] - pts[i][1]) * share))
    out.append(pts[-1])
    out = dedupe(out)
    return [round(v, 5) for p in out for v in p]


def leg_shape(tracks: dict[str, dict], leg: dict, tol_m: float = 200.0) -> list[float]:
    """Verlauf einer Fahrt entlang der gelernten Gleisgeometrie: Halt fuer Halt wird der Abschnitt der Linie gesucht, dessen Enden
    (in beliebiger Richtung) nahe an beiden Haltestellen liegen; ohne Treffer wird gerade verbunden. Flach: [x, y, x, y, ...]."""
    from math import cos, radians, hypot

    kx, ky = cos(radians(53.55)) * 111320, 110540
    ok = lambda p: isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float))
    stops = [p for p in [leg.get("from"), *(leg.get("stops") or []), leg.get("to")] if ok(p)]
    line = leg.get("line") or ""
    base = line[:2] if line[:1] in "SU" and len(line) > 2 else line  # S11/S21/S31 fahren auf dem Gleis der Grundlinie
    mine = [t["p"] for t in tracks.values() if len(t.get("p", ())) >= 4 and (line in t.get("l", ()) or base in t.get("l", ()))]
    dist = lambda x1, y1, x2, y2: hypot((x1 - x2) * kx, (y1 - y2) * ky)
    out: list[float] = []

    def add(points: list[float]) -> None:
        for i in range(0, len(points) - 1, 2):
            if len(out) >= 2 and out[-2] == points[i] and out[-1] == points[i + 1]:
                continue
            out.extend((points[i], points[i + 1]))

    for a, b in zip(stops, stops[1:]):
        best = None
        for p in mine:
            for pts in (p, [c for i in range(len(p) - 2, -1, -2) for c in (p[i], p[i + 1])]):
                d = dist(a["x"], a["y"], pts[0], pts[1]) + dist(b["x"], b["y"], pts[-2], pts[-1])
                if d <= 2 * tol_m and (best is None or d < best[0]):
                    best = (d, pts)
        add([a["x"], a["y"]])
        if best:
            add(best[1])
        add([b["x"], b["y"]])
    return [round(v, 5) for v in out]


RAIL_LINE = __import__("re").compile(r"^(?:[SU]\d+|R[BE]\d+)$")  # S-Bahn, U-Bahn, Regionalbahn/-express: Nahverkehr, ohne Fern- und Gueterverkehr


def _rdp(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker (iterativ), tol in Grad."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        far, far_d = None, tol
        for i in range(a + 1, b):
            x, y = points[i]
            d = abs(dy * (x - x1) - dx * (y - y1)) / norm if norm else ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
            if d > far_d:
                far, far_d = i, d
        if far is not None:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(points, keep) if k]


def rail_features(tracks: dict[str, dict], tol: float = 0.00004) -> dict:
    """GeoJSON der Nahverkehrsgleise (S, U, RB, RE) aus der gelernten Gleisgeometrie, jede Strecke nur einmal (Hin- und Rueckrichtung
    liegen auf demselben Gleis), vereinfacht auf ca. 4 m. Eigenschaft m: S, U oder R."""
    seen: dict[tuple, dict] = {}
    for track in tracks.values():
        lines = [l for l in track.get("l", ()) if RAIL_LINE.match(l)]
        pts = track.get("p") or []
        if not lines or len(pts) < 4 or track.get("v"):
            continue
        coords = [(round(pts[i], 5), round(pts[i + 1], 5)) for i in range(0, len(pts) - 1, 2)]
        key = tuple(sorted((coords[0], coords[-1]))) + (len(coords) // 8,)
        mode = "S" if any(l[0] == "S" for l in lines) else "U" if any(l[0] == "U" for l in lines) else "R"
        if key in seen:
            if mode in "SU" and seen[key]["properties"]["m"] == "R":
                seen[key]["properties"]["m"] = mode
            continue
        simple = _rdp(coords, tol)
        seen[key] = {"type": "Feature", "properties": {"m": mode}, "geometry": {"type": "LineString", "coordinates": [list(p) for p in simple]}}
    return {"type": "FeatureCollection", "features": list(seen.values())}
