import threading
import unittest

from hvv_gti import collector, usage
from hvv_gti.client import MAX_CALL_LOG, ApiCall, GtiClient, GtiError
from hvv_gti.config import Config
from hvv_gti.influx import InfluxWriter

from .fakes import FakeGti, FakeInflux
from .test_integration import client_for, departure, gti_responses


class CallLogTest(unittest.TestCase):
    def test_successful_call_is_logged_with_size_and_latency(self):
        with FakeGti(responses={"init": {"returnCode": "OK", "beginOfService": "01.01.2026"}}) as gti:
            client = client_for(gti)
            client.init()
        (call,) = client.take_calls()
        self.assertEqual((call.endpoint, call.status), ("init", "ok"))
        self.assertGreater(call.response_bytes, 0)
        self.assertGreaterEqual(call.latency_s, 0)
        self.assertGreater(call.started_ns, 0)

    def test_take_calls_empties_the_log(self):
        with FakeGti() as gti:
            client = client_for(gti)
            client.init()
            client.init()
            self.assertEqual(len(client.take_calls()), 2)
            self.assertEqual(client.take_calls(), [])

    def test_failures_are_logged_with_their_reason(self):
        responses = {
            "init": (500, {"returnCode": "ERROR_TEXT", "errorText": "kaputt"}),
            "checkName": {"returnCode": "ERROR_COMM", "errorText": "x"},
        }
        with FakeGti(responses=responses) as gti:
            client = client_for(gti)
            for call in (client.init, lambda: client.check_name("x")):
                with self.assertRaises(GtiError):
                    call()
            bad = GtiClient(gti.user, "falsch", base_url=gti.url, min_interval=0)
            with self.assertRaises(GtiError):
                bad.init()
            calls = client.take_calls() + bad.take_calls()
        self.assertEqual([c.status for c in calls], ["500", "ERROR_COMM", "401"])
        self.assertEqual(calls[1].endpoint, "checkName")

    def test_unreachable_server_is_logged_as_network(self):
        client = GtiClient("u", "p", base_url="http://127.0.0.1:9", min_interval=0, timeout=1)
        with self.assertRaises(GtiError):
            client.init()
        (call,) = client.take_calls()
        self.assertEqual((call.endpoint, call.status, call.response_bytes), ("init", "network", 0))

    def test_log_is_capped(self):
        with FakeGti() as gti:
            client = client_for(gti)
            client.call_log = [ApiCall("init", i, 0.0, 0, "ok") for i in range(MAX_CALL_LOG)]
            client.init()
        self.assertEqual(len(client.call_log), MAX_CALL_LOG)
        self.assertEqual(client.call_log[0].started_ns, 1)


class CallLinesTest(unittest.TestCase):
    def test_line_protocol_shape(self):
        (line,) = usage.call_lines([ApiCall("getVehicleMap", 1_700_000_000_123_456_789, 2.8771, 266_000, "ok")])
        self.assertTrue(line.startswith("hvv_api_request,endpoint=getVehicleMap,status=ok "))
        self.assertIn("latency_ms=2877i", line)
        self.assertIn("bytes=266000i", line)
        self.assertTrue(line.endswith(" 1700000000123456789"))

    def test_empty(self):
        self.assertEqual(usage.call_lines([]), [])


def responses():
    return gti_responses([departure("S5", 2, 90, direction="Elbgaustraße")])


class RunUsageTest(unittest.TestCase):
    def test_failed_cycle_still_reports_the_requests(self):
        failing = {"departureList": (500, {"returnCode": "ERROR_TEXT", "errorText": "kaputt"})}
        with FakeGti(responses=failing) as gti, FakeInflux() as influx:
            writer = InfluxWriter(influx.url, "o", "b", "t")
            with self.assertLogs("hvv_gti.collector", level="ERROR"):
                code = collector.run(Config(), client_for(gti), writer, threading.Event(), once=True)
        self.assertEqual(code, 1)
        body = influx.writes[0][2]
        self.assertEqual(body.count("endpoint=departureList,status=500"), 9)
        self.assertNotIn("hvv_stop_event", body)

    def test_usage_is_kept_while_influx_is_down_and_written_with_the_next_cycle(self):
        stop = threading.Event()
        waits = []
        with FakeGti(responses=responses()) as gti, FakeInflux(status=500) as influx:

            def wait(_timeout):
                waits.append(_timeout)
                influx.status = 204
                if len(waits) >= 3:
                    stop.set()

            stop.wait = wait
            writer = InfluxWriter(influx.url, "o", "b", "t")
            with self.assertLogs("hvv_gti.collector", level="ERROR"):
                code = collector.run(Config(poll_interval=0, announcement_interval=0), client_for(gti), writer, stop)
        self.assertEqual(code, 0)
        self.assertEqual(len(influx.writes), 3)  # 1. Zyklus scheitert, 2. und 3. Zyklus werden geschrieben
        per_cycle = len(gti.requests) // 3
        count = lambda body: sum(1 for l in body.splitlines() if l.startswith("hvv_api_request,"))
        self.assertEqual(count(influx.writes[0][2]), per_cycle)
        self.assertEqual(count(influx.writes[1][2]), 2 * per_cycle)  # Aufrufe des gescheiterten Zyklus kommen nach
        self.assertEqual(count(influx.writes[2][2]), per_cycle)  # danach nichts doppelt


if __name__ == "__main__":
    unittest.main()
