import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from hvv_gti.__main__ import main

from .fakes import FakeGti
from .test_integration import HBF, STADE, departure, gti_responses

VEHICLES = {
    "returnCode": "OK",
    "journeys": [
        {
            "journeyID": "j1",
            "line": {"name": "S5"},
            "vehicleType": "S_BAHN",
            "realtime": True,
            "segments": [
                {"startStationName": "Stade", "endStationName": "Buxtehude", "destination": "Elbgaustraße", "realtimeDelay": 120},
            ],
        },
        {"journeyID": "j2", "line": {"name": "113"}, "vehicleType": "METROBUS", "realtime": False, "segments": [{"realtimeDelay": None}]},
    ],
}


def run_cli(gti: FakeGti, *argv: str, extra_env: dict | None = None) -> tuple[int, str, str]:
    env = {
        "HVV_GTI_USER": gti.user,
        "HVV_GTI_PASSWORD": gti.password,
        "HVV_GTI_BASE_URL": gti.url,
        "HVV_MIN_REQUEST_INTERVAL": "0",
        **(extra_env or {}),
    }
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
        cwd = os.getcwd()
        os.chdir(tmp)  # kein .env aus dem Projektordner
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(list(argv))
        finally:
            os.chdir(cwd)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_missing_credentials(self):
        with FakeGti() as gti:
            code, _, err = run_cli(gti, "check-name", "Stade", extra_env={"HVV_GTI_USER": "", "HVV_GTI_PASSWORD": ""})
        self.assertEqual(code, 2)
        self.assertIn("HVV_GTI_USER", err)

    def test_check_name(self):
        with FakeGti(responses=gti_responses([])) as gti:
            code, out, _ = run_cli(gti, "check-name", "Stade")
        self.assertEqual(code, 0)
        self.assertIn("Master:80900", out)
        self.assertIn("Hamburg Hbf", out)

    def test_departures_filters_line_and_shows_delay(self):
        responses = gti_responses([departure("S5", 4, 180), departure("S3", 5, 0), departure("S5", 24, 0, cancelled=True)])
        with FakeGti(responses=responses) as gti:
            code, out, _ = run_cli(gti, "departures", "Stade", "--station-id", STADE["id"], "--line", "S5")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 2)
        self.assertIn("+3.0 min", out)
        self.assertIn("AUSFALL", out)
        self.assertNotIn("S3", out)

    def test_departures_types_option(self):
        with FakeGti(responses=gti_responses([])) as gti:
            run_cli(gti, "departures", "Bergedorf", "--station-id", "Master:25950", "--types", "BUS,XPRESSBUS")
            run_cli(gti, "departures", "Bergedorf", "--station-id", "Master:25950")
        self.assertEqual(gti.requests[0][1]["serviceTypes"], ["BUS", "XPRESSBUS"])
        self.assertNotIn("serviceTypes", gti.requests[1][1])

    def test_departures_arrivals_flag(self):
        with FakeGti(responses=gti_responses([])) as gti:
            run_cli(gti, "departures", "Hauptbahnhof", "--station-id", HBF["id"], "--arrivals")
        self.assertIs(gti.requests[-1][1]["departure"], False)

    def test_collect_dry_run_prints_line_protocol(self):
        with FakeGti(responses=gti_responses([departure("S5", 2, 60, direction="Elbgaustraße")])) as gti:
            code, out, _ = run_cli(gti, "collect", "--dry-run", "--once")
        self.assertEqual(code, 0)
        printed = out.strip().splitlines()
        lines = [l for l in printed if l.startswith("hvv_stop_event,")]
        self.assertEqual(len(lines), 3)  # 3 S5-Messpunkte, nur Abfahrten
        self.assertTrue(all("kind=departure" in l and ",route=" in l for l in lines))
        requests = [r[1] for r in gti.requests if r[0].endswith("departureList")]
        self.assertEqual(len(requests), 9)  # 9 Haltestellen/Verkehrsmittel-Kombinationen, keine Ankuenfte
        usage = [l for l in printed if l.startswith("hvv_api_request,")]
        self.assertEqual(len(usage), len(gti.requests))  # jeder GTI-Aufruf steht im Anfrageprotokoll
        self.assertEqual(len(printed), len(lines) + len(usage))

    def test_collect_requires_influx_settings(self):
        with FakeGti() as gti:
            code, _, err = run_cli(gti, "collect", "--once")
        self.assertEqual(code, 2)
        self.assertIn("INFLUX_TOKEN", err)

    def test_probe_vehicles_summary(self):
        with FakeGti(responses={"getVehicleMap": VEHICLES}) as gti:
            code, out, _ = run_cli(gti, "probe-vehicles", "--line", "S5", "--types", "S_BAHN,METROBUS")
        self.assertEqual(code, 0)
        self.assertIn("Fahrten 2, Segmente 2, mit Echtzeit 1", out)
        self.assertIn("median 120", out)
        self.assertIn("Linie S5: 1 Fahrten", out)
        self.assertIn("Stade -> Buxtehude", out)
        self.assertEqual(gti.requests[-1][1]["vehicleTypes"], ["S_BAHN", "METROBUS"])


if __name__ == "__main__":
    unittest.main()
