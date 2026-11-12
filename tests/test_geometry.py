import math
import unittest

from hvv_gti.geometry import KX, KY, clean

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
