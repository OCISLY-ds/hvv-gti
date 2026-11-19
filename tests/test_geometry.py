import math
import unittest

from hvv_gti.geometry import KX, KY, clean, leg_shape

X0, Y0 = 10.0, 53.5


def at(east_m: float, north_m: float = 0.0) -> tuple[float, float]:
    return X0 + east_m / KX, Y0 + north_m / KY


def flat(*points: tuple[float, float]) -> list[float]:
    return [v for p in points for v in p]


def metres(out: list[float]) -> list[tuple[float, float]]:
    return [((out[i] - X0) * KX, (out[i + 1] - Y0) * KY) for i in range(0, len(out), 2)]


class CleanTests(unittest.TestCase):
    def test_removes_the_stub_at_the_platform_end_and_keeps_both_ends(self):
        out = metres(clean(flat(at(0), at(47), at(-55), at(-200, 10))))
        self.assertEqual(len(out), 3)  # Spitze weg, die Kurve am Schluss wird abgerundet
        self.assertAlmostEqual(out[0][0], 0, places=0)
        self.assertAlmostEqual(out[-1][0], -200, delta=0.5)
        self.assertTrue(all(x <= 0.5 for x, _ in out))

    def test_drops_duplicate_points_and_one_metre_spikes(self):
        out = metres(clean(flat(at(0), at(0.1), at(100), at(100.5, 1), at(100, 0.2), at(200))))
        self.assertTrue(all(abs(out[i + 1][0] - out[i][0]) > 0.4 for i in range(len(out) - 1)))
        self.assertAlmostEqual(out[-1][0], 200, delta=0.5)

    def test_rounds_a_right_angle_without_moving_the_ends(self):
        out = metres(clean(flat(at(0), at(100), at(100, 100))))
        self.assertEqual(len(out), 4)
        self.assertEqual((round(out[0][0]), round(out[0][1])), (0, 0))
        self.assertAlmostEqual(out[-1][0], 100, delta=1.5)
        self.assertAlmostEqual(out[-1][1], 100, delta=1.5)
        for x, y in out[1:-1]:  # die Ecke (100, 0) wird um hoechstens ~12 m abgeschnitten
            self.assertTrue(1 < math.hypot(100 - x, y) <= 12.6, (x, y))

    def test_straight_line_is_untouched(self):
        pts = flat(at(0), at(50, 0.5), at(100), at(200, 1))
        self.assertEqual(len(clean(pts)) // 2, 4)


if __name__ == "__main__":
    unittest.main()


class LegShapeTests(unittest.TestCase):
    def stop(self, east, north=0.0):
        x, y = at(east, north)
        return {"x": x, "y": y}

    def tracks(self):
        return {
            "a>b": {"p": flat(at(0), at(500, 300), at(1000)), "l": ["S1"]},
            "b>c": {"p": flat(at(2000), at(1500, 200), at(1000)), "l": ["S1"]},  # gegen die Fahrtrichtung gespeichert
            "x>y": {"p": flat(at(0), at(1000, -800), at(2000)), "l": ["U3"]},
        }

    def test_follows_the_tracks_of_the_line_in_either_direction(self):
        leg = {"line": "S1", "from": self.stop(0), "stops": [self.stop(1000)], "to": self.stop(2000)}
        out = metres(leg_shape(self.tracks(), leg))
        self.assertEqual(len(out), 5)  # beide Abschnitte, der zweite umgedreht, Stoss an der Mittelhaltestelle nur einmal
        self.assertAlmostEqual(out[1][1], 300, places=-1)
        self.assertAlmostEqual(out[3][1], 200, places=-1)
        self.assertNotIn(-800, [round(p[1]) for p in out])  # nicht die U3

    def test_connects_straight_where_no_track_is_known_and_uses_the_base_line(self):
        leg = {"line": "S11", "from": self.stop(0), "stops": [], "to": self.stop(5000)}
        self.assertEqual(len(leg_shape({}, leg)) // 2, 2)
        leg = {"line": "S11", "from": self.stop(0), "to": self.stop(1000)}
        self.assertEqual(len(leg_shape(self.tracks(), leg)) // 2, 3)


class RailFeaturesTest(unittest.TestCase):
    def test_only_local_passenger_lines_each_track_once(self):
        from hvv_gti.geometry import rail_features

        a, b = at(0), at(1000)
        mid = at(500, 3)
        tracks = {
            "s>": {"p": flat(a, mid, b), "l": ["S5", "S3"]},
            "<s": {"p": flat(b, mid, a), "l": ["S5"]},  # Gegenrichtung: dasselbe Gleis
            "re": {"p": flat(at(0, 500), at(1000, 500)), "l": ["RE5"]},
            "bus": {"p": flat(at(0, 900), at(1000, 900)), "l": ["X80", "5"]},
            "virtual": {"p": flat(at(0, 700), at(1000, 700)), "l": ["U1"], "v": 1},
        }
        feats = rail_features(tracks)["features"]
        self.assertEqual(sorted(f["properties"]["m"] for f in feats), ["R", "S"])
