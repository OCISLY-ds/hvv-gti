# Umzug des Routenplaners auf eine stärkere Maschine (TimescaleDB statt InfluxDB)

Der **Pi bleibt wie er ist**: Collector, InfluxDB und Grafana mit allen Dashboards laufen unverändert weiter. Die neue Maschine bekommt nur den
**Routenplaner** (mit 3D-Bahnhöfen) und seine eigene Datenbank; Grafana gibt es dort nicht.

```
   Raspberry Pi (bleibt)                      GitHub (privates Repo)                  Neue Maschine
 ┌──────────────────────────┐               ┌────────────────────────┐        ┌─────────────────────────────────┐
 │ hvv-collector            │  Pakete alle  │ Releases:              │ holt   │ importer ──► TimescaleDB        │
 │  fragt die GTI ab        │  6 h (gzip)   │  data-YYYY-MM  (live)  │ alle   │                  ▲      ▲       │
 │  schreibt Spool-Dateien  ├──────────────►│  data-history (einmal) │◄───────┤ map (Planer, 3D) ┘      │       │
 │ hvv-shipper              │               │  assets-map (Kacheln)  │ 5 min  │                                 │
 │ Influx + Grafana bleiben │               └────────────────────────┘        │ tunnel (Cloudflare)             │
 └──────────────────────────┘                                                 └─────────────────────────────────┘
```

Die beiden Maschinen sprechen **nie direkt miteinander**. Der Pi sammelt unabhängig weiter (er braucht nur die GTI-Zugangsdaten und GitHub),
die neue Maschine zieht die Daten aus den Releases. Fällt eine Seite aus, läuft die andere weiter: der Pi sammelt in Dateien und holt das
Verschicken nach, die neue Maschine liest ein, was da ist. Auf GitHub liegen **nur Messwerte** (Verspätungen, Haltestellennamen, Störungstexte,
Aufrufstatistik), keine Zugangsdaten und keine .env.

## 0. Voraussetzungen

| | |
|---|---|
| Neue Maschine | Linux mit Docker Compose, mindestens 4 GB RAM (empfohlen 8 GB+), SSD. x86_64 oder arm64 (alle Images sind Multi-Arch). Benutzer mit UID 1000 (sonst `user:` in der Compose-Datei anpassen). |
| GitHub | Privates Repo `OCISLY-ds/hvv-gti`. Zwei **fine-grained Personal Access Tokens**, nur für dieses Repo: Pi = *Contents: Read and write*, neue Maschine = *Contents: Read-only*. (Releases brauchen Schreibrecht auf Contents.) |
| GTI | Dieselben `HVV_GTI_USER`/`HVV_GTI_PASSWORD` wie auf dem Pi. Beide Maschinen teilen sich das Limit von 1 Aufruf/s (Sammler ca. 0,2/s): `HVV_MIN_REQUEST_INTERVAL` nicht senken. |

## 1. Pi: Spool und Shipper einschalten (parallel zu Influx)

In der `.env` auf dem Pi ergänzen (siehe `.env.example`):

```
HVV_SINKS=influx,spool
GITHUB_REPO=OCISLY-ds/hvv-gti
GITHUB_TOKEN=<Token mit Contents: Read and write>
```

```bash
cd ~/projects/hvv-gti
git pull
sudo docker compose up -d --build hvv-collector              # schreibt jetzt zusätzlich nach data/spool/
sudo docker compose --profile ship up -d --build hvv-shipper  # verschickt alle 6 h ein Paket
sudo docker compose logs -f hvv-shipper                       # nach einer Stunde: "N Stunden als hvv-... verschickt"
```

Solange beide Senken laufen, bleibt Influx vollständig (Rückweg jederzeit möglich). Die Pakete landen im Release `data-YYYY-MM`.

## 2. Pi: Historie und Dateien hochladen (einmalig)

```bash
cd ~/projects/hvv-gti
python3 -m hvv_gti export-history --days 60     # liest den Influx-Bucket tageweise, lädt hist-YYYY-MM-DD.lp.gz in das Release data-history
python3 -m hvv_gti push-assets                  # Kartenkacheln (66 MB), gelernte Gleise, Signale, Logos -> Release assets-map
```

`export-history` ist wiederholbar (vorhandene Tage werden übersprungen) und braucht keinen Neustart. Die Historie vor dem 7.11.2026 enthält durch einen
damaligen Fehler verfälschte Verspätungen bei verspäteten Zügen (Geisterfahrten, siehe README); sie wird trotzdem übernommen.

## 3. Neue Maschine aufsetzen

```bash
git clone git@github.com:OCISLY-ds/hvv-gti.git && cd hvv-gti && git checkout dev
cd deploy/newhost
cp .env.example .env && nano .env          # Passwörter, GitHub-Token (Read-only), GTI-Zugang, ggf. DB_MEMORY/DB_CPUS
mkdir -p ../../data ../../map/data
docker compose up -d --build timescaledb dbinit
docker compose run --rm importer pull-assets     # holt Kacheln, Gleise, Logos nach map/data und data/icons
docker compose up -d                              # importer, map
docker compose logs -f importer                   # liest data-history und alle data-YYYY-MM Pakete ein
```

Prüfen:

```bash
docker compose exec timescaledb psql -U postgres hvv -c "select asset, points, imported_at from hvv_import_log order by imported_at"
docker compose exec timescaledb psql -U postgres hvv -c "select m, count(*), min(time), max(time) from hvv_stop group by m"
curl -s localhost:8090/healthz ; curl -s 'localhost:8090/api/stations?q=Hbf' | head -c 200
```

Ein Datenpunkt, der in Historie **und** Live-Paket steckt, wird nur einmal gespeichert (Schlüssel = Zeit + alle Tags, der spätere überschreibt).
Der Importer wiederholt fehlgeschlagene Pakete von selbst; `hvv_import_log` zeigt, was drin ist.

## 4. Umschalten

1. Auf der neuen Maschine alles prüfen: Planer unter `http://<host>:8090/`.
2. Öffentlicher Zugang: `TUNNEL_TOKEN` in `.env`, dann `docker compose --profile tunnel up -d tunnel`. Danach auf dem **Pi** `hvv-tunnel` stoppen
   (`sudo docker compose stop hvv-tunnel hvv-proxy`), damit nur ein Connector antwortet.
3. Auf dem Pi läuft alles weiter (Influx und Grafana bleiben vollständig, `HVV_SINKS=influx,spool`). Nur den Planer dort abschalten, wenn er nicht mehr gebraucht wird
   (`sudo docker compose stop hvv-map`).

## 5. Betrieb

| | |
|---|---|
| Sicherung | `docker compose exec -T timescaledb pg_dump -U postgres -Fc hvv > hvv-$(date +%F).dump` (Rückspielen: `pg_restore -U postgres -d hvv --clean`). Die Rohdaten stehen zusätzlich als Pakete in den GitHub-Releases. |
| Neu aufbauen | Datenbank löschen (`docker compose down -v`), neu starten: der Importer liest alle Pakete von vorn ein. |
| Speicher | Ein Chunk = 7 Tage, Kompression ab 45 Tagen (`COMPRESS_AFTER` in `hvv_gti/tsdb.py`). Es gibt keine automatische Löschfrist; gewünscht: `select add_retention_policy('hvv_stop', interval '180 days');` |
| Verzögerung | Statistik hinkt dem Pi um höchstens `HVV_SHIP_INTERVAL` (6 h) + `HVV_IMPORT_INTERVAL` (5 min) hinterher. Für die Zuverlässigkeits-Prozente des Planers (30 Tage Rückblick) ist das unerheblich. Kürzer: `HVV_SHIP_INTERVAL=3600` auf dem Pi (24 Pakete/Tag, bis ca. 40 Tage je Release). |
| GitHub-Grenzen | Pro Release höchstens 1000 Anhänge (bei 6-h-Paketen ca. 120 je Monat, deshalb ein Release je Monat); je Anhang höchstens 2 GB. |
| Aufrufbudget | Planer und Sammler nutzen dasselbe GTI-Konto. Der Planer ruft nur auf Anfrage ab (Stationssuche, Routen, Abfahrten), begrenzt durch `--planner-rate`. |

## 6. Rückweg

Der Pi ist nie umgestellt worden: `hvv-map` ohne `HVV_DB_URL` liest dort weiter aus Influx. Die neue Maschine einfach abschalten.

## 7. Sicherheit

* `.env`-Dateien nie committen oder hochladen (sie sind in `.gitignore`). Tokens nur mit den genannten Rechten und nur für dieses Repo.
* Die Datenbank hört nur auf `127.0.0.1` der neuen Maschine. Der Planer liest mit der Rolle `hvv_ro` (nur SELECT).
* Der Importer liest ausschließlich Anhänge der Releases `data-*` und nur Zeilen im Line-Protocol-Format in feste Tabellen; Unbekanntes landet als JSON in
  `hvv_other` und wird nie ausgeführt. `pull-assets` entpackt nur erlaubte Pfade (`map/data/…`, `data/icons/…`).
