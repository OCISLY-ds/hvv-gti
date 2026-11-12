import threading
import unittest
from datetime import datetime, timezone

from hvv_gti import announcements, collector
from hvv_gti.client import GtiAuthError
from hvv_gti.config import CORRIDOR, Config
from hvv_gti.influx import InfluxWriter

from .fakes import FakeGti, FakeInflux
from .test_integration import client_for, departure, gti_responses

NOW = datetime(2026, 3, 10, 7, 41, 33, tzinfo=timezone.utc)
WANTED = {"S5", "S7", "RE5"}


def announcement(summary="Polizeieinsatz", lines=("S5",), reason="POLICE_REQUEST", planned=False, validities=None, aid="a1", version=3):
    return {
        "id": aid,
        "version": version,
        "summary": summary,
        "reason": reason,
        "planned": planned,
        "locations": [{"type": "SINGLE_LINE", "line": {"name": name}, "bothDirections": True} for name in lines],
        "publication": {"begin": "2026-03-09T00:00:00.000+0100", "end": "2026-03-20T00:00:00.000+0100"},
        "validities": validities if validities is not None else [{"begin": "2026-03-10T08:00:00.000+0100", "end": "2026-03-10T12:00:00.000+0100"}],
    }


class ParseTest(unittest.TestCase):
    def parse(self, *items, wanted=WANTED):
        return announcements.parse_announcements({"announcements": list(items)}, wanted, NOW)

    def test_point_per_tracked_line_with_reason_tag_and_summary_field(self):
        lines = self.parse(announcement(lines=("S5", "S3", "S7")))
        self.assertEqual(len(lines), 2)  # S3 ist keine ausgewertete Linie
        self.assertTrue(lines[0].startswith("hvv_announcement,"))
        self.assertIn("line=S5", lines[0])
        self.assertIn("line=S7", lines[1])
        self.assertIn("reason=POLICE_REQUEST", lines[0])
        self.assertIn("planned=false", lines[0])
        self.assertIn("announcement_id=a1", lines[0])
        self.assertIn('summary="Polizeieinsatz"', lines[0])
        self.assertIn("version=3i", lines[0])
        self.assertTrue(lines[0].endswith(f" {int(datetime(2026, 3, 10, 7, 41, tzinfo=timezone.utc).timestamp()) * 1_000_000_000}"))

    def test_planned_flag(self):
        self.assertIn("planned=true", self.parse(announcement(planned=True))[0])

    def test_validity_window_decides(self):
        # NOW = 08:41 Berlin
        future = announcement(validities=[{"begin": "2026-03-10T09:00:00.000+0100", "end": "2026-03-10T12:00:00.000+0100"}])
        expired = announcement(validities=[{"begin": "2026-03-10T06:00:00.000+0100", "end": "2026-03-10T08:41:00.000+0100"}])
        running = announcement(validities=[{"begin": "2026-03-10T06:00:00.000+0100", "end": "2026-03-10T08:42:00.000+0100"}])
        self.assertEqual(self.parse(future), [])
        self.assertEqual(self.parse(expired), [])
        self.assertEqual(len(self.parse(running)), 1)

    def test_any_validity_window_counts(self):
        windows = [
            {"begin": "2026-03-09T01:00:00.000+0100", "end": "2026-03-09T04:00:00.000+0100"},
            {"begin": "2026-03-10T08:00:00.000+0100", "end": "2026-03-10T09:00:00.000+0100"},
        ]
        self.assertEqual(len(self.parse(announcement(validities=windows))), 1)

    def test_without_validities_the_publication_window_is_used(self):
        item = announcement(validities=[])
        self.assertEqual(len(self.parse(item)), 1)
        item["publication"] = {"begin": "2026-03-11T00:00:00.000+0100", "end": "2026-03-12T00:00:00.000+0100"}
        self.assertEqual(self.parse(item), [])

    def test_two_announcements_for_the_same_line_stay_separate_series(self):
        lines = self.parse(announcement(aid="a1"), announcement(aid="a2", summary="Oberleitungsschaden", reason="SERVICE_FAILURE"))
        self.assertEqual(len(lines), 2)
        self.assertNotEqual(lines[0].split(" ", 1)[0], lines[1].split(" ", 1)[0])

    def test_untracked_or_malformed_locations_are_ignored(self):
        item = announcement(lines=("S3",))
        item["locations"].append({"type": "WHOLE_NETWORK"})
        item["locations"].append({"type": "SINGLE_LINE", "line": None})
        self.assertEqual(self.parse(item), [])

    def test_missing_reason_and_summary(self):
        item = announcement()
        item.pop("reason")
        item.pop("summary")
        line = self.parse(item)[0]
        self.assertIn("reason=UNKNOWN", line)
        self.assertIn('summary=""', line)

    def test_tracked_lines_come_from_routes_and_corridor(self):
        self.assertTrue({"S5", "S7", "X30", "RE5", "234"} <= announcements.tracked_lines(Config()))
        self.assertNotIn("RE5", announcements.tracked_lines(Config(routes=(), corridor=None)))
        self.assertEqual(announcements.tracked_lines(Config(routes=(), corridor=CORRIDOR)), {line for line, _ in CORRIDOR.lines})


class CollectTest(unittest.TestCase):
    def collect(self, responses, denied=None):
        with FakeGti(responses=responses) as gti:
            lines = announcements.collect_announcements(client_for(gti), Config(), now=NOW, denied=denied)
        return lines, gti

    def test_request_names_the_tracked_lines(self):
        lines, gti = self.collect({"getAnnouncements": {"returnCode": "OK", "announcements": [announcement()]}})
        self.assertEqual(len(lines), 1)
        path, payload, _ = gti.requests[0]
        self.assertTrue(path.endswith("/getAnnouncements"))
        self.assertTrue(payload["full"])
        self.assertEqual(payload["names"], sorted(announcements.tracked_lines(Config())))

    def test_permission_denied_is_remembered(self):
        denied: set[str] = set()
        denial = (403, {"returnCode": "ERROR_TEXT", "errorText": "x", "errorDevInfo": "getAnnouncements not allowed for user"})
        lines, _ = self.collect({"getAnnouncements": denial}, denied)
        self.assertEqual((lines, denied), ([], {"announcements"}))
        again, gti = self.collect({}, denied)
        self.assertEqual(again, [])
        self.assertEqual(gti.requests, [])

    def test_other_errors_are_logged_and_cost_only_this_measurement(self):
        with self.assertLogs("hvv_gti.announcements", level="ERROR"):
            lines, _ = self.collect({"getAnnouncements": (500, {"returnCode": "ERROR_TEXT", "errorText": "kaputt"})})
        self.assertEqual(lines, [])

    def test_auth_error_propagates(self):
        with FakeGti(password="anders") as gti:
            bad = client_for(gti)
            bad._password = "falsch"
            with self.assertRaises(GtiAuthError):
                announcements.collect_announcements(bad, Config(), now=NOW)


ALWAYS = [{"begin": "2000-01-01T00:00:00.000+0100", "end": "2100-01-01T00:00:00.000+0100"}]  # run() nutzt die echte Uhrzeit


class RunTest(unittest.TestCase):
    def run_cycles(self, cfg, cycles):
        responses = {**gti_responses([departure("S5", 2, 90, direction="Elbgaustraße")]), "getAnnouncements": {"returnCode": "OK", "announcements": []}}
        stop = threading.Event()
        waits = []

        def wait(_timeout):
            waits.append(_timeout)
            if len(waits) >= cycles:
                stop.set()

        stop.wait = wait
        with FakeGti(responses=responses) as gti, FakeInflux() as influx:
            collector.run(cfg, client_for(gti), InfluxWriter(influx.url, "o", "b", "t"), stop)
        return [p for p, _, _ in gti.requests if p.endswith("/getAnnouncements")]

    def test_polled_on_first_cycle_then_only_after_the_interval(self):
        self.assertEqual(len(self.run_cycles(Config(poll_interval=0, announcement_interval=3600, corridor=None), cycles=3)), 1)

    def test_interval_zero_disables(self):
        self.assertEqual(self.run_cycles(Config(poll_interval=0, announcement_interval=0, corridor=None), cycles=2), [])

    def test_polled_every_cycle_when_interval_is_short(self):
        self.assertEqual(len(self.run_cycles(Config(poll_interval=0, announcement_interval=0.001, corridor=None), cycles=3)), 3)

    def test_announcement_lines_are_written_with_the_departures(self):
        responses = {
            **gti_responses([departure("S5", 2, 90, direction="Elbgaustraße")]),
            "getAnnouncements": {"returnCode": "OK", "announcements": [announcement(validities=ALWAYS)]},
        }
        with FakeGti(responses=responses) as gti, FakeInflux() as influx:
            writer = InfluxWriter(influx.url, "o", "b", "t")
            code = collector.run(Config(corridor=None), client_for(gti), writer, threading.Event(), once=True)
        self.assertEqual(code, 0)
        body = influx.writes[0][2]
        self.assertIn("hvv_stop_event,", body)
        self.assertIn("hvv_announcement,", body)
        self.assertIn("reason=POLICE_REQUEST", body)


class ConfigTest(unittest.TestCase):
    def test_default_and_env_override(self):
        self.assertEqual(Config().announcement_interval, 300.0)
        self.assertEqual(Config.from_env({}).announcement_interval, 300.0)
        self.assertEqual(Config.from_env({"HVV_ANNOUNCEMENT_INTERVAL": "0"}).announcement_interval, 0.0)


if __name__ == "__main__":
    unittest.main()
