import math
import unittest

from hvv_gti.stationctx import Context, building_height, poi_class

LON0, LAT0 = 10.0, 53.55
KX, KY = 111320 * math.cos(math.radians(LAT0)), 110574
BOUNDS = (LAT0 - 150 / KY, LON0 - 150 / KX, LAT0 + 150 / KY, LON0 + 150 / KX)


def way(wid, tags, pts):
    return {"type": "way", "id": wid, "tags": tags, "nodes": [wid * 100 + i for i in range(len(pts))], "geometry": [{"lon": LON0 + x / KX, "lat": LAT0 + y / KY} for x, y in pts]}


def node(nid, x, y, tags):
    return {"type": "node", "id": nid, "lon": LON0 + x / KX, "lat": LAT0 + y / KY, "tags": tags}


def model(*elements):
    return Context((LON0, LAT0), list(elements), BOUNDS).model()


def square(x, y, size=10):
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]


class HeightTests(unittest.TestCase):
    def test_height_tags_win_over_levels_over_the_default(self):
        self.assertEqual(building_height({"height": "21.5 m"}), (21.5, 0.0))
        self.assertEqual(building_height({"building:levels": "5"}), (17.0, 0.0))
        self.assertEqual(building_height({}), (9.0, 0.0))
        self.assertEqual(building_height({"height": "30", "min_height": "12"}), (30.0, 12.0))
        self.assertEqual(building_height({"building:levels": "3", "building:min_level": "1"})[1], 3.2)


class ContextTests(unittest.TestCase):
    def test_roads_are_clipped_to_the_area_and_underground_roads_are_dropped(self):
        m = model(
            way(1, {"highway": "primary", "name": "Kirchenallee"}, [(-400, 0), (400, 0)]),
            way(2, {"highway": "primary", "tunnel": "yes"}, [(-50, 20), (50, 20)]),
            way(3, {"highway": "footway", "indoor": "yes"}, [(-20, 30), (20, 30)]),
            way(4, {"highway": "footway", "level": "-1"}, [(-20, 40), (20, 40)]),
        )
        self.assertEqual([r["c"] for r in m["roads"]], ["primary"])
        xs = [p[0] for p in m["roads"][0]["g"]]
        self.assertLess(max(xs), LON0 + 160 / KX)
        self.assertGreater(min(xs), LON0 - 160 / KX)

    def test_one_label_per_street_name_with_a_bearing(self):
        m = model(way(1, {"highway": "residential", "name": "Steintordamm"}, [(-100, 0), (0, 0)]), way(2, {"highway": "residential", "name": "Steintordamm"}, [(0, 5), (60, 5)]),
                  way(3, {"highway": "footway", "name": "Fussweg"}, [(0, 0), (80, 0)]), way(4, {"highway": "residential", "name": "Kurz"}, [(0, 50), (10, 50)]))
        self.assertEqual([lab["n"] for lab in m["labels"]], ["Steintordamm"])
        self.assertEqual(m["labels"][0]["a"], 90)  # verlaeuft von Westen nach Osten

    def test_buildings_trees_green_water_and_pois(self):
        m = model(
            way(1, {"building": "yes", "building:levels": "4"}, square(0, 0)),
            way(2, {"building": "yes", "location": "underground"}, square(50, 0)),
            way(3, {"building": "roof"}, square(70, 0)),
            way(4, {"building:part": "yes", "height": "40"}, square(90, 0)),
            node(5, 3, 3, {"natural": "tree"}),
            node(6, 5000, 0, {"natural": "tree"}),
            way(7, {"natural": "tree_row"}, [(-20, 60), (20, 60)]),
            way(8, {"leisure": "park"}, square(-60, -60, 40)),
            way(9, {"natural": "water"}, square(60, 60, 30)),
            node(10, 10, 10, {"amenity": "cafe", "name": "Kaffee"}),
            node(11, 12, 10, {"amenity": "cafe", "name": "Kaffee"}),
            node(12, 20, 10, {"highway": "bus_stop", "name": "Hbf/Steintor"}),
            node(13, 25, 10, {"amenity": "bench", "name": "Bank"}),
        )
        self.assertEqual(sorted(b["h"] for b in m["buildings"]), [13.8, 40.0])
        self.assertEqual(len(m["trees"]), 1 + 4)  # ein Baum im Bereich (der ausserhalb nicht) und vier aus der 40-m-Baumreihe (alle 10 m)
        self.assertEqual([g["k"] for g in m["green"]], ["park"])
        self.assertEqual(len(m["water"]), 1)
        self.assertEqual([(p["n"], p["k"]) for p in m["pois"]], [("Hbf/Steintor", "bus"), ("Kaffee", "food")])  # Bus zuerst, doppelter Name nur einmal, Bank kein Ort

    def test_poi_classes(self):
        self.assertEqual(poi_class({"shop": "bakery"}), "shop")
        self.assertEqual(poi_class({"tourism": "hotel"}), "hotel")
        self.assertEqual(poi_class({"historic": "memorial"}), "culture")
        self.assertIsNone(poi_class({"amenity": "bench"}))


if __name__ == "__main__":
    unittest.main()
