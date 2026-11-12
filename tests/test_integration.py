import re
import threading
import unittest
from datetime import datetime, timezone

from hvv_gti import collector
from hvv_gti.client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from hvv_gti.config import ROUTES, Config, Route
from hvv_gti.influx import InfluxError, InfluxWriter

from .fakes import FakeGti, FakeInflux

NOW = datetime(2026, 3, 10, 7, 41, 33, tzinfo=timezone.utc)  # 08:41 Berlin
REFERENCE = datetime(2026, 3, 10, 7, 41, tzinfo=timezone.utc)

STADE = {"id": "Master:80900", "type": "STATION", "name": "Stade", "city": "Stade", "combinedName": "Stade"}
HBF = {"id": "Master:9910", "type": "STATION", "name": "Hauptbahnhof", "city": "Hamburg", "combinedName": "Hamburg Hbf"}


STADE_ROUTE = Route("S5 Stade", "S5", "SBAHN", "Stade", STADE["id"], "Nach Stade", directions=("Stade",))
HBF_ROUTE = Route("S5 Hbf", "S5", "SBAHN", "Hauptbahnhof", HBF["id"], "Sonst")
TEST_ROUTES = (STADE_ROUTE, HBF_ROUTE)


def departure(line, offset, delay, direction="Stade", **extra):
    dep = {
        "line": {"name": line, "direction": direction, "origin": "Elbgaustraße", "type": {"simpleType": "TRAIN"}},
        "timeOffset": offset,
        "serviceId": 1000 + offset,
        **extra,
    }
    if delay is not None:
        dep["delay"] = delay
    return dep


def gti_responses(departures):
    return {
        "checkName": {"returnCode": "OK", "results": [STADE, HBF, {"id": "x", "type": "ADDRESS", "name": "Stade"}]},
        "departureList": {"returnCode": "OK", "departures": departures},
    }


ARRIVAL_DENIED = (
    403,
    {"returnCode": "ERROR_TEXT", "errorText": "A technical problem occured while processing your request.", "errorDevInfo": "ArrivalList not allowed for user"},
)


def departures_only(departures):
    """Wie der echte Account: Abfahrten ja, Ankuenfte (departure=false) 403."""

    def respond(body):
        if body.get("departure") is False:
            return ARRIVAL_DENIED
        return {"returnCode": "OK", "departures": departures}

    return respond


def client_for(gti: FakeGti, **kw) -> GtiClient:
    return GtiClient(gti.user, gti.password, base_url=gti.url, min_interval=0, **kw)


class ClientTest(unittest.TestCase):
    def test_signature_accepted_and_body_is_exact(self):
        with FakeGti(responses={"init": {"returnCode": "OK", "beginOfService": "01.01.2026"}}) as gti:
            data = client_for(gti).init()
        self.assertEqual(data["beginOfService"], "01.01.2026")
        path, body, headers = gti.requests[0]
        self.assertEqual(path, "/gti/public/init")
        self.assertEqual(body, {"language": "de", "version": 63})
        self.assertEqual({k.lower(): v for k, v in headers.items()}["geofox-auth-user"], "tester")

    def test_wrong_password_raises_auth_error(self):
        with FakeGti() as gti:
            bad = GtiClient("tester", "falsch", base_url=gti.url, min_interval=0)
            with self.assertRaises(GtiAuthError):
                bad.init()

    def test_umlauts_are_signed_correctly(self):
        with FakeGti(responses=gti_responses([])) as gti:
            client_for(gti).check_name("Elbgaustraße / Süderelbe")
        self.assertEqual(gti.requests[0][1]["theName"]["name"], "Elbgaustraße / Süderelbe")

    def test_missing_permission_is_not_an_auth_error(self):
        with FakeGti(responses={"departureList": departures_only([])}) as gti:
            with self.assertRaises(GtiPermissionError) as ctx:
                client_for(gti).departure_list(STADE, REFERENCE, departure=False)
        self.assertNotIsInstance(ctx.exception, GtiAuthError)
        self.assertEqual(ctx.exception.code, "403")

    def test_return_code_error(self):
        with FakeGti(responses={"init": {"returnCode": "ERROR_COMM", "errorText": "kaputt"}}) as gti:
            with self.assertRaises(GtiError) as ctx:
                client_for(gti).init()
        self.assertEqual(ctx.exception.code, "ERROR_COMM")

    def test_departure_list_payload(self):
        with FakeGti(responses=gti_responses([])) as gti:
            client_for(gti).departure_list(STADE, REFERENCE, departure=False, service_types=["SBAHN"], max_list=7)
        payload = gti.requests[0][1]
        self.assertEqual(payload["time"], {"date": "10.03.2026", "time": "08:41"})
        self.assertIs(payload["departure"], False)
        self.assertIs(payload["useRealtime"], True)
        self.assertEqual(payload["serviceTypes"], ["SBAHN"])
        self.assertEqual(payload["maxList"], 7)

    def test_vehicle_map_payload(self):
        with FakeGti(responses={"getVehicleMap": {"returnCode": "OK", "journeys": []}}) as gti:
            client_for(gti).get_vehicle_map((9.7, 53.35), (10.35, 53.75), 100, 220, vehicle_types=["S_BAHN"])
        payload = gti.requests[0][1]
        self.assertEqual(payload["boundingBox"]["lowerLeft"], {"x": 9.7, "y": 53.35, "type": "EPSG_4326"})
        self.assertIs(payload["withoutCoords"], True)
        self.assertEqual((payload["periodBegin"], payload["periodEnd"]), (100, 220))
        self.assertEqual(payload["vehicleTypes"], ["S_BAHN"])


class ParseEventsTest(unittest.TestCase):
    stop = collector.Stop("Stade", STADE)
    routes = (STADE_ROUTE,)

    def parse(self, departures, reference=REFERENCE, routes=None, kind="departure", unmatched=None):
        return collector.parse_events({"departures": departures}, reference, self.stop, kind, routes or self.routes, unmatched)

    def test_filters_line_and_computes_planned_time(self):
        lines = self.parse(
            [
                departure("S5", 4, 120),
                departure("S3", 5, 0),
                departure("S5", 34, 0, cancelled=True),
                departure("S5", 19, 0, extra=True),
            ]
        )
        self.assertEqual(len(lines), 3)
        planned_ns = int(REFERENCE.timestamp() + 4 * 60) * 1_000_000_000
        self.assertTrue(lines[0].endswith(f" {planned_ns}"))
        self.assertIn("delay_s=120i", lines[0])
        self.assertIn("cancelled=0i", lines[0])
        self.assertIn("cancelled=1i", lines[1])
        self.assertIn("extra=1i", lines[2])
        self.assertIn("line=S5", lines[0])
        self.assertIn("route=S5\\ Stade", lines[0])
        self.assertIn("direction=Nach\\ Stade", lines[0])
        self.assertIn("service_id=1004i", lines[0])
        self.assertIn("kind=departure", lines[0])
        self.assertIn("station=Stade", lines[0])

    def test_missing_direction_and_origin_get_placeholder(self):
        d = departure("S5", 1, 0)
        d["line"].pop("direction")
        d["line"]["origin"] = ""
        line = self.parse([d], routes=(Route("alle", "S5", "SBAHN", "Stade", STADE["id"], "alle"),))[0]
        self.assertIn("destination=-", line)
        self.assertIn("direction=alle", line)
        self.assertIn("origin=-", line)

    def test_missing_delay_has_no_delay_field(self):
        lines = self.parse([departure("S5", 1, None), departure("S5", 2, None, cancelled=True)], kind="arrival")
        self.assertNotIn("delay_s", lines[0])
        self.assertIn("cancelled=0i", lines[0])
        self.assertIn("delay_s=0i", lines[1])  # Ausfall ohne Echtzeitwert zaehlt weiter als Ausfall

    def test_zero_delay_is_kept(self):
        self.assertIn("delay_s=0i", self.parse([departure("S5", 1, 0)])[0])

    def test_same_event_in_two_polls_has_same_timestamp(self):
        first = self.parse([departure("S5", 5, 0)])
        later = datetime(2026, 3, 10, 7, 42, tzinfo=timezone.utc)
        second = self.parse([departure("S5", 4, 180)], reference=later)
        ts = lambda line: line.rsplit(" ", 1)[1]
        self.assertEqual(ts(first[0]), ts(second[0]))

    def test_first_matching_route_wins_and_empty_directions_catch_all(self):
        routes = (
            Route("S7", "S7", "SBAHN", "Stade", STADE["id"], "Aumühle", directions=("Aumühle",)),
            Route("S7", "S7", "SBAHN", "Stade", STADE["id"], "Hamburg"),
        )
        lines = self.parse(
            [departure("S7", 1, 0, direction="Aumühle"), departure("S7", 2, 0, direction="Hamburg Hbf")], routes=routes
        )
        self.assertIn("route=S7,", lines[0] + ",")
        self.assertIn("direction=Aumühle", lines[0])
        self.assertIn("direction=Hamburg", lines[1])
        self.assertIn("destination=Hamburg\\ Hbf", lines[1])

    def test_direction_match_is_case_insensitive_substring(self):
        routes = (Route("x", "S5", "SBAHN", "Stade", STADE["id"], "E", directions=("elbgaustra",)),)
        self.assertEqual(len(self.parse([departure("S5", 1, 0, direction="Elbgaustraße")], routes=routes)), 1)

    def test_unmatched_direction_is_kept_under_the_line_and_warned_once(self):
        unmatched: set = set()
        with self.assertLogs("hvv_gti.collector", level="WARNING") as logs:
            first = self.parse([departure("S5", 1, 0, direction="Hamburg Hbf")], unmatched=unmatched)
            self.parse([departure("S5", 2, 0, direction="Hamburg Hbf")], unmatched=unmatched)
        self.assertIn("route=S5\\ Stade", first[0])
        self.assertIn("direction=Hamburg\\ Hbf", first[0])
        self.assertIn("destination=Hamburg\\ Hbf", first[0])
        self.assertEqual(len(logs.records), 1)

    def test_same_line_in_other_group_without_route_is_ignored(self):
        self.assertEqual(self.parse([departure("X30", 1, 0)]), [])

    def test_attributes_become_reason_fields(self):
        jam = {"isPlanned": False, "value": "Prognose ungenau - Fahrzeug im Stau", "types": ["REALTIME", "TRAFFIC_JAM"]}
        tech = {"isPlanned": False, "value": "Technische Störung", "types": ["TECHNICAL_PROBLEM"]}
        line = self.parse([departure("S5", 3, 0, attributes=[jam, tech, dict(jam)])])[0]
        self.assertIn('reason_types="REALTIME,TRAFFIC_JAM,TECHNICAL_PROBLEM"', line)
        self.assertIn('reason_text="Prognose ungenau - Fahrzeug im Stau | Technische Störung"', line)

    def test_plain_line_info_is_no_reason(self):
        info = {"isPlanned": True, "value": "Nach 19 Uhr Ausstieg zwischen den Haltestellen", "types": ["NORMAL"]}
        for attributes in ([info], [], None):
            line = self.parse([departure("S5", 3, 0, attributes=attributes)])[0]
            self.assertNotIn("reason_", line)

    def test_reason_is_a_field_not_a_tag(self):
        jam = {"value": "Fahrzeug im Stau", "types": ["TRAFFIC_JAM"]}
        plain = self.parse([departure("S5", 3, 0)])[0]
        with_reason = self.parse([departure("S5", 3, 0, attributes=[jam])])[0]
        series = lambda line: line.split(" ", 1)[0]
        self.assertEqual(series(plain), series(with_reason))  # gleiche Serie, sonst wuerde die Abfahrt doppelt gezaehlt

    def test_attribute_without_types_or_value(self):
        line = self.parse([departure("S5", 3, 0, attributes=[{"title": "Hinweis"}, {"types": ["TRAFFIC_JAM"]}])])[0]
        self.assertIn('reason_text="Hinweis"', line)
        self.assertIn('reason_types="TRAFFIC_JAM"', line)


def by_group(table):
    """Antwort je (Haltestellen-ID, Verkehrsmittel), wie sie der echte GTI-Server trennt."""

    def respond(body):
        return {"returnCode": "OK", "departures": table.get((body["station"]["id"], body["serviceTypes"][0]), [])}

    return respond


class CollectTest(unittest.TestCase):
    def cfg(self, **kw):
        return Config(routes=TEST_ROUTES, **{"collect_arrivals": True, **kw})

    def stops(self, client, cfg):
        return collector.resolve_stops(client, cfg.routes)

    def test_collect_once_all_stations_and_kinds(self):
        responses = gti_responses([departure("S5", 2, 60), departure("S3", 3, 0)])
        with FakeGti(responses=responses) as gti:
            client = client_for(gti)
            cfg = self.cfg()
            lines = collector.collect_once(client, self.stops(client, cfg), cfg, now=NOW)
        self.assertEqual(len(lines), 4)  # 2 Stationen x (Abfahrt + Ankunft), je 1 S5
        self.assertEqual(sum("kind=arrival" in l for l in lines), 2)
        lists = [r for r in gti.requests if r[0].endswith("departureList")]
        self.assertEqual([r[1]["station"]["id"] for r in lists], ["Master:80900"] * 2 + ["Master:9910"] * 2)
        self.assertTrue(all(r[1]["serviceTypes"] == ["SBAHN"] for r in lists))

    def test_arrivals_can_be_disabled(self):
        with FakeGti(responses=gti_responses([departure("S5", 2, 0)])) as gti:
            client = client_for(gti)
            cfg = self.cfg(collect_arrivals=False)
            lines = collector.collect_once(client, self.stops(client, cfg), cfg, now=NOW)
        self.assertEqual(len(lines), 2)  # beide Haltestellen, nur Abfahrten
        self.assertEqual(len([r for r in gti.requests if r[0].endswith("departureList")]), 2)

    def test_resolve_by_id_skips_check_name(self):
        with FakeGti(responses=gti_responses([])) as gti:
            stop = collector.resolve_station(client_for(gti), "Stade", "Master:80900")
        self.assertEqual(stop.sd_name["id"], "Master:80900")
        self.assertEqual(gti.requests, [])

    def test_resolve_by_name_prefers_station_over_address(self):
        with FakeGti(responses=gti_responses([])) as gti:
            stop = collector.resolve_station(client_for(gti), "Hauptbahnhof", None)
        self.assertEqual(stop.sd_name["id"], "Master:9910")

    def test_resolve_unknown_station(self):
        with FakeGti(responses={"checkName": {"returnCode": "OK", "results": []}}) as gti:
            with self.assertRaises(GtiError):
                collector.resolve_station(client_for(gti), "Nirgendwo", None)

    def test_resolve_stops_deduplicates_stations(self):
        routes = ROUTES
        with FakeGti(responses=gti_responses([])) as gti:
            stops = collector.resolve_stops(client_for(gti), routes)
        self.assertEqual(set(stops), {"Stade", "Neugraben", "Hauptbahnhof", "Bergedorf", "Harburg Rathaus", "Rathausmarkt"})

    def test_group_routes_one_request_per_station_and_service_type(self):
        groups = collector.group_routes(ROUTES)
        keys = [key for key, _ in groups]
        self.assertEqual(len(groups), 9)
        self.assertEqual(len(set(keys)), 9)
        self.assertIn(("Bergedorf", "Master:25950", "BUS"), keys)
        self.assertIn(("Bergedorf", "Master:25950", "XPRESSBUS"), keys)
        self.assertIn(("Harburg Rathaus", "Master:40950", "XPRESSBUS"), keys)
        self.assertIn(("Rathausmarkt", "Master:11003", "BUS"), keys)
        self.assertIn(("Stade", "Master:8000089", "ZUG"), keys)

    def test_collect_once_real_routes(self):
        table = {
            ("Master:8000089", "SBAHN"): [departure("S5", 3, 60, direction="Neugraben"), departure("S3", 4, 0)],
            ("Master:41951", "SBAHN"): [
                departure("S5", 5, 0, direction="Buxtehude"),
                departure("S5", 6, 120, direction="Elbgaustraße"),
            ],
            ("Master:9910950", "SBAHN"): [departure("S5", 7, 0, direction="Neugraben")],
            ("Master:25950", "SBAHN"): [departure("S7", 8, 0, direction="Aumühle")],
            ("Master:25950", "BUS"): [
                departure("S7-SEV", 9, None, direction="U S Berliner Tor (Beim Strohhause)"),
                departure("234", 9, 60, direction="Fanny-Lewald-Ring"),
                departure("234", 10, 0, direction="S Nettelnburg"),
                departure("X30", 10, 0, direction="S Harburg Rathaus (Hölertwiete)"),  # gehoert zur XPRESSBUS-Abfrage
                departure("113", 11, 0),
            ],
            ("Master:11003", "BUS"): [
                departure("4", 5, 0, direction="Osakaallee (Überseequartier)"),
                departure("4", 6, 120, direction="Wildacker"),
                departure("4", 7, 0, direction="Langenfelder Damm"),
                departure("5", 8, 0, direction="Hauptbahnhof/ZOB"),
                departure("5", 9, 60, direction="U Niendorf Markt"),
                departure("5", 9, 0, direction="A Burgwedel"),
                departure("6", 7, 0),  # Linie ohne Route
            ],
            ("Master:25950", "XPRESSBUS"): [departure("X30", 10, 60, direction="S Harburg Rathaus (Hölertwiete)")],
            ("Master:40950", "XPRESSBUS"): [departure("X30", 12, 0, direction="Bf. Bergedorf")],
            ("Master:8000089", "ZUG"): [
                departure("RE4", 14, 0),
                departure("RE5", 15, 0, direction="Cuxhaven"),
                departure("RE5", 16, 120, direction="Hamburg Hbf"),
                departure("RE5", 17, 1680, direction="Hamburg-Harburg"),
            ],
        }
        cfg = Config(min_request_interval=0)
        with FakeGti(responses={"departureList": by_group(table)}) as gti:
            client = client_for(gti)
            stops = {name: collector.Stop(name, {"id": sid}) for name, sid in {(r.station, r.station_id) for r in cfg.routes}}
            lines = collector.collect_once(client, stops, cfg, now=NOW)
        requests = [r[1] for r in gti.requests if r[0].endswith("departureList")]
        self.assertEqual(len(requests), 9)
        self.assertEqual({r["maxList"] for r in requests if r["serviceTypes"] == ["BUS"]}, {120})
        def tag(name, line):
            return re.search(rf"[, ]{name}=((?:\\.|[^, ])+)", line).group(1)

        routes = [(tag("route", l), tag("direction", l)) for l in lines]
        self.assertEqual(
            routes,
            [
                ("S5", "Neugraben"),
                ("S5", "Stade"),
                ("S5", "Elbgaustraße"),
                ("S5", "Neugraben"),
                ("S7", "Aumühle"),
                ("S7", "Hamburg"),
                ("234", "Leuschnerstraße"),
                ("234", "Leuschnerstraße"),
                ("X30", "Harburg"),
                ("X30", "Bergedorf"),
                ("4", "Überseequartier"),
                ("4", "Eidelstedt"),
                ("4", "Eidelstedt"),
                ("5", "Hauptbahnhof"),
                ("5", "Niendorf\\ /\\ Burgwedel"),
                ("5", "Niendorf\\ /\\ Burgwedel"),
                ("RE5", "Cuxhaven"),
                ("RE5", "Hamburg"),
                ("RE5", "Hamburg"),
            ],
        )
        sev = next(l for l in lines if "line=S7-SEV" in l)
        self.assertNotIn("delay_s", sev)  # Bus ohne Echtzeit
        self.assertEqual(sum("line=X30" in l for l in lines), 2)  # die X30 aus der BUS-Antwort wird nicht doppelt gezaehlt

    def test_full_list_warns(self):
        table = {("Master:80900", "SBAHN"): [departure("S5", i, 0) for i in range(1, 41)]}
        with FakeGti(responses={"departureList": by_group(table)}) as gti, self.assertLogs("hvv_gti.collector", level="WARNING") as logs:
            client = client_for(gti)
            cfg = self.cfg(collect_arrivals=False)
            collector.collect_once(client, self.stops(client, cfg), cfg, now=NOW)
        self.assertTrue(any("Liste voll" in r.getMessage() for r in logs.records))

    def test_partial_failure_is_tolerated_total_failure_raises(self):
        calls = {"n": 0}

        def flaky(body):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                return {"returnCode": "ERROR_COMM", "errorText": "boom"}
            return {"returnCode": "OK", "departures": [departure("S5", 2, 0)]}

        with FakeGti(responses={"departureList": flaky}) as gti:
            client = client_for(gti)
            cfg = self.cfg()
            lines = collector.collect_once(client, self.stops(client, cfg), cfg, now=NOW)
        self.assertEqual(len(lines), 2)  # 4 Aufrufe, jeder zweite scheitert

        with FakeGti(responses={"departureList": {"returnCode": "ERROR_COMM"}}) as gti:
            client = client_for(gti)
            cfg = self.cfg()
            with self.assertRaises(GtiError):
                collector.collect_once(client, self.stops(client, cfg), cfg, now=NOW)

    def test_denied_arrivals_are_skipped_and_remembered(self):
        with FakeGti(responses={"departureList": departures_only([departure("S5", 2, 60)])}) as gti:
            denied: set[str] = set()
            client = client_for(gti)
            cfg = self.cfg()
            stops = self.stops(client, cfg)
            first = collector.collect_once(client, stops, cfg, now=NOW, denied=denied)
            requests_after_first = len(gti.requests)
            second = collector.collect_once(client, stops, cfg, now=NOW, denied=denied)
        self.assertEqual(len(first), 2)  # nur die beiden Abfahrten
        self.assertTrue(all("kind=departure" in l for l in first + second))
        self.assertEqual(denied, {"arrival"})
        # Zyklus 1: Stade dep + Stade arr (403) + Hbf dep (Ankunft dort uebersprungen), Zyklus 2: nur 2 Abfahrten
        self.assertEqual(requests_after_first, 3)
        self.assertEqual(len(gti.requests) - requests_after_first, 2)

    def test_everything_denied_raises(self):
        with FakeGti(responses={"departureList": (403, ARRIVAL_DENIED[1])}) as gti:
            client = client_for(gti)
            cfg = self.cfg()
            with self.assertRaises(GtiPermissionError):
                collector.collect_once(client, self.stops(client, cfg), cfg, now=NOW)

    def test_auth_error_propagates(self):
        with FakeGti() as gti:
            bad = GtiClient("tester", "falsch", base_url=gti.url, min_interval=0)
            cfg = self.cfg()
            with self.assertRaises(GtiAuthError):
                collector.collect_once(bad, self.stops(bad, cfg), cfg, now=NOW)


class RunTest(unittest.TestCase):
    def test_run_once_writes_to_influx(self):
        responses = gti_responses([departure("S5", 2, 90, direction="Elbgaustraße")])
        with FakeGti(responses=responses) as gti, FakeInflux() as influx:
            cfg = Config(poll_interval=0.01, collect_arrivals=True)
            writer = InfluxWriter(influx.url, "Bergedorf", "hvv", "tok")
            code = collector.run(cfg, client_for(gti), writer, threading.Event(), once=True)
        self.assertEqual(code, 0)
        path, auth, body = influx.writes[0]
        self.assertIn("org=Bergedorf", path)
        self.assertIn("bucket=hvv", path)
        self.assertIn("precision=ns", path)
        self.assertEqual(auth, "Token tok")
        events = [l for l in body.splitlines() if l.startswith("hvv_stop_event,")]
        self.assertEqual(len(events), 6)  # 3 S5-Messpunkte x (Abfahrt + Ankunft)
        self.assertIn("route=", body)
        usage = [l for l in body.splitlines() if l.startswith("hvv_api_request,")]
        self.assertEqual(len(usage), len(gti.requests))
        self.assertIn("endpoint=departureList,status=ok", body)

    def test_run_once_reports_failure_exit_code(self):
        responses = gti_responses([departure("S5", 2, 0)])
        with FakeGti(responses=responses) as gti, FakeInflux(status=401) as influx:
            writer = InfluxWriter(influx.url, "o", "b", "t")
            code = collector.run(Config(), client_for(gti), writer, threading.Event(), once=True)
        self.assertEqual(code, 1)

    def test_influx_error_message(self):
        with FakeInflux(status=401) as influx:
            with self.assertRaises(InfluxError) as ctx:
                InfluxWriter(influx.url, "o", "b", "t").write(["m v=1i 1"])
        self.assertIn("401", str(ctx.exception))

    def test_influx_unreachable(self):
        with self.assertRaises(InfluxError):
            InfluxWriter("http://127.0.0.1:9", "o", "b", "t", timeout=1).write(["m v=1i 1"])

    def test_empty_write_is_noop(self):
        with FakeInflux() as influx:
            InfluxWriter(influx.url, "o", "b", "t").write([])
        self.assertEqual(influx.writes, [])


if __name__ == "__main__":
    unittest.main()
