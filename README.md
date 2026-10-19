# hvv-gti

Werkzeuge für die HVV-Schnittstelle (Geofox GTI, <https://gti.geofox.de/>): Pünktlichkeitsstatistik der **S5 Stade ↔ Hamburg Hbf** in Grafana und eine Bewertung, ob eine Live-Karte mit Bussen und Bahnen machbar ist.

```
GTI (departureList) --> Collector (Docker) --> InfluxDB (Bucket hvv) --> Grafana-Dashboard
```

- `hvv_gti/` – Python (nur Standardbibliothek plus `tzdata`): GTI-Client mit HMAC-Signatur, Collector, Influx-Writer, CLI
- `grafana/dashboards/hvv-s5-puenktlichkeit.json` – Dashboard (Flux)
- `docs/live-map-evaluation.md` – Machbarkeit der Live-Karte
- `tests/` – 45 Tests gegen Fake-Server (`python3 -m unittest discover -s tests -t .`)

Branches: `main` = stabiler Stand, `dev` = Entwicklung.

## Einrichtung

### 1. GTI-Zugang

Zugangsdaten (Benutzer und Passwort) per Mail an **api@hochbahn.de** anfragen. Ohne sie läuft nichts gegen die echte API.

```bash
cp .env.example .env
# HVV_GTI_USER, HVV_GTI_PASSWORD und INFLUX_TOKEN eintragen
```

### 2. InfluxDB: Bucket und Tokens

Im Influx-UI (Port 8086, Organisation `Bergedorf`) einen Bucket `hvv` anlegen und zwei Tokens erzeugen:

- **Schreib-Token** für `hvv` → `INFLUX_TOKEN` in `.env` (Collector)
- **Lese-Token** für `hvv` → Grafana-Datenquelle

Eine Retention von 1–2 Jahren reicht: es entstehen nur ein paar tausend Punkte pro Tag.

### 3. Erst testen, dann dauerhaft laufen lassen

```bash
python3 -m hvv_gti check-name Stade                   # Haltestelle und ID finden
python3 -m hvv_gti departures Stade --line S5         # Abfahrten mit Verspätung
python3 -m hvv_gti departures Neugraben --line S5
python3 -m hvv_gti collect --dry-run --once           # gibt nur Line Protocol aus
```

Zum ersten echten Lauf (Stationsauflösung, Linienname `S5` und Tags `direction`/`origin` sind damit am 19.10.2026 bestätigt):

- Ankünfte: Der Account des Projektinhabers darf keine Ankunftstafeln abfragen (HTTP 403, `ArrivalList not allowed for user`). Deshalb ist `HVV_COLLECT_ARRIVALS=false` der Standard, und der Collector überspringt Ankünfte bei einem 403 von selbst. Es zählen nur Abfahrten.

### 4. Collector starten

Claude hat keine Docker-Rechte, daher mit `sudo`:

```bash
sudo docker compose up -d --build
sudo docker logs -f hvv-collector
```

Der Container läuft im Host-Netz (`INFLUX_URL=http://localhost:8086`, der Port ist im iot-stack veröffentlicht), ist auf 128 MB RAM begrenzt und rotiert seine Logs.

### 5. Grafana

1. Datenquelle **InfluxDB** hinzufügen: Query Language **Flux**, URL `http://influxdb:8086` (im iot-stack), Organisation `Bergedorf`, Default Bucket `hvv`, Lese-Token.
2. Dashboards → Import → `grafana/dashboards/hvv-s5-puenktlichkeit.json`, die eben angelegte Datenquelle wählen (Variable *Datenquelle*).

## Wie gemessen wird

- Alle 60 s wird `departureList` je Haltestelle abgefragt (Abfahrten, optional auch Ankünfte).
- Ein Ereignis ist eine Zeile `hvv_stop_event`: Tags `line`, `station`, `kind` (`departure`/`arrival`), `direction`, `origin`; Felder `delay_s`, `cancelled`, `extra`, `service_id`. Der **Zeitstempel ist die planmäßige Zeit**. Dasselbe Ereignis wird bei jeder Abfrage erneut geschrieben und überschreibt sich damit: gespeichert bleibt **die zuletzt gemeldete Verspätung vor der Fahrt**.
- **Pünktlich** heißt Verspätung unter der Schwelle (Standard 3 Minuten, im Dashboard wählbar). Ausfälle zählen nicht als verspätet, sondern werden getrennt als Ausfallquote ausgewiesen.
- **Stade → Hbf:** Stand 19.10.2026 fährt die S5 nicht durchgehend, sondern geteilt (live gemessen): Stade–Neugraben und Neugraben–Elbgaustraße über den Hbf, Zugwechsel in Neugraben. Deshalb liegt Neugraben in `HVV_STATIONS`. Dashboard-Filter:
  - Stade-Zweig: *Station* = `Stade` (Abfahrt nach Neugraben, am Start meist nahe 0) und *Station* = `Neugraben`, *Richtung* = `Stade`.
  - Hbf-Zweig: *Station* = `Hauptbahnhof`, *Richtung* = `Elbgaustraße`: Abfahrt der Züge aus Neugraben am Hbf, also Ankunftsverspätung plus Haltezeit.
  - Fährt die S5 wieder durchgehend, taucht am Hbf von selbst *Von* = `Stade` auf.
- Die Daten wachsen ab dem ersten Start. Es gibt keine Historie vor der Inbetriebnahme.

## Live-Karte

Kurz: machbar, aber ohne GPS, nur mit berechneten Positionen auf Minutenniveau. Details, Varianten und Messplan in [docs/live-map-evaluation.md](docs/live-map-evaluation.md). Zum Messen gegen die echte API:

```bash
python3 -m hvv_gti probe-vehicles --types S_BAHN --line S5
```

## Nutzungsbedingungen

Die GTI-Daten sind für kostenlose Fahrgastinformation freigegeben, ein Link zu hvv.de gehört dazu (steht im Hinweis-Panel des Dashboards). Mehr als im Schnitt 1 Aufruf pro Sekunde führt zu einer vorübergehenden Sperre; `HVV_MIN_REQUEST_INTERVAL` erzwingt den Abstand. Bedingungen bitte selbst gegenlesen, bevor etwas öffentlich gezeigt wird.

## Was geprüft wurde und was nicht

Geprüft:

- 45 Unit- und Integrationstests (HMAC-Signatur gegen RFC-2202-Vektor, Umlaute im Body, gzip, Teilausfälle, Influx-Fehler, CLI).
- Line Protocol und Schreibpfad gegen eine echte InfluxDB 2.9.1, Idempotenz durch Überschreiben.
- Alle Dashboard-Abfragen gegen echtes Grafana 13.2.2 und InfluxDB, Ergebnisse stimmen mit einer Python-Gegenrechnung überein (Schwellen 3 und 6 Minuten, mehrere Filter).

Nicht geprüft:

- Echte GTI-Antworten über `check-name` und eine Abfahrtstafel hinaus: Werte von `direction`/`origin` und `getVehicleMap` (Freischaltung, Antwortgröße) sind offen. Ankünfte sind mit dem vorhandenen Account nicht erlaubt.
- Darstellung der Panels im Browser (kein Browser vorhanden). Importiert und abgefragt wurde über die Grafana-API, die Variablen mit „All“ bitte einmal im UI ansehen.

## Entwicklung

```bash
python3 -m unittest discover -s tests -t .
```
