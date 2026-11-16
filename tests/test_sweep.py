import unittest
from datetime import datetime, timezone

from hvv_gti.config import MAX_REQUESTS_PER_SECOND, MIN_REQUEST_INTERVAL, Config, SweepStation
from hvv_gti.sweep import MEASUREMENT, Sweeper, parse_sweep

from .fakes import FakeGti
from .test_integration import client_for

NOW = datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)


def dep(line, offset, direction="Irgendwo", **extra):
    return {"line": {"name": line, "direction": direction, "origin": "Anderswo"}, "timeOffset": offset, **extra}


class SweepTests(unittest.TestCase):
    def test_parse_keeps_local_lines_and_cancellations_in_own_measurement(self):
        response = {"departures": [dep("U3", 3, cancelled=True), dep("234", 5, delay=120), dep("RE1", 7), dep("ICE 701", 9), dep("S7", 4)]}
        lines = parse_sweep(response, NOW, "Rathaus", skip={("Rathaus", "S7")})
        self.assertEqual(len(lines), 3)  # kein ICE, kein bereits erfasster Messpunkt (S7)
        self.assertTrue(all(l.startswith(f"{MEASUREMENT},") for l in lines))
        u3 = next(l for l in lines if "line=U3" in l)
        self.assertIn("cancelled=1i", u3)
        self.assertIn("station=Rathaus", u3)
        self.assertIn("delay_s=120i", next(l for l in lines if "line=234" in l))

    def test_rotation_visits_every_node_and_wraps(self):
        cfg = Config(sweep=tuple(SweepStation(f"H{i}", f"Master:{i}", ("BUS",)) for i in range(5)), sweep_per_cycle=2)
        sweeper = Sweeper()
        seen = [[n.name for n in sweeper.batch(cfg)] for _ in range(4)]
        self.assertEqual(seen, [["H0", "H1"], ["H2", "H3"], ["H4", "H0"], ["H1", "H2"]])
        self.assertEqual(Sweeper().batch(Config(sweep_per_cycle=0)), [])

    def test_collect_asks_one_departure_list_per_node(self):
        cfg = Config(sweep=(SweepStation("Altona", "Master:80953", ("ZUG", "SBAHN")), SweepStation("Wedel", "Master:85950", ("BUS",))), sweep_per_cycle=5)
        with FakeGti(responses={"departureList": {"returnCode": "OK", "departures": [dep("RE70", 4, cancelled=True)]}}) as gti:
            lines = Sweeper().collect(client_for(gti), cfg, now=NOW)
        self.assertEqual(len(gti.requests), 2)
        self.assertEqual(gti.requests[0][1]["serviceTypes"], ["ZUG", "SBAHN"])
        self.assertEqual(gti.requests[0][1]["station"], {"id": "Master:80953", "type": "STATION"})
        self.assertEqual(len(lines), 2)

    def test_request_rate_cannot_exceed_the_limit(self):
        self.assertLess(1 / MIN_REQUEST_INTERVAL, MAX_REQUESTS_PER_SECOND)
        self.assertGreaterEqual(Config.from_env({"HVV_MIN_REQUEST_INTERVAL": "0.2"}).min_request_interval, MIN_REQUEST_INTERVAL)
        self.assertEqual(Config.from_env({"HVV_MIN_REQUEST_INTERVAL": "1.5"}).min_request_interval, 1.5)
        # Aufrufe je Zyklus (11 Messpunkt- und Netzaufrufe + Ausfall-Erfassung) bleiben weit unter dem Limit, selbst mit 3 s je Aufruf
        calls = 11 + Config().sweep_per_cycle
        self.assertLess(calls / (60 + calls * 3.0), 0.5)


if __name__ == "__main__":
    unittest.main()
