import threading
import unittest
from datetime import datetime, timezone

from hvv_gti import collector
from hvv_gti.client import GtiAuthError, GtiClient, GtiError, GtiPermissionError
from hvv_gti.config import Config
from hvv_gti.influx import InfluxError, InfluxWriter

from .fakes import FakeGti, FakeInflux

NOW = datetime(2026, 3, 10, 7, 41, 33, tzinfo=timezone.utc)  # 08:41 Berlin
REFERENCE = datetime(2026, 3, 10, 7, 41, tzinfo=timezone.utc)

STADE = {"id": "Master:80900", "type": "STATION", "name": "Stade", "city": "Stade", "combinedName": "Stade"}
HBF = {"id": "Master:9910", "type": "STATION", "name": "Hauptbahnhof", "city": "Hamburg", "combinedName": "Hamburg Hbf"}


def departure(line, offset, delay, **extra):
    return {
        "line": {"name": line, "direction": "Stade", "origin": "Elbgaustraße", "type": {"simpleType": "TRAIN"}},
        "timeOffset": offset,
        "delay": delay,
        "serviceId": 1000 + offset,
        **extra,
    }


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

    def test_gzip_response(self):
        with FakeGti(responses=gti_responses([departure("S5", 3, 60)]), gzip_responses=True) as gti:
            data = client_for(gti).departure_list(STADE, REFERENCE)
        self.assertEqual(len(data["departures"]), 1)

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

    def test_filters_line_and_computes_planned_time(self):
        response = {
            "departures": [
                departure("S5", 4, 120),
                departure("S3", 5, 0),
                departure("S5", 34, 0, cancelled=True),
                departure("S5", 19, 0, extra=True),
            ]
        }
        lines = collector.parse_events(response, REFERENCE, self.stop, "departure", "S5")
        self.assertEqual(len(lines), 3)
        planned_ns = int(REFERENCE.timestamp() + 4 * 60) * 1_000_000_000
        self.assertTrue(lines[0].endswith(f" {planned_ns}"))
        self.assertIn("delay_s=120i", lines[0])
        self.assertIn("cancelled=0i", lines[0])
        self.assertIn("cancelled=1i", lines[1])
        self.assertIn("extra=1i", lines[2])
        self.assertIn("line=S5", lines[0])
        self.assertIn("kind=departure", lines[0])
        self.assertIn("station=Stade", lines[0])

    def test_missing_direction_and_origin_get_placeholder(self):
        d = departure("S5", 1, 0)
        d["line"].pop("direction")
        d["line"]["origin"] = ""
        line = collector.parse_events({"departures": [d]}, REFERENCE, self.stop, "departure", "S5")[0]
        self.assertIn("direction=-", line)
        self.assertIn("origin=-", line)

    def test_missing_delay_is_zero(self):
        d = departure("S5", 1, None)
        lines = collector.parse_events({"departures": [d]}, REFERENCE, self.stop, "arrival", "S5")
        self.assertIn("delay_s=0i", lines[0])

    def test_same_event_in_two_polls_has_same_timestamp(self):
        first = collector.parse_events({"departures": [departure("S5", 5, 0)]}, REFERENCE, self.stop, "departure", "S5")
        later = datetime(2026, 3, 10, 7, 42, tzinfo=timezone.utc)
        second = collector.parse_events({"departures": [departure("S5", 4, 180)]}, later, self.stop, "departure", "S5")
        ts = lambda line: line.rsplit(" ", 1)[1]
        self.assertEqual(ts(first[0]), ts(second[0]))


class CollectTest(unittest.TestCase):
    def cfg(self, **kw):
        return Config(stations=(("Stade", None), ("Hauptbahnhof", None)), **kw)

    def test_collect_once_all_stations_and_kinds(self):
        responses = gti_responses([departure("S5", 2, 60), departure("S3", 3, 0)])
        with FakeGti(responses=responses) as gti:
            client = client_for(gti)
            cfg = self.cfg()
            stops = [collector.resolve_station(client, n, i) for n, i in cfg.stations]
            lines = collector.collect_once(client, stops, cfg, now=NOW)
        self.assertEqual(len(lines), 4)  # 2 Stationen x (Abfahrt + Ankunft), je 1 S5
        self.assertEqual(sum("kind=arrival" in l for l in lines), 2)
        # checkName liefert die STATION, nicht die ADDRESS; Hbf wird per exaktem Namen gewaehlt
        lists = [r for r in gti.requests if r[0].endswith("departureList")]
        self.assertEqual([r[1]["station"]["id"] for r in lists], ["Master:80900"] * 2 + ["Master:9910"] * 2)

    def test_arrivals_can_be_disabled(self):
        with FakeGti(responses=gti_responses([departure("S5", 2, 0)])) as gti:
            client = client_for(gti)
            lines = collector.collect_once(client, [collector.Stop("Stade", STADE)], self.cfg(collect_arrivals=False), now=NOW)
        self.assertEqual(len(lines), 1)
        self.assertEqual(len([r for r in gti.requests if r[0].endswith("departureList")]), 1)

    def test_resolve_by_id_skips_check_name(self):
        with FakeGti(responses=gti_responses([])) as gti:
            stop = collector.resolve_station(client_for(gti), "Stade", "Master:80900")
        self.assertEqual(stop.sd_name["id"], "Master:80900")
        self.assertEqual(gti.requests, [])

    def test_resolve_unknown_station(self):
        with FakeGti(responses={"checkName": {"returnCode": "OK", "results": []}}) as gti:
            with self.assertRaises(GtiError):
                collector.resolve_station(client_for(gti), "Nirgendwo", None)

    def test_partial_failure_is_tolerated_total_failure_raises(self):
        calls = {"n": 0}

        def flaky(body):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                return {"returnCode": "ERROR_COMM", "errorText": "boom"}
            return {"returnCode": "OK", "departures": [departure("S5", 2, 0)]}

        with FakeGti(responses={"departureList": flaky}) as gti:
            stops = [collector.Stop("Stade", STADE)]
            lines = collector.collect_once(client_for(gti), stops, self.cfg(), now=NOW)
        self.assertEqual(len(lines), 1)

        with FakeGti(responses={"departureList": {"returnCode": "ERROR_COMM"}}) as gti:
            with self.assertRaises(GtiError):
                collector.collect_once(client_for(gti), [collector.Stop("Stade", STADE)], self.cfg(), now=NOW)

    def test_denied_arrivals_are_skipped_and_remembered(self):
        stops = [collector.Stop("Stade", STADE), collector.Stop("Hauptbahnhof", HBF)]
        with FakeGti(responses={"departureList": departures_only([departure("S5", 2, 60)])}) as gti:
            denied: set[str] = set()
            client = client_for(gti)
            first = collector.collect_once(client, stops, self.cfg(), now=NOW, denied=denied)
            requests_after_first = len(gti.requests)
            second = collector.collect_once(client, stops, self.cfg(), now=NOW, denied=denied)
        self.assertEqual(len(first), 2)  # nur die beiden Abfahrten
        self.assertTrue(all("kind=departure" in l for l in first + second))
        self.assertEqual(denied, {"arrival"})
        # Zyklus 1: Stade dep + Stade arr (403) + Hbf dep (Ankunft dort uebersprungen), Zyklus 2: nur 2 Abfahrten
        self.assertEqual(requests_after_first, 3)
        self.assertEqual(len(gti.requests) - requests_after_first, 2)

    def test_everything_denied_raises(self):
        with FakeGti(responses={"departureList": (403, ARRIVAL_DENIED[1])}) as gti:
            with self.assertRaises(GtiPermissionError):
                collector.collect_once(client_for(gti), [collector.Stop("Stade", STADE)], self.cfg(), now=NOW)

    def test_auth_error_propagates(self):
        with FakeGti() as gti:
            bad = GtiClient("tester", "falsch", base_url=gti.url, min_interval=0)
            with self.assertRaises(GtiAuthError):
                collector.collect_once(bad, [collector.Stop("Stade", STADE)], self.cfg(), now=NOW)


class RunTest(unittest.TestCase):
    def test_run_once_writes_to_influx(self):
        responses = gti_responses([departure("S5", 2, 90)])
        with FakeGti(responses=responses) as gti, FakeInflux() as influx:
            cfg = Config(poll_interval=0.01)
            writer = InfluxWriter(influx.url, "Bergedorf", "hvv", "tok")
            code = collector.run(cfg, client_for(gti), writer, threading.Event(), once=True)
        self.assertEqual(code, 0)
        path, auth, body = influx.writes[0]
        self.assertIn("org=Bergedorf", path)
        self.assertIn("bucket=hvv", path)
        self.assertIn("precision=ns", path)
        self.assertEqual(auth, "Token tok")
        self.assertEqual(len(body.splitlines()), 6)
        self.assertIn("hvv_stop_event,", body)

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
