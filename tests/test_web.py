import json
import tempfile
import time
import unittest
from pathlib import Path

from hvv_gti import icons, web


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get_vehicle_map(self, *args, **kwargs):
        self.calls += 1
        return self.response


def journey(line="S1", start=2_000_000_000, vehicle_type="S_BAHN", journey_id="j1"):
    return {
        "journeyID": journey_id,
        "line": {"name": line, "direction": "Wedel"},
        "vehicleType": vehicle_type,
        "realtime": True,
        "segments": [
            {
                "startStopPointKey": "A:1", "endStopPointKey": "B:1", "startStationKey": "SA", "endStationKey": "SB",
                "startStationName": "Alpha", "endStationName": "Beta", "startDateTime": start, "endDateTime": start + 60,
                "realtimeDelay": 1, "isLast": False, "track": {"track": [10.0, 53.5, 10.1, 53.6]},
            }
        ],
    }


class QuietServerTest(unittest.TestCase):
    def _errors(self, exc):
        import contextlib
        import io

        server = web.QuietServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(server.server_close)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                raise exc
            except Exception:
                server.handle_error(None, ("127.0.0.1", 1))
        return err.getvalue()

    def test_client_aborts_are_silent_but_real_errors_are_reported(self):
        for exc in (ConnectionResetError("reset"), BrokenPipeError("pipe"), ConnectionAbortedError("abort"), TimeoutError("slow")):
            self.assertEqual(self._errors(exc), "")
        self.assertIn("ValueError", self._errors(ValueError("kaputt")))


class RateLimitTest(unittest.TestCase):
    def test_limits_per_key_within_the_window_and_recovers(self):
        limiter = web.RateLimiter(3, window_s=60)
        self.assertEqual([limiter.allow("a", now=t) for t in (0, 1, 2, 3)], [True, True, True, False])
        self.assertTrue(limiter.allow("b", now=3))  # anderer Client unberuehrt
        self.assertTrue(limiter.allow("a", now=61))  # Fenster vorbei
        self.assertTrue(web.RateLimiter(0).allow("x"))  # 0 = aus

    def test_client_ip_trusts_forwarded_for_only_behind_a_private_proxy(self):
        self.assertEqual(web.client_ip("10.99.0.1", "203.0.113.7, 10.99.0.1"), "203.0.113.7")
        self.assertEqual(web.client_ip("127.0.0.1", None), "127.0.0.1")
        self.assertEqual(web.client_ip("198.51.100.9", "203.0.113.7"), "198.51.100.9")  # oeffentlicher Peer: Header nicht glaubwuerdig
        self.assertEqual(web.client_ip("10.0.0.1", "kein-ip"), "10.0.0.1")


class IconIndexTest(unittest.TestCase):
    def test_train_beats_bus_and_missing_keys_are_skipped(self):
        lines = [
            {"name": "S1", "id": "bus", "type": {"simpleType": "BUS"}},
            {"name": "S1", "id": "train", "type": {"simpleType": "TRAIN"}},
            {"name": "U1", "id": None},
            {"name": "X", "id": "gone", "exists": False},
        ]
        self.assertEqual(icons.build_index(lines), {"S1": "train"})

    def test_unsafe_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = icons.IconStore(tmp)
            for name in ("../x", "a/b", "", "S1;rm"):
                self.assertIsNone(store.get(name))
            self.assertIsNone(store.get("S1", "exe"))


def two_stop_journey(journey_id, now, delay=0, line="S1", first=True):
    """Fahrt A -> B -> C; die Zeiten enthalten die Verspaetung, die Planzeit bleibt gleich."""
    def seg(a, b, start, end, **extra):
        return {
            "startStopPointKey": f"{a}:1", "endStopPointKey": f"{b}:1", "startStationKey": a, "endStationKey": b,
            "startStationName": f"Halt {a}", "endStationName": f"Halt {b}", "startDateTime": start + 60 * delay, "endDateTime": end + 60 * delay,
            "realtimeDelay": delay, "isFirst": False, "isLast": False, "track": {"track": [10.0, 53.5, 10.1, 53.6]}, **extra,
        }

    return {
        "journeyID": journey_id,
        "line": {"name": line, "direction": "Halt C", "origin": "Halt A"},
        "vehicleType": "S_BAHN",
        "realtime": True,
        "segments": [seg("A", "B", now - 10, now + 110, isFirst=first), seg("B", "C", now + 140, now + 260, isLast=True)],
    }


class TripIdsTest(unittest.TestCase):
    def fetch(self, feed, journeys):
        feed.client.response = {"journeys": journeys}
        return feed._fetch(first=False)["journeys"]

    def test_id_survives_a_changed_journey_id_and_a_changed_delay(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({}), Path(tmp))
            first = self.fetch(feed, [two_stop_journey("S1:Fahrt 0", now)])
            # naechster Abruf: GTI nennt dieselbe Fahrt anders und meldet eine Minute Verspaetung
            second = self.fetch(feed, [two_stop_journey("S1:Fahrt 1", now, delay=1)])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["id"], second[0]["id"])

    def test_following_trains_do_not_swap_ids(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({}), Path(tmp))
            ahead, behind = two_stop_journey("x", now), two_stop_journey("y", now + 120)
            ids = {j["s"][0]["a"]: j["id"] for j in self.fetch(feed, [ahead, behind])}
            # die GTI vertauscht die Namen der beiden Fahrten
            again = {j["s"][0]["a"]: j["id"] for j in self.fetch(feed, [two_stop_journey("y", now), two_stop_journey("x", now + 120)])}
        self.assertEqual(ids, again)
        self.assertEqual(len(set(ids.values())), 2)

    def test_duplicate_journeys_are_shown_once(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({}), Path(tmp))
            shown = self.fetch(feed, [two_stop_journey("a", now), two_stop_journey("b", now)])
        self.assertEqual(len(shown), 1)

    def test_same_planned_time_on_another_line_is_another_trip(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({}), Path(tmp))
            shown = self.fetch(feed, [two_stop_journey("a", now, line="S3"), two_stop_journey("b", now, line="S5")])
        self.assertEqual(len(shown), 2)

    def test_course_is_found_again_under_a_new_journey_id(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient({"journeys": [two_stop_journey("narrow", now, delay=2)]})
            feed = web.VehicleFeed(client, Path(tmp))
            uid = feed._fetch(first=False)["journeys"][0]["id"]
            client.response = {"journeys": [two_stop_journey("wide-1", now + 3000), two_stop_journey("wide-2", now, delay=2)]}
            course = feed.course(uid, "S")
        self.assertEqual(course["id"], uid)
        self.assertEqual(
            [(s["n"], s["ad"], s["d"]) for s in course["stops"]],
            [("Halt A", None, 2), ("Halt B", 2, 2), ("Halt C", 2, None)],  # Ursprung nur Abfahrt, Ziel nur Ankunft
        )
        self.assertEqual((course["o"], course["dir"]), ("Halt A", "Halt C"))
        self.assertEqual(course["dep"], (now - 10) // 60 * 60)  # Planabfahrt am Ursprung ohne Verspaetung

    def test_course_of_unknown_trip_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [two_stop_journey("a", int(time.time()))]}), Path(tmp))
            self.assertIsNone(feed.course("t999", "S"))
            self.assertIsNone(feed.course("t1", "Q"))


class BuildCourseTest(unittest.TestCase):
    def test_gap_between_segments_adds_the_missing_stop(self):
        now = 2_000_000_000
        a, b = two_stop_journey("g", now), None
        a["segments"][1]["startStationKey"] = "X"  # zweiter Abschnitt beginnt nicht dort, wo der erste endet
        a["segments"][1]["startStationName"] = "Halt X"
        stops = web.build_course(a)["stops"]
        self.assertEqual([s["n"] for s in stops], ["Halt A", "Halt B", "Halt X", "Halt C"])
        # B: nur Ankunft (der Abschnitt danach fehlt), X: nur Abfahrt
        self.assertEqual([(s["a"] is not None, s["t"] is not None) for s in stops], [(False, True), (True, False), (False, True), (True, False)])

    def test_no_planned_departure_when_the_journey_does_not_start_here(self):
        self.assertIsNone(web.build_course(two_stop_journey("m", 2_000_000_000, first=False))["dep"])


class VehicleFeedTest(unittest.TestCase):
    def test_learn_reads_the_past_and_the_future_window_and_saves(self):
        class Recording(FakeClient):
            def get_vehicle_map(self, *args, **kwargs):
                self.windows = getattr(self, "windows", []) + [args[2:4]]
                return super().get_vehicle_map(*args, **kwargs)

        soon = int(time.time()) + 10
        with tempfile.TemporaryDirectory() as tmp:
            client = Recording({"journeys": [journey("S5", soon)]})
            feed = web.VehicleFeed(client, Path(tmp))
            self.assertEqual(feed.learn(span_s=600), 1)
            (b1, e1), (b2, e2) = client.windows
            self.assertEqual((e1 - b1, e2 - b2, e1 <= b2 + 1), (600, 600, True))  # zurueck und voraus, lueckenlos
            self.assertIn("A:1>B:1", json.loads((Path(tmp) / "tracks.json").read_text())["tracks"])

    def test_signals_fall_back_to_the_seed_file_in_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({}), Path(tmp))
        self.assertIsNotNone(feed.signals)
        self.assertGreater(len(feed.signals.signals), 1000)

    def _gap_journey(self, line, soon, a="SA", b="SB", sp_a="A:2", sp_b="B:2"):
        seg = journey(line, soon)["segments"][0]
        seg.update({"startStationKey": a, "endStationKey": b, "startStopPointKey": sp_a, "endStopPointKey": sp_b, "track": None})
        return {"journeyID": f"gap-{line}", "line": {"name": line, "direction": "Wedel"}, "vehicleType": "S_BAHN", "realtime": True, "segments": [seg]}

    def test_segment_without_geometry_joins_the_existing_track_between_the_same_stops(self):
        soon = int(time.time()) + 10
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [journey("S5", soon)]}), Path(tmp))
            feed._fetch(first=False)  # lernt Gleis A:1>B:1 und die Halte SA, SB
            feed.client = FakeClient({"journeys": [self._gap_journey("S3", soon)]})
            data = feed._fetch(first=False)
        self.assertEqual(sorted(feed.tracks["A:1>B:1"]["l"]), ["S3", "S5"])
        self.assertEqual(data["journeys"][0]["s"][0]["k"], "A:1>B:1")  # der Zug laesst sich dort animieren
        self.assertNotIn("A:2>B:2", feed.tracks)

    def test_segment_without_geometry_and_without_track_gets_a_straight_stand_in_that_real_data_replaces(self):
        soon = int(time.time()) + 10
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [journey("S5", soon)]}), Path(tmp))
            feed._fetch(first=False)
            feed.client = FakeClient({"journeys": [self._gap_journey("S3", soon, a="SB", b="SA", sp_a="B:9", sp_b="A:9")]})
            feed._fetch(first=False)
            stand_in = feed.tracks["B:9>A:9"]
            self.assertTrue(stand_in["v"])
            self.assertEqual((stand_in["p"], stand_in["l"]), ([10.1, 53.6, 10.0, 53.5], ["S3"]))
            real = journey("S3", soon)["segments"][0]
            real.update({"startStationKey": "SB", "endStationKey": "SA", "startStopPointKey": "B:9", "endStopPointKey": "A:9", "track": {"track": [10.1, 53.6, 10.05, 53.55, 10.0, 53.5]}})
            feed.client = FakeClient({"journeys": [{"journeyID": "real", "line": {"name": "S3", "direction": "x"}, "vehicleType": "S_BAHN", "realtime": True, "segments": [real]}]})
            feed._fetch(first=False)
        self.assertNotIn("v", feed.tracks["B:9>A:9"])
        self.assertEqual(len(feed.tracks["B:9>A:9"]["p"]), 6)

    def test_xbus_is_on_by_default_with_tracks_and_stops(self):
        self.assertIn("XPRESSBUS", web.VehicleFeed(FakeClient({}), Path("/nonexistent")).vehicle_types)

    def test_xbus_draws_tracks_and_stops(self):
        soon = int(time.time()) + 10
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [journey("X22", soon, "XPRESSBUS")]}), Path(tmp), vehicle_types=("XPRESSBUS",))
            data = feed._fetch(first=False)
        self.assertEqual((len(feed.tracks), set(feed.stations)), (1, {"SA", "SB"}))
        self.assertEqual([j["m"] for j in data["journeys"]], ["X"])

    def test_all_re_and_rb_lines_of_the_regional_trains_are_shown(self):
        soon = int(time.time()) + 10
        trains = [journey("RE4", soon, "R_BAHN"), journey("RE5", soon, "R_BAHN"), journey("RB81", soon, "R_BAHN"), journey("IC", soon, "R_BAHN"), journey("S1", soon)]
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": trains}), Path(tmp))
            data = feed._fetch(first=False)
        self.assertEqual(sorted(j["l"] for j in data["journeys"]), ["RB81", "RE4", "RE5", "S1"])
        self.assertEqual({j["m"] for j in data["journeys"]}, {"R", "S"})

    def test_fetch_builds_journeys_tracks_and_stations(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [journey(start=2_000_000_000)]}), Path(tmp))
            data = feed._fetch(first=True)
            feed._fetch(first=True)
            self.assertEqual(len(feed.tracks), 1)
            self.assertEqual(set(feed.stations), {"SA", "SB"})
            self.assertEqual(feed.tracks["A:1>B:1"]["l"], ["S1"])
            self.assertEqual(data["journeys"], [])  # liegt weit in der Zukunft, ausserhalb des Fensters

    def test_current_segment_is_kept(self):
        import time

        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [journey(start=now - 10)]}), Path(tmp))
            data = feed._fetch(first=False)
            j = data["journeys"][0]
            self.assertEqual((j["l"], j["m"], j["s"][0]["k"], j["s"][0]["d"]), ("S1", "S", "A:1>B:1", 1))

    def test_tracks_persist_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = web.VehicleFeed(FakeClient({"journeys": [journey()]}), Path(tmp))
            feed._fetch(first=True)
            feed._save(force=True)
            again = web.VehicleFeed(FakeClient({}), Path(tmp))
            self.assertEqual(again.tracks, feed.tracks)
            self.assertEqual(json.loads(Path(tmp, "tracks.json").read_text())["stations"]["SA"]["n"], "Alpha")

    def test_vehicles_are_cached_between_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient({"journeys": []})
            feed = web.VehicleFeed(client, Path(tmp), refresh_s=60)
            feed.vehicles()
            feed.vehicles()
            self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
