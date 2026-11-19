"""Tests gegen eine echte PostgreSQL/TimescaleDB: nur mit HVV_TEST_DB (DSN), sonst uebersprungen.
Start der Wegwerf-Datenbank: sudo docker compose --profile dbtest up -d hvv-dbtest  (DSN postgresql://postgres:test@127.0.0.1:5544/hvv)"""

import datetime as dt
import os
import unittest

from hvv_gti.influx import line_protocol

DSN = os.environ.get("HVV_TEST_DB")
try:
    import psycopg
except ImportError:  # Treiber nur im Container
    psycopg = None

NS = 1_000_000_000
NOW = int(dt.datetime.now(dt.timezone.utc).timestamp())


@unittest.skipUnless(DSN and psycopg, "HVV_TEST_DB nicht gesetzt oder psycopg fehlt")
class TsdbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hvv_gti import tsdb

        cls.tsdb = tsdb
        cls.conn = psycopg.connect(DSN)
        with cls.conn.cursor() as cur:
            cur.execute("drop schema public cascade; create schema public")
        cls.conn.commit()
        cls.timescale = tsdb.ensure_schema(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        with self.conn.cursor() as cur:
            for t in [*(t.name for t in self.tsdb.TABLES), "hvv_other", "hvv_import_log"]:
                cur.execute(f"truncate {t}")
        self.conn.commit()

    def load(self, lines):
        return self.tsdb.Loader(self.conn).load(lines)

    def rows(self, sql):
        with self.conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def stop(self, line, station, delay, ts, m="hvv_network_stop", kind="departure"):
        return line_protocol(m, {"mode": "S", "line": line, "station": station, "direction": "Wedel", "kind": kind}, {"delay_s": delay, "actual": 1}, ts * NS)

    def test_schema_is_idempotent_and_uses_hypertables_when_available(self):
        self.assertEqual(self.tsdb.ensure_schema(self.conn), self.timescale)
        if self.timescale:
            names = {r[0] for r in self.rows("select hypertable_name from timescaledb_information.hypertables")}
            self.assertIn("hvv_stop", names)

    def test_points_land_in_typed_tables_and_a_rewrite_overwrites(self):
        self.assertEqual(self.load([self.stop("S1", "Altona", 120, NOW)])["points"], 1)
        self.load([self.stop("S1", "Altona", 240, NOW)])  # gleicher Schluessel: spaeter geschrieben gewinnt
        rows = self.rows("select m, line, station, delay_s from hvv_stop")
        self.assertEqual(rows, [("hvv_network_stop", "S1", "Altona", 240)])

    def test_same_time_in_two_measurements_stays_apart_and_batches_are_deduplicated(self):
        lines = [self.stop("S1", "Altona", 60, NOW, m="hvv_network_stop"), self.stop("S1", "Altona", 120, NOW, m="hvv_bus_stop")] + [self.stop("S3", "Hbf", i, NOW + 5) for i in range(3)]
        self.load(lines)
        self.assertEqual(self.rows("select count(*) from hvv_stop"), [(3,)])
        self.assertEqual(self.rows("select delay_s from hvv_stop where line = 'S3'"), [(2,)])

    def test_events_trains_announcements_requests_and_unknown_points(self):
        self.load([
            line_protocol("hvv_stop_event", {"line": "S5", "station": "Stade", "route": "S5", "kind": "departure"}, {"delay_s": 0, "cancelled": 1, "extra": 0, "service_id": 77, "reason_text": "Stau"}, NOW * NS),
            line_protocol("hvv_train_pos", {"line": "RE5", "journey": "j1", "direction": "Hamburg"}, {"pos": 0.5, "where": "unterwegs", "delay_min": 2}, NOW * NS),
            line_protocol("hvv_train_stop", {"line": "RE5", "journey": "j1", "direction": "Hamburg", "station": "Stade"}, {"delay_min": 3, "idx": 4}, NOW * NS),
            line_protocol("hvv_announcement", {"line": "S1", "announcement_id": "a1", "planned": "true", "reason": "ROADWORKS"}, {"summary": "Bauarbeiten", "version": 2}, NOW * NS),
            line_protocol("hvv_api_request", {"endpoint": "departureList", "status": "200"}, {"latency_ms": 110, "bytes": 5000}, NOW * NS),
            line_protocol("hvv_neu", {"a": "b"}, {"x": 1.5}, NOW * NS),
            line_protocol("hvv_network_stop", {"line": "S1", "unbekannter_tag": "x"}, {"delay_s": 1}, NOW * NS),
        ])
        self.assertEqual(self.rows("select cancelled, service_id, reason_text from hvv_event"), [(1, 77, "Stau")])
        self.assertEqual(self.rows("select pos, where_, delay_min from hvv_train_pos"), [(0.5, "unterwegs", 2)])
        self.assertEqual(self.rows("select delay_min, idx from hvv_train_stop"), [(3, 4)])
        self.assertEqual(self.rows("select summary, version from hvv_announcement"), [("Bauarbeiten", 2)])
        self.assertEqual(self.rows("select latency_ms from hvv_api_request"), [(110,)])
        self.assertEqual(sorted(r[0] for r in self.rows("select m from hvv_other")), ["hvv_network_stop", "hvv_neu"])  # nichts geht verloren

    def test_broken_lines_are_counted_not_fatal(self):
        counts = self.load(["kaputt", "", "m x=1i", self.stop("S1", "Altona", 5, NOW)])
        self.assertEqual((counts["points"], counts["skipped"]), (1, 2))  # "m x=1i" hat keinen Zeitstempel

    def test_reader_matches_the_influx_reader_interface(self):
        base = NOW - 3600
        lines = [self.stop("S1", "Altona", d, base + i * 60) for i, d in enumerate([0, 60, 120, 600])]
        lines += [self.stop("S1", "Hbf", 300, base + i * 60, m="hvv_network_stop") for i in range(5)]
        lines += [line_protocol("hvv_train_stop", {"line": "RE5", "journey": f"j{i}", "direction": "Hamburg", "station": "Stade"}, {"delay_min": i}, (base + i) * NS) for i in range(3)]
        lines += [line_protocol("hvv_stop_event", {"line": "S5", "station": "Stade", "route": "S5", "kind": "departure", "destination": f"d{i}"}, {"cancelled": 1 if i == 0 else 0}, (base + i) * NS) for i in range(4)]
        self.load(lines)
        reader = self.tsdb.TimescaleReader(DSN)
        by_station = reader.delays("hvv_network_stop", "S1", "Altona")
        self.assertEqual(sorted(v for _, v in by_station), [0.0, 60.0, 120.0, 600.0])
        self.assertTrue(all(isinstance(t, float) and abs(t - base) < 4000 for t, _ in by_station))
        sample = reader.delays("hvv_network_stop", "S1", None)  # ganze Linie: rund jede 10. Messung (Zufallsstichprobe), also 0 bis 9 von 9
        self.assertLessEqual(len(sample), 9)
        self.assertTrue(all(isinstance(t, float) and isinstance(v, float) for t, v in sample))
        self.assertEqual(sorted(v for _, v in reader.train_delays("RE5", "Stade", "Hamburg")), [0.0, 60.0, 120.0])
        self.assertEqual(reader.cancel_rate("S5"), (4, 0.25))
        self.assertIsNone(reader.cancel_rate("U9"))
        self.assertEqual(reader.frequent(10)[0], ("hvv_network_stop", "S1", "Hbf"))
        self.assertEqual(len(reader.frequent(1)), 1)

    def test_a_dead_database_is_an_oserror(self):
        reader = self.tsdb.TimescaleReader("postgresql://postgres:x@127.0.0.1:1/none", timeout=2)
        with self.assertRaises(OSError):
            reader.cancel_rate("S1")


if __name__ == "__main__":
    unittest.main()
