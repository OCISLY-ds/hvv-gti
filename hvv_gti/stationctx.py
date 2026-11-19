"""Umgebung eines Bahnhofs fuer die 3D-Ansicht aus OpenStreetMap: Strassen, Gebaeude, Baeume, Gruen, Wasser, Orte.

Ergaenzt das Bahnhofsmodell (hvv_gti/stationmap.py) um das, was darueber liegt: ein Diorama wie eine Stadtkarte in 3D. Die Ausgabe ist
absichtlich schlank (Linien und Ringe, keine fertigen Flaechen): der Browser macht daraus Strassenbaender, Baumkronen und Gebaeudekoerper.
Alles bezieht sich auf die Hoehe der Ebene 0; unterirdische Strassen und Gebaeudeteile unter der Erde werden ausgelassen.
"""

import math
import re

from .stationmap import Frame, clip_runs

ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service", "living_street", "pedestrian", "cycleway", "footway", "path", "steps", "track", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
ROAD_RANK = {"motorway": 0, "trunk": 0, "primary": 1, "secondary": 2, "tertiary": 3, "unclassified": 4, "residential": 4, "living_street": 5, "pedestrian": 5, "service": 6, "cycleway": 7, "footway": 8, "path": 8, "steps": 8, "track": 8}
GREEN = {("leisure", "park"): "park", ("leisure", "garden"): "park", ("leisure", "playground"): "park", ("leisure", "common"): "park", ("landuse", "grass"): "grass",
         ("landuse", "village_green"): "park", ("landuse", "recreation_ground"): "park", ("landuse", "meadow"): "grass", ("landuse", "forest"): "wood", ("natural", "wood"): "wood",
         ("natural", "scrub"): "grass", ("natural", "grassland"): "grass", ("leisure", "pitch"): "pitch"}
WATER = {("natural", "water"), ("waterway", "riverbank"), ("landuse", "basin")}
POI_CLASSES = {
    "food": {"restaurant", "cafe", "fast_food", "bar", "pub", "biergarten", "ice_cream", "food_court"},
    "culture": {"theatre", "cinema", "museum", "arts_centre", "library", "gallery", "attraction", "monument", "memorial", "artwork"},
    "hotel": {"hotel", "hostel", "guest_house"},
    "health": {"pharmacy", "hospital", "clinic", "doctors", "dentist"},
    "money": {"bank", "atm", "post_office"},
    "info": {"information", "police", "townhall", "courthouse", "place_of_worship", "toilets"},
}
POI_PRIORITY = {"culture": 0, "bus": 1, "hotel": 2, "food": 3, "health": 4, "money": 5, "info": 6, "shop": 7}
MAX_POIS = 70
MAX_TREES = 1200


def _number(value: str | None) -> float | None:
    match = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", value or "")
    return float(match.group(1).replace(",", ".")) if match else None


def building_height(tags: dict) -> tuple[float, float]:
    """(Hoehe, Fusshoehe) in Metern: `height`, sonst `building:levels` * 3,2, sonst 9; Fuss aus min_height / building:min_level."""
    top = _number(tags.get("height"))
    if top is None:
        levels = _number(tags.get("building:levels"))
        top = levels * 3.2 + 1.0 if levels else 9.0
    base = _number(tags.get("min_height"))
    if base is None:
        level = _number(tags.get("building:min_level"))
        base = level * 3.2 if level else 0.0
    return round(max(top, base + 1.0), 1), round(base, 1)


def _underground(tags: dict) -> bool:
    if tags.get("tunnel") not in (None, "no") or tags.get("location") == "underground" or tags.get("indoor") not in (None, "no"):
        return True
    levels = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", tags.get("level", ""))]
    return bool(levels) and max(levels) < 0


def poi_class(tags: dict) -> str | None:
    if tags.get("highway") == "bus_stop":
        return "bus"
    for key in ("amenity", "tourism", "historic", "leisure"):
        value = tags.get(key)
        if not value:
            continue
        for cls, values in POI_CLASSES.items():
            if value in values:
                return cls
        if key == "tourism" and value in ("hotel", "hostel", "guest_house"):
            return "hotel"
        if key == "historic":
            return "culture"
    if tags.get("shop"):
        return "shop"
    return None


class Context:
    def __init__(self, center: tuple[float, float], elements: list[dict], bounds: tuple[float, float, float, float]):
        self.frame = Frame(*center)
        south, west, north, east = bounds
        (x1, y1), (x2, y2) = self.frame.xy(west, south), self.frame.xy(east, north)
        self.box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.elements = elements

    def _inside(self, p: tuple[float, float]) -> bool:
        return self.box[0] <= p[0] <= self.box[2] and self.box[1] <= p[1] <= self.box[3]

    def _xy(self, el: dict) -> list[tuple[float, float]]:
        return [self.frame.xy(p["lon"], p["lat"]) for p in el.get("geometry", [])]

    def _ll(self, p: tuple[float, float]) -> list[float]:
        lon, lat = self.frame.lonlat(*p)
        return [round(lon, 6), round(lat, 6)]

    def _ring(self, pts: list[tuple[float, float]]) -> list[list[float]]:
        return [self._ll(p) for p in (pts if pts[0] == pts[-1] else pts + [pts[0]])]

    def model(self) -> dict:
        roads, labels, buildings, trees, green, water, pois = [], [], [], [], [], [], []
        seen_names: dict[str, tuple[int, float]] = {}
        for el in self.elements:
            tags = el.get("tags", {})
            if el["type"] == "node":
                p = self.frame.xy(el["lon"], el["lat"])
                if tags.get("natural") == "tree" and self._inside(p):
                    trees.append(self._ll(p))
                cls = poi_class(tags)
                if cls and self._inside(p) and (tags.get("name") or cls == "bus"):
                    pois.append({"n": tags.get("name", ""), "k": cls, "x": self._ll(p)[0], "y": self._ll(p)[1]})
                continue
            if el["type"] != "way" or not el.get("geometry"):
                continue
            pts = self._xy(el)
            closed = len(pts) > 3 and pts[0] == pts[-1]
            if tags.get("highway") in ROAD_CLASSES and not _underground(tags):
                runs = clip_runs(pts, self.box, 0.0)
                for run in runs:
                    roads.append({"c": tags["highway"], "g": [self._ll(p) for p in run], **({"b": 1} if tags.get("bridge") not in (None, "no") else {})})
                name = tags.get("name")
                if name and tags["highway"] not in ("footway", "path", "steps", "cycleway", "track") and runs:
                    longest = max(runs, key=lambda r: sum(math.dist(a, b) for a, b in zip(r, r[1:])))
                    length = sum(math.dist(a, b) for a, b in zip(longest, longest[1:]))
                    if length > 25 and length > seen_names.get(name, (0, 0.0))[1]:
                        half, acc = length / 2, 0.0
                        for a, b in zip(longest, longest[1:]):
                            seg = math.dist(a, b)
                            if acc + seg >= half:
                                f = (half - acc) / (seg or 1)
                                x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                                angle = round(math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 180)  # Streifen ohne Richtung: 0-180 Grad von Norden
                                seen_names[name] = (ROAD_RANK.get(tags["highway"], 6), length)
                                labels.append({"n": name, "x": self._ll((x, y))[0], "y": self._ll((x, y))[1], "a": angle, "r": ROAD_RANK.get(tags["highway"], 6)})
                                break
                            acc += seg
                continue
            if tags.get("natural") == "tree_row" and len(pts) > 1:
                for a, b in zip(pts, pts[1:]):
                    steps = max(1, int(math.dist(a, b) // 8))
                    for i in range(steps):
                        p = (a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
                        if self._inside(p):
                            trees.append(self._ll(p))
                continue
            if closed and (("building" in tags and tags["building"] != "roof") or "building:part" in tags):
                if tags.get("location") == "underground" or not any(self._inside(p) for p in pts):
                    continue
                height, base = building_height(tags)
                buildings.append({"g": self._ring(pts), "h": height, **({"m": base} if base else {})})
                continue
            kind = next((v for (k, val), v in GREEN.items() if tags.get(k) == val), None)
            if kind and closed and any(self._inside(p) for p in pts):
                green.append({"g": self._ring(pts), "k": kind})
                continue
            if closed and any(tags.get(k) == v for k, v in WATER) and any(self._inside(p) for p in pts):
                water.append({"g": self._ring(pts)})
                continue
            cls = poi_class(tags)
            if cls and tags.get("name") and closed and any(self._inside(p) for p in pts):
                cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                pois.append({"n": tags["name"], "k": cls, "x": self._ll((cx, cy))[0], "y": self._ll((cx, cy))[1]})
        # Orte: je Name einmal, wichtigste Klassen zuerst, begrenzt
        unique, taken = [], set()
        for poi in sorted(pois, key=lambda p: (POI_PRIORITY.get(p["k"], 9), p["n"])):
            key = (poi["n"], poi["k"])
            if key in taken:
                continue
            taken.add(key)
            unique.append(poi)
        step = max(1, len(trees) // MAX_TREES + (1 if len(trees) % MAX_TREES else 0)) if len(trees) > MAX_TREES else 1
        names = sorted(labels, key=lambda lab: (lab["r"], lab["n"]))
        return {"roads": roads, "labels": names[:60], "buildings": buildings, "trees": trees[::step], "green": green, "water": water, "pois": unique[:MAX_POIS]}
