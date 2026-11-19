import argparse
import logging
import signal
import statistics
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import collector
from .client import TZ, GtiClient, GtiError
from .config import Config, ConfigError, load_dotenv
from .influx import InfluxWriter
from .spool import MultiWriter

HAMBURG_BBOX = "9.70,53.35,10.35,53.75"


def _client(cfg: Config) -> GtiClient:
    cfg.require_gti()
    return GtiClient(
        cfg.gti_user,
        cfg.gti_password,
        base_url=cfg.gti_base_url,
        version=cfg.api_version,
        min_interval=cfg.min_request_interval,
    )


def cmd_check_name(cfg: Config, args: argparse.Namespace) -> int:
    for r in _client(cfg).check_name(args.name, args.max):
        print(f"{r.get('id', '-'):<22} {r.get('type', '-'):<8} {r.get('combinedName', r.get('name', '-'))}")
    return 0


def cmd_departures(cfg: Config, args: argparse.Namespace) -> int:
    client = _client(cfg)
    stop = collector.resolve_station(client, args.station, args.station_id)
    reference = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    response = client.departure_list(
        stop.sd_name,
        reference,
        max_list=args.max_list,
        max_time_offset=args.offset,
        departure=not args.arrivals,
        service_types=[x for x in args.types.split(",") if x] or None,
    )
    for dep in response.get("departures", []):
        service = dep.get("line", {})
        if args.line and service.get("name") != args.line:
            continue
        planned = (reference + timedelta(minutes=int(dep.get("timeOffset", 0)))).astimezone(TZ)
        flags = ("AUSFALL " if dep.get("cancelled") else "") + ("EXTRA" if dep.get("extra") else "")
        delay_min = (dep.get("delay") or 0) / 60
        note = collector.reason_fields(dep).get("reason_text")
        print(f"{planned:%H:%M}  {service.get('name', '?'):<6} {delay_min:+5.1f} min  {service.get('direction', '')}  {flags}" + (f"  [{note}]" if note else ""))
    return 0


def cmd_collect(cfg: Config, args: argparse.Namespace) -> int:
    client = _client(cfg)
    writer = None
    if not args.dry_run:
        unknown = set(cfg.sinks) - {"influx", "spool"}
        if unknown or not cfg.sinks:
            raise ConfigError(f"HVV_SINKS: unbekannte Senke {sorted(unknown) or '(leer)'}; erlaubt sind influx und spool")
        sinks = []
        if "influx" in cfg.sinks:
            cfg.require_influx()
            sinks.append(InfluxWriter(cfg.influx_url, cfg.influx_org, cfg.influx_bucket, cfg.influx_token))
        if "spool" in cfg.sinks:
            from .spool import SpoolWriter

            sinks.append(SpoolWriter(cfg.spool_dir))
        writer = sinks[0] if len(sinks) == 1 else MultiWriter(sinks)
    stop_event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop_event.set())
    return collector.run(cfg, client, writer, stop_event, once=args.once)


def _stop_on_signal() -> threading.Event:
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    return stop


def cmd_ship(cfg: Config, args: argparse.Namespace) -> int:
    from .github import Releases
    from .spool import Shipper, SpoolWriter

    cfg.require_github()
    shipper = Shipper(SpoolWriter(cfg.spool_dir), Releases(cfg.github_repo, cfg.github_token), cfg.ship_retention_days)
    if args.once:
        print(shipper.ship_once() or "nichts zu verschicken")
        return 0
    shipper.run(_stop_on_signal(), cfg.ship_interval)
    return 0


def cmd_export_history(cfg: Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from .github import Releases
    from .migrate import InfluxExport, export_history

    cfg.require_influx()
    token = cfg.influx_read_token or cfg.influx_token
    files = export_history(InfluxExport(cfg.influx_url, cfg.influx_org, cfg.influx_bucket, token), args.days, Path(args.out))
    print(f"{len(files)} Tagesdateien in {args.out}")
    if args.no_upload:
        return 0
    cfg.require_github()
    github = Releases(cfg.github_repo, cfg.github_token)
    release = github.release("data-history")
    sent = sum(1 for f in files if github.upload(release, f))
    print(f"{sent} neue Dateien in das Release data-history geladen ({len(files) - sent} waren schon da)")
    return 0


def cmd_push_assets(cfg: Config, args: argparse.Namespace) -> int:
    import tempfile
    from pathlib import Path

    from .github import Releases
    from .migrate import pack_assets

    cfg.require_github()
    github = Releases(cfg.github_repo, cfg.github_token)
    with tempfile.TemporaryDirectory() as tmp:
        name = f"hvv-assets-{datetime.now(timezone.utc):%Y%m%d}.tar.gz"
        archive = Path(tmp) / name
        names = pack_assets(Path(args.root), archive)
        print(f"{len(names)} Eintraege gepackt ({archive.stat().st_size // 1024 // 1024} MB): " + ", ".join(names))
        sent = github.upload(github.release("assets-map"), archive, name)
    print("hochgeladen" if sent else "dieses Paket gibt es heute schon")
    return 0


def cmd_pull_assets(cfg: Config, args: argparse.Namespace) -> int:
    import tempfile
    from pathlib import Path

    from .github import Releases
    from .migrate import unpack_assets

    cfg.require_github()
    github = Releases(cfg.github_repo, cfg.github_token)
    release = github.release("assets-map", create=False)
    assets = sorted(github.assets(release), key=lambda a: a["name"]) if release else []
    if not assets:
        print("Kein Paket im Release assets-map", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / assets[-1]["name"]
        github.download(assets[-1], archive)
        done = unpack_assets(archive, Path(args.root))
    print(f"{len(done)} Dateien aus {assets[-1]['name']} nach {args.root} entpackt")
    return 0


def _connect(cfg: Config):
    import psycopg

    cfg.require_db()
    return psycopg.connect(cfg.db_url)


def cmd_db_init(cfg: Config, args: argparse.Namespace) -> int:
    from .tsdb import ensure_schema

    with _connect(cfg) as conn:
        print("TimescaleDB aktiv" if ensure_schema(conn) else "Schema angelegt (ohne TimescaleDB-Erweiterung)")
    return 0


def cmd_import_file(cfg: Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from .migrate import import_file
    from .tsdb import ensure_schema

    with _connect(cfg) as conn:
        ensure_schema(conn)
        for name in args.files:
            counts = import_file(conn, Path(name))
            print(f"{name}: {counts['points']} Punkte, {counts['skipped']} uebersprungen")
    return 0


def cmd_import_github(cfg: Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from .github import Releases
    from .migrate import import_pending, run_importer
    from .tsdb import ensure_schema

    cfg.require_github()
    cfg.require_db()
    github = Releases(cfg.github_repo, cfg.github_token)
    if args.once:
        with _connect(cfg) as conn:
            ensure_schema(conn)
            done = import_pending(conn, github, Path(args.tmp) if args.tmp else None)
        print(f"{len(done)} Pakete eingelesen: " + ", ".join(f"{a} ({c['points']})" for a, c in done))
        return 0
    run_importer(cfg.db_url, github, _stop_on_signal(), cfg.import_interval, Path(args.tmp) if args.tmp else None)
    return 0


def cmd_probe_vehicles(cfg: Config, args: argparse.Namespace) -> int:
    client = _client(cfg)
    lon1, lat1, lon2, lat2 = (float(x) for x in args.bbox.split(","))
    begin = int(datetime.now(timezone.utc).timestamp())
    types = [t for t in args.types.split(",") if t] or None
    response = client.get_vehicle_map(
        (lon1, lat1), (lon2, lat2), begin, begin + args.window, realtime=not args.schedule, vehicle_types=types
    )
    journeys = response.get("journeys", [])
    segments = [s for j in journeys for s in j.get("segments", [])]
    delays = [s["realtimeDelay"] for s in segments if s.get("realtimeDelay") is not None]
    call = client.call_log[-1]
    print(f"Latenz {call.latency_s:.2f}s, Antwort {call.response_bytes / 1024:.0f} KiB")
    print(f"Fahrten {len(journeys)}, Segmente {len(segments)}, mit Echtzeit {sum(1 for j in journeys if j.get('realtime'))}")
    if journeys:
        print(f"Bytes pro Fahrt ~{call.response_bytes // len(journeys)}")
    if delays:
        print(f"realtimeDelay (min): median {statistics.median(delays)}, max {max(delays)}, Segmente mit Wert {len(delays)}")
    for vehicle_type, n in Counter(j.get("vehicleType") for j in journeys).most_common():
        print(f"  {vehicle_type:<12} {n}")
    line_filter = args.line
    if line_filter:
        hits = [j for j in journeys if j.get("line", {}).get("name") == line_filter]
        print(f"Linie {line_filter}: {len(hits)} Fahrten")
        for j in hits[:10]:
            for s in j.get("segments", [])[:1]:
                print(f"  {s.get('startStationName')} -> {s.get('endStationName')} (Ziel {s.get('destination')}), Verspaetung {s.get('realtimeDelay')} min")
    return 0


def cmd_icons(cfg: Config, args: argparse.Namespace) -> int:
    from .icons import IconStore

    store = IconStore(args.dir, _client(cfg))
    names = [x for x in args.names.split(",") if x] or None
    done, missing = store.prefetch(names, formats=tuple(args.formats.split(",")))
    print(f"{done} Icons geladen nach {args.dir}")
    if missing:
        print("Ohne Icon: " + ", ".join(missing))
    return 0


def _background(name: str, fn, *args) -> None:
    def run() -> None:
        try:
            fn(*args)
        except Exception:  # der Hintergrundlauf darf die Karte nie beenden
            logging.getLogger("hvv_gti").exception("%s fehlgeschlagen", name)

    threading.Thread(target=run, name=name, daemon=True).start()


def cmd_serve_map(cfg: Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from . import web
    from .icons import IconStore

    client = _client(cfg)
    data_dir = Path(args.data_dir)
    bbox = tuple(float(x) for x in args.bbox.split(","))
    types = ("S_BAHN", "U_BAHN", "R_BAHN") + (() if args.no_xbus else ("XPRESSBUS",))
    feed = web.VehicleFeed(client, data_dir, refresh_s=args.refresh, window_s=args.window, bbox=bbox, vehicle_types=types)
    from .planner import InfluxReader, Planner, Reliability

    token = cfg.influx_read_token or cfg.influx_token
    if cfg.db_url:  # neue Maschine: Statistik aus der TimescaleDB
        from .tsdb import TimescaleReader

        reader = TimescaleReader(cfg.db_url)
    else:
        reader = InfluxReader(cfg.influx_url, cfg.influx_org, cfg.influx_bucket, token) if token and cfg.influx_org else None
    reliability = Reliability(reader)
    from .stationmatch import StationModels
    from .web import SEED_DIR

    planner = Planner(client, reliability, StationModels.load(SEED_DIR / "stations"))
    planner.shaper = feed.shape
    icons = IconStore(args.icons_dir, client)
    app = web.MapApp(feed, icons, Path(args.static_dir), data_dir, planner, args.planner_rate, live=args.live_map)
    # Frischer Host: Gleise und Linienlogos (daraus die Linienfarben) im Hintergrund nachholen, die Karte laeuft sofort
    if args.live_map and args.learn_span and not feed.tracks:
        _background("Gleise vorlernen", feed.learn, args.learn_span)
    if not any(Path(args.icons_dir).glob("*.svg")):
        _background("Linienlogos laden", icons.prefetch)
    stop_event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop_event.set())
    if reader is not None and not args.no_prewarm:
        reliability.start_warming(stop_event)
    web.serve(app, args.host, args.port, stop_event)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hvv_gti", description="HVV Geofox GTI Werkzeuge")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-name", help="Haltestelle suchen und IDs anzeigen")
    p.add_argument("name")
    p.add_argument("--max", type=int, default=10)
    p.set_defaults(func=cmd_check_name)

    p = sub.add_parser("departures", help="Abfahrten/Ankuenfte einer Haltestelle anzeigen")
    p.add_argument("station")
    p.add_argument("--station-id")
    p.add_argument("--arrivals", action="store_true")
    p.add_argument("--line")
    p.add_argument("--types", default="", help="Verkehrsmittel, z.B. SBAHN,BUS (leer = alle)")
    p.add_argument("--max-list", type=int, default=20)
    p.add_argument("--offset", type=int, default=60)
    p.set_defaults(func=cmd_departures)

    p = sub.add_parser("collect", help="Puenktlichkeitsdaten in InfluxDB schreiben")
    p.add_argument("--once", action="store_true", help="nur ein Zyklus")
    p.add_argument("--dry-run", action="store_true", help="Line Protocol ausgeben statt zu schreiben")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("ship", help="Spool-Dateien als Datenpaket in ein GitHub-Release hochladen (Schleife oder --once)")
    p.add_argument("--once", action="store_true")
    p.set_defaults(func=cmd_ship)

    p = sub.add_parser("export-history", help="Historie aus InfluxDB als Tagesdateien exportieren und in das Release data-history laden")
    p.add_argument("--days", type=int, default=60, help="so viele Tage zurueck (Tage ohne Daten werden uebersprungen)")
    p.add_argument("--out", default="data/export")
    p.add_argument("--no-upload", action="store_true")
    p.set_defaults(func=cmd_export_history)

    p = sub.add_parser("push-assets", help="Kartenkacheln, gelernte Gleise und Logos (nicht in Git) als Paket in das Release assets-map laden")
    p.add_argument("--root", default=".")
    p.set_defaults(func=cmd_push_assets)

    p = sub.add_parser("pull-assets", help="das neueste Paket aus assets-map holen und entpacken")
    p.add_argument("--root", default=".")
    p.set_defaults(func=cmd_pull_assets)

    p = sub.add_parser("db-init", help="Tabellen in der TimescaleDB anlegen (idempotent)")
    p.set_defaults(func=cmd_db_init)

    p = sub.add_parser("import-file", help="Line-Protocol-Dateien (.lp/.lp.gz) direkt in die TimescaleDB einlesen")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_import_file)

    p = sub.add_parser("import-github", help="Datenpakete aus den GitHub-Releases data-* in die TimescaleDB einlesen (Schleife oder --once)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--tmp", default="")
    p.set_defaults(func=cmd_import_github)

    p = sub.add_parser("probe-vehicles", help="getVehicleMap einmal abfragen und Umfang messen")
    p.add_argument("--bbox", default=HAMBURG_BBOX, help="lon1,lat1,lon2,lat2 (WGS84)")
    p.add_argument("--window", type=int, default=120, help="Zeitfenster in Sekunden ab jetzt")
    p.add_argument("--types", default="", help="z.B. S_BAHN,R_BAHN (Pflicht: ohne Typ liefert die GTI nichts)")
    p.add_argument("--line", help="Linie, deren Fahrten aufgelistet werden")
    p.add_argument("--schedule", action="store_true", help="Fahrplan- statt Echtzeitdaten")
    p.set_defaults(func=cmd_probe_vehicles)

    p = sub.add_parser("icons", help="Linien-Logos vom Geofox-Icon-Service vorladen")
    p.add_argument("--dir", default="data/icons")
    p.add_argument("--names", default="", help="Komma-getrennte Linien, leer = alle Schienenlinien")
    p.add_argument("--formats", default="svg", help="svg,png")
    p.set_defaults(func=cmd_icons)

    p = sub.add_parser("serve-map", help="Routenplaner (und mit --live-map die Live-Karte) ausliefern")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--bbox", default="8.60,53.20,10.60,53.95", help="lon1,lat1,lon2,lat2 (Standard reicht bis Cuxhaven fuer den RE5 und deckt die X-Busse im Umland ab)")
    p.add_argument("--refresh", type=float, default=30.0, help="Sekunden zwischen GTI-Abfragen")
    p.add_argument("--window", type=int, default=300, help="Vorausschau in Sekunden")
    p.add_argument("--live-map", action="store_true", help="die Live-Karte (live.html) einschalten; ohne bleibt nur der Routenplaner und es gibt keine Fahrzeugabrufe")
    p.add_argument("--no-prewarm", action="store_true", help="haeufige Linien nicht im Hintergrund vorrechnen (Zuverlaessigkeit wird erst bei der ersten Anfrage aus Influx gelesen)")
    p.add_argument("--no-xbus", action="store_true", help="die X-Busse (Xpressbus) nicht abfragen und nicht auf der Karte anbieten")
    p.add_argument("--learn-span", type=int, default=5400, help="beim ersten Start ohne tracks.json Gleise aus +-N Sekunden um jetzt vorlernen (0 = aus)")
    p.add_argument("--planner-rate", type=int, default=60, help="Abfahrten/Routen/Stationssuche je Client-IP und Minute (0 = unbegrenzt)")
    p.add_argument("--static-dir", default="map/static")
    p.add_argument("--data-dir", default="map/data")
    p.add_argument("--icons-dir", default="data/icons")
    p.set_defaults(func=cmd_serve_map)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(Config.from_env(), args)
    except (ConfigError, GtiError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
