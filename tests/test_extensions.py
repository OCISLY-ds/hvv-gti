import tempfile
import time
import unittest
from pathlib import Path

from hvv_gti import web
from hvv_gti.extensions import DOLLERN, Seed, extend

from tests.test_web import FakeClient


def s5(now, *, toward_stade, delay=4):
    """Wie die GTI am 07.11.2026: Zug nach Neugraben beginnt in Dollern, Zug nach Stade endet dort (jeweils isFirst/isLast falsch)."""
    if toward_stade:
        a, b = ("SBH:1114502:1", "SBH:1115001:1"), ("Horneburg", "Master:8003002", "Dollern", DOLLERN)
        start = now - 120
        direction, origin = "Stade", "Neugraben"
    else:
        a, b = ("SBH:1115002:1", "SBH:1114503:1"), ("Dollern", DOLLERN, "Horneburg", "Master:8003002")
        start = now + 600
        direction, origin = "Neugraben", "Stade"
    seg = {
        "startStopPointKey": a[0], "endStopPointKey": a[1], "startStationName": b[0], "startStationKey": b[1],
        "endStationName": b[2], "endStationKey": b[3], "startDateTime": start, "endDateTime": start + 240, "destination": direction,
        "realtimeDelay": delay, "isFirst": False, "isLast": False, "track": {"track": [9.5, 53.5, 9.6, 53.6]},
    }
    return {"journeyID": "x", "line": {"name": "S5", "direction": direction, "origin": origin}, "vehicleType": "S_BAHN", "realtime": True, "segments": [seg]}


class ExtensionTest(unittest.TestCase):
    seed = Seed()

    def test_seed_has_the_track_and_the_three_stations(self):
        self.assertEqual(len(self.seed.tracks), 4)
        self.assertEqual({s["n"] for s in self.seed.stations.values()}, {"Stade", "Agathenburg", "Dollern"})

    def test_train_to_neugraben_gets_stade_and_agathenburg_before_dollern(self):
        now = 2_000_000_000
        j = s5(now, toward_stade=False)
        self.assertTrue(extend(j, self.seed))
        segs = j["segments"]
        self.assertEqual([(s["startStationName"], s["endStationName"]) for s in segs], [("Stade", "Agathenburg"), ("Agathenburg", "Dollern"), ("Dollern", "Horneburg")])
        self.assertEqual([s["isFirst"] for s in segs], [True, False, False])
        # Fahrplan: Stade ab 8 min vor Dollern ab, Agathenburg ab 3 min vor Dollern ab
        self.assertEqual((now + 600) - segs[0]["startDateTime"], 480)
        self.assertEqual((now + 600) - segs[1]["startDateTime"], 180)
        self.assertTrue(all(a["endDateTime"] <= b["startDateTime"] for a, b in zip(segs, segs[1:])))
        self.assertEqual({s["realtimeDelay"] for s in segs}, {4})

    def test_train_to_stade_runs_on_to_stade(self):
        now = 2_000_000_000
        j = s5(now, toward_stade=True)
        self.assertTrue(extend(j, self.seed))
        segs = j["segments"]
        self.assertEqual([(s["startStationName"], s["endStationName"]) for s in segs], [("Horneburg", "Dollern"), ("Dollern", "Agathenburg"), ("Agathenburg", "Stade")])
        self.assertEqual([s["isLast"] for s in segs], [False, False, True])
        self.assertEqual(segs[1]["startDateTime"] - segs[0]["endDateTime"], 30)
        self.assertEqual(segs[2]["endDateTime"] - segs[1]["startDateTime"], 180 + 300)  # Dollern ab -> Stade an

    def test_complete_trips_and_other_lines_are_left_alone(self):
        j = s5(2_000_000_000, toward_stade=False)
        j["segments"][0]["isFirst"] = True  # beginnt wirklich in Dollern: nichts zu ergaenzen
        self.assertFalse(extend(j, self.seed))
        other = s5(2_000_000_000, toward_stade=False)
        other["line"]["name"] = "S3"
        self.assertFalse(extend(other, self.seed))
        self.assertFalse(extend(s5(2_000_000_000, toward_stade=False), Seed(Path("/nonexistent"))))  # ohne Startdatei unveraendert

    def test_map_draws_the_section_and_the_train_is_on_it(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [s5(now, toward_stade=False)]}), Path(tmp))
            data = feed._fetch(first=False)
        self.assertEqual(len(data["journeys"]), 1)  # Hauptabruf und Verlaengerungsabruf liefern dieselbe Fahrt, sie erscheint einmal
        j = data["journeys"][0]
        self.assertEqual([s["f"] for s in j["s"]], ["Stade"])  # Stade ab liegt 8 min vor Dollern ab, Agathenburg ab erst nach dem 5-min-Fenster
        self.assertEqual(j["end"], False)
        tracks = [t for t in feed.tracks if t.startswith("SBH:1117003:1>")]
        self.assertEqual(tracks, ["SBH:1117003:1>SBH:1116002:1"])
        self.assertEqual(feed.tracks[tracks[0]]["l"], ["S5"])
        self.assertIn("Master:8000089", feed.stations)

    def test_train_to_stade_is_marked_as_ending_there(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [s5(now, toward_stade=True)]}), Path(tmp))
            j = feed._fetch(first=False)["journeys"][0]
        self.assertEqual([s["f"] for s in j["s"]], ["Horneburg", "Dollern"])  # Agathenburg ab liegt hinter dem 5-min-Fenster
        self.assertTrue(j["end"])  # der ergaenzte Abschnitt endet in Stade


if __name__ == "__main__":
    unittest.main()
