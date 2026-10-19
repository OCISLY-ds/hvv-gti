# hvv-gti

Werkzeuge für die HVV-Schnittstelle (Geofox GTI, <https://gti.geofox.de/>): Pünktlichkeitsstatistik der **S5 (Stade – Neugraben – Hamburg), S7 (Bergedorf – Hamburg) und X30 (Bergedorf ↔ Harburg)** in Grafana und eine Bewertung, ob eine Live-Karte mit Bussen und Bahnen machbar ist.

```
GTI (departureList) --> Collector (Docker) --> InfluxDB (Bucket hvv) --> Grafana-Dashboard
```

- `hvv_gti/` – Python (nur Standardbibliothek plus `tzdata`): GTI-Client mit HMAC-Signatur, Collector, Influx-Writer, CLI
- `grafana/dashboards/hvv-s5-puenktlichkeit.json` – Dashboard (Flux)
- `docs/live-map-evaluation.md` – Machbarkeit der Live-Karte
- `tests/` – 58 Tests gegen Fake-Server (`python3 -m unittest discover -s tests -t .`)

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
python3 -m hvv_gti departures Bergedorf --line X30 --types XPRESSBUS
python3 -m hvv_gti collect --dry-run --once           # gibt nur Line Protocol aus
```

Zum ersten echten Lauf (Stationsauflösung, Linienname `S5` und Tags `direction`/`origin` sind damit am 19.10.2026 bestätigt):

- Ankünfte: Der Account des Projektinhabers darf keine Ankunftstafeln abfragen (HTTP 403, `ArrivalList not allowed for user`). Deshalb ist `collect_arrivals` in `hvv_gti/config.py` fest auf `False` gesetzt (nicht in der `.env`), und der Collector überspringt Ankünfte bei einem 403 ohnehin von selbst. Es zählen nur Abfahrten.

### 4. Collector starten

Claude hat keine Docker-Rechte, daher mit `sudo`:

```bash
sudo docker compose up -d --build
sudo docker logs -f hvv-collector
```

Der Container läuft im Host-Netz (`INFLUX_URL=http://localhost:8086`, der Port ist im iot-stack veröffentlicht), ist auf 128 MB RAM begrenzt und rotiert seine Logs.

### 5. Grafana

1. Datenquelle **InfluxDB** hinzufügen: Name `HVV InfluxDB` (UID `hvv-influx`, dann ist sie im Dashboard vorausgewählt), Query Language **Flux**, URL `http://influxdb:8086` (im iot-stack), Organisation `Bergedorf`, Default Bucket `hvv`, Lese-Token.
2. Dashboards → Import → `grafana/dashboards/hvv-s5-puenktlichkeit.json`. Die Variable *Datenquelle* listet alle InfluxDB-Datenquellen, also bei Bedarf `HVV InfluxDB` wählen und nicht die des iot-stacks.

Per API geht dasselbe mit einem Service-Account-Token (Rolle Admin): `POST /api/datasources` mit den Werten oben, danach `POST /api/dashboards/db` mit dem Dashboard-JSON. Der Token gehört nur in die git-ignorierte `.env` (`GRAFANA_URL`, `GRAFANA_TOKEN`, `INFLUX_READ_TOKEN`).

Das Dashboard hat oben die Filter *Strecke* (z. B. `S5 Stade → Neugraben`, `S7 Bergedorf → Hamburg`, `X30 Harburg → Bergedorf`), *Linie*, *Station*, *Art*, *Richtung*, *Von* und die Verspätungsschwelle. Die Filter hängen voneinander ab: Wer eine Strecke wählt, sieht bei den übrigen nur noch passende Werte. Neben Pünktlichkeit, Ø Verspätung, Ausfallquote und Fahrten zeigt es den Echtzeit-Anteil und eine Tabelle mit Kennzahlen je Strecke, Station und Richtung.

## Wie gemessen wird

- Alle 60 s wird `departureList` je Messpunkt abgefragt (7 Aufrufe nacheinander, gruppiert nach Haltestelle und Verkehrsmittel). Die Strecken stehen fest im Code (`ROUTES` in `hvv_gti/config.py`, mit GTI-IDs), nicht in der `.env`.
- Ein Ereignis ist eine Zeile `hvv_stop_event`: Tags `route`, `line`, `station`, `kind` (`departure`/`arrival`), `direction`, `origin`; Felder `delay_s`, `cancelled`, `extra`, `service_id`. Der **Zeitstempel ist die planmäßige Zeit**. Dasselbe Ereignis wird bei jeder Abfrage erneut geschrieben und überschreibt sich damit: gespeichert bleibt **die zuletzt gemeldete Verspätung vor der Fahrt**.
- **Fehlende Echtzeit:** Meldet die GTI für eine Abfahrt keine Verspätung (`delay` fehlt, das kann bei Bussen vorkommen), fehlt auch das Feld `delay_s`. Solche Fahrten zählen weder als pünktlich noch als verspätet. Das Dashboard weist dafür den **Echtzeit-Anteil** aus. Ausfälle bekommen `delay_s=0`.
- **Pünktlich** heißt Verspätung unter der Schwelle (Standard 3 Minuten, im Dashboard wählbar). Ausfälle zählen nicht als verspätet, sondern werden getrennt als Ausfallquote ausgewiesen.
- Es gibt keine Historie vor der Inbetriebnahme, die Daten wachsen ab dem ersten Start.

### Strecken

Stand 19.10.2026 (live gemessen). Messpunkt ist immer die Abfahrt an der genannten Haltestelle. Ankunftstafeln sind für den Account gesperrt, die Verspätung am Streckenende lässt sich also nicht direkt messen.

| Strecke (Tag `route`) | Messpunkt | Hinweis |
| --- | --- | --- |
| S5 Stade → Neugraben | Abfahrt Stade | am Start meist nahe 0 |
| S5 Neugraben → Stade | Abfahrt Neugraben Richtung Stade/Buxtehude | enthält näherungsweise die Verspätung, mit der der Zug aus Stade ankam (Wendezeit nicht herausgerechnet) |
| S5 Neugraben → Elbgaustraße | Abfahrt Neugraben und Hauptbahnhof | die S5 fährt geteilt, es gibt keinen durchgehenden Zug Stade–Hbf |
| S5 Elbgaustraße → Neugraben | Abfahrt Hauptbahnhof Richtung Neugraben | |
| S7 Bergedorf → Aumühle | Abfahrt Bergedorf | die S7 fährt derzeit nur Bergedorf–Aumühle |
| S7 Bergedorf → Hamburg | Abfahrt Bergedorf, Linie `S7` (Zug) und `S7-SEV` (Ersatzbus nach Berliner Tor) | Züge Richtung Hamburg gibt es derzeit nicht, der Ersatzbus trägt dieselbe Strecke |
| X30 Bergedorf → Harburg | Abfahrt Bergedorf | |
| X30 Harburg → Bergedorf | Abfahrt Harburg Rathaus | |

Ändert sich der Fahrplan (Ende der Bauarbeiten), taucht der Verkehr an den Messpunkten unter einem anderen `direction`-Wert auf. Der Collector schreibt dann unter dem Tag `route` den Fallback `<Linie> <Station> → <Richtung>` und warnt einmal im Log. Dann `ROUTES` anpassen. Bei vollen Abfragelisten (`total >= max_list`) warnt er ebenfalls.

## Live-Karte

Kurz: machbar, aber ohne GPS, nur mit berechneten Positionen auf Minutenniveau. Details, Varianten und Messplan in [docs/live-map-evaluation.md](docs/live-map-evaluation.md). Zum Messen gegen die echte API:

```bash
python3 -m hvv_gti probe-vehicles --types S_BAHN --line S5
```

## Nutzungsbedingungen

Die GTI-Daten sind für kostenlose Fahrgastinformation freigegeben, ein Link zu hvv.de gehört dazu (steht im Hinweis-Panel des Dashboards). Mehr als im Schnitt 1 Aufruf pro Sekunde führt zu einer vorübergehenden Sperre; `HVV_MIN_REQUEST_INTERVAL` erzwingt den Abstand. Bedingungen bitte selbst gegenlesen, bevor etwas öffentlich gezeigt wird.

## Was geprüft wurde und was nicht

Geprüft:

- 58 Unit- und Integrationstests (HMAC-Signatur gegen RFC-2202-Vektor, Umlaute im Body, gzip, Teilausfälle, Influx-Fehler, CLI).
- Line Protocol und Schreibpfad gegen eine echte InfluxDB 2.9.1, Idempotenz durch Überschreiben.
- Alle Dashboard-Abfragen gegen echtes Grafana 13.2.2 und InfluxDB, Ergebnisse stimmen mit einer Handrechnung überein (Schwellen 3 und 6 Minuten, mehrere Filter, Strecken mit und ohne Echtzeitwerte). Die Testdaten wurden danach wieder gelöscht.
- Trockenlauf (`collect --dry-run --once`) gegen die echte GTI am 19.10.2026: alle 9 Streckennamen kommen vor, keine unzugeordneten Richtungen.
- Das Dashboard ist im echten Grafana (13.2.2, 19.10.2026) importiert. Alle 12 Panels und 9 Variablen laufen dort ohne Fehler gegen die Datenquelle `HVV InfluxDB` mit dem Lese-Token. Im Bucket liegen aber nur 12 alte Testpunkte ohne `route`-Tag, solange der Collector nicht läuft, sind die Panels leer.

Nicht geprüft:

- Echte GTI-Antworten über `check-name` und eine Abfahrtstafel hinaus: Ob die Segmentzeiten von `getVehicleMap` die Verspätung schon enthalten, ist offen (siehe `docs/live-map-evaluation.md`). Ankünfte sind mit dem vorhandenen Account nicht erlaubt.
- Darstellung der Panels im Browser (kein Browser vorhanden). Importiert und abgefragt wurde über die Grafana-API, die Variablen mit „All“ bitte einmal im UI ansehen.

## Entwicklung

```bash
python3 -m unittest discover -s tests -t .
```
