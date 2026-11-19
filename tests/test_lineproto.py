import unittest

from hvv_gti.influx import line_protocol
from hvv_gti.lineproto import LineError, parse_line


class RoundTripTests(unittest.TestCase):
    def roundtrip(self, measurement, tags, fields, ts):
        return parse_line(line_protocol(measurement, tags, fields, ts))

    def test_plain_point(self):
        m, tags, fields, ts = self.roundtrip("hvv_network_stop", {"line": "S1", "station": "Altona"}, {"delay_s": 120, "actual": 1700000000}, 1700000000_000000000)
        self.assertEqual((m, tags, fields, ts), ("hvv_network_stop", {"line": "S1", "station": "Altona"}, {"delay_s": 120, "actual": 1700000000}, 1700000000_000000000))

    def test_special_characters_in_tags_and_strings(self):
        tags = {"station": "Hamburg Hbf, Gleis=3", "direction": "Über\\Weg", "route": "S5 Stade → Neugraben"}
        fields = {"reason_text": 'Prognose "ungenau", Fahrzeug im Stau = ja', "cancelled": 0, "pos": 0.25, "flag": True, "gone": False, "neg": -5}
        m, t, f, ts = self.roundtrip("hvv_stop_event", tags, fields, 5)
        self.assertEqual((t, f, ts), (tags, fields, 5))

    def test_float_formats_and_missing_timestamp(self):
        self.assertEqual(parse_line("m,a=b x=1e-05,y=3 ")[2], {"x": 1e-05, "y": 3.0})
        self.assertIsNone(parse_line("m,a=b x=1i")[3])

    def test_empty_tags_are_not_written_and_measurement_without_tags(self):
        m, tags, fields, _ = self.roundtrip("hvv_api_request", {"endpoint": "departureList", "status": ""}, {"latency_ms": 110, "bytes": 5000}, 9)
        self.assertEqual(tags, {"endpoint": "departureList"})
        self.assertEqual(parse_line("only x=1i 3")[:3], ("only", {}, {"x": 1}))

    def test_broken_lines_raise(self):
        for bad in ("nofields", "m,a x=notanumber 1", "m,badtag x=1i 1", "m x 1"):
            with self.assertRaises(LineError, msg=bad):
                parse_line(bad)


class FastPathTests(unittest.TestCase):
    def test_fast_and_exact_parser_agree_on_plain_and_special_lines(self):
        import random

        rng = random.Random(3)
        words = ["S1", "Altona", "Hamburg Hbf", "a,b", "x=y", "Über\\Weg", 'sagt "hi"', "", "plain"]
        for _ in range(400):
            tags = {f"t{i}": rng.choice(words) for i in range(rng.randrange(0, 4))}
            fields = {"i": rng.randrange(-5, 99999), "f": rng.random() * 100, "b": rng.random() < 0.5}
            if rng.random() < 0.3:
                fields["s"] = rng.choice(words)
            line = line_protocol("hvv_x", tags, fields, rng.randrange(10**18, 2 * 10**18))
            got = parse_line(line)
            self.assertEqual((got[1], got[2]), ({k: v for k, v in tags.items() if v != ""}, fields), line)

    def test_fast_path_rejects_broken_lines_like_the_exact_one(self):
        for bad in ("nofields", "m,a x=notanumber 1", "m,badtag x=1i 1", "m x 1", "m a=1i x"):
            with self.assertRaises(LineError, msg=bad):
                parse_line(bad)


if __name__ == "__main__":
    unittest.main()
