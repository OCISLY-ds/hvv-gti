import tempfile
import time
import unittest
from pathlib import Path

from hvv_gti import network, web
from hvv_gti.dedupe import drop_ghosts

from tests.test_web import FakeClient


def trip(now, delay, journey_id, line="S3", direction="Elbgaustraße"):
    """Hauptbahnhof -> Altona; die Zeiten enthalten die Verspaetung, die Planzeit (start - delay) ist bei beiden Varianten gleich."""
    start = now - 200 + delay * 60
    seg = lambda a, b, s0, s1, **kw: {
        "startStopPointKey": f"{a}:1", "endStopPointKey": f"{b}:1", "startStationKey": a, "endStationKey": b,
        "startStationName": a, "endStationName": b, "startDateTime": s0, "endDateTime": s1, "realtimeDelay": delay,
        "isFirst": False, "isLast": False, "track": {"track": [10.0, 53.5, 10.1, 53.6]}, **kw,
    }
    return {
        "journeyID": journey_id,
        "line": {"name": line, "direction": direction, "origin": "Neugraben"},
        "vehicleType": "S_BAHN",
        "realtime": True,
        "segments": [seg("Hbf", "Jungfernstieg", start, start + 120), seg("Jungfernstieg", "Altona", start + 150, start + 300, isLast=True)],
    }


class GhostTripTest(unittest.TestCase):
    """07.11.2026: GTI liefert fuer einen um 6 min verspaeteten Zug auch eine Geisterfahrt zur Planzeit mit 0 min."""

    def test_the_delayed_trip_wins_in_either_order(self):
        now = int(time.time())
        real, ghost = trip(now, 6, "real"), trip(now, 0, "ghost")
        for order in ((real, ghost), (ghost, real)):
            kept = drop_ghosts(list(order))
            self.assertEqual([j["journeyID"] for j in kept], ["real"])

    def test_identical_duplicates_keep_the_first_and_other_trips_stay(self):
        now = int(time.time())
        kept = drop_ghosts([trip(now, 0, "a"), trip(now, 0, "b"), trip(now + 600, 0, "later"), trip(now, 0, "c", line="S1")])
        self.assertEqual([j["journeyID"] for j in kept], ["a", "later", "c"])

    def test_map_shows_the_delay_whatever_the_order(self):
        now = int(time.time())
        for order in ((6, 0), (0, 6)):
            with tempfile.TemporaryDirectory() as tmp:
                feed = web.VehicleFeed(FakeClient({"journeys": [trip(now, d, f"j{d}") for d in order]}), Path(tmp))
                shown = feed._fetch(first=False)["journeys"]
            self.assertEqual([(j["s"][0]["d"]) for j in shown], [6], order)

    def test_course_of_a_delayed_trip_shows_the_delay(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [trip(now, 0, "ghost"), trip(now, 6, "real")]}), Path(tmp))
            jid = feed._fetch(first=False)["journeys"][0]["id"]
            course = feed.course(jid, "S")
        self.assertEqual([s["d"] for s in course["stops"] if s["d"] is not None][0], 6)

    def test_statistics_get_one_point_per_stop_with_the_real_delay(self):
        now = int(time.time())
        for order in ((6, 0), (0, 6)):
            journeys = drop_ghosts([trip(now, d, f"j{d}") for d in order])
            lines = [l for j in journeys for l in network.parse_journey(j, now, 900)]
            self.assertEqual(len(lines), 3, order)  # Hbf, Jungfernstieg, Altona (Ankunft)
            self.assertTrue(all("delay_s=360i" in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
