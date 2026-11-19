"""TimescaleDB (PostgreSQL): Schema, Einlesen von Line-Protocol und die Abfragen des Routenplaners.

Je Art von Messung eine Hypertabelle mit Tags als Textspalten und typisierten Feldern:

  hvv_stop          Halt fuer Halt (S, U, X, Regional, Bus 5, Faehre): Spalte `m` = Name der Influx-Messung
  hvv_event         Abfahrten mit Ausfall und Grund (hvv_stop_event, hvv_sweep_event)
  hvv_announcement  Stoerungsmeldungen
  hvv_api_request   GTI-Aufrufe (Latenz, Bytes)
  hvv_train_stop / hvv_train_pos   Zuglaeufe Stade-Hamburg
  hvv_other         alles Unbekannte (Tags und Felder als JSON), damit beim Einlesen nie etwas verloren geht

Schluessel ist (Zeit, `m`, alle Tags): ein erneut gelesener Punkt ueberschreibt den alten, wie in Influx. Das Einlesen ist damit
wiederholbar (Ueberlappung von Historie und Live-Daten, doppelt geholte Dateien).
"""

import datetime as dt
import json
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from .lineproto import LineError, parse_line

log = logging.getLogger("hvv_gti.tsdb")

UTC = dt.timezone.utc
BATCH = 20000


@dataclass(frozen=True)
class Table:
    name: str
    measurements: frozenset[str]
    tags: tuple[str, ...]
    fields: dict[str, str]  # Spalte -> SQL-Typ; die Influx-Feldnamen stehen in COLUMN_OF
    has_m: bool = False


STOP = ("hvv_network_stop", "hvv_bus_stop", "hvv_ferry_stop", "hvv_re_stop", "hvv_regional_stop", "hvv_xbus_stop")
TABLES = [
    Table("hvv_stop", frozenset(STOP), ("mode", "line", "station", "direction", "kind"), {"delay_s": "bigint", "actual": "bigint"}, True),
    Table("hvv_event", frozenset({"hvv_stop_event", "hvv_sweep_event"}), ("destination", "direction", "kind", "line", "origin", "route", "station"),
          {"cancelled": "bigint", "delay_s": "bigint", "extra": "bigint", "reason_text": "text", "reason_types": "text", "service_id": "bigint"}, True),
    Table("hvv_announcement", frozenset({"hvv_announcement"}), ("announcement_id", "line", "planned", "reason"), {"summary": "text", "version": "bigint"}),
    Table("hvv_api_request", frozenset({"hvv_api_request"}), ("endpoint", "status"), {"bytes": "bigint", "latency_ms": "bigint"}),
    Table("hvv_train_pos", frozenset({"hvv_train_pos"}), ("direction", "journey", "line"),
          {"delay_min": "bigint", "next": "text", "next_eta": "bigint", "pos": "double precision", "state": "text", "train": "text", "where_": "text"}),
    Table("hvv_train_stop", frozenset({"hvv_train_stop"}), ("direction", "journey", "line", "station"),
          {"actual": "bigint", "arrival": "bigint", "delay_min": "bigint", "idx": "bigint", "train": "text"}),
]
COLUMN_OF = {"where": "where_"}  # Influx-Feldname -> Spalte (where ist in SQL reserviert)
FIELD_OF = {v: k for k, v in COLUMN_OF.items()}
BY_MEASUREMENT = {m: t for t in TABLES for m in t.measurements}


def ddl(table: Table) -> str:
    cols = ["time timestamptz not null"]
    if table.has_m:
        cols.append("m text not null")
    cols += [f"{t} text not null default ''" for t in table.tags]
    cols += [f"{c} {typ}" for c, typ in table.fields.items()]
    key = ["time"] + (["m"] if table.has_m else []) + list(table.tags)
    cols.append(f"primary key ({', '.join(key)})")
    return f"create table if not exists {table.name} (\n  " + ",\n  ".join(cols) + "\n)"


EXTRA_DDL = [
    "create table if not exists hvv_other (time timestamptz not null, m text not null, tags jsonb not null default '{}', fields jsonb not null default '{}')",
    "create table if not exists hvv_import_log (asset text primary key, imported_at timestamptz not null default now(), points bigint not null, skipped bigint not null default 0)",
    "create index if not exists hvv_stop_lookup on hvv_stop (m, line, station, time desc)",
    "create index if not exists hvv_stop_line_time on hvv_stop (m, line, time desc)",
    "create index if not exists hvv_event_lookup on hvv_event (line, time desc)",
    "create index if not exists hvv_train_stop_lookup on hvv_train_stop (line, station, direction, time desc)",
    "create index if not exists hvv_other_time on hvv_other (m, time desc)",
]
# Ab einem Alter von COMPRESS_AFTER werden Chunks komprimiert (ein Chunk = 7 Tage); der Routenplaner liest 30 Tage, Auswertungen alles.
COMPRESS = {"hvv_stop": "m, line, station", "hvv_event": "m, line, station", "hvv_api_request": "endpoint", "hvv_train_pos": "line", "hvv_train_stop": "line, station"}
COMPRESS_AFTER = "45 days"


def ensure_schema(conn, timescale: bool = True) -> bool:
    """Tabellen anlegen (idempotent). Mit TimescaleDB werden Hypertabellen und Kompression eingerichtet; gibt zurueck, ob sie aktiv ist."""
    cur = conn.cursor()
    for table in TABLES:
        cur.execute(ddl(table))
    for stmt in EXTRA_DDL[:2]:
        cur.execute(stmt)
    active = False
    if timescale:
        cur.execute("select count(*) from pg_available_extensions where name = 'timescaledb'")
        if cur.fetchone()[0]:
            cur.execute("create extension if not exists timescaledb")
            for table in [t.name for t in TABLES] + ["hvv_other"]:
                cur.execute("select create_hypertable(%s, 'time', chunk_time_interval => interval '7 days', if_not_exists => true, migrate_data => true)", (table,))
            for table, segment in COMPRESS.items():
                order = "time desc"
                cur.execute(f"alter table {table} set (timescaledb.compress, timescaledb.compress_segmentby = '{segment}', timescaledb.compress_orderby = '{order}')")
                cur.execute("select add_compression_policy(%s, %s::interval, if_not_exists => true)", (table, COMPRESS_AFTER))
            active = True
    for stmt in EXTRA_DDL[2:]:
        cur.execute(stmt)
    conn.commit()
    return active


def _time(ts_ns: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts_ns // 1_000_000_000, tz=UTC) + dt.timedelta(microseconds=(ts_ns % 1_000_000_000) // 1000)


def _coerce(value, sql_type: str):
    """Feldwert in den Spaltentyp bringen (Influx kennt je Feld einen festen Typ, aber ein Export kann Zahlen als 12.0 schreiben)."""
    if value is None:
        return None
    if sql_type == "bigint":
        return int(round(value)) if isinstance(value, (int, float)) else None
    if sql_type == "double precision":
        return float(value) if isinstance(value, (int, float)) else None
    return value if isinstance(value, str) else str(value)


class Loader:
    """Liest Line-Protocol-Zeilen in die Tabellen: Zeilen gruppieren, je Schluessel nur die letzte behalten, per COPY + Upsert schreiben."""

    def __init__(self, conn):
        self.conn = conn
        self.rows: dict[str, dict[tuple, tuple]] = {}
        self.other: list[tuple] = []
        self.counts = {"points": 0, "skipped": 0}

    def add(self, line: str) -> None:
        line = line.strip()
        if not line or line.startswith("#"):
            return
        try:
            measurement, tags, fields, ts = parse_line(line)
        except LineError as exc:
            self.counts["skipped"] += 1
            log.warning("Zeile uebersprungen (%s): %.120s", exc, line)
            return
        if ts is None:
            self.counts["skipped"] += 1
            return
        self.counts["points"] += 1
        table = BY_MEASUREMENT.get(measurement)
        columns = {COLUMN_OF.get(k, k) for k in fields}
        if table is None or not set(tags) <= set(table.tags) or not columns <= set(table.fields):
            self.other.append((_time(ts), measurement, json.dumps(tags, ensure_ascii=False), json.dumps(fields, ensure_ascii=False)))
            return
        key = (_time(ts), *( [measurement] if table.has_m else [] ), *(tags.get(t, "") for t in table.tags))
        values = tuple(_coerce(fields.get(FIELD_OF.get(c, c)), typ) for c, typ in table.fields.items())
        self.rows.setdefault(table.name, {})[key] = values
        if len(self.rows[table.name]) >= BATCH:
            self._flush(table)

    def _flush(self, table: Table) -> None:
        rows = self.rows.pop(table.name, None)
        if not rows:
            return
        key_cols = ["time"] + (["m"] if table.has_m else []) + list(table.tags)
        cols = key_cols + list(table.fields)
        stage = f"_stage_{table.name}"
        with self.conn.cursor() as cur:
            cur.execute(f"create temp table if not exists {stage} (like {table.name} including defaults) on commit delete rows")
            with cur.copy(f"copy {stage} ({', '.join(cols)}) from stdin") as copy:
                for key, values in rows.items():
                    copy.write_row([*key, *values])
            updates = ", ".join(f"{c} = excluded.{c}" for c in table.fields)
            cur.execute(f"insert into {table.name} ({', '.join(cols)}) select {', '.join(cols)} from {stage} on conflict ({', '.join(key_cols)}) do update set {updates}")
        self.conn.commit()

    def flush(self) -> dict:
        for table in TABLES:
            self._flush(table)
        if self.other:
            with self.conn.cursor() as cur:
                cur.executemany("insert into hvv_other (time, m, tags, fields) values (%s, %s, %s::jsonb, %s::jsonb)", self.other)
            self.conn.commit()
            self.other = []
        return dict(self.counts)

    def load(self, lines: Iterable[str]) -> dict:
        for line in lines:
            self.add(line)
        return self.flush()


# ---- Abfragen des Routenplaners (gleiche Schnittstelle wie planner.InfluxReader) ----


class TimescaleReader:
    """Verspaetungsverteilungen fuer die Zuverlaessigkeit der Verbindungen. Fehler kommen als OSError, wie beim Influx-Leser."""

    LOOKBACK = "30 days"
    RANK_LOOKBACK = "7 days"

    def __init__(self, dsn: str, timeout: float = 20.0):
        self.dsn, self.timeout = dsn, timeout
        self._conn = None
        self._lock = threading.Lock()

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, autocommit=True, connect_timeout=int(self.timeout), options=f"-c statement_timeout={int(self.timeout * 1000)}")

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        import psycopg

        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None or self._conn.closed:
                        self._conn = self._connect()
                    with self._conn.cursor() as cur:
                        cur.execute(sql, params)
                        return cur.fetchall()
                except psycopg.OperationalError as exc:
                    self._conn = None
                    if attempt == 2:
                        raise OSError(f"Datenbank: {exc}") from exc
                except psycopg.Error as exc:
                    raise OSError(f"Datenbank: {exc}") from exc
        return []

    def delays(self, measurement: str, line: str, station: str | None) -> list[tuple[float, float]]:
        """(Zeitstempel, Verspaetung in s) der letzten 30 Tage; ohne `station` ueber die ganze Linie (rund jede 10. Messung), hoechstens 6000."""
        if station:
            rows = self.query(
                "select extract(epoch from time), delay_s from hvv_stop where m = %s and line = %s and station = %s and delay_s is not null "
                f"and time > now() - interval '{self.LOOKBACK}' order by time desc limit 6000", (measurement, line, station))
        else:
            rows = self.query(
                "select extract(epoch from time), delay_s from hvv_stop where m = %s and line = %s and delay_s is not null "
                f"and time > now() - interval '{self.LOOKBACK}' and random() < 0.1 order by time desc limit 6000", (measurement, line))
        return [(float(t), float(v)) for t, v in rows]

    def train_delays(self, line: str, station: str, direction: str) -> list[tuple[float, float]]:
        rows = self.query(
            "select extract(epoch from time), delay_min * 60 from hvv_train_stop where line = %s and station = %s and direction = %s and delay_min is not null "
            f"and time > now() - interval '{self.LOOKBACK}' order by time desc limit 6000", (line, station, direction))
        return [(float(t), float(v)) for t, v in rows]

    def cancel_rate(self, line: str) -> tuple[int, float] | None:
        rows = self.query(
            "select count(*), avg(case when cancelled > 0 then 1.0 else 0.0 end) from (select cancelled from hvv_event where line = %s and cancelled is not null "
            f"and time > now() - interval '{self.LOOKBACK}' limit 20000) q", (line,))
        n, rate = rows[0]
        return (int(n), float(rate)) if n else None

    def frequent(self, limit: int, per_line: int = 5) -> list[tuple[str, str, str]]:
        """Je Linie die `per_line` meistgemessenen Halte der letzten 7 Tage, hoechstens `limit` (Messung, Linie, Halt) insgesamt."""
        rows = self.query(
            "select m, line, station from ("
            " select m, line, station, n, row_number() over (partition by m, line order by n desc) as r from ("
            f"  select m, line, station, count(*) as n from hvv_stop where delay_s is not null and time > now() - interval '{self.RANK_LOOKBACK}' and station <> '' group by 1, 2, 3) c"
            ") ranked where r <= %s order by n desc limit %s", (per_line, limit))
        return [(m, line, station) for m, line, station in rows]
