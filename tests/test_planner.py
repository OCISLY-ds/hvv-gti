import random
import unittest
from datetime import datetime, timedelta

from hvv_gti import planner
from hvv_gti.planner import Planner, Reliability, TZ, line_mode, normalize_route, pick_window

from .fakes import FakeGti
from .test_integration import client_for

SCHEDULE = {
    "plannedDepartureTime": "2026-11-07T13:23:00.000+0100",
    "plannedArrivalTime": "2026-11-07T14:30:00.000+0100",
    "realDepartureTime": "2026-11-07T13:25:00.000+0100",
    "realArrivalTime": "2026-11-07T14:34:00.000+0100",
    "time": 67,
    "scheduleElements": [
        {
            "from": {"name": "Wedel", "id": "Master:85950", "depTime": {"date": "07.11.2026", "time": "13:23"}, "depDelay": 120, "platform": "Gleis 1"},
            "to": {"name": "Altona", "id": "Master:1", "arrTime": {"date": "07.11.2026", "time": "13:55"}, "arrDelay": 240},
            "line": {"name": "S1", "direction": "Poppenbüttel", "type": {"simpleType": "TRAIN"}},
        },
        {
            "from": {"name": "Altona", "id": "Master:1", "depTime": {"date": "07.11.2026", "time": "13:55"}},
            "to": {"name": "Altona", "id": "Master:1", "arrTime": {"date": "07.11.2026", "time": "13:58"}},
            "line": {"name": "Umstiegsfußweg", "type": {"simpleType": "CHANGE"}},
        },
        {
            "from": {"name": "Altona", "id": "Master:1", "depTime": {"date": "07.11.2026", "time": "14:00"}},
            "to": {"name": "Flughafen", "id": "Master:2", "arrTime": {"date": "07.11.2026", "time": "14:30"}, "arrDelay": 0},
            "line": {"name": "S11", "direction": "Flughafen", "type": {"simpleType": "TRAIN"}},
        },
    ],
}


class StubReader:
    """Verspaetungen je (Linie, Halt) fest vorgegeben, in der Mitte des Tages"""

    def __init__(self, delays: dict[tuple[str, str | None], float], cancel: dict[str, float] | None = None):
        self.delays, self.cancel = delays, cancel or {}

    def delays_for(self, key):
        return self.delays.get(key)

    def delays(self, measurement, line, station):  # noqa: F811 (Instanz-Attribut wird unten ersetzt)
        raise AssertionError

    def cancel_rate(self, line):
        return (100, self.cancel[line]) if line in self.cancel else None


def reader(delays, cancel=None):
    stub = StubReader(delays, cancel)
    noon = datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp()
    stub.delays = lambda measurement, line, station: [(noon + i, delays[(line, station)]) for i in range(60)] if (line, station) in delays else []
    return stub


class PlannerHelperTests(unittest.TestCase):
    def test_line_mode(self):
        self.assertEqual([line_mode(x) for x in ("S5", "U3", "X35", "RE5", "RB81", "234", "A1", None)], ["S", "U", "X", "R", "R", None, None, None])

    def test_normalize_route_keeps_rides_and_walks(self):
        legs = normalize_route(SCHEDULE)
        self.assertEqual([leg["line"] for leg in legs], ["S1", None, "S11"])
        self.assertEqual((legs[0]["depDelayMin"], legs[0]["arrDelayMin"], legs[0]["from"]["platform"]), (2, 4, "Gleis 1"))
        self.assertEqual(legs[1]["walkMin"], 3)
        self.assertEqual(legs[2]["dep"] - legs[0]["arr"], 5 * 60)

    def test_pick_window_falls_back_to_all_times(self):
        when = datetime(2026, 11, 7, 13, 0, tzinfo=TZ)
        night = [(datetime(2026, 11, 3, 3, 0, tzinfo=TZ).timestamp() + i, 60.0) for i in range(30)]
        values, window = pick_window(night, when)
        self.assertEqual((len(values), window), (30, "alle Zeiten"))
        saturday = [(datetime(2026, 10, 31, 13, 0, tzinfo=TZ).timestamp() + i, 0.0) for i in range(30)]  # 7.11.2026 ist ein Samstag
        self.assertEqual(pick_window(saturday + night, when)[1], "gleiche Tagesart und Uhrzeit")
        weekday = [(datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp() + i, 0.0) for i in range(30)]
        self.assertEqual(pick_window(weekday + night, when)[1], "gleiche Uhrzeit")


class ReliabilityTests(unittest.TestCase):
    when = datetime(2026, 11, 7, 13, 23, tzinfo=TZ)

    def test_punctual_lines_give_full_reliability(self):
        r = Reliability(reader({("S1", "Wedel"): 0.0, ("S1", "Altona"): 0.0, ("S11", "Altona"): 0.0, ("S11", "Flughafen"): 0.0}), random.Random(1))
        out = r.assess(normalize_route(SCHEDULE), self.when)
        self.assertEqual((out["ontimePct"], out["connectionPct"], out["transfers"]), (100.0, 100.0, 1))
        self.assertEqual(out["legsWithData"], 2)

    def test_late_feeder_misses_the_connection(self):
        delays = {("S1", "Wedel"): 0.0, ("S1", "Altona"): 600.0, ("S11", "Altona"): 0.0, ("S11", "Flughafen"): 0.0}
        out = Reliability(reader(delays), random.Random(1)).assess(normalize_route(SCHEDULE), self.when)
        self.assertEqual((out["connectionPct"], out["ontimePct"]), (0.0, 0.0))  # 5 min Umstieg, Zubringer immer 10 min spaet

    def test_missing_data_gives_no_percentages_and_known_cancel_rate(self):
        r = Reliability(reader({}, cancel={"S1": 0.1}), random.Random(1))
        out = r.assess(normalize_route(SCHEDULE), self.when)
        self.assertNotIn("ontimePct", out)
        self.assertEqual((out["cancelPct"], out["cancelCoverage"]), (10.0, "1 von 2 Abschnitten"))

    def test_without_reader_nothing_is_claimed(self):
        out = Reliability(None).assess(normalize_route(SCHEDULE), self.when)
        self.assertEqual(out, {"legsWithData": 0, "legs": 2})


class GtiPlannerTests(unittest.TestCase):
    def test_stations_departures_and_route(self):
        now_min = datetime.now(TZ).replace(second=0, microsecond=0)
        responses = {
            "checkName": {"returnCode": "OK", "results": [{"id": "Master:1", "type": "STATION", "name": "Altona", "city": "Hamburg", "combinedName": "Altona"}, {"id": "x", "type": "ADDRESS", "name": "Altonaer Str"}]},
            "departureList": {
                "returnCode": "OK",
                "station": {"combinedName": "Altona"},
                "departures": [
                    {"line": {"name": "S1", "direction": "Wedel", "type": {"simpleType": "TRAIN", "shortInfo": "S-Bahn"}}, "timeOffset": 5, "delay": 120},
                    {"line": {"name": "X35", "direction": "Rathaus", "type": {"simpleType": "BUS", "shortInfo": "XpressBus"}}, "timeOffset": 2, "cancelled": True},
                    {"line": {"name": "RE5", "direction": "Stade", "type": {"simpleType": "TRAIN"}}, "timeOffset": -30},
                ],
            },
            "getRoute": {"returnCode": "OK", "realtimeSchedules": [SCHEDULE]},
        }
        with FakeGti(responses=responses) as gti:
            p = Planner(client_for(gti), Reliability(None))
            self.assertEqual([s["id"] for s in p.stations("Alt")], ["Master:1"])
            data = p.departures("Master:1")
            self.assertEqual([(d["line"], d["mode"], d["cancelled"]) for d in data["departures"]], [("X35", "X", True), ("S1", "S", False)])
            self.assertEqual(data["departures"][1]["delayMin"], 2)
            route = p.route("Master:85950", "Master:2", now_min)
            self.assertEqual(len(route["routes"]), 1)
            self.assertEqual([l["line"] for l in route["routes"][0]["legs"]], ["S1", None, "S11"])
            sent = [body for path, body, _ in gti.requests if path.endswith("getRoute")][0]
            self.assertEqual((sent["start"]["id"], sent["timeIsDeparture"], sent["realtime"]), ("Master:85950", True, "REALTIME"))


if __name__ == "__main__":
    unittest.main()
