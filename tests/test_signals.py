import unittest

from hvv_gti.signals import KX, KY, SignalIndex

X0, Y0 = 10.0, 53.5


def east(metres: float) -> float:
    return X0 + metres / KX


class SignalIndexTests(unittest.TestCase):
    # Gleis 1.000 m nach Osten; Signale: Mitte (Kurs Ost), Gegengleis 5 m weiter noerdlich (Kurs West), eins am Anfang
    line = [X0, Y0, east(1000), Y0]

    def test_assigns_only_signals_facing_the_direction_of_travel(self):
        index = SignalIndex(
            [
                [east(400), Y0 + 3 / KY, 90],  # gilt fuer Ost
                [east(700), Y0 + 5 / KY, 270],  # Gegengleis
                [east(5), Y0, 90],  # direkt am Anfang: gehoert zum Bahnsteig
            ]
        )
        self.assertEqual(index.fractions(self.line), [0.4])
        self.assertEqual(index.fractions([east(1000), Y0, X0, Y0]), [0.3])  # Gegenrichtung: das Signal bei 700 m

    def test_ignores_far_signals_and_merges_close_ones(self):
        index = SignalIndex([[east(400), Y0 + 40 / KY, 90], [east(500), Y0, 90], [east(520), Y0, 90]])
        self.assertEqual(index.fractions(self.line), [0.5])

    def test_short_or_empty(self):
        self.assertEqual(SignalIndex([]).fractions(self.line), [])
        self.assertEqual(SignalIndex([[X0, Y0, 90]]).fractions([X0, Y0, east(20), Y0]), [])


if __name__ == "__main__":
    unittest.main()
