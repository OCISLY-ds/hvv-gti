import re
import unittest
from datetime import datetime, timezone

from hvv_gti import network
from hvv_gti.client import GtiAuthError, GtiClient
from hvv_gti.config import NETWORK, XBUS, Config

from .fakes import FakeGti
from .test_integration import client_for

T0 = int(datetime(2026, 3, 10, 7, 0, tzinfo=timezone.utc).timestamp())  # 08:00 Berlin
NOW_TS = T0 + 10 * 60


def seg(start, end, t_start, t_end, delay):
    """Zeiten in Minuten nach T0; wie die GTI enthalten sie die Verspaetung bereits."""
    return {
        "startStationName": start,
        "endStationName": end,
        "startDateTime": T0 + t_start * 60,
        "endDateTime": T0 + t_end * 60,
        "realtimeDelay": delay,
    }


def journey(line="U3", direction="Barmbek", vehicle_type="U_BAHN", realtime=True, segments=None, truncated=False):
    if segments:
        segments = [{**x, "isLast": False} for x in segments]
        segments[-1]["isLast"] = not truncated
    return {
        "journeyID": f"{line}-1",
        "line": {"name": line, "direction": direction},
        "vehicleType": vehicle_type,
        "realtime": realtime,
        "segments": segments,
    }


# Der Halt am Segmentanfang traegt die Verspaetung des Segments, der letzte Halt die des letzten Segments.
# Rathaus 08:10 geplant (+1 -> 08:11), Mönckebergstraße 08:11 geplant (+3 -> 08:14), Hauptbahnhof Nord 08:16 geplant (+3 -> 08:19)
RUN = [seg("Rathaus", "Mönckebergstraße", 11, 13, 1), seg("Mönckebergstraße", "Hauptbahnhof Nord", 14, 19, 3)]


def field(line, name):
    value = re.search(rf"[ ,]{name}=(\"[^\"]*\"|(?:\\.|[^, ])+)", line).group(1)
    return re.sub(r"\\(.)", r"\1", value)


class ParseJourneyTest(unittest.TestCase):
    def parse(self, j, now_ts=NOW_TS, horizon=600):
        return network.parse_journey(j, now_ts, horizon)

    def test_stops_with_tags_planned_timestamp_and_delay_in_seconds(self):
        lines = self.parse(journey(segments=RUN))
        self.assertEqual([field(l, "station") for l in lines], ["Rathaus", "Mönckebergstraße", "Hauptbahnhof Nord"])
        self.assertEqual([field(l, "kind") for l in lines], ["departure", "departure", "arrival"])
        self.assertTrue(all(l.startswith("hvv_network_stop,") for l in lines))
        self.assertEqual({field(l, "mode") for l in lines}, {"U"})
        self.assertEqual({field(l, "line") for l in lines}, {"U3"})
        self.assertEqual({field(l, "direction") for l in lines}, {"Barmbek"})
        self.assertEqual([field(l, "delay_s") for l in lines], ["60i", "180i", "180i"])
        planned = [int(l.rsplit(" ", 1)[1]) // 1_000_000_000 for l in lines]
        self.assertEqual(planned, [T0 + 10 * 60, T0 + 11 * 60, T0 + 16 * 60])
        self.assertEqual(field(lines[0], "actual"), f"{T0 + 11 * 60}i")

    def test_only_stops_inside_the_horizon_are_written(self):
        # tatsaechliche Zeiten: 08:11, 08:14, 08:19; jetzt = 08:10
        self.assertEqual(len(self.parse(journey(segments=RUN), horizon=5 * 60)), 2)
        self.assertEqual(len(self.parse(journey(segments=RUN), horizon=60)), 1)
        # bereits passierte Halte (vor jetzt) fehlen
        self.assertEqual([field(l, "station") for l in self.parse(journey(segments=RUN), now_ts=T0 + 12 * 60)], ["Mönckebergstraße", "Hauptbahnhof Nord"])
        self.assertEqual(self.parse(journey(segments=RUN), now_ts=T0 + 30 * 60), [])

    def test_without_realtime_the_delay_field_is_omitted(self):
        lines = self.parse(journey(realtime=False, segments=RUN))
        self.assertEqual(len(lines), 3)
        self.assertTrue(all("delay_s" not in l for l in lines))
        # ohne Echtzeit gilt die Segmentzeit als planmaessig
        self.assertEqual(int(lines[0].rsplit(" ", 1)[1]) // 1_000_000_000, T0 + 10 * 60)

    def test_missing_delay_value_omits_the_field(self):
        segments = [seg("A", "B", 11, 13, None)]
        lines = self.parse(journey(segments=segments))
        self.assertTrue(all("delay_s" not in l for l in lines))

    def test_planned_timestamp_is_floored_to_the_minute(self):
        segments = [{**seg("A", "B", 11, 13, 1), "startDateTime": T0 + 11 * 60 + 40}]
        lines = self.parse(journey(segments=segments))
        self.assertEqual(int(lines[0].rsplit(" ", 1)[1]) // 1_000_000_000, T0 + 10 * 60)

    def test_s_bahn_mode_and_unknown_types(self):
        s_bahn = self.parse(journey("S1", "Poppenbüttel", "S_BAHN", segments=RUN))
        self.assertEqual({field(l, "mode") for l in s_bahn}, {"S"})
        other = self.parse(journey("A1", "Ulzburg", "A_BAHN", segments=RUN))
        self.assertEqual({field(l, "mode") for l in other}, {"a_bahn"})

    def test_truncated_journey_has_no_arrival_stop(self):
        lines = self.parse(journey(segments=RUN, truncated=True), horizon=600)
        self.assertEqual([field(l, "station") for l in lines], ["Rathaus", "Mönckebergstraße"])
        self.assertEqual({field(l, "kind") for l in lines}, {"departure"})

    def test_journey_without_segments_is_ignored(self):
        self.assertEqual(self.parse(journey(segments=[])), [])


class CollectNetworkTest(unittest.TestCase):
    NOW = datetime.fromtimestamp(NOW_TS, timezone.utc)

    def collect(self, responses, denied=None, cfg=None):
        with FakeGti(responses=responses) as gti:
            lines = network.collect_network(client_for(gti), cfg or Config(), now=self.NOW, denied=denied)
        return lines, gti

    def test_single_call_for_all_vehicle_types_and_horizon_window(self):
        answer = {"returnCode": "OK", "journeys": [journey("U3", segments=RUN), journey("S1", "Poppenbüttel", "S_BAHN", segments=RUN)]}
        lines, gti = self.collect({"getVehicleMap": answer})
        self.assertEqual(len(gti.requests), 1)
        body = gti.requests[0][1]
        self.assertEqual(body["vehicleTypes"], ["S_BAHN", "U_BAHN"])
        self.assertEqual((body["periodBegin"], body["periodEnd"]), (NOW_TS, NOW_TS + NETWORK.horizon_s))
        self.assertEqual({field(l, "line") for l in lines}, {"U3", "S1"})
        self.assertEqual(len(lines), 2 * 2)  # je Fahrt die Halte 08:11 und 08:14, der Halt 08:19 liegt hinter dem Horizont

    def test_permission_denied_is_remembered(self):
        denied: set[str] = set()
        lines, gti = self.collect({"getVehicleMap": (403, {"returnCode": "ERROR_TEXT", "errorText": "x", "errorDevInfo": "not allowed for user"})}, denied)
        self.assertEqual((lines, denied), ([], {"vehicle_map"}))
        again, gti2 = self.collect({}, denied)
        self.assertEqual((again, gti2.requests), ([], []))

    def test_server_error_is_tolerated_but_wrong_credentials_raise(self):
        with self.assertLogs("hvv_gti.network", level="ERROR"):
            lines, _ = self.collect({"getVehicleMap": (500, {"returnCode": "ERROR_TEXT", "errorText": "kaputt"})})
        self.assertEqual(lines, [])
        with FakeGti(password="anders") as gti:
            client = GtiClient(gti.user, "falsch", base_url=gti.url, min_interval=0)
            with self.assertRaises(GtiAuthError):
                network.collect_network(client, Config(), now=self.NOW)

    def test_xbus_has_own_call_measurement_and_area(self):
        answer = {"returnCode": "OK", "journeys": [journey("X22", "Rathaus Pinneberg", "XPRESSBUS", segments=RUN)]}
        with FakeGti(responses={"getVehicleMap": answer}) as gti:
            lines = network.collect_xbus(client_for(gti), Config(), now=self.NOW)
        body = gti.requests[0][1]
        self.assertEqual(body["vehicleTypes"], ["XPRESSBUS"])
        self.assertEqual((body["boundingBox"]["lowerLeft"]["x"], body["boundingBox"]["upperRight"]["y"]), (XBUS.bbox[0], XBUS.bbox[3]))
        self.assertTrue(lines and all(l.startswith("hvv_xbus_stop,") for l in lines))
        self.assertEqual({field(l, "mode") for l in lines}, {"X"})
        self.assertEqual({field(l, "line") for l in lines}, {"X22"})

    def test_regional_keeps_re_and_rb_lines_in_its_own_measurement(self):
        answer = {
            "returnCode": "OK",
            "journeys": [
                journey("RE1", "Hamburg Hbf", "R_BAHN", segments=RUN),
                journey("RB81", "Hamburg Hbf", "R_BAHN", segments=RUN),
                journey("RE70", "Kiel Hbf", "R_BAHN", segments=RUN),
                journey("ICE", "Berlin", "R_BAHN", segments=RUN),
            ],
        }
        with FakeGti(responses={"getVehicleMap": answer}) as gti:
            lines = network.collect_regional(client_for(gti), Config(), now=self.NOW)
        self.assertEqual(gti.requests[0][1]["vehicleTypes"], ["R_BAHN"])
        self.assertTrue(lines and all(l.startswith("hvv_regional_stop,") for l in lines))
        self.assertEqual({field(l, "line") for l in lines}, {"RE1", "RB81", "RE70"})
        self.assertEqual({field(l, "mode") for l in lines}, {"R"})
        with FakeGti() as gti:
            self.assertEqual(network.collect_regional(client_for(gti), Config(regional=None), now=self.NOW), [])
            self.assertEqual(gti.requests, [])

    def test_xbus_denial_is_tracked_separately_from_the_network(self):
        denied = {"vehicle_map"}
        with FakeGti(responses={"getVehicleMap": {"returnCode": "OK", "journeys": []}}) as gti:
            network.collect_xbus(client_for(gti), Config(), now=self.NOW, denied=denied)
            self.assertEqual(len(gti.requests), 1)  # "vehicle_map" gesperrt blockiert die X-Busse nicht
        with FakeGti() as gti:
            self.assertEqual(network.collect_xbus(client_for(gti), Config(xbus=None), now=self.NOW), [])
            self.assertEqual(gti.requests, [])

    def test_disabled_without_network(self):
        with FakeGti() as gti:
            self.assertEqual(network.collect_network(client_for(gti), Config(network=None), now=self.NOW), [])
            self.assertEqual(gti.requests, [])


if __name__ == "__main__":
    unittest.main()
