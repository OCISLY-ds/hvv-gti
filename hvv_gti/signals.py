"""Haltsignale aus OSM (scripts/fetch_signals.py) den Gleisabschnitten der Karte zuordnen.

Ein Signal gehoert zu einem Abschnitt, wenn es hoechstens MAX_DIST_M neben der Linie steht und sein Kurs (Richtung, fuer die es
gilt) nicht mehr als MAX_ANGLE_DEG von der Fahrtrichtung des Abschnitts abweicht. So landet das Signal des Gegengleises nicht
auf dem falschen Abschnitt, auch wenn beide Gleise nur wenige Meter auseinanderliegen.
"""

import json
import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)

MAX_DIST_M = 12.0
MAX_ANGLE_DEG = 50.0
MIN_GAP_M = 40.0  # naehere Signale (Vor-/Hauptsignal-Paare, Doppelungen) gelten als ein Signal
EDGE_M = 15.0  # Signale direkt am Abschnittsende gehoeren zum Bahnsteigbereich und teilen nichts auf
CELL = 0.002
KX = math.cos(math.radians(53.55)) * 111320
KY = 110540


class SignalIndex:
    def __init__(self, signals: list[list[float]]):
        self.signals = signals
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, (x, y, _) in enumerate(signals):
            self.grid.setdefault((int(x / CELL), int(y / CELL)), []).append(i)

    @classmethod
    def load(cls, path: Path) -> "SignalIndex | None":
        try:
            signals = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        log.info("%d Signale geladen", len(signals))
        return cls(signals)

    def _near(self, x0: float, y0: float, x1: float, y1: float) -> set[int]:
        found: set[int] = set()
        for cx in range(int(x0 / CELL) - 1, int(x1 / CELL) + 2):
            for cy in range(int(y0 / CELL) - 1, int(y1 / CELL) + 2):
                found.update(self.grid.get((cx, cy), ()))
        return found

    def fractions(self, points: list[float]) -> list[float]:
        """Streckenanteile (0..1, aufsteigend) der Signale auf der Linie `points` [x0, y0, x1, y1, ...]."""
        xs, ys = points[0::2], points[1::2]
        if len(xs) < 2:
            return []
        cum = [0.0]
        for i in range(1, len(xs)):
            cum.append(cum[-1] + math.hypot((xs[i] - xs[i - 1]) * KX, (ys[i] - ys[i - 1]) * KY))
        total = cum[-1]
        if total <= 2 * EDGE_M:
            return []
        hits: list[float] = []
        for si in self._near(min(xs), min(ys), max(xs), max(ys)):
            sx, sy, heading = self.signals[si]
            best = None  # (Abstand, Streckenmeter)
            for i in range(1, len(xs)):
                ax, ay = (xs[i - 1] - sx) * KX, (ys[i - 1] - sy) * KY
                bx, by = (xs[i] - sx) * KX, (ys[i] - sy) * KY
                dx, dy = bx - ax, by - ay
                length2 = dx * dx + dy * dy
                if length2 == 0:
                    continue
                u = min(1.0, max(0.0, -(ax * dx + ay * dy) / length2))
                dist = math.hypot(ax + u * dx, ay + u * dy)
                if dist > MAX_DIST_M or (best and dist >= best[0]):
                    continue
                course = math.degrees(math.atan2(dx, dy)) % 360
                if abs((course - heading + 180) % 360 - 180) > MAX_ANGLE_DEG:
                    continue
                best = (dist, cum[i - 1] + u * math.sqrt(length2))
            if best and EDGE_M < best[1] < total - EDGE_M:
                hits.append(best[1])
        hits.sort()
        merged: list[float] = []
        for m in hits:
            if not merged or m - merged[-1] >= MIN_GAP_M:
                merged.append(m)
        return [round(m / total, 4) for m in merged]
