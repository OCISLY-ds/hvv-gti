import base64
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from hvv_gti.client import gti_time, sign
from hvv_gti.config import ROUTES, Config, ConfigError, load_dotenv
from hvv_gti.influx import line_protocol


class SignTest(unittest.TestCase):
    def test_rfc2202_vector(self):
        # RFC 2202, Test Case 2: effcdf6ae5eb2fa2d27416d5f184df9c259a7c79
        expected = base64.b64encode(bytes.fromhex("effcdf6ae5eb2fa2d27416d5f184df9c259a7c79")).decode()
        self.assertEqual(sign("Jefe", b"what do ya want for nothing?"), expected)

    def test_signs_utf8_bytes(self):
        self.assertNotEqual(sign("pw", "Süd".encode("utf-8")), sign("pw", "Sud".encode("utf-8")))


class GtiTimeTest(unittest.TestCase):
    def test_winter_time(self):
        moment = datetime(2026, 1, 15, 8, 5, tzinfo=timezone.utc)
        self.assertEqual(gti_time(moment), {"date": "15.01.2026", "time": "09:05"})

    def test_summer_time_and_date_rollover(self):
        moment = datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(gti_time(moment), {"date": "02.07.2026", "time": "00:30"})


class LineProtocolTest(unittest.TestCase):
    def test_basic(self):
        line = line_protocol("m", {"b": "2", "a": "1"}, {"x": 3, "y": True, "z": 1.5, "s": 'a"b'}, 42)
        self.assertEqual(line, 'm,a=1,b=2 x=3i,y=true,z=1.5,s="a\\"b" 42')

    def test_escaping_and_empty_tags(self):
        line = line_protocol("my m", {"direction": "Hamburg, Hbf=Süd", "origin": ""}, {"v": 1}, 1)
        self.assertEqual(line, "my\\ m,direction=Hamburg\\,\\ Hbf\\=Süd v=1i 1")


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = Config.from_env({})
        self.assertEqual(cfg.routes, ROUTES)
        self.assertEqual({r.line for r in cfg.routes}, {"S5", "S7", "S7-SEV", "X30", "234", "4", "RE5"})
        self.assertFalse(cfg.collect_arrivals)

    def test_overrides_and_bool(self):
        cfg = Config.from_env(
            {
                "HVV_COLLECT_ARRIVALS": "true",  # wird ignoriert
                "HVV_POLL_INTERVAL": "30",
                "HVV_LINE": "S3",  # veraltet, wird ignoriert
                "HVV_SERVICE_TYPES": "UBAHN",  # veraltet, wird ignoriert
                "INFLUX_URL": "http://x:1/",
                "HVV_STATIONS": "A;B",
            }
        )
        self.assertEqual(cfg.routes, ROUTES)  # nicht per Umgebung aenderbar
        self.assertFalse(cfg.collect_arrivals)
        self.assertEqual(cfg.poll_interval, 30.0)
        self.assertEqual(cfg.influx_url, "http://x:1")

    def test_secrets_not_in_repr(self):
        cfg = Config(gti_password="topsecret", influx_token="tok123")
        self.assertNotIn("topsecret", repr(cfg))
        self.assertNotIn("tok123", repr(cfg))

    def test_require(self):
        with self.assertRaises(ConfigError):
            Config().require_gti()
        with self.assertRaises(ConfigError) as ctx:
            Config(influx_org="o").require_influx()
        self.assertIn("INFLUX_TOKEN", str(ctx.exception))
        Config(gti_user="u", gti_password="p").require_gti()
        Config(influx_org="o", influx_token="t").require_influx()


class RoutesTest(unittest.TestCase):
    def test_station_ids_are_master_ids(self):
        self.assertTrue(all(r.station_id.startswith("Master:") for r in ROUTES))

    def test_catch_all_routes_come_after_specific_ones(self):
        seen_catch_all: set[tuple[str, str, str]] = set()
        for r in ROUTES:
            key = (r.station_id, r.service_type, r.line)
            self.assertNotIn(key, seen_catch_all, f"{r.name}: Route steht hinter einer Route ohne Richtungsfilter")
            if not r.directions:
                seen_catch_all.add(key)

    def test_routes_cover_requested_lines(self):
        pairs = {(r.name, r.heading) for r in ROUTES}
        for pair in [
            ("S5", "Neugraben"),
            ("S5", "Stade"),
            ("S7", "Aumühle"),
            ("S7", "Hamburg"),
            ("X30", "Harburg"),
            ("X30", "Bergedorf"),
            ("234", "Leuschnerstraße"),
            ("4", "Überseequartier"),
            ("4", "Eidelstedt"),
            ("RE5", "Hamburg"),
            ("RE5", "Cuxhaven"),
        ]:
            self.assertIn(pair, pairs)

    def test_route_is_the_line_not_a_connection(self):
        self.assertTrue(all("→" not in r.name for r in ROUTES))


class DotenvTest(unittest.TestCase):
    def test_load_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# Kommentar\n\nHVV_T_A=eins\nHVV_T_B=\"zwei woerter\"\nHVV_T_C='x'\nHVV_T_KEEP=aus_datei\nkaputt\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"HVV_T_KEEP": "aus_umgebung"}, clear=False):
                for k in ("HVV_T_A", "HVV_T_B", "HVV_T_C"):
                    os.environ.pop(k, None)
                load_dotenv(path)
                self.assertEqual(os.environ["HVV_T_A"], "eins")
                self.assertEqual(os.environ["HVV_T_B"], "zwei woerter")
                self.assertEqual(os.environ["HVV_T_C"], "x")
                self.assertEqual(os.environ["HVV_T_KEEP"], "aus_umgebung")
                for k in ("HVV_T_A", "HVV_T_B", "HVV_T_C"):
                    os.environ.pop(k, None)

    def test_missing_file_is_ok(self):
        load_dotenv("/nonexistent/.env")


if __name__ == "__main__":
    unittest.main()
