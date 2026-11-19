"""Bahnhofsmodell aus OpenStreetMap: Ebenen, Bahnsteige, Treppen, Aufzuege und Umsteigewege fuer die 3D-Ansicht.

Eingabe sind die Elemente einer Overpass-Abfrage (`out geom tags`, siehe scripts/build_stations.py). Ausgabe je Bahnhof ein
JSON-Modell, das der Browser mit MapLibre (`fill-extrusion`, je Ebene eine Hoehe) zeichnet:

- `features`: GeoJSON mit `k` (Art: floor, room, part, track, walk, steps, lift), `l` (Ebene, bei Treppen/Aufzuegen von `l` bis `h`)
- `platforms`, `entrances`: Bahnsteige (mit Gleisnummer, Ebene, Verkehrsmittel) und Eingaenge
- `paths`: Umsteigewege zwischen allen Bahnsteigen (Dijkstra auf einem ebenenbewussten Fusswegegraphen), mit Zeit, Laenge,
  Geometrie (lon, lat, Ebene) und Schritten (gehen, Treppe, Rolltreppe, Aufzug)

Der Graph arbeitet auf (OSM-Knoten, Ebene): ein Knoten, der in Wegen verschiedener Ebenen vorkommt, verbindet diese nur als Aufzug.
Fehlende gemeinsame Knoten werden auf gleicher Ebene bis SNAP_M zusammengezogen. Wie gut ein Weg ist, haengt an der OSM-Qualitaet:
`src` sagt, ob er auf dem Graphen berechnet ("graph") oder nur geschaetzt ("estimated", Luftlinie) wurde.
"""

import heapq
import math
from collections import defaultdict

WALK_MS = 1.25  # m/s Gehgeschwindigkeit in Bahnhoefen
STAIR_S_PER_STEP = 0.6
STAIR_MS = 0.7  # m/s Weg entlang der Treppe, wenn die Stufenzahl fehlt
ESCALATOR_MS = 0.7
LIFT_BASE_S = 22.0  # Warten + Tuer + Fahrt
LIFT_PER_LEVEL_S = 6.0
SNAP_M = 2.5
PORTAL_M = 8.0
PLATFORM_HALF_WIDTH_M = 3.0  # offene OSM-Bahnsteigkanten werden zu einem Streifen dieser halben Breite
WALK_HALF_WIDTH_M = 1.3
TRACK_HALF_WIDTH_M = 0.75
WALKWAYS = {"footway", "corridor", "pedestrian", "path", "steps", "elevator"}
RAIL = {"rail", "subway", "light_rail", "tram", "narrow_gauge"}


def _levels(value: str | None) -> list[float]:
    out = []
    for part in (value or "").replace(",", ";").split(";"):
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


def tag_levels(tags: dict) -> list[float]:
    return _levels(tags.get("level")) or _levels(tags.get("repeat_on")) or _levels(tags.get("layer"))


def way_level(tags: dict) -> float:
    levels = tag_levels(tags)
    return min(levels) if levels else 0.0


class Frame:
    """Lokale Meter um die Bahnhofsmitte (x Ost, y Nord)."""

    def __init__(self, lon: float, lat: float):
        self.lon, self.lat = lon, lat
        self.kx = 111320 * math.cos(math.radians(lat))
        self.ky = 110574

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon - self.lon) * self.kx, (lat - self.lat) * self.ky

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        return self.lon + x / self.kx, self.lat + y / self.ky


def _round(p: tuple[float, float]) -> list[float]:
    return [round(p[0], 6), round(p[1], 6)]


def offset_ring(points: list[tuple[float, float]], half: float) -> list[tuple[float, float]]:
    """Umriss eines Streifens der Breite 2*half um eine Linie (Gehrung mit Begrenzung), in Metern."""
    pts = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    if len(pts) < 2:
        return []
    normals = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        d = math.hypot(x2 - x1, y2 - y1) or 1.0
        normals.append((-(y2 - y1) / d, (x2 - x1) / d))
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        a = normals[max(i - 1, 0)]
        b = normals[min(i, len(normals) - 1)]
        nx, ny = a[0] + b[0], a[1] + b[1]
        n = math.hypot(nx, ny)
        if n < 1e-6:
            nx, ny, n = a[0], a[1], 1.0
        mx, my = nx / n, ny / n
        dot = max(mx * a[0] + my * a[1], 0.4)
        k = half / dot
        left.append((x + mx * k, y + my * k))
        right.append((x - mx * k, y - my * k))
    ring = left + right[::-1]
    return ring + [ring[0]]


def clip_segment(a, b, box, margin):
    """Stueck der Strecke a-b innerhalb des (um `margin` erweiterten) Rechtecks `box` = (x0, y0, x1, y1) (Liang-Barsky) oder None."""
    x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def clip_runs(pts, box, margin=15.0):
    """Zusammenhaengende Stuecke einer Linie innerhalb von `box` (ohne Box: die ganze Linie)."""
    if box is None:
        return [pts] if len(pts) >= 2 else []
    runs, run = [], []
    for a, b in zip(pts, pts[1:]):
        piece = clip_segment(a, b, box, margin)
        if piece is None:
            if len(run) >= 2:
                runs.append(run)
            run = []
            continue
        start, end = piece
        if run and math.dist(run[-1], start) < 1e-6:
            run.append(end)
        else:
            if len(run) >= 2:
                runs.append(run)
            run = [start, end]
    if len(run) >= 2:
        runs.append(run)
    return runs


def _point_segment(p, a, b) -> float:
    """Abstand eines Punkts von der Strecke a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _is_closed(el: dict) -> bool:
    n = el.get("nodes") or []
    return len(n) > 3 and n[0] == n[-1]


def classify(tags: dict, closed: bool) -> str | None:
    hw = tags.get("highway")
    rw = tags.get("railway")
    if rw == "platform" or tags.get("public_transport") == "platform":
        return "floor"
    if hw == "elevator":
        return "lift"
    if hw == "steps" or (tags.get("conveying") not in (None, "no") and hw in ("footway", "corridor", "path")):
        return "steps"
    if hw in WALKWAYS and tags.get("access") not in ("no", "private") and tags.get("foot") != "no":
        return "walk"
    if rw in RAIL and tags.get("service") not in ("yard", "siding") and tags.get("tunnel") != "building_passage":
        return "track"
    if "building:part" in tags and closed:
        return "part"
    if tags.get("indoor") in ("room", "area") and closed:
        return "room"
    if tags.get("indoor") == "corridor":
        return "walk" if not closed else "room"
    return None


def platform_label(tags: dict) -> tuple[str, str]:
    """(Anzeigename, Verkehrsmittel S/U/R/B/?) eines Bahnsteigs."""
    ref = (tags.get("ref") or tags.get("local_ref") or "").replace(";", "/")
    name = tags.get("name") or ""
    mode = "U" if tags.get("subway") == "yes" else "S" if tags.get("light_rail") == "yes" else "R" if tags.get("train") == "yes" else "?"
    if ref and name:
        return f"{name} · Gleis {ref}", mode
    return (f"Gleis {ref}" if ref else name or "Bahnsteig"), mode


class Station:
    def __init__(self, sid: str, name: str, center: tuple[float, float], elements: list[dict], bounds: tuple[float, float, float, float] | None = None):
        """`bounds` = (Sued, West, Nord, Ost): Linien und Flaechen werden darauf zugeschnitten (Overpass liefert ganze Wege)."""
        self.id, self.name = sid, name
        self.frame = Frame(*center)
        self.center = center
        self.box = None
        self.bounds = bounds
        if bounds:
            south, west, north, east = bounds
            (x1, y1), (x2, y2) = self.frame.xy(west, south), self.frame.xy(east, north)
            self.box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.nodes: dict[int, dict] = {e["id"]: e for e in elements if e["type"] == "node"}
        self.ways = [e for e in elements if e["type"] == "way" and e.get("geometry")]
        self.features: list[dict] = []
        self.platforms: list[dict] = []
        self.entrances: list[dict] = []
        self.levels: set[float] = set()
        # Graph: Knoten (osm_id, ebene) -> [(ziel, kosten_s, laenge_m, art)]
        self.adj: dict[tuple, list[tuple]] = defaultdict(list)
        self.pos: dict[tuple, tuple[float, float]] = {}
        self.build()

    # ---- Zeichnen ----

    def _in(self, p: tuple[float, float], margin: float = 0.0) -> bool:
        if self.box is None:
            return True
        return self.box[0] - margin <= p[0] <= self.box[2] + margin and self.box[1] - margin <= p[1] <= self.box[3] + margin

    def _runs(self, pts: list[tuple[float, float]], margin: float = 15.0) -> list[list[tuple[float, float]]]:
        """Zusammenhaengende Stuecke einer Linie innerhalb des Bahnhofsbereichs, an dessen Rand (plus `margin` m) abgeschnitten."""
        return clip_runs(pts, self.box, margin)

    def _xy_geom(self, el: dict) -> list[tuple[float, float]]:
        return [self.frame.xy(p["lon"], p["lat"]) for p in el["geometry"]]

    def _poly(self, ring: list[tuple[float, float]]) -> dict:
        return {"type": "Polygon", "coordinates": [[_round(self.frame.lonlat(x, y)) for x, y in ring]]}

    def _line(self, pts: list[tuple[float, float]]) -> dict:
        return {"type": "LineString", "coordinates": [_round(self.frame.lonlat(x, y)) for x, y in pts]}

    def _add(self, geom: dict, kind: str, level: float, **props) -> None:
        if kind not in ("step", "esc"):  # Treppenbloecke liegen zwischen den Ebenen; ihre Enden stehen schon in `levels`
            self.levels.add(level)
        self.features.append({"type": "Feature", "properties": {"k": kind, "l": level, **props}, "geometry": geom})

    def _mode(self, tags: dict, pts: list[tuple[float, float]], level: float) -> str:
        """Verkehrsmittel eines Bahnsteigs: ausdrueckliche Tags, sonst das naechste Gleis (gleiche Ebene), sonst `train=yes`."""
        if tags.get("subway") == "yes":
            return "U"
        if tags.get("light_rail") == "yes":
            return "S"
        best, best_d = None, 14.0
        for track_pts, track_level, railway in self._tracks:
            if abs(track_level - level) > 0.75:
                continue
            for a, b in zip(track_pts, track_pts[1:]):
                for p in pts[:: max(1, len(pts) // 8)]:
                    d = _point_segment(p, a, b)
                    if d < best_d:
                        best, best_d = railway, d
        if best:
            return {"subway": "U", "light_rail": "S"}.get(best, "R")
        return "R" if tags.get("train") == "yes" else "?"

    def _stairs(self, pts: list[tuple[float, float]], start: float, end: float, half: float, escalator: bool) -> None:
        """Treppe als Folge kleiner Stufenbloecke (je Block eine Ebene zwischen `start` und `end`), damit sie in 3D ansteigt."""
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        if length < 0.5:
            return
        n = max(3, min(10, round(length / 1.0)))
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + math.dist(a, b))

        def at(distance: float) -> tuple[float, float]:
            for i in range(1, len(cum)):
                if distance <= cum[i] or i == len(cum) - 1:
                    f = (distance - cum[i - 1]) / ((cum[i] - cum[i - 1]) or 1)
                    f = min(max(f, 0.0), 1.0)
                    return pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f
            return pts[-1]

        for i in range(n):
            a, b = at(length * i / n), at(length * (i + 1) / n)
            ring = offset_ring([a, b], half)
            if len(ring) >= 4:
                level = start + (end - start) * (i + 1) / n
                self._add(self._poly(ring), "esc" if escalator else "step", round(level, 3))

    def build(self) -> None:
        self._tracks: list[tuple[list[tuple[float, float]], float, str]] = []
        for el in self.ways:
            tags = el.get("tags", {})
            if tags.get("railway") in RAIL:
                self._tracks.append((self._xy_geom(el), way_level(tags), tags["railway"]))
        floors = []
        for el in self.ways:
            tags = el.get("tags", {})
            closed = _is_closed(el)
            kind = classify(tags, closed)
            if kind is None:
                continue
            pts = self._xy_geom(el)
            if not (any(self._in(p) for p in pts) or self._runs(pts, 0.0)):
                continue
            lvls = tag_levels(tags)
            level = min(lvls) if lvls else 0.0
            if kind == "floor":
                floors.append((el, tags, pts, closed, level))
            elif kind == "room" or kind == "part":
                ring = pts if pts[0] == pts[-1] else pts + [pts[0]]
                if len(ring) < 4:
                    continue
                if kind == "part":
                    base = _levels(tags.get("building:min_level"))
                    top = _levels(tags.get("building:levels"))
                    self._add(self._poly(ring), "part", min(base) if base else 0.0, h=(max(top) if top else 3.0) + (min(base) if base else 0.0))
                else:
                    self._add(self._poly(ring), "room", level, n=tags.get("name", ""))
            elif kind == "track":
                for run in self._runs(pts):
                    ring = offset_ring(run, TRACK_HALF_WIDTH_M)
                    if len(ring) >= 4:
                        self._add(self._poly(ring), "track", level, r=tags["railway"])
            elif kind == "walk":
                for run in self._runs(pts):
                    ring = offset_ring(run, WALK_HALF_WIDTH_M)
                    if len(ring) >= 4:
                        self._add(self._poly(ring), "walk", level)
            elif kind == "steps":
                lo, hi = (min(lvls), max(lvls)) if lvls else (level, level)
                self.levels.update((lo, hi))
                start, end = (hi, lo) if tags.get("incline") == "down" else (lo, hi)
                width = tags.get("width", "")
                half = min(max(float(width.split()[0]) / (200 if "cm" in width else 2), 0.6), 3.0) if width.split()[:1] and width.split()[0].replace(".", "", 1).isdigit() else 1.1
                if any(self._in(p, 10) for p in pts):
                    self._stairs(pts, start, end, half, tags.get("conveying", "no") not in ("no", None))
        for el, tags, pts, closed, level in floors:
            label, _ = platform_label(tags)
            mode = self._mode(tags, pts, level)
            area = closed or tags.get("area") == "yes"
            ring = (pts if pts[0] == pts[-1] else pts + [pts[0]]) if area else offset_ring(pts, PLATFORM_HALF_WIDTH_M)
            if len(ring) < 4:
                continue
            self._add(self._poly(ring), "floor", level, n=label, m=mode, p=f"w{el['id']}")
            if tags.get("railway") == "platform" and (tags.get("ref") or tags.get("name")):
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                lon, lat = self.frame.lonlat(cx, cy)
                self.platforms.append({"id": f"w{el['id']}", "label": label, "mode": mode, "level": level, "way": el["id"], "lon": round(lon, 6), "lat": round(lat, 6)})
        for node in self.nodes.values():
            tags = node.get("tags", {})
            lvls = tag_levels(tags)
            if tags.get("highway") == "elevator":
                lo, hi = (min(lvls), max(lvls)) if lvls else (0.0, 0.0)
                self.levels.update((lo, hi))
                x, y = self.frame.xy(node["lon"], node["lat"])
                ring = [(x - 1.1, y - 1.1), (x + 1.1, y - 1.1), (x + 1.1, y + 1.1), (x - 1.1, y + 1.1), (x - 1.1, y - 1.1)]
                self.features.append({"type": "Feature", "properties": {"k": "lift", "l": lo, "h": hi}, "geometry": self._poly(ring)})
            if tags.get("railway") == "subway_entrance" or tags.get("public_transport") == "entrance":
                self.entrances.append({"id": f"n{node['id']}", "name": tags.get("name", ""), "ref": tags.get("ref", ""), "level": min(lvls) if lvls else 0.0, "lon": round(node["lon"], 6), "lat": round(node["lat"], 6)})

    # ---- Graph ----

    def _vertex(self, node_id: int, level: float, xy: tuple[float, float]) -> tuple:
        key = (node_id, level)
        self.pos.setdefault(key, xy)
        return key

    def _edge(self, a: tuple, b: tuple, seconds: float, metres: float, kind: str) -> None:
        self.adj[a].append((b, seconds, metres, kind))
        self.adj[b].append((a, seconds, metres, kind))

    def build_graph(self) -> None:
        self.adj.clear()
        self.pos.clear()
        platform_vertices: dict[int, list[tuple]] = {}
        for el in self.ways:
            tags = el.get("tags", {})
            kind = classify(tags, _is_closed(el))
            if kind not in ("walk", "steps", "floor"):
                continue
            ids = el["nodes"]
            pts = self._xy_geom(el)
            lvls = tag_levels(tags)
            if kind == "steps":
                lo, hi = (min(lvls), max(lvls)) if lvls else (way_level(tags),) * 2
                start, end = (hi, lo) if tags.get("incline") == "down" else (lo, hi)
            else:
                start = end = way_level(tags)
            verts = []
            for i, (node_id, p) in enumerate(zip(ids, pts)):
                level = start if i == 0 else end if i == len(ids) - 1 else (start if kind != "steps" else (start + (end - start) * i / (len(ids) - 1)))
                verts.append(self._vertex(node_id, round(level, 3), p))
            length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
            if kind == "steps":
                escalator = tags.get("conveying", "no") not in ("no", None)
                count = float(tags["step_count"]) if tags.get("step_count", "").replace(".", "", 1).isdigit() else 0.0
                given = tags.get("time", "").split()[0] if tags.get("time") else ""
                if given.replace(".", "", 1).isdigit():
                    seconds = float(given)
                elif escalator:
                    seconds = length / ESCALATOR_MS
                else:
                    seconds = count * STAIR_S_PER_STEP if count else length / STAIR_MS
                kind_name = "escalator" if escalator else "stairs"
                # Treppen verbinden nur ihre Enden; die Zwischenknoten laufen auf der Treppe mit (Ebene wechselt linear)
                self._edge(verts[0], verts[-1], seconds, length, kind_name)
                continue
            for a, b, p, q in zip(verts, verts[1:], pts, pts[1:]):
                d = math.dist(p, q)
                self._edge(a, b, d / WALK_MS, d, "walk")
            if kind == "floor":
                # Flaechen-Bahnsteig oder Kantenlinie: ueber die Mitte quer gehen koennen
                if verts and ids[0] == ids[-1]:
                    cx = sum(p[0] for p in pts) / len(pts)
                    cy = sum(p[1] for p in pts) / len(pts)
                    hub = self._vertex(-el["id"], way_level(tags), (cx, cy))
                    for v, p in zip(verts, pts):
                        self._edge(hub, v, math.dist((cx, cy), p) / WALK_MS, math.dist((cx, cy), p), "walk")
                platform_vertices[el["id"]] = verts
        self._link_nodes()
        self.platform_vertices = platform_vertices

    def _link_nodes(self) -> None:
        """Aufzuege (Knoten mit Ebenenliste) und fehlende gemeinsame Knoten verbinden."""
        grid: dict[tuple, list[tuple]] = defaultdict(list)
        for key, (x, y) in self.pos.items():
            grid[(int(x // PORTAL_M), int(y // PORTAL_M), key[1])].append(key)

        def near(x: float, y: float, level: float, radius: float) -> list[tuple]:
            out = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for key in grid.get((int(x // PORTAL_M) + dx, int(y // PORTAL_M) + dy, level), ()):
                        if math.dist(self.pos[key], (x, y)) <= radius:
                            out.append(key)
            return out

        for node in self.nodes.values():
            tags = node.get("tags", {})
            if tags.get("highway") != "elevator":
                continue
            lvls = sorted(set(tag_levels(tags)))
            if len(lvls) < 2:
                continue
            x, y = self.frame.xy(node["lon"], node["lat"])
            ports = []
            for level in lvls:
                key = (node["id"], level)
                self.pos[key] = (x, y)
                ports.append(key)
                # an das naechste vorhandene Wegenetz dieser Ebene anschliessen
                candidates = sorted(near(x, y, level, PORTAL_M), key=lambda k: math.dist(self.pos[k], (x, y)))
                for other in candidates[:2]:
                    if other != key:
                        d = math.dist(self.pos[other], (x, y))
                        self._edge(key, other, d / WALK_MS, d, "walk")
            for a, b in zip(ports, ports[1:]):
                span = abs(b[1] - a[1])
                self._edge(a, b, LIFT_BASE_S + LIFT_PER_LEVEL_S * span, 0.0, "lift")
        # fehlende gemeinsame Knoten: gleiche Ebene, nahe beieinander
        keys = list(self.pos)
        for key in keys:
            x, y = self.pos[key]
            for other in near(x, y, key[1], SNAP_M):
                if other[0] != key[0] and other > key:
                    d = math.dist(self.pos[other], (x, y))
                    self._edge(key, other, d * 1.2 / WALK_MS, d, "walk")

    def dijkstra(self, sources: list[tuple], targets: set[tuple]) -> tuple[float, list[tuple]] | None:
        dist = {s: 0.0 for s in sources}
        prev: dict[tuple, tuple] = {}
        heap = [(0.0, i, s) for i, s in enumerate(sources)]
        heapq.heapify(heap)
        counter = len(sources)
        while heap:
            d, _, u = heapq.heappop(heap)
            if d > dist.get(u, math.inf):
                continue
            if u in targets:
                path = [u]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return d, path[::-1]
            for v, seconds, _, _ in self.adj.get(u, ()):
                nd = d + seconds
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    counter += 1
                    heapq.heappush(heap, (nd, counter, v))
        return None

    def _describe(self, path: list[tuple]) -> tuple[list[list[float]], list[dict], float]:
        coords, steps, metres = [], [], 0.0
        for a, b in zip(path, path[1:]):
            edge = min((e for e in self.adj[a] if e[0] == b), key=lambda e: e[1])
            _, _, m, kind = edge
            metres += m
            px, py = self.pos[b]
            lon, lat = self.frame.lonlat(px, py)
            if not coords:
                ax, ay = self.pos[a]
                coords.append([*_round(self.frame.lonlat(ax, ay)), a[1]])
            coords.append([round(lon, 6), round(lat, 6), b[1]])
            if steps and steps[-1]["t"] == kind and kind == "walk":
                steps[-1]["m"] += m
                steps[-1]["b"] = b[1]
            elif steps and steps[-1]["t"] == kind and kind == "lift":
                steps[-1]["b"] = b[1]
            else:
                steps.append({"t": kind, "a": a[1], "b": b[1], "m": m})
        for s in steps:
            s["m"] = round(s["m"])
        return coords, steps, metres

    def paths(self) -> list[dict]:
        self.build_graph()
        by_way = {p["way"]: p for p in self.platforms}
        out = []
        for a_way, a in by_way.items():
            sources = self.platform_vertices.get(a_way) or []
            for b_way, b in by_way.items():
                if a_way == b_way:
                    continue
                targets = set(self.platform_vertices.get(b_way) or [])
                found = self.dijkstra(sources, targets) if sources and targets else None
                if found is None:
                    air = math.dist(self.frame.xy(a["lon"], a["lat"]), self.frame.xy(b["lon"], b["lat"]))
                    levels = abs(a["level"] - b["level"])
                    out.append({"from": a["id"], "to": b["id"], "s": round(air / WALK_MS * 1.4 + 25 * levels), "m": round(air), "src": "estimated"})
                    continue
                seconds, path = found
                coords, steps, metres = self._describe(path)
                out.append({"from": a["id"], "to": b["id"], "s": round(seconds), "m": round(metres), "src": "graph", "g": coords, "steps": steps})
        return out

    def model(self, with_paths: bool = True) -> dict:
        paths = self.paths() if with_paths else []
        levels = sorted(self.levels)
        return {
            "id": self.id,
            "name": self.name,
            "center": [round(self.center[0], 6), round(self.center[1], 6)],
            **({"bbox": [self.bounds[1], self.bounds[0], self.bounds[3], self.bounds[2]]} if self.bounds else {}),  # Westen, Sueden, Osten, Norden
            "levels": levels,
            "features": {"type": "FeatureCollection", "features": self.features},
            "platforms": [{k: v for k, v in p.items() if k != "way"} | {"id": p["id"]} for p in self.platforms],
            "entrances": self.entrances,
            "paths": paths,
            "stats": {
                "graph_paths": sum(p["src"] == "graph" for p in paths),
                "estimated_paths": sum(p["src"] == "estimated" for p in paths),
            },
        }
