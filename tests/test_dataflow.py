import gzip
import http.server
import json
import os
import re
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hvv_gti import migrate
from hvv_gti.github import GithubError, Releases
from hvv_gti.influx import InfluxError, line_protocol
from hvv_gti.lineproto import parse_line
from hvv_gti.spool import MultiWriter, Shipper, SpoolWriter

NS = 1_000_000_000


def at(hour, day=19, month=11):
    return datetime(2026, month, day, hour, 30, tzinfo=timezone.utc).timestamp()


class FakeGithub(http.server.BaseHTTPRequestHandler):
    """Nachbau der benutzten GitHub-Endpunkte; `state` haelt Releases und Anhaenge, `fail` laesst Uploads scheitern."""

    state: dict = {}

    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        return self.headers.get("Authorization") == "Bearer tok"

    def do_GET(self):
        s = self.state
        if self.path.startswith("/blob/"):
            if self.headers.get("Authorization"):  # S3 lehnt zusaetzliche Authentifizierung ab
                return self._send(400, b"auth header forwarded")
            return self._send(200, s["blobs"][int(self.path.split("/")[-1])], "application/gzip")
        if not self._auth():
            return self._send(401, b'{"message":"bad credentials"}')
        if re.match(r"^/repos/o/r/releases\?", self.path):
            return self._send(200, json.dumps(s["releases"]).encode())
        m = re.match(r"^/repos/o/r/releases/(\d+)/assets\?", self.path)
        if m:
            return self._send(200, json.dumps([a for a in s["assets"] if a["release"] == int(m.group(1))]).encode())
        m = re.match(r"^/repos/o/r/releases/assets/(\d+)$", self.path)
        if m:
            return self._send(302, b"", extra={"Location": f"http://127.0.0.1:{self.server.server_port}/blob/{m.group(1)}"})
        self._send(404)

    def do_POST(self):
        s = self.state
        if not self._auth():
            return self._send(401)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/repos/o/r/releases":
            rel = {"id": len(s["releases"]) + 1, **json.loads(body)}
            s["releases"].append(rel)
            return self._send(201, json.dumps(rel).encode())
        m = re.match(r"^/repos/o/r/releases/(\d+)/assets\?name=(.+)$", self.path)
        if m:
            if s.get("fail"):
                return self._send(503, b"down")
            asset = {"id": 100 + len(s["assets"]), "release": int(m.group(1)), "name": m.group(2)}
            s["blobs"][asset["id"]] = body
            s["assets"].append(asset)
            return self._send(201, json.dumps(asset).encode())
        self._send(404)


class GithubTestCase(unittest.TestCase):
    def setUp(self):
        FakeGithub.state = {"releases": [], "assets": [], "blobs": {}}
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGithub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.gh = Releases("o/r", "tok", api=base, uploads=base, timeout=10)
        self.state = FakeGithub.state
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))


class SpoolTests(GithubTestCase):
    def spool(self, now):
        self.now = now
        return SpoolWriter(self.tmp / "spool", clock=lambda: self.now)

    def point(self, i):
        return line_protocol("hvv_network_stop", {"line": "S1", "station": "Altona", "mode": "S", "direction": "W", "kind": "departure"}, {"delay_s": i}, (1_790_000_000 + i) * NS)

    def test_hours_are_separate_files_and_only_finished_hours_are_compressed(self):
        sp = self.spool(at(10))
        sp.write([self.point(1), self.point(2)])
        self.now = at(11)
        sp.write([self.point(3)])
        self.assertEqual(sp.close_finished(), 1)
        names = sorted(p.name for p in sp.dir.iterdir())
        self.assertEqual(names, ["hvv-20261119T10.lp.gz", "hvv-20261119T11.lp"])
        self.assertEqual(len(gzip.open(sp.dir / names[0], "rt").read().splitlines()), 2)

    def test_the_shipper_bundles_open_hours_into_one_monthly_release_asset_and_remembers_them(self):
        sp = self.spool(at(10))
        sp.write([self.point(1)])
        self.now = at(11)
        sp.write([self.point(2)])
        self.now = at(13)
        shipper = Shipper(sp, self.gh)
        name = shipper.ship_once()
        self.assertEqual(name, "hvv-20261119T10_20261119T11.lp.gz")
        self.assertEqual([r["tag_name"] for r in self.state["releases"]], ["data-2026-11"])
        text = gzip.decompress(self.state["blobs"][self.state["assets"][0]["id"]]).decode()  # zwei gzip-Teile hintereinander sind ein gzip
        self.assertEqual([parse_line(x)[2]["delay_s"] for x in text.splitlines()], [1, 2])
        self.assertIsNone(shipper.ship_once())  # nichts Neues
        self.assertEqual(len(self.state["assets"]), 1)

    def test_a_failed_upload_keeps_everything_and_is_retried_without_duplicates(self):
        sp = self.spool(at(10))
        sp.write([self.point(1)])
        self.now = at(12)
        shipper = Shipper(sp, self.gh)
        self.state["fail"] = True
        with self.assertRaises(GithubError):
            shipper.ship_once()
        self.assertEqual(len(shipper.pending()), 1)
        self.state["fail"] = False
        self.assertIsNotNone(shipper.ship_once())
        self.assertEqual(len(self.state["assets"]), 1)
        # Abbruch zwischen Upload und Merkzettel: gleicher Paketname wird nicht doppelt hochgeladen
        shipper.manifest.write_text("[]")
        shipper.ship_once()
        self.assertEqual(len(self.state["assets"]), 1)

    def test_shipped_files_are_deleted_after_the_retention_time_only(self):
        sp = self.spool(at(10))
        sp.write([self.point(1)])
        self.now = at(12)
        shipper = Shipper(sp, self.gh, retention_days=7, clock=lambda: self.now)
        shipper.ship_once()
        self.assertEqual(len(list(sp.dir.glob("*.lp.gz"))), 1)
        self.now += 8 * 86400
        os.utime(next(sp.dir.glob("*.lp.gz")), (at(10), at(10)))
        shipper.cleanup()
        self.assertEqual(list(sp.dir.glob("*.lp.gz")), [])
        self.assertEqual(shipper.pending(), [])

    def test_download_follows_the_redirect_without_the_token(self):
        sp = self.spool(at(10))
        sp.write([self.point(1)])
        self.now = at(12)
        Shipper(sp, self.gh).ship_once()
        out = self.tmp / "x.gz"
        self.gh.download(self.state["assets"][0], out)
        self.assertEqual(len(gzip.open(out, "rt").read().splitlines()), 1)

    def test_bad_token_is_an_error(self):
        with self.assertRaises(GithubError):
            Releases("o/r", "falsch", api=f"http://127.0.0.1:{self.server.server_port}", uploads="x").list_releases()
        with self.assertRaises(GithubError):
            Releases("ohne-owner", "tok")


class MultiWriterTests(unittest.TestCase):
    def test_one_failing_sink_does_not_block_the_other(self):
        got = []

        class Good:
            def write(self, lines):
                got.extend(lines)

        class Bad:
            def write(self, lines):
                raise InfluxError("weg")

        with self.assertRaises(InfluxError):
            MultiWriter([Bad(), Good()]).write(["a"])
        self.assertEqual(got, ["a"])


class FakeInflux(migrate.InfluxExport):
    def __init__(self):
        super().__init__("http://x", "o", "hvv", "t")
        self.asked = []

    def _rows(self, flux):
        self.asked.append(flux)
        if "schema.measurements" in flux:
            return iter([({"_value": m}, {}) for m in ("hvv_network_stop", "hvv_api_request", "tranco_rank")])
        if "hvv_network_stop" in flux and "start: 2026-11-18T00" in flux:
            base = {"_time": "2026-11-18T10:00:00Z", "_measurement": "hvv_network_stop", "line": "S1", "station": "Hamburg Hbf, Gleis 3", "direction": "Wedel", "kind": "departure", "mode": "S", "result": "_result", "table": "0", "_start": "", "_stop": ""}
            types = {"_value": "long"}
            return iter([({**base, "_field": "delay_s", "_value": "120"}, types), ({**base, "_field": "actual", "_value": "1795000000"}, types)])
        return iter([])


class ExportTests(unittest.TestCase):
    def test_history_becomes_typed_line_protocol_per_day_and_skips_other_buckets_data(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        src = FakeInflux()
        files = migrate.export_history(src, 2, tmp, now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc))
        self.assertEqual([f.name for f in files], ["hist-2026-11-18.lp.gz"])
        lines = gzip.open(files[0], "rt").read().splitlines()
        m, tags, fields, ts = parse_line(lines[0])
        self.assertEqual((m, tags["station"], fields, ts), ("hvv_network_stop", "Hamburg Hbf, Gleis 3", {"delay_s": 120, "actual": 1795000000}, int(datetime(2026, 11, 18, 10, tzinfo=timezone.utc).timestamp()) * NS))
        self.assertFalse(any("tranco_rank" in q for q in src.asked))  # nur hvv_*-Messungen


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_pack_and_unpack_round_trip_only_the_known_paths(self):
        src, dst = self.tmp / "src", self.tmp / "dst"
        (src / "map/data").mkdir(parents=True)
        (src / "data/icons").mkdir(parents=True)
        (src / "map/data/tracks.json").write_text("{}")
        (src / "map/data/geheim.env").write_text("NICHT")  # nicht in ASSET_PATHS
        (src / "data/icons/S1.svg").write_text("<svg/>")
        archive = self.tmp / "a.tar.gz"
        self.assertEqual(sorted(migrate.pack_assets(src, archive)), ["data/icons", "map/data/tracks.json"])
        dst.mkdir()
        self.assertEqual(sorted(migrate.unpack_assets(archive, dst)), ["data/icons/S1.svg", "map/data/tracks.json"])
        self.assertFalse((dst / "map/data/geheim.env").exists())

    def test_a_manipulated_package_cannot_write_outside_the_allowed_folders(self):
        import io
        import tarfile

        evil = self.tmp / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            for name in ("map/data/../../escape.txt", "../outside.txt", "etc/passwd", "map/data/tracks.json"):
                info = tarfile.TarInfo(name)
                info.size = 2
                tar.addfile(info, io.BytesIO(b"xx"))
        root = self.tmp / "root"
        root.mkdir()
        self.assertEqual(migrate.unpack_assets(evil, root), ["map/data/tracks.json"])
        self.assertFalse((self.tmp / "escape.txt").exists() or (self.tmp / "outside.txt").exists() or (root / "escape.txt").exists())


class ConfigTests(unittest.TestCase):
    def test_sinks_and_data_settings_come_from_the_environment(self):
        from hvv_gti.config import Config

        c = Config.from_env({"HVV_SINKS": "influx, spool", "GITHUB_REPO": "o/r", "GITHUB_TOKEN": "t", "HVV_DB_URL": "postgresql://x", "HVV_SHIP_INTERVAL": "3600"})
        self.assertEqual((c.sinks, c.github_repo, c.ship_interval), (("influx", "spool"), "o/r", 3600.0))
        self.assertEqual(Config.from_env({}).sinks, ("influx",))
        with self.assertRaises(Exception):
            Config.from_env({}).require_github()


DSN = os.environ.get("HVV_TEST_DB")
try:
    import psycopg
except ImportError:
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "HVV_TEST_DB nicht gesetzt oder psycopg fehlt")
class ImportTests(GithubTestCase):
    def setUp(self):
        super().setUp()
        from hvv_gti import tsdb

        self.conn = psycopg.connect(DSN)
        self.addCleanup(self.conn.close)
        with self.conn.cursor() as cur:
            cur.execute("drop schema public cascade; create schema public")
        self.conn.commit()
        tsdb.ensure_schema(self.conn)

    def put(self, tag, name, lines):
        rel = next((r for r in self.state["releases"] if r["tag_name"] == tag), None) or self.gh.release(tag)
        path = self.tmp / name
        with gzip.open(path, "wt") as fh:
            fh.write("\n".join(lines) + "\n")
        self.gh.upload(rel, path, name)

    def stop(self, delay, ts):
        return line_protocol("hvv_network_stop", {"line": "S1", "station": "Altona", "mode": "S", "direction": "W", "kind": "departure"}, {"delay_s": delay}, ts * NS)

    def count(self):
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from hvv_stop")
            return cur.fetchone()[0]

    def test_packages_are_imported_once_in_order_and_overlaps_do_not_duplicate(self):
        t0 = 1_790_000_000
        self.put("data-history", "hist-2026-11-18.lp.gz", [self.stop(i, t0 + i) for i in range(5)])
        self.put("data-2026-11", "hvv-a.lp.gz", [self.stop(i + 100, t0 + i) for i in range(3, 8)])  # ueberlappt die Historie bei 3 und 4
        first = migrate.import_pending(self.conn, self.gh, self.tmp)
        self.assertEqual([a for a, _ in first], ["data-2026-11/hvv-a.lp.gz", "data-history/hist-2026-11-18.lp.gz"])
        self.assertEqual(self.count(), 8)
        self.assertEqual(migrate.import_pending(self.conn, self.gh, self.tmp), [])
        self.put("data-2026-11", "hvv-b.lp.gz", [self.stop(9, t0 + 20)])
        self.assertEqual([a for a, _ in migrate.import_pending(self.conn, self.gh, self.tmp)], ["data-2026-11/hvv-b.lp.gz"])
        self.assertEqual(self.count(), 9)

    def test_a_broken_package_does_not_stop_the_others(self):
        self.put("data-2026-11", "hvv-a.lp.gz", [self.stop(1, 1_790_000_000)])
        rel = self.state["releases"][0]
        self.state["assets"].append({"id": 999, "release": rel["id"], "name": "hvv-kaputt.lp.gz"})
        self.state["blobs"][999] = b"kein gzip"
        done = migrate.import_pending(self.conn, self.gh, self.tmp)
        self.assertEqual([a for a, _ in done], ["data-2026-11/hvv-a.lp.gz"])
        self.assertEqual(self.count(), 1)

    def test_a_pi_spool_package_arrives_in_the_database(self):
        sp = SpoolWriter(self.tmp / "spool", clock=lambda: at(10))
        sp.write([self.stop(7, 1_790_000_000)])
        sp.clock = lambda: at(12)
        Shipper(sp, self.gh).ship_once()
        done = migrate.import_pending(self.conn, self.gh, self.tmp)
        self.assertEqual(done[0][1]["points"], 1)
        with self.conn.cursor() as cur:
            cur.execute("select delay_s from hvv_stop")
            self.assertEqual(cur.fetchall(), [(7,)])


if __name__ == "__main__":
    unittest.main()
