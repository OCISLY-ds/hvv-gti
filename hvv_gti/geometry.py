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
