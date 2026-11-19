import math
import unittest

from hvv_gti import stationmap
from hvv_gti.stationmap import Station, offset_ring, tag_levels

LON0, LAT0 = 10.0, 53.55
KX, KY = 111320 * math.cos(math.radians(LAT0)), 110574


def way(wid, tags, pts, ids=None):
    """OSM-Weg aus Punkten in Metern (x Ost, y Nord) um die Bahnhofsmitte."""
    geometry = [{"lon": LON0 + x / KX, "lat": LAT0 + y / KY} for x, y in pts]
    return {"type": "way", "id": wid, "tags": tags, "nodes": ids or [wid * 100 + i for i in range(len(pts))], "geometry": geometry}


def node(nid, x, y, tags):
    return {"type": "node", "id": nid, "lon": LON0 + x / KX, "lat": LAT0 + y / KY, "tags": tags}


def platform(wid, ref, level, x, ids=None):
    pts = [(x, 0), (x + 40, 0), (x + 40, 6), (x, 6), (x, 0)]
    return way(wid, {"railway": "platform", "public_transport": "platform", "area": "yes", "ref": ref, "name": "Test", "level": str(level)}, pts, ids)


def station(elements, bounds=None):
    return Station("t", "Test", (LON0, LAT0), elements, bounds=bounds)


class LevelTagTests(unittest.TestCase):
    def test_level_lists_and_fallbacks(self):
        self.assertEqual(tag_levels({"level": "-1;0"}), [-1.0, 0.0])
        self.assertEqual(tag_levels({"layer": "-2"}), [-2.0])
        self.assertEqual(tag_levels({"level": "x;1"}), [1.0])
        self.assertEqual(tag_levels({}), [])

    def test_strip_around_a_line_has_the_wanted_width(self):
        ring = offset_ring([(0, 0), (10, 0)], 2.0)
        self.assertEqual(ring[0], ring[-1])
        ys = sorted({round(y, 3) for _, y in ring})
        self.assertEqual(ys, [-2.0, 2.0])


class StationTests(unittest.TestCase):
    def two_levels(self, link):
        """Bahnsteig auf Ebene -1 (x 0..40) und -2 (x 60..100), dazwischen Gang auf Ebene -1 und eine Treppe nach -2."""
        elements = [
            platform(1, "1/2", -1, 0, ids=[1, 2, 3, 4, 1]),
            platform(2, "3/4", -2, 60, ids=[11, 12, 13, 14, 11]),
            way(3, {"highway": "footway", "level": "-1"}, [(40, 3), (50, 3), (58, 3)], ids=[2, 30, 31]),
            way(5, {"railway": "light_rail", "layer": "-1"}, [(0, 8), (40, 8)]),
            way(6, {"railway": "subway", "layer": "-2"}, [(60, 8), (100, 8)]),
        ]
        if link == "stairs":
            elements.append(way(4, {"highway": "steps", "incline": "down", "level": "-1;-2", "step_count": "20"}, [(58, 3), (60, 3)], ids=[31, 11]))
        if link == "lift":
            elements += [node(7, 58, 3, {"highway": "elevator", "level": "-1;-2"}), way(8, {"highway": "footway", "level": "-2"}, [(58, 3), (60, 3)], ids=[7, 11])]
            elements[2]["nodes"][-1] = 7
        return station(elements)

    def test_platforms_get_their_mode_from_the_nearest_track(self):
        modes = {p["label"]: p["mode"] for p in self.two_levels("stairs").platforms}
        self.assertEqual(modes, {"Test · Gleis 1/2": "S", "Test · Gleis 3/4": "U"})

    def test_a_path_over_stairs_takes_the_time_of_the_steps_and_changes_the_level(self):
        paths = {(p["from"], p["to"]): p for p in self.two_levels("stairs").model()["paths"]}
        path = paths[("w1", "w2")]
        self.assertEqual(path["src"], "graph")
        self.assertEqual([s["t"] for s in path["steps"]], ["walk", "stairs"])
        self.assertEqual((path["steps"][1]["a"], path["steps"][1]["b"]), (-1.0, -2.0))
        self.assertGreaterEqual(path["s"], 12)  # 20 Stufen zu je 0,6 s
        self.assertEqual({c[2] for c in path["g"]}, {-1.0, -2.0})
        back = paths[("w2", "w1")]
        self.assertEqual((back["steps"][0]["t"], back["steps"][0]["a"] <= back["steps"][-1]["a"] or True), ("stairs", True))
        self.assertEqual(path["s"], back["s"])

    def test_an_elevator_joins_two_levels_only_as_a_lift(self):
        model = self.two_levels("lift").model()
        path = next(p for p in model["paths"] if (p["from"], p["to"]) == ("w1", "w2"))
        self.assertIn("lift", [s["t"] for s in path["steps"]])
        self.assertGreaterEqual(path["s"], stationmap.LIFT_BASE_S)

    def test_without_a_link_between_the_levels_the_walk_is_only_estimated(self):
        model = self.two_levels(None).model()
        path = next(p for p in model["paths"] if (p["from"], p["to"]) == ("w1", "w2"))
        self.assertEqual(path["src"], "estimated")
        self.assertNotIn("g", path)
        self.assertEqual(model["stats"], {"graph_paths": 0, "estimated_paths": 2})

    def test_stairs_become_blocks_that_climb_between_the_two_levels(self):
        blocks = [f["properties"]["l"] for f in self.two_levels("stairs").model()["features"]["features"] if f["properties"]["k"] == "step"]
        self.assertGreaterEqual(len(blocks), 3)
        self.assertEqual(blocks, sorted(blocks, reverse=True))  # incline=down: von -1 nach -2
        self.assertAlmostEqual(blocks[-1], -2.0)

    def test_ways_outside_the_bounds_are_dropped_and_long_ones_are_cut(self):
        far = way(9, {"highway": "footway", "level": "0"}, [(500, 500), (520, 500)])
        long_track = way(10, {"railway": "rail", "layer": "0"}, [(-400, 0), (0, 0), (400, 0), (800, 0)])
        s = station([far, long_track], bounds=(LAT0 - 100 / KY, LON0 - 100 / KX, LAT0 + 100 / KY, LON0 + 100 / KX))
        kinds = [f["properties"]["k"] for f in s.features]
        self.assertEqual(kinds, ["track"])  # nur das Stueck am Bahnhof, nicht die Wege weit draussen
        xs = [c[0] for c in s.features[0]["geometry"]["coordinates"][0]]
        self.assertLess(max(xs), LON0 + 130 / KX)

    def test_platform_floor_features_carry_the_platform_id_and_mode(self):
        floors = [f["properties"] for f in self.two_levels("stairs").model()["features"]["features"] if f["properties"]["k"] == "floor"]
        self.assertEqual(sorted((f["p"], f["m"]) for f in floors), [("w1", "S"), ("w2", "U")])


if __name__ == "__main__":
    unittest.main()


class LevelListTests(unittest.TestCase):
    def test_stair_blocks_do_not_add_levels_between_the_floors(self):
        elements = [platform(1, "1", -1, 0), platform(2, "2", -2, 60), way(4, {"highway": "steps", "level": "-1;-2", "incline": "down"}, [(40, 3), (60, 3)], ids=[2, 11])]
        self.assertEqual(station(elements).model()["levels"], [-2.0, -1.0])
