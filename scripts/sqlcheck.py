"""Fuehrt eine Grafana-SQL-Abfrage gegen die (Test-)Datenbank aus und zeigt Spalten und Zeilen: zur Pruefung der Dashboards.

Grafana-Makros werden wie im PostgreSQL-Datenquellentyp ersetzt: $__timeFilter(spalte), $__timeFrom(), $__timeTo(), $__interval, $__interval_ms,
$__timeGroup(spalte, 5m) und $__timeGroupAlias(...); Dashboard-Variablen mit --var name=wert ($name und ${name}).
Aufruf: python3 scripts/sqlcheck.py [--from ISO] [--to ISO] [--var k=v ...] [--rows N] [--format table|time_series] < abfrage.sql
Umgebung: HVV_TEST_DB oder HVV_DB_URL (postgresql://...).
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone


def expand(sql: str, start: datetime, stop: datetime, variables: dict[str, str]) -> str:
    q = lambda d: "'" + d.strftime("%Y-%m-%dT%H:%M:%SZ") + "'"
    span = (stop - start).total_seconds()
    interval = max(int(span / 500), 1)
    sql = re.sub(r"\$__timeFilter\(([^)]+)\)", lambda m: f"{m.group(1)} BETWEEN {q(start)} AND {q(stop)}", sql)
    sql = sql.replace("$__timeFrom()", q(start)).replace("$__timeTo()", q(stop))
    sql = sql.replace("$__interval_ms", str(interval * 1000)).replace("$__interval", f"'{interval}s'")

    def group(m, alias):
        every = m.group(2).strip().strip("'\"")
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[every[-1]] * int(every[:-1])
        expr = f"floor(extract(epoch from {m.group(1)})/{seconds})*{seconds}"
        return f"{expr} AS \"time\"" if alias else expr

    sql = re.sub(r"\$__timeGroupAlias\(([^,]+),\s*([^)]+)\)", lambda m: group(m, True), sql)
    sql = re.sub(r"\$__timeGroup\(([^,]+),\s*([^)]+)\)", lambda m: group(m, False), sql)
    for name, value in variables.items():
        sql = sql.replace("${" + name + "}", value).replace("$" + name, value)
    return sql


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="stop")
    ap.add_argument("--var", action="append", default=[])
    ap.add_argument("--rows", type=int, default=8)
    ap.add_argument("--format", default="table")
    args = ap.parse_args()
    stop = datetime.fromisoformat(args.stop.replace("Z", "+00:00")) if args.stop else datetime.now(timezone.utc)
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")) if args.start else stop - timedelta(days=30)
    variables = dict(v.split("=", 1) for v in args.var)
    sql = expand(sys.stdin.read(), start, stop, variables)
    import psycopg

    with psycopg.connect(os.environ.get("HVV_TEST_DB") or os.environ["HVV_DB_URL"], autocommit=True) as conn, conn.cursor() as cur:
        try:
            cur.execute(sql)
        except psycopg.Error as exc:
            print("FEHLER:", str(exc).strip().splitlines()[0])
            print("SQL:", sql.strip()[:600])
            return 1
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    print(f"{len(rows)} Zeilen, Spalten: {cols}")
    if args.format == "time_series" and cols and cols[0] != "time":
        print("WARNUNG: im Format time_series muss die erste Spalte `time` heissen")
    for row in rows[: args.rows]:
        print("  ", tuple(round(v, 4) if isinstance(v, float) else str(v)[:40] if not isinstance(v, (int, type(None))) else v for v in row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
