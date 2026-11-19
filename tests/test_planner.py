import json
import random
import unittest
import unittest.mock
from datetime import datetime, timedelta

from hvv_gti import planner
from hvv_gti.planner import Planner, is_walk, Reliability, TZ, data_mode, flux_str, line_mode, normalize_route, pick_window

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
    def test_flux_str_keeps_umlauts_and_escapes_quotes_and_interpolation(self):
        self.assertEqual(flux_str("Poppenbüttel"), '"Poppenbüttel"')
        self.assertEqual(flux_str('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(flux_str("${x}"), '"\\${x}"')

    def test_line_mode(self):
        self.assertEqual([line_mode(x) for x in ("S5", "U3", "X35", "RE5", "RB81", "234", "A1", None)], ["S", "U", "X", "R", "R", None, None, None])

    def test_normalize_route_keeps_rides_and_walks(self):
        legs = normalize_route(SCHEDULE)
        self.assertEqual([leg["line"] for leg in legs], ["S1", None, "S11"])
        self.assertEqual((legs[0]["depDelayMin"], legs[0]["arrDelayMin"], legs[0]["from"]["platform"]), (2, 4, "Gleis 1"))
        self.assertEqual(legs[1]["walkMin"], 3)
        self.assertEqual(legs[2]["dep"] - legs[0]["arr"], 5 * 60)

    def test_transfer_carries_wait_station_and_both_platforms(self):
        schedule = {
            "scheduleElements": [
                {
                    "from": {"name": "Bergedorf", "id": "Master:1", "depTime": {"date": "12.11.2026", "time": "15:28"}, "platform": "Gleis 4"},
                    "to": {"name": "Hamburg Hbf", "id": "Master:2", "arrTime": {"date": "12.11.2026", "time": "15:49"}, "platform": "Gleis 1 (S-Bahn)"},
                    "line": {"name": "S7", "direction": "Altona", "type": {"simpleType": "TRAIN"}},
                },
                {
                    "from": {"name": "Hamburg Hbf", "id": "Master:2", "depTime": {"date": "12.11.2026", "time": "15:49"}, "platform": "Gleis 1 (S-Bahn)"},
                    "to": {"name": "Hamburg Hbf", "id": "Master:2", "arrTime": {"date": "12.11.2026", "time": "15:49"}, "platform": "Gleis 2 (S-Bahn)"},
                    "line": {"name": "Umstiegsfußweg", "type": {"simpleType": "CHANGE"}},
                },
                {
                    "from": {"name": "Hamburg Hbf", "id": "Master:2", "depTime": {"date": "12.11.2026", "time": "15:50"}, "platform": "Gleis 2 (S-Bahn)"},
                    "to": {"name": "Dammtor", "id": "Master:3", "arrTime": {"date": "12.11.2026", "time": "15:52"}, "platform": "Gleis 1"},
                    "line": {"name": "S5", "direction": "Elbgaustraße", "type": {"simpleType": "TRAIN"}},
                },
            ]
        }
        legs = normalize_route(schedule)
        self.assertEqual(legs[0]["to"]["platform"], "Gleis 1 (S-Bahn)")  # Ankunftsgleis kommt mit
        self.assertNotIn("transfer", legs[0])
        transfer = legs[2]["transfer"]
        self.assertEqual((transfer["station"], transfer["waitMin"], transfer["walkMin"]), ("Hamburg Hbf", 1, 0))
        self.assertEqual((transfer["arrPlatform"], transfer["depPlatform"]), ("Gleis 1 (S-Bahn)", "Gleis 2 (S-Bahn)"))
        self.assertEqual(transfer["arr"], legs[0]["arr"])

    def test_ride_details_carry_stops_vehicle_and_notices(self):
        schedule = json.loads(json.dumps(SCHEDULE))
        first = schedule["scheduleElements"][0]
        first["intermediateStops"] = [{"name": "Hauptbahnhof", "depTime": {"date": "07.11.2026", "time": "13:40"}, "depDelay": 120}]
        first["line"].update({"origin": "Wedel", "carrierNameShort": "S-Bahn", "type": {"simpleType": "TRAIN", "longInfo": "S-Bahn"}})
        first["announcements"] = [
            {"summary": "Bauarbeiten", "description": "Gleisbau. ", "validities": [{"begin": "2026-11-02T04:00:00.000+0100", "end": "2026-11-30T04:00:00.000+0100"}],
             "lastModified": "2026-10-26T09:21:54.776+0100", "links": [{"label": "Karte", "url": "https://example.org/k.png"}, {"label": "x", "url": "javascript:alert(1)"}]},
            {"summary": "Bauarbeiten"},
            {"summary": "Aufzug defekt"},
        ]
        leg = normalize_route(schedule)[0]
        self.assertEqual(leg["stopCount"], 2)
        self.assertEqual((leg["stops"][0]["name"], leg["stops"][0]["delayMin"]), ("Hauptbahnhof", 2))
        self.assertEqual((leg["vehicle"], leg["carrier"], leg["origin"], leg["durationMin"]), ("S-Bahn", "S-Bahn", "Wedel", 32))
        self.assertEqual(leg["notices"], ["Bauarbeiten", "Aufzug defekt"])
        info = leg["noticeInfo"][0]
        self.assertEqual((info["summary"], info["text"], info["links"]), ("Bauarbeiten", "Gleisbau.", [{"label": "Karte", "url": "https://example.org/k.png"}]))
        self.assertEqual((info["from"], info["to"]), (1793588400.0, 1796007600.0))
        self.assertIsNone(leg["noticeInfo"][1]["from"])
        self.assertEqual(normalize_route(schedule)[2]["transfer"]["toStation"], "Altona")

    def test_segment_notice_only_for_rides_through_that_segment(self):
        schedule = json.loads(json.dumps(SCHEDULE))
        first = schedule["scheduleElements"][0]
        first["announcements"] = [
            {"summary": "🚧 Fahrplanänderung zw. Bergedorf u. Aumühle"},
            {"summary": "S7: Bergedorf -> Aumühle Abfahrtszeiten in den Auskunftssystemen nicht korrekt"},
            {"summary": "Aufzug defekt"},
        ]
        first["from"]["name"], first["to"]["name"] = "Bergedorf", "Hamburg Hbf"
        self.assertEqual(normalize_route(schedule)[0]["notices"], ["Aufzug defekt"])  # faehrt nicht bis Aumuehle
        first["intermediateStops"] = [{"name": "Aumühle", "depTime": {"date": "07.11.2026", "time": "13:40"}}]
        self.assertEqual(len(normalize_route(schedule)[0]["notices"]), 3)

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

    def setUp(self):
        # die Testreihen haben nur 60 Messungen je Halt; die Schwelle fuer "zu wenig am Halt" gilt in eigenen Tests mit dem echten Wert
        self.enterContext(unittest.mock.patch.object(planner, "MIN_STATION_SAMPLES", 25))

    def test_terminus_without_usable_data_uses_the_last_reliable_stop(self):
        noon = datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp()
        asked = []

        class Stub:
            def delays(self, measurement, line, station):
                return [(noon + i, 0.0) for i in range(42)]  # Stade: nur Nullen

            def train_delays(self, line, station, direction):
                asked.append((line, station, direction))
                return [(noon + i, 120.0 * (i % 4)) for i in range(400)]

            def cancel_rate(self, line):
                return (200, 0.01)

        dist = Reliability(Stub(), random.Random(1)).distribution("S5", "Stade", self.when)
        self.assertEqual(asked, [("S5", "Dollern", "Stade")])
        self.assertEqual((dist["scope"], dist["n"]), ("Vorhalt Dollern", 400))
        self.assertGreater(max(dist["values"]), 0)

    def test_a_station_with_too_few_measurements_falls_back_to_the_whole_line(self):
        self.enterContext(unittest.mock.patch.object(planner, "MIN_STATION_SAMPLES", 100))
        noon = datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp()

        class Stub:
            def delays(self, measurement, line, station):
                return [(noon + i, 0.0) for i in range(42)] if station == "Stade" else [(noon + i, 120.0 * (i % 3)) for i in range(500)]

            def cancel_rate(self, line):
                return (200, 0.01)

        dist = Reliability(Stub(), random.Random(1)).distribution("S5", "Stade", self.when)
        self.assertEqual((dist["scope"], dist["n"]), ("ganze Linie", 500))

    def test_bus_lines_have_data_too(self):
        # Buslinien (nur Ziffern) haben Messreihen: Bus 5 je Halt (hvv_bus_stop), die uebrigen aus der Abfahrtstafel (hvv_stop_event)
        self.assertEqual([data_mode(x) for x in ("4", "5", "234", "S7", "RE5", "X30", "A1", "S7-SEV", None)], ["B", "B", "B", "S", "R", "X", None, "S", None])
        asked = []
        noon = datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp()

        class Stub:
            def delays(self, measurement, line, station):
                asked.append((measurement, line, station))
                # Bus 4 kennt nur Rathausmarkt (Abfahrtstafel): andere Halte fallen auf die ganze Linie zurueck
                return [(noon + i, 60.0) for i in range(60)] if measurement == "hvv_stop_event" and station in (None, "Rathausmarkt") else []

            def cancel_rate(self, line):
                return (200, 0.01)

        r = Reliability(Stub(), random.Random(1))
        dist = r.distribution("4", "Universität/Staatsbibliothek", self.when)
        self.assertEqual((dist["n"], dist["scope"]), (60, "ganze Linie"))
        self.assertIn(("hvv_bus_stop", "4", None), asked)
        self.assertEqual(r.distribution("4", "Rathausmarkt", self.when)["scope"], "Halt")
        self.assertEqual(r.cancel_rate("234"), {"n": 200, "rate": 0.01})

    def test_punctual_lines_give_full_reliability(self):
        r = Reliability(reader({("S1", "Wedel"): 0.0, ("S1", "Altona"): 0.0, ("S11", "Altona"): 0.0, ("S11", "Flughafen"): 0.0}), random.Random(1))
        out = r.assess(normalize_route(SCHEDULE), self.when)
        self.assertEqual((out["ontimePct"], out["connectionPct"], out["transfers"]), (100.0, 100.0, 1))
        self.assertEqual(out["legsWithData"], 2)

    def test_late_feeder_misses_the_connection(self):
        delays = {("S1", "Wedel"): 0.0, ("S1", "Altona"): 600.0, ("S11", "Altona"): 0.0, ("S11", "Flughafen"): 0.0}
        out = Reliability(reader(delays), random.Random(1)).assess(normalize_route(SCHEDULE), self.when)
        self.assertEqual((out["connectionPct"], out["ontimePct"]), (0.0, 0.0))  # 5 min Umstieg, Zubringer immer 10 min spaet

    def schedule_with_gap(self, walk_min, wait_min):
        """SCHEDULE mit `walk_min` Fussweg und `wait_min` Warten nach dem Fussweg (Summe = Zeit zwischen Ankunft und Abfahrt)."""
        schedule = json.loads(json.dumps(SCHEDULE))
        elements = schedule["scheduleElements"]
        elements[0]["to"]["arrTime"]["time"] = "13:30"
        elements[1]["from"]["depTime"]["time"] = "13:30"
        elements[1]["to"]["arrTime"]["time"] = f"13:{30 + walk_min}"
        elements[2]["from"]["depTime"]["time"] = f"13:{30 + walk_min + wait_min}"
        return schedule

    def assess_with_feeder_delay(self, schedule, feeder_s, connecting_s=0.0):
        delays = {("S1", "Wedel"): 0.0, ("S1", "Altona"): feeder_s, ("S11", "Altona"): connecting_s, ("S11", "Flughafen"): 0.0}
        legs = normalize_route(schedule)
        out = Reliability(reader(delays), random.Random(1)).assess(legs, self.when)
        return out, legs[2]["transfer"]

    def test_delay_equal_to_the_planned_buffer_misses_the_connection(self):
        """HVV-App sagt 3 min Umstieg passt; der Zubringer kommt 3 min zu spaet -> verpasst, bei 2 min Verspaetung noch erreicht."""
        schedule = self.schedule_with_gap(walk_min=0, wait_min=3)
        late3, _ = self.assess_with_feeder_delay(schedule, 180.0)
        late2, transfer = self.assess_with_feeder_delay(schedule, 120.0)
        self.assertEqual(late3["connectionPct"], 0.0)
        self.assertEqual(late2["connectionPct"], 100.0)
        self.assertEqual(transfer["catchByMin"]["0"], 100.0)

    def test_walk_counts_and_one_minute_slack_tolerates_no_minute_of_delay(self):
        """2 min Fussweg + 1 min Warten: ein 1 min verspaeteter Zug gilt als verpasst, ein puenktlicher nicht."""
        schedule = self.schedule_with_gap(walk_min=2, wait_min=1)
        self.assertEqual(self.assess_with_feeder_delay(schedule, 0.0)[0]["connectionPct"], 100.0)
        self.assertEqual(self.assess_with_feeder_delay(schedule, 60.0)[0]["connectionPct"], 0.0)

    def test_min_transfer_time_overrides_a_shorter_planned_gap(self):
        """Der Regler ersetzt den Fussweg nur, wenn er groesser ist (max, keine Summe); 1 min Zeit reicht dann nie fuer 2 min."""
        schedule = self.schedule_with_gap(walk_min=0, wait_min=1)
        out, transfer = self.assess_with_feeder_delay(schedule, 0.0)
        by_min = {int(k): v["connectionPct"] for k, v in out["byMinTransfer"].items()}
        self.assertEqual((by_min[0], by_min[1], by_min[2]), (100.0, 0.0, 0.0))  # bei T=1 bleibt keine Reserve mehr
        self.assertEqual(transfer["catchByMin"]["0"], 100.0)
        self.assertEqual(transfer["catchByMin"]["3"], 0.0)

    def test_connection_probability_never_rises_with_larger_min_transfer(self):
        schedule = self.schedule_with_gap(walk_min=1, wait_min=4)
        delays = {("S1", "Wedel"): 0.0, ("S1", "Altona"): 0.0, ("S11", "Altona"): 0.0, ("S11", "Flughafen"): 0.0}
        reliability = Reliability(reader(delays), random.Random(3))
        reliability.reader.delays = lambda m, line, st: [(datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp() + i, 60.0 * (i % 4)) for i in range(60)]
        out = reliability.assess(normalize_route(schedule), self.when)
        values = [out["byMinTransfer"][str(t)]["connectionPct"] for t in planner.TRANSFER_STEPS]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertLess(values[-1], values[0])

    def test_missing_data_gives_no_percentages_and_known_cancel_rate(self):
        r = Reliability(reader({}, cancel={"S1": 0.1}), random.Random(1))
        out = r.assess(normalize_route(SCHEDULE), self.when)
        self.assertNotIn("ontimePct", out)
        self.assertEqual((out["cancelPct"], out["cancelCoverage"]), (10.0, "1 von 2 Abschnitten"))
        self.assertEqual(len(out["cancelMissing"]), 1)
        self.assertTrue(out["cancelMissing"][0].startswith("S11 ("), out["cancelMissing"])

    def test_influx_failure_does_not_break_the_route(self):
        class Broken:
            def delays(self, *args):
                raise OSError("Influx: HTTP Error 400")

            def cancel_rate(self, line):
                raise OSError("Influx: down")

        responses = {"getRoute": {"returnCode": "OK", "realtimeSchedules": [SCHEDULE]}}
        with FakeGti(responses=responses) as gti:
            route = Planner(client_for(gti), Reliability(Broken())).route("Master:85950", "Master:2", datetime.now(TZ))
        self.assertEqual(len(route["routes"]), 1)
        self.assertTrue(route["routes"][0]["reliability"]["unavailable"])

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
            data_station = data.get("station")
            self.assertIsNone(data_station)  # der Name kommt von der Karte, nicht von der Schnittstelle
            route = p.route("Master:85950", "Master:2", now_min)
            self.assertEqual(len(route["routes"]), 1)
            self.assertEqual([l["line"] for l in route["routes"][0]["legs"]], ["S1", None, "S11"])
            sent = [body for path, body, _ in gti.requests if path.endswith("getRoute")][0]
            self.assertEqual((sent["start"]["id"], sent["timeIsDeparture"], sent["realtime"]), ("Master:85950", True, "REALTIME"))


ADDRESS_RESULTS = [
    {"id": "Master:80932", "type": "STATION", "name": "Elbphilharmonie", "city": "Hamburg", "combinedName": "Elbphilharmonie", "coordinate": {"x": 9.986437, "y": 53.540339}},
    {"id": "25636", "type": "ADDRESS", "name": "Leuschnerstraße 12", "city": "Hamburg", "combinedName": "Leuschnerstraße 12, Hamburg", "coordinate": {"x": 10.207964, "y": 53.497196}},
    {"id": "25636", "type": "ADDRESS", "name": "Leuschnerstraße 12", "city": "Hamburg", "combinedName": "doppelt", "coordinate": {"x": 10.207964, "y": 53.497196}},
    {"id": "205021", "type": "POI", "name": "Planten un Blomen", "city": "Hamburg", "combinedName": "Planten un Blomen", "coordinate": {"x": 9.981223, "y": 53.562073}},
    {"id": "9", "type": "ADDRESS", "name": "Ohne Koordinate", "city": "Hamburg"},
    {"id": "10", "type": "COORDINATE", "name": "Koordinate"},
]


class PlaceTests(unittest.TestCase):
    def test_parse_place_keeps_plain_station_ids_and_checks_json_places(self):
        self.assertEqual(planner.parse_place("Master:9910950"), {"id": "Master:9910950", "type": "STATION"})
        address = planner.parse_place(json.dumps({"type": "ADDRESS", "id": "25636", "name": "Leuschnerstraße 12", "city": "Hamburg", "x": 10.2, "y": 53.49}, ensure_ascii=False))
        self.assertEqual(address, {"type": "ADDRESS", "id": "25636", "name": "Leuschnerstraße 12", "city": "Hamburg", "coordinate": {"x": 10.2, "y": 53.49}})
        here = planner.parse_place(json.dumps({"type": "COORDINATE", "x": 10.0, "y": 53.5}))
        self.assertEqual(here, {"type": "COORDINATE", "name": "Mein Standort", "coordinate": {"x": 10.0, "y": 53.5}})

    def test_parse_place_rejects_nonsense(self):
        bad = [
            "", "{", "[]", "a b",
            json.dumps({"type": "WALL", "x": 10, "y": 53}),
            json.dumps({"type": "ADDRESS", "name": "x"}),  # ohne Koordinate
            json.dumps({"type": "ADDRESS", "name": "x", "x": "abc", "y": 53}),
            json.dumps({"type": "ADDRESS", "name": "x", "x": 100, "y": 53}),  # ausserhalb Europas
            json.dumps({"type": "ADDRESS", "name": "x" * 500, "x": 10, "y": 53}),
            json.dumps({"type": "ADDRESS", "id": "../etc", "name": "x", "x": 10, "y": 53}),
            json.dumps({"type": "ADDRESS", "name": 5, "x": 10, "y": 53}),
            json.dumps({"type": "STATION", "name": "ohne id"}),
        ]
        for raw in bad:
            with self.assertRaises(ValueError, msg=raw):
                planner.parse_place(raw)

    def test_places_returns_stations_addresses_and_pois_once_and_caches(self):
        with FakeGti(responses={"checkName": {"returnCode": "OK", "results": ADDRESS_RESULTS}}) as gti:
            p = Planner(client_for(gti), Reliability(None))
            found = p.places("Leuschnerstraße 12")
            self.assertEqual([(x["type"], x["id"]) for x in found], [("STATION", "Master:80932"), ("ADDRESS", "25636"), ("POI", "205021")])
            self.assertEqual(found[1]["label"], "Leuschnerstraße 12, Hamburg")
            self.assertEqual((found[1]["x"], found[1]["y"]), (10.207964, 53.497196))
            sent = [body for path, body, _ in gti.requests if path.endswith("checkName")]
            self.assertEqual(sent[0]["theName"], {"name": "Leuschnerstraße 12", "type": "UNKNOWN"})
            p.places("  leuschnerstraße   12 ")  # gleiche Eingabe in anderer Schreibweise: aus dem Zwischenspeicher
            self.assertEqual(len([1 for path, _, _ in gti.requests if path.endswith("checkName")]), 1)

    def test_route_sends_addresses_and_locations_as_full_places(self):
        responses = {"getRoute": {"returnCode": "OK", "realtimeSchedules": [SCHEDULE]}}
        start = planner.parse_place(json.dumps({"type": "ADDRESS", "id": "25636", "name": "Leuschnerstraße 12", "city": "Hamburg", "x": 10.2, "y": 53.49}, ensure_ascii=False))
        here = planner.parse_place(json.dumps({"type": "COORDINATE", "x": 10.0, "y": 53.5}))
        with FakeGti(responses=responses) as gti:
            p = Planner(client_for(gti), Reliability(None))
            p.route(start, "Master:2", datetime.now(TZ))
            p.route("Master:2", here, datetime.now(TZ), arrive=True)
            first, second = [body for path, body, _ in gti.requests if path.endswith("getRoute")]
        self.assertEqual(first["start"], start)
        self.assertEqual(first["dest"], {"id": "Master:2", "type": "STATION"})
        self.assertEqual(second["dest"]["type"], "COORDINATE")
        self.assertFalse(second["timeIsDeparture"])

    def test_footpaths_to_and_from_addresses_are_walk_legs(self):
        schedule = {
            "scheduleElements": [
                {"from": {"name": "Leuschnerstraße 12", "type": "ADDRESS", "depTime": {"date": "16.11.2026", "time": "13:52"}}, "to": {"name": "Sanmannreihe", "arrTime": {"date": "16.11.2026", "time": "13:57"}}, "line": {"name": "Fußweg", "type": {"simpleType": "FOOTPATH"}}},
                {"from": {"name": "Sanmannreihe", "id": "x", "depTime": {"date": "16.11.2026", "time": "13:58"}}, "to": {"name": "Bf. Bergedorf", "id": "y", "arrTime": {"date": "16.11.2026", "time": "14:05"}}, "line": {"name": "234", "direction": "Bergedorf", "type": {"simpleType": "BUS"}}},
            ]
        }
        legs = normalize_route(schedule)
        self.assertEqual([(l["line"], l.get("walkMin")) for l in legs], [(None, 5), ("234", None)])


if __name__ == "__main__":
    unittest.main()


class AlternativesTests(unittest.TestCase):
    def test_other_departures_of_the_same_line_and_direction(self):
        def dep(line, direction, offset, **extra):
            return {"line": {"name": line, "direction": direction}, "timeOffset": offset, **extra}

        responses = {"departureList": {"returnCode": "OK", "departures": [
            dep("4", "Osakaallee", 4), dep("4", "Osakaallee", 24, delay=120), dep("4", "Osakaallee", 44, cancelled=True),
            dep("4", "Eidelstedt", 10), dep("5", "Osakaallee", 12), dep("4", "Osakaallee", 64),
        ]}}
        with FakeGti(responses=responses) as gti:
            p = Planner(client_for(gti), Reliability(None))
            now = datetime.now(TZ).timestamp()
            found = p.alternatives("Master:11021", "4", "Osakaallee", now + 10 * 60)["alternatives"]
            base = now // 60 * 60  # die Tafel rechnet ab der vollen Minute
            self.assertEqual([round((a["real"] - base) / 60) for a in found], [26, 64])  # 4 min liegt vor dem fruehesten Zeitpunkt, 44 faellt aus
            self.assertEqual(found[0]["delayMin"], 2)
            p.alternatives("Master:11021", "4", "Osakaallee", now)
            self.assertEqual(len([r for r in gti.requests if r[0].endswith("departureList")]), 1)  # Tafel wird kurz gemerkt


class RerouteTests(unittest.TestCase):
    NEXT = {
        "plannedDepartureTime": "2026-11-07T14:10:00.000+0100",
        "plannedArrivalTime": "2026-11-07T14:50:00.000+0100",
        "scheduleElements": [
            {
                "from": {"name": "Altona", "id": "Master:1", "depTime": {"date": "07.11.2026", "time": "14:10"}},
                "to": {"name": "Flughafen", "id": "Master:2", "arrTime": {"date": "07.11.2026", "time": "14:50"}, "arrDelay": 60},
                "line": {"name": "S11", "direction": "Flughafen", "type": {"simpleType": "TRAIN"}},
            }
        ],
    }

    def test_later_departure_replaces_the_ride_and_searches_the_rest_again(self):
        first = normalize_route(SCHEDULE)[0]  # S1 Wedel -> Altona, 13:23
        later = json.loads(json.dumps(SCHEDULE["scheduleElements"][0]))
        later["from"]["depTime"]["time"], later["to"]["arrTime"]["time"] = "13:43", "14:15"
        ride_schedule = {"scheduleElements": [later], "plannedDepartureTime": "2026-11-07T13:43:00.000+0100"}

        def get_route(body):
            if body["start"]["id"] == "Master:85950":
                return {"returnCode": "OK", "realtimeSchedules": [ride_schedule]}
            return {"returnCode": "OK", "realtimeSchedules": [self.NEXT]}

        with FakeGti(responses={"getRoute": get_route}) as gti:
            p = Planner(client_for(gti), Reliability(None))
            planned = datetime(2026, 11, 7, 13, 43, tzinfo=TZ).timestamp()
            route = p.reroute([], first, planned, {"id": "Master:2", "type": "STATION"}, True)
            self.assertEqual([l["line"] for l in route["legs"]], ["S1", "S11"])
            self.assertEqual(route["plannedDep"], planned)
            self.assertEqual(route["legs"][1]["transfer"]["station"], "Altona")  # Umstieg wurde neu berechnet
            self.assertEqual(route["realArr"], route["plannedArr"] + 60)
            asked = [r[1] for r in gti.requests if r[0].endswith("getRoute")]
            self.assertEqual([a["start"]["id"] for a in asked], ["Master:85950", "Master:1"])
            self.assertGreaterEqual(datetime.strptime(asked[1]["time"]["time"], "%H:%M").hour * 60 + datetime.strptime(asked[1]["time"]["time"], "%H:%M").minute, 14 * 60 + 17)  # nach Ankunft 14:15 + 2 min
            with self.assertRaises(LookupError):
                p.reroute([], first, planned + 3600, {"id": "Master:2", "type": "STATION"}, False)


class WalkAndReverseTests(unittest.TestCase):
    def test_change_on_the_same_platform_is_not_a_ride(self):
        self.assertTrue(all(is_walk(t) for t in ("CHANGE", "CHANGE_SAME_PLATFORM", "FOOTPATH", "BICYCLE")))
        self.assertFalse(any(is_walk(t) for t in ("TRAIN", "BUS", None)))
        schedule = json.loads(json.dumps(SCHEDULE))
        schedule["scheduleElements"][1]["line"]["type"]["simpleType"] = "CHANGE_SAME_PLATFORM"
        legs = normalize_route(schedule)
        self.assertEqual([l.get("line") for l in legs], ["S1", None, "S11"])  # Umstieg ist ein Fussweg, keine Linie "Umstiegsfussweg"
        self.assertEqual(legs[2]["transfer"]["waitMin"], 5)

    def test_reverse_geocoding_gives_an_address_label(self):
        found = {"returnCode": "OK", "results": [{"name": "Weidenbaumsweg 14", "city": "Hamburg", "combinedName": "(Nähe) Weidenbaumsweg 14", "type": "COORDINATE"}]}
        with FakeGti(responses={"checkName": found}) as gti:
            p = Planner(client_for(gti), Reliability(None))
            self.assertEqual(p.reverse(10.2057, 53.4895), {"name": "Weidenbaumsweg 14", "city": "Hamburg", "kind": "address", "label": "Weidenbaumsweg 14, Hamburg"})
            sent = [r[1] for r in gti.requests][-1]
            self.assertEqual(sent["theName"], {"name": "", "type": "COORDINATE", "coordinate": {"x": 10.2057, "y": 53.4895}})
            with self.assertRaises(ValueError):
                p.reverse(0.0, 0.0)
        with FakeGti(responses={"checkName": {"returnCode": "OK", "results": []}}) as gti:
            self.assertIsNone(Planner(client_for(gti), Reliability(None)).reverse(10.2, 53.5))

    def test_reverse_geocoding_falls_back_to_the_nearest_station(self):
        def check_name(body):  # Gewerbegebiet: die GTI kennt dort keine Adresse, nur die Koordinate und Haltestellen
            if body["theName"]["type"] == "COORDINATE":
                return {"returnCode": "OK", "results": [{"type": "COORDINATE", "coordinate": body["theName"]["coordinate"]}]}
            return {"returnCode": "OK", "results": [
                {"name": "Airbus (Eingang)", "city": "Stade", "type": "STATION", "distance": 190},
                {"name": "Airbus/CFK Nord", "city": "Stade", "type": "STATION", "distance": 88},
                {"name": "Weit weg", "city": "Stade", "type": "STATION", "distance": 900},
            ]}

        with FakeGti(responses={"checkName": check_name}) as gti:
            found = Planner(client_for(gti), Reliability(None)).reverse(9.5086, 53.5742)
        self.assertEqual((found["kind"], found["label"]), ("station", "Nahe Airbus/CFK Nord, Stade"))
        far = lambda body: {"returnCode": "OK", "results": [{"name": "Weit weg", "city": "Stade", "type": "STATION", "distance": 900}] if body["theName"]["type"] == "STATION" else []}
        with FakeGti(responses={"checkName": far}) as gti:
            self.assertIsNone(Planner(client_for(gti), Reliability(None)).reverse(9.5086, 53.5742))


class AccessNoticeTest(unittest.TestCase):
    STOPS = ["Dammtor", "Sternschanze", "Heimfeld", "Neugraben"]

    def test_only_stations_where_you_board_or_alight_matter(self):
        from hvv_gti.planner import notice_applies

        self.assertFalse(notice_applies("Kein barrierefreier Zugang zur S-Bahn in Heimfeld. Bitte nutzen Sie die Buslinie 42.", self.STOPS))
        self.assertTrue(notice_applies("Neugraben: Aufzug zu Gleis 2/3 laengerfristig ausser Betrieb", self.STOPS))
        self.assertTrue(notice_applies("Aufzug am Dammtor gesperrt", self.STOPS))
        self.assertTrue(notice_applies("Bis 22.11. hohes Fahrgastaufkommen erwartet.", self.STOPS))  # keine Barrierefreiheit: unveraendert
        self.assertTrue(notice_applies("Aufzuege im Netz teils gestoert", self.STOPS))  # nennt keinen Halt: im Zweifel zeigen


class HistogramTest(unittest.TestCase):
    def test_classes_sum_to_100_and_follow_the_delay(self):
        from hvv_gti.planner import _histogram

        hist = _histogram([-30, 0, 30, 120, 200, 400, 1000])
        self.assertEqual(len(hist), 7)
        self.assertAlmostEqual(sum(hist), 100, delta=0.3)
        self.assertEqual(hist[0], round(100 * 2 / 7, 1))  # <= 0 min
        self.assertEqual(hist[-1], round(100 / 7, 1))  # > 15 min


class StayOnBoardTest(unittest.TestCase):
    def schedule(self, attributes):
        place = lambda name, **extra: {"name": name, "id": "Master:" + name, "coordinate": {"x": 10.0, "y": 53.5}, **extra}
        t = lambda hhmm: {"date": "19.11.2026", "time": hhmm}
        ride = lambda frm, to, dep, arr, direction, sid: {
            "from": place(frm, depTime=t(dep), depDelay=0, platform="Gleis 1"),
            "to": place(to, arrTime=t(arr), arrDelay=0, platform="Gleis 1"),
            "line": {"name": "S5", "direction": direction, "type": {"simpleType": "TRAIN"}},
            "serviceId": sid,
        }
        change = {
            "from": place("Neugraben", depTime=t("14:30"), platform="Gleis 1"),
            "to": place("Neugraben", arrTime=t("14:30"), platform="Gleis 1"),
            "line": {"name": "Umstiegsfußweg", "type": {"simpleType": "CHANGE_SAME_PLATFORM"}},
            "attributes": attributes,
        }
        return {"scheduleElements": [ride("Stade", "Neugraben", "13:56", "14:30", "Neugraben", 1), change, ride("Neugraben", "Hamburg Hbf", "14:32", "14:59", "Elbgaustraße", 2)]}

    def test_same_vehicle_continuing_is_marked_and_is_not_a_transfer_risk(self):
        legs = normalize_route(self.schedule([{"isPlanned": True, "value": "Fährt weiter nach Elbgaustraße.", "types": ["NOCHANGE"]}]))
        walk = [leg for leg in legs if not leg.get("line")][0]
        self.assertEqual(walk["stay"], "Fährt weiter nach Elbgaustraße.")
        second = [leg for leg in legs if leg.get("line")][1]
        self.assertTrue(second["transfer"]["stay"])
        self.assertEqual(second["transfer"]["stayText"], "Fährt weiter nach Elbgaustraße.")
        self.assertEqual(second["transfer"]["waitMin"], 2)

    def test_a_normal_change_stays_a_change(self):
        legs = normalize_route(self.schedule([]))
        second = [leg for leg in legs if leg.get("line")][1]
        self.assertNotIn("stay", second["transfer"])


class PrewarmTests(unittest.TestCase):
    when = datetime(2026, 11, 7, 13, 23, tzinfo=TZ)

    def setUp(self):
        self.enterContext(unittest.mock.patch.object(planner, "WARM_PAUSE_S", 0))
        self.noon = datetime(2026, 11, 3, 13, 0, tzinfo=TZ).timestamp()

    def reader(self, calls):
        noon = self.noon

        class Stub:
            def frequent(self, limit):
                calls.append(("frequent", limit))
                return [("hvv_network_stop", "S1", "Altona"), ("hvv_network_stop", "S1", "Berliner Tor"), ("hvv_network_stop", "S3", "Altona")]

            def delays(self, measurement, line, station):
                calls.append(("delays", measurement, line, station))
                return [(noon + i, 60.0 * (i % 3)) for i in range(150)]

            def train_delays(self, line, station, direction):
                calls.append(("train", line, station, direction))
                return [(noon + i, 0.0) for i in range(150)]

            def cancel_rate(self, line):
                calls.append(("cancel", line))
                return (200, 0.01)

        return Stub()

    def test_warming_fetches_pairs_lines_and_cancel_rates_once_and_requests_hit_the_cache(self):
        calls = []
        r = Reliability(self.reader(calls), random.Random(1))
        self.assertEqual(r.warm_once(), 3 + 2 + 2 + 1)  # 3 Paare, 2 Linien gesamt, 2 Ausfallraten, S5-Dollern-Vorhalt
        asked = len(calls)
        dist = r.distribution("S1", "Altona", self.when)
        self.assertEqual(dist["n"], 150)
        self.assertEqual(r.cancel_rate("S1"), {"n": 200, "rate": 0.01})
        self.assertEqual(len(calls), asked, "nach dem Vorrechnen darf eine Anfrage nicht mehr zu Influx gehen")

    def test_the_ranking_is_read_only_once_per_hour(self):
        calls = []
        r = Reliability(self.reader(calls), random.Random(1))
        r.warm_once()
        r.warm_once()
        self.assertEqual([c for c in calls if c[0] == "frequent"], [("frequent", planner.WARM_PAIRS)])

    def test_an_unreachable_influx_is_survived(self):
        class Down:
            def frequent(self, limit):
                raise OSError("Influx: weg")

        r = Reliability(Down(), random.Random(1))
        self.assertEqual(r.warm_once(), 0)
        self.assertEqual(Reliability(None).warm_once(), 0)

    def test_samples_behave_like_a_list_of_pairs(self):
        s = planner.Samples([(1.5, 60.0), (2.5, 0.0)])
        self.assertEqual((len(s), list(s)), (2, [(1.5, 60.0), (2.5, 0.0)]))
        found = []
        found += s
        self.assertEqual(found, [(1.5, 60.0), (2.5, 0.0)])


class SamplesWindowTests(unittest.TestCase):
    def test_local_parts_match_a_direct_conversion_also_across_the_dst_change(self):
        # Zeitumstellung 2026-10-25 (Sommer -> Winterzeit) und der Wechsel um 01:00 UTC
        start = datetime(2026, 10, 24, 20, 0, tzinfo=TZ).timestamp()
        rows = [(start + i * 1234.5, 0.0) for i in range(700)]
        hours, weekends = planner.Samples(rows).local_parts()
        for (ts, _), hour, weekend in zip(rows, hours, weekends):
            local = datetime.fromtimestamp(ts, TZ)
            self.assertEqual((hour, bool(weekend)), (local.hour, local.weekday() >= 5), local)

    def test_pick_window_gives_the_same_answer_for_samples_and_a_plain_list(self):
        rng = random.Random(5)
        base = datetime(2026, 11, 1, 0, 0, tzinfo=TZ).timestamp()
        rows = [(base + rng.randrange(0, 20 * 86400), float(60 * rng.randrange(0, 6))) for _ in range(900)]
        for when in (datetime(2026, 11, 19, 8, 5, tzinfo=TZ), datetime(2026, 11, 21, 23, 40, tzinfo=TZ), datetime(2026, 11, 19, 0, 10, tzinfo=TZ)):
            self.assertEqual(planner.pick_window(planner.Samples(rows), when), planner.pick_window(rows, when), when)
        self.assertEqual(planner.pick_window(planner.Samples(rows[:3]), when)[1], "alle Zeiten")

    def test_joined_samples_keep_every_row(self):
        a, b = planner.Samples([(1.0, 60.0)]), planner.Samples([(2.0, 0.0), (3.0, 120.0)])
        self.assertEqual(list(planner.Samples.join([a, b])), [(1.0, 60.0), (2.0, 0.0), (3.0, 120.0)])


class StationMatchTests(unittest.TestCase):
    INDEX = [
        {"id": "hamburg-hbf", "name": "Hamburg Hbf", "names": ["Hauptbahnhof Nord", "Hauptbahnhof Süd"], "platforms": [
            {"id": "w1", "label": "Hamburg Hbf · Gleis 3/4", "mode": "S", "level": -1},
            {"id": "w2", "label": "Hamburg Hbf · Gleis 5/6", "mode": "R", "level": -1},
            {"id": "w3", "label": "Hauptbahnhof Nord · Gleis 1", "mode": "U", "level": -3},
            {"id": "w4", "label": "Mönckebergstraße · Gleis 1", "mode": "U", "level": -1},
            {"id": "w5", "label": "Hamburg Hbf · Gleis 7/8", "mode": "R", "level": -1},
        ]},
    ]

    def match(self, transfer, a, b):
        from hvv_gti.stationmatch import StationModels

        return StationModels(self.INDEX).match(transfer, a, b)

    def test_gti_platforms_are_matched_by_number_and_mode(self):
        found = self.match({"station": "Hamburg Hbf", "toStation": "Hamburg Hbf", "arrPlatform": "Gleis 4 (S-Bahn)", "depPlatform": "Gleis 6A-C"}, "S", "R")
        self.assertEqual(found, {"id": "hamburg-hbf", "from": "w1", "to": "w2"})

    def test_a_platform_missing_in_the_model_gives_no_link(self):
        self.assertIsNone(self.match({"station": "Hamburg Hbf", "toStation": "Hamburg Hbf", "arrPlatform": "Gleis 2 (S-Bahn)", "depPlatform": "Gleis 6A-C"}, "S", "R"))

    def test_the_same_number_on_another_mode_is_not_confused(self):
        # Gleis 4 gibt es als S-Bahn; ein Regionalzug auf "Gleis 4" waere nicht dieser Bahnsteig
        self.assertIsNone(self.match({"station": "Hamburg Hbf", "toStation": "Hamburg Hbf", "arrPlatform": "Gleis 4", "depPlatform": "Gleis 6"}, "R", "R"))

    def test_two_u_bahn_platforms_with_one_number_are_told_apart_by_the_station_name(self):
        found = self.match({"station": "Hauptbahnhof Nord", "toStation": "Hamburg Hbf", "arrPlatform": "Gleis 1", "depPlatform": "Gleis 7"}, "U", "R")
        self.assertEqual(found, {"id": "hamburg-hbf", "from": "w3", "to": "w5"})
        self.assertIsNone(self.match({"station": "Hamburg Hbf", "toStation": "Hamburg Hbf", "arrPlatform": "Gleis 1", "depPlatform": "Gleis 7"}, "U", "R"))

    def test_same_platform_bus_and_unknown_stations_get_nothing(self):
        t = {"station": "Hamburg Hbf", "toStation": "Hamburg Hbf", "arrPlatform": "Gleis 5", "depPlatform": "Gleis 6"}
        self.assertIsNone(self.match(t, "R", "R"))  # derselbe Bahnsteig 5/6
        self.assertIsNone(self.match({**t, "depPlatform": "Bereich F"}, "R", "X"))
        self.assertIsNone(self.match({**t, "station": "Harburg", "toStation": "Harburg"}, "R", "S"))

    def test_attach_models_marks_only_real_transfers(self):
        from hvv_gti.stationmatch import StationModels

        legs = [
            {"line": "S3", "from": {"name": "A", "platform": ""}, "to": {"name": "Hamburg Hbf", "platform": "Gleis 4 (S-Bahn)"}, "dep": 0, "arr": 600},
            {"line": "RE1", "from": {"name": "Hamburg Hbf", "platform": "Gleis 6A-C"}, "to": {"name": "B", "platform": ""}, "dep": 1000, "arr": 2000},
        ]
        planner.annotate_transfers(legs)
        planner.attach_models(legs, StationModels(self.INDEX))
        self.assertEqual(legs[1]["transfer"]["model"], {"id": "hamburg-hbf", "from": "w1", "to": "w2"})
        self.assertNotIn("model", legs[0].get("transfer", {}))
        legs[1]["transfer"].pop("model")
        planner.attach_models(legs, None)  # ohne Modelle bleibt alles wie vorher
        self.assertNotIn("model", legs[1]["transfer"])
