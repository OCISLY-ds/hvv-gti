import re
import unittest
from datetime import datetime, timezone

from hvv_gti import trains
from hvv_gti.client import GtiAuthError, GtiClient
from hvv_gti.config import CORRIDOR, Config

from .fakes import FakeGti
from .test_integration import client_for

T0 = int(datetime(2026, 3, 10, 7, 0, tzinfo=timezone.utc).timestamp())  # 08:00 Berlin
NOW = datetime.fromtimestamp(T0 + 25 * 60, timezone.utc)


def seg(start, end, t_start, t_end, delay):
    """Zeiten in Minuten nach T0; wie die GTI enthalten sie die Verspaetung bereits."""
    return {
        "startStationName": start,
        "endStationName": end,
        "startDateTime": T0 + t_start * 60,
        "endDateTime": T0 + t_end * 60,
        "realtimeDelay": delay,
    }


def journey(jid="j1", line="RE5", segments=None):
    return {"journeyID": jid, "line": {"name": line}, "segments": segments}


# Stade 08:00 (+2 -> 08:02), Buxtehude 08:20 geplant (+4 -> 08:24), Neugraben 08:35 geplant (+5 -> 08:40)
RUN = [
    seg("Stade", "Buxtehude", 2, 24, 2),
    seg("Buxtehude", "Neugraben", 24, 40, 4),
]


def field(line, name):
    return re.search(rf"[ ,]{name}=(\"[^\"]*\"|[^, ]+)", line).group(1)


class ParseJourneyTest(unittest.TestCase):
    def setUp(self):
        self.lines = trains.parse_journey(journey(segments=RUN), CORRIDOR, T0 + 25 * 60)
        self.stops = [l for l in self.lines if l.startswith(trains.STOP_MEASUREMENT)]
        self.pos = [l for l in self.lines if l.startswith(trains.POSITION_MEASUREMENT)]

    def test_one_point_per_station_including_arrival(self):
        self.assertEqual(len(self.stops), 3)
        self.assertEqual([field(l, "station") for l in self.stops], ["Stade", "Buxtehude", "Neugraben"])
        self.assertEqual([field(l, "idx") for l in self.stops], ["0i", "4i", "7i"])
        self.assertEqual([field(l, "arrival") for l in self.stops], ["0i", "0i", "1i"])

    def test_delay_is_minutes_and_timestamp_is_planned_time(self):
        self.assertEqual([field(l, "delay_min") for l in self.stops], ["2i", "4i", "4i"])
        planned = [int(l.rsplit(" ", 1)[1]) // 1_000_000_000 for l in self.stops]
        self.assertEqual(planned, [T0, T0 + 20 * 60, T0 + 36 * 60])

    def test_actual_time_keeps_the_reported_epoch(self):
        self.assertEqual(field(self.stops[0], "actual"), f"{T0 + 2 * 60}i")

    def test_direction_and_train_label(self):
        self.assertTrue(all("direction=Hamburg" in l for l in self.lines))
        self.assertEqual(field(self.stops[0], "train"), '"RE5 08:00 ab Stade"')
        self.assertEqual(len({field(l, "journey") for l in self.lines}), 1)

    def test_direction_towards_stade(self):
        back = [seg("Neugraben", "Buxtehude", 0, 15, 0), seg("Buxtehude", "Stade", 15, 35, 0)]
        lines = trains.parse_journey(journey(segments=back), CORRIDOR, T0 + 5 * 60)
        self.assertTrue(all("direction=Stade" in l for l in lines))

    def test_position_between_stations(self):
        self.assertEqual(len(self.pos), 1)
        pos = self.pos[0]
        self.assertEqual(field(pos, "state"), '"fährt"')
        self.assertEqual(field(pos, "next"), '"Neugraben"')
        # jetzt 08:25 (T0+25): Abschnitt Buxtehude (Idx 4) -> Neugraben (Idx 7) laeuft von T0+24 bis T0+40, Anteil 1/16
        self.assertAlmostEqual(float(field(pos, "pos")), 4 + 3 / 16, places=6)
        self.assertEqual(field(pos, "delay_min"), "4i")

    def test_position_waiting_at_first_station(self):
        lines = trains.parse_journey(journey(segments=RUN), CORRIDOR, T0 + 1 * 60)
        pos = [l for l in lines if l.startswith(trains.POSITION_MEASUREMENT)][0]
        self.assertEqual(field(pos, "state"), '"wartet"')
        self.assertEqual(float(field(pos, "pos")), 0.0)

    def test_no_position_after_arrival_or_long_before_departure(self):
        for now in (T0 + 41 * 60, T0 - 10 * 60):
            lines = trains.parse_journey(journey(segments=RUN), CORRIDOR, now)
            self.assertFalse(any(l.startswith(trains.POSITION_MEASUREMENT) for l in lines))
            self.assertEqual(len([l for l in lines if l.startswith(trains.STOP_MEASUREMENT)]), 3)

    def test_station_alias_and_foreign_stations(self):
        run = [seg("Cuxhaven", "Stade", -30, 0, 0), seg("Stade", "Hamburg-Harburg", 0, 30, 1), seg("Hamburg-Harburg", "Hamburg Hbf", 30, 45, 1)]
        lines = trains.parse_journey(journey(segments=run), CORRIDOR, T0 + 10 * 60)
        stops = [l for l in lines if l.startswith(trains.STOP_MEASUREMENT)]
        self.assertEqual([field(l, "station") for l in stops][:2], ["Stade", "Harburg"])
        self.assertEqual(len(stops), 3)
        self.assertNotIn("Cuxhaven", "".join(stops))

    def test_journey_outside_corridor_and_empty_give_nothing(self):
        self.assertEqual(trains.parse_journey(journey(segments=[seg("Berlin", "Hannover", 0, 90, 0)]), CORRIDOR, T0), [])
        self.assertEqual(trains.parse_journey(journey(segments=[]), CORRIDOR, T0), [])

    def test_missing_delay_omits_field(self):
        run = [dict(seg("Stade", "Buxtehude", 0, 20, 0), realtimeDelay=None), seg("Buxtehude", "Neugraben", 20, 35, 0)]
        del run[0]["realtimeDelay"]
        lines = trains.parse_journey(journey(segments=run), CORRIDOR, T0 + 5 * 60)
        self.assertNotIn("delay_min", [l for l in lines if "station=Stade" in l][0])

    def test_journey_key_is_stable_and_short(self):
        self.assertEqual(trains.journey_key("abc"), trains.journey_key("abc"))
        self.assertNotEqual(trains.journey_key("abc"), trains.journey_key("abd"))
        self.assertEqual(len(trains.journey_key("abc")), 10)


class CollectTrainsTest(unittest.TestCase):
    def collect(self, responses, denied=None):
        with FakeGti(responses=responses) as gti:
            lines = trains.collect_trains(client_for(gti), Config(), now=NOW, denied=denied)
        return lines, gti

    def test_one_call_per_vehicle_type_and_line_filter(self):
        def respond(body):
            types = body["vehicleTypes"]
            line = "RE5" if types == ["R_BAHN"] else "S5"
            other = journey("x", "RE4", RUN)
            return {"returnCode": "OK", "journeys": [journey(f"id-{line}", line, RUN), other]}

        lines, gti = self.collect({"getVehicleMap": respond})
        calls = [r[1] for r in gti.requests]
        self.assertEqual(sorted(c["vehicleTypes"][0] for c in calls), ["R_BAHN", "S_BAHN"])
        self.assertEqual(calls[0]["periodEnd"] - calls[0]["periodBegin"], 180 * 60)
        self.assertEqual({field(l, "line") for l in lines}, {"RE5", "S5"})
        self.assertEqual(len(lines), 2 * 4)

    def test_permission_denied_is_remembered(self):
        denied: set[str] = set()
        lines, gti = self.collect({"getVehicleMap": (403, {"returnCode": "ERROR_TEXT", "errorText": "x", "errorDevInfo": "not allowed for user"})}, denied)
        self.assertEqual(lines, [])
        self.assertEqual(denied, {"vehicle_map"})
        self.assertEqual(len(gti.requests), 1)
        again, gti2 = self.collect({}, denied)
        self.assertEqual((again, gti2.requests), ([], []))

    def test_server_error_is_tolerated_but_wrong_credentials_raise(self):
        lines, _ = self.collect({"getVehicleMap": (500, {"returnCode": "ERROR_TEXT", "errorText": "kaputt"})})
        self.assertEqual(lines, [])
        with FakeGti(password="anders") as gti:
            client = GtiClient(gti.user, "falsch", base_url=gti.url, min_interval=0)
            with self.assertRaises(GtiAuthError):
                trains.collect_trains(client, Config(), now=NOW)

    def test_disabled_without_corridor(self):
        with FakeGti() as gti:
            self.assertEqual(trains.collect_trains(client_for(gti), Config(corridor=None), now=NOW), [])
            self.assertEqual(gti.requests, [])


if __name__ == "__main__":
    unittest.main()
