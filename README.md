# hvv-gti

Werkzeuge für die HVV-Schnittstelle (Geofox GTI, <https://gti.geofox.de/>): Pünktlichkeitsstatistik der **S5 (Stade – Neugraben – Hamburg), S7 (Bergedorf – Hamburg), X30 (Bergedorf ↔ Harburg), RE5 (Stade ↔ Hamburg/Cuxhaven) und Bus 234 (Bergedorf – Leuschnerstraße)** und ein Zuglauf Stade – Hamburg in Grafana, dazu die Pünktlichkeit **aller S- und U-Bahn-Linien** in Hamburg, ein Diagramm der **API-Anfragen pro Sekunde** sowie eine Bewertung, ob eine Live-Karte mit Bussen und Bahnen machbar ist.

```
GTI (departureList, getVehicleMap) --> Collector (Docker) --> InfluxDB (Bucket hvv) --> Grafana-Dashboards
```

- `hvv_gti/` – Python (nur Standardbibliothek plus `tzdata`): GTI-Client mit HMAC-Signatur, Collector, Influx-Writer, CLI
- `grafana/dashboards/hvv-s5-puenktlichkeit.json` – Pünktlichkeits-Dashboard (Flux)
- `grafana/dashboards/hvv-zuglauf-stade-hamburg.json` – Zuglauf Stade – Hamburg (RE5 und S5, Zug-ID, Position, Verspätung je Halt)
- `grafana/dashboards/hvv-netz-s-u-bahn.json` – Pünktlichkeit und Verspätungen aller S- und U-Bahn-Linien (Messung `hvv_network_stop`)
- `grafana/dashboards/hvv-api-nutzung.json` – Anfragen pro Sekunde, Latenz, Fehler und Aufrufe je Endpunkt (Messung `hvv_api_request`)
- `scripts/migrate_route_tags.py` – schreibt Altdaten (Tag `route` = „S5 Stade → Neugraben“) auf das neue Schema um
- `docs/live-map-evaluation.md` – Machbarkeit der Live-Karte
- `tests/` – 118 Tests gegen Fake-Server (`python3 -m unittest discover -s tests -t .`)

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

Danach einmal `python3 scripts/migrate_route_tags.py` ausführen (zuerst mit `--dry-run`): Es schreibt die bisherigen Punkte (Tag `route` = „S5 Stade → Neugraben“) auf das neue Schema um und löscht die alten Punkte. Bei laufendem alten Container nach dem Neustart noch einmal.

Der Container läuft im Host-Netz (`INFLUX_URL=http://localhost:8086`, der Port ist im iot-stack veröffentlicht), ist auf 128 MB RAM begrenzt und rotiert seine Logs.

### X-Busse

Alle X-Linien (Xpressbus, `getVehicleMap`-Typ `XPRESSBUS`, u. a. X3, X22, X30, X35, X80+) werden je Zyklus in einem eigenen Aufruf für das Gebiet `XBUS` in `hvv_gti/config.py` (Hamburg und Umland, wie der Kartenausschnitt) abgefragt und im Format des S-/U-Bahn-Netzes in die Messung `hvv_xbus_stop` geschrieben (Tags `mode=X`, `line`, `station`, `kind`, `direction`; Felder `delay_s`, `actual`). Eine eigene Messung hält die S-/U-Bahn-Dashboards unverändert. Auf der Live-Karte sind die X-Busse standardmäßig dabei (magenta, mit Fahrwegen, Haltestellen ab Zoomstufe 11, deren Namen ab 14, Fahrten wie die Bahnen); ein Schalter „X-Busse anzeigen“ im Panel blendet sie im Browser aus und wieder ein (die Wahl merkt sich der Browser). `serve-map --no-xbus` lässt sie ganz weg. Es gibt noch kein Dashboard für die Messung.

### Live-Karte und Linien-Logos

`hvv-map` (Port 8090, `http://<pi>:8090/`) zeigt eine Karte von Hamburg und Umland mit den S- und U-Bahn-Gleisen (Linien, die sich eine Strecke teilen, nebeneinander wie auf dem Netzplan) sowie dem RE5 Cuxhaven–Stade–Hamburg (die übrigen Regionalzüge werden ausgefiltert) und den Zügen, deren Position aus den Abschnittszeiten von `getVehicleMap` (inkl. Verspätung) entlang der gelieferten Gleisgeometrie interpoliert wird. Der Server ruft `getVehicleMap` höchstens alle 30 s ab (ein gemeinsamer Cache, egal wie viele Browser offen sind); die Seite fragt `/api/vehicles` alle 10 s ab, um neue Verspätungen früher zu übernehmen, und zeichnet die Züge mit bis zu 60 Bildern pro Sekunde (`requestAnimationFrame`, nur der sichtbare Ausschnitt; wird das Zeichnen teuer, fällt sie auf 30 bzw. 15 fps zurück). Die Züge fahren mit Anfahr- und Bremsrampe (je 20 s), stehen 25 s an jeder Haltestelle (kennt die API die Abfahrt, rollt der Zug 25 s vorher ein) und tragen Logos der Linien (Legende, Zugsymbole, Popup; Klick auf die Legende blendet eine Linie aus). An jedem Zug zeigt ein Pfeil die Fahrtrichtung, darunter läuft ab Zoomstufe 11,5 der Countdown (`m:ss`) bis zum nächsten Halt, ab Zoomstufe 13 steht dessen Name darüber.

**Keine Sprünge:** Die `journeyID` der GTI zählt Fahrten relativ zum Abrufzeitfenster (`Fahrt 0`, `sdIndex 3`) und wechselt von Abruf zu Abruf, teils zwischen hintereinanderfahrenden Zügen; außerdem liefert die GTI manche Fahrten doppelt (gemessen: nur 69 % der IDs blieben beim nächsten Abruf gleich, rund 7 % der Fahrten waren doppelt). Der Server vergibt deshalb eigene Fahrt-IDs (`TripIds` in `hvv_gti/web.py`) aus Linie, Haltepunktpaar und planmäßiger Startzeit (Startzeit minus Verspätung, ändert sich nicht) und blendet Dubletten aus. Ändert ein neuer Abruf den Fahrplan (z. B. eine Minute mehr Verspätung), springt der Zug nicht: Jeder Zug hat im Browser eine eigene Uhr (`map/static/motion.js`), die so gesetzt wird, dass er genau dort bleibt, wo er gerade zu sehen ist, und den Unterschied danach langsamer (0,3×) oder schneller (bis 2,5×) fahrend aufholt. Die Serverzeit für die Uhr kommt aus dem Feld `server` der Antwort (nicht aus der Zeit des gecachten Abrufs). Tests der Bewegung: `node --test tests/js/motion.test.js`.

**Verspätungsplakette:** Neben der Verspätung (`+3`) zeigt ein roter Pfeil nach oben rechts, dass sie wächst, ein grüner nach unten links, dass der Zug sie abbaut (Vergleich mit der zuletzt angezeigten Verspätung; beim ersten Sehen mit dem Abschnitt davor, sonst kein Pfeil).

**Handy und Laptop:** Die Seite passt sich der Größe an. Auf dem Laptop schwebt das Popup am Zug (er wird mitgeführt), das Panel ist offen. Auf dem Handy (Breite ≤ 700 px oder Höhe ≤ 500 px) ist das Panel eingeklappt (Antippen der Kopfzeile öffnet es, die Wahl wird gemerkt), das Popup erscheint als Karte am unteren Rand (im Querformat links), die Karte rückt den Zug in den freien Bereich und führt ihn mit, bis man selbst verschiebt; ein Tipp auf die leere Karte schließt sie. Dazu größere Tippflächen (Trefferfläche 16 px, höhere Legendenzeilen), Bereiche außerhalb der Notch (`viewport-fit=cover`) und weniger Beschriftungen bei niedrigem Zoom.

**Popup:** Ein Klick auf einen Zug zeigt Linie und Ziel, eine lesbare Fahrt-Bezeichnung (`Fahrt 20:43 Uhr ab Wedel`: planmäßige Abfahrt am Ursprung und Ursprungshalt; die technische GTI-ID steht nur noch als Tooltip) und den kompletten Halteverlauf mit Plan- und Ist-Zeit sowie Verspätung je Halt (Ist und Verspätung erscheinen erst, wenn der Zug den Halt erreicht hat, kommende Halte zeigen nur den Plan; vergangene Halte grau, nächster Halt markiert, die Liste scrollt dorthin). Das Popup folgt dem Zug. Den Halteverlauf holt `/api/journey?id=…&m=…` mit einem Abruf der Fahrzeugart über 90 Minuten zurück und voraus; der Server cacht ihn 60 s und findet die Fahrt über ihre Abschnitte wieder, ein Klick auf einen weiteren Zug derselben Art kostet also keinen weiteren GTI-Aufruf.

Die Gleisgeometrie wird aus den Antworten gesammelt und in `map/data/tracks.json` gespeichert; direkt nach dem ersten Start sind deshalb nur die gerade fahrenden Linien vollständig, nach einigen Minuten das ganze Netz.

Basiskarte: ohne weitere Schritte dunkle CARTO-Rasterkacheln aus dem Netz. Eigene Vektorkacheln (Ansatz und tilemaker-Konfiguration von [FreiFahren/tile-server](https://github.com/FreiFahren/FreiFahren/tree/main/packages/tile-server), MIT) baut `sudo map/tiles/build.sh` aus den Geofabrik-Extrakten Hamburg, Niedersachsen und Schleswig-Holstein zu `map/data/hamburg.pmtiles`. Das Skript lädt rund 3 GB, schneidet mit osmium den Ausschnitt `BBOX` (Standard `8.6,53.1,10.65,53.95`: Cuxhaven, Stade, Buxtehude, Pinneberg, Elmshorn, Kaltenkirchen, Ahrensburg, Lüneburg) heraus und braucht auf dem Pi 4 etwa eine Stunde (anderer Ausschnitt: `sudo BBOX=lon1,lat1,lon2,lat2 map/tiles/build.sh`); der Server liefert die Datei per HTTP-Range, die Seite nimmt sie danach automatisch. Daten © OpenStreetMap-Mitwirkende.

Logos: Die GTI selbst liefert keine, wohl aber der Geofox-Icon-Service (`https://cloud.geofox.de/icon/line?lineKey=...`, ohne Login; der `lineKey` kommt aus `listLines`). `python -m hvv_gti icons` lädt alle Schienenlinien nach `data/icons/<Linie>.svg|png`, weitere Linien (auch Busse) lädt der Kartenserver bei Bedarf nach (`/icon/<Linie>.svg`). `scripts/add_line_logos.py` hat in den Grafana-Tabellen mit Linienspalte eine Spalte *Logo* ergänzt; Grafana liest sie aus `/public/img/hvv/`, dafür wird `data/icons` in den Grafana-Container gemountet (siehe unten).

```bash
sudo docker compose up -d --build hvv-map
```

Grafana-Mount in `~/iot-stack/docker-compose.override.yml` (unter `services.grafana`):

```yaml
    volumes:
      - /home/florian/projects/hvv-gti/data/icons:/usr/share/grafana/public/img/hvv:ro
```

danach `cd ~/iot-stack && sudo docker compose up -d grafana` und die vier Dashboards neu importieren.

### 5. Grafana

1. Datenquelle **InfluxDB** hinzufügen: Name `HVV InfluxDB` (UID `hvv-influx`, dann ist sie im Dashboard vorausgewählt), Query Language **Flux**, URL `http://influxdb:8086` (im iot-stack), Organisation `Bergedorf`, Default Bucket `hvv`, Lese-Token.
2. Dashboards → Import → `grafana/dashboards/hvv-s5-puenktlichkeit.json`, `grafana/dashboards/hvv-zuglauf-stade-hamburg.json`, `grafana/dashboards/hvv-netz-s-u-bahn.json` und `grafana/dashboards/hvv-api-nutzung.json`. Die Variable *Datenquelle* listet alle InfluxDB-Datenquellen, also bei Bedarf `HVV InfluxDB` wählen und nicht die des iot-stacks.

**Ohne Login ansehen:** Anonymer Lesezugriff ist in `~/iot-stack/docker-compose.override.yml` eingestellt (`GF_AUTH_ANONYMOUS_*`, Rolle Viewer). Aktiv wird er mit `cd ~/iot-stack && sudo docker compose up -d grafana`. Er gilt für die ganze Grafana-Organisation, also auch für die iot-Dashboards; Port 3000 deshalb nicht ins Internet freigeben. Öffentliche Dashboards (Teilen → Extern) taugen hier nicht: Grafana wertet dabei keine Variablen aus, die Filter der HVV-Dashboards und die Datenquellenvariable würden fehlschlagen (HTTP 400/500, geprüft). Der Login mit Benutzer und Passwort funktioniert weiter, auch für Änderungen.

Per API geht dasselbe mit einem Service-Account-Token (Rolle Admin): `POST /api/datasources` mit den Werten oben, danach `POST /api/dashboards/db` mit dem Dashboard-JSON. Der Token gehört nur in die git-ignorierte `.env` (`GRAFANA_URL`, `GRAFANA_TOKEN`, `INFLUX_READ_TOKEN`).

Das Pünktlichkeits-Dashboard hat oben die Filter *Linie* (S5, S7, X30, RE5, 234, 4), *Richtung* (z. B. Neugraben oder Stade), *Station*, *Art*, *Von* und die Verspätungsschwelle. Die Filter hängen voneinander ab: Wer eine Linie wählt, sieht bei den übrigen nur noch passende Werte. Neben Pünktlichkeit, Ø Verspätung, Ausfallquote und Fahrten zeigt es den Echtzeit-Anteil und eine Tabelle mit Kennzahlen je Linie, Station und Richtung. *Letzte Verspätungen* enthält die **Zug-ID** (GTI-`serviceId`, je Fahrt eindeutig und an jeder Station dieselbe) und die planmäßige Abfahrt. Der Abschnitt **Gründe** darunter zeigt, was die GTI über Ursachen weiß (siehe „Grund der Verspätung“): den Anteil verspäteter Fahrten mit Hinweis, die Zahl aktiver ungeplanter und geplanter Störungsmeldungen, Tabellen mit Balkenzellen für die Abfahrtshinweise nach Typ und die Störungsmeldungen nach Grund (dieses Grafana hat kein Balkendiagramm-Plugin) sowie zwei Tabellen (*Fahrten mit Hinweis*, *Störungsmeldungen der Linien*). Er folgt den Filtern *Linie*, *Station*, *Richtung*, *Art* und *Von*; die Störungsmeldungen kennen nur die Linie und hängen deshalb nur an *Linie*.

Das Dashboard **Zuglauf Stade – Hamburg** (Filter *Linie* und *Richtung*) zeigt für RE5 und S5 mit allen Halten:

- *Züge unterwegs*: je Zug Position (Fortschritt, aktueller Abschnitt, nächster Halt mit Ankunftszeit) und Verspätung. Die Position ist aus den Abschnittszeiten berechnet, es gibt keine GPS-Daten.
- *Bildfahrplan*: Zeit waagerecht, Halt senkrecht, eine Linie je Zug.
- *Verspätung entlang der Strecke*: je Zug die Verspätung an jedem Halt.
- *Verspätung je Zug und Halt*: Tabelle mit eingefärbten Zellen, Zeile von links nach rechts lesen.

## Wie gemessen wird

- Alle 60 s wird `departureList` je Messpunkt abgefragt (9 Aufrufe nacheinander, gruppiert nach Haltestelle und Verkehrsmittel). Danach folgen zwei `getVehicleMap`-Aufrufe (`R_BAHN`, `S_BAHN`) für den Zuglauf und ein weiterer `getVehicleMap`-Aufruf für das ganze S- und U-Bahn-Netz. Die Strecken stehen fest im Code (`ROUTES` in `hvv_gti/config.py`, mit GTI-IDs), nicht in der `.env`.
- Ein Ereignis ist eine Zeile `hvv_stop_event`: Tags `route` (Linie, z. B. `S5`), `line` (genauer, z. B. `S7-SEV`), `station`, `kind` (`departure`/`arrival`), `direction` (Fahrtrichtung der Strecke, z. B. `Neugraben`), `destination` (Ziel laut GTI), `origin`; Felder `delay_s`, `cancelled`, `extra`, `service_id`. Der **Zeitstempel ist die planmäßige Zeit**. Dasselbe Ereignis wird bei jeder Abfrage erneut geschrieben und überschreibt sich damit: gespeichert bleibt **die zuletzt gemeldete Verspätung vor der Fahrt**.
- **Fehlende Echtzeit:** Meldet die GTI für eine Abfahrt keine Verspätung (`delay` fehlt, das kann bei Bussen vorkommen), fehlt auch das Feld `delay_s`. Solche Fahrten zählen weder als pünktlich noch als verspätet. Das Dashboard weist dafür den **Echtzeit-Anteil** aus. Ausfälle bekommen `delay_s=0`.
- **Pünktlich** heißt Verspätung unter der Schwelle (Standard 3 Minuten, im Dashboard wählbar). Ausfälle zählen nicht als verspätet, sondern werden getrennt als Ausfallquote ausgewiesen.
- Es gibt keine Historie vor der Inbetriebnahme, die Daten wachsen ab dem ersten Start.

### Alle S- und U-Bahn-Linien

Je Zyklus fragt der Collector mit **einem** `getVehicleMap`-Aufruf (`vehicleTypes` = `S_BAHN`, `U_BAHN`, Rechteck um Hamburg, `NETWORK` in `hvv_gti/config.py`) das ganze Netz ab, statt Abfahrten für jede der rund 150 Stationen einzeln zu holen. Daraus entsteht je Halt der nächsten 5 Minuten (`horizon_s`) eine Zeile `hvv_network_stop`:

- Tags `mode` (`S`/`U`), `line` (z. B. `S1`, `U3`), `station`, `kind` (`departure`/`arrival`), `direction` (Fahrtrichtung der Linie). Es gibt bewusst keinen Tag je Fahrt, damit die Zahl der Reihen begrenzt bleibt.
- Felder `delay_s` (Verspätung in Sekunden, die GTI meldet ganze Minuten; fehlt ohne Echtzeitwert) und `actual` (tatsächliche Zeit als Epoche).
- Der Zeitstempel ist die planmäßige Zeit, auf die Minute abgerundet: derselbe Halt überschreibt sich bei jedem Zyklus, und die zuletzt gemeldete Verspätung vor der Durchfahrt bleibt stehen. Bereits passierte Halte werden nicht mehr geschrieben.
- Die GTI kappt manche Fahrten (z. B. S5 Richtung Stade endet bei Dollern). Einen Ankunftshalt gibt es deshalb nur, wenn das letzte Segment `isLast` ist; sonst würde ein Zwischenhalt als Endstation erscheinen. Dieselbe Fahrt kann unter zwei `journeyID` auftauchen; mit dem Zeitstempel als Schlüssel zählt sie trotzdem nur einmal.
- Die Messung ist getrennt von `hvv_stop_event` (S5, S7, X30, RE5, 234), damit deren Auswertung unverändert bleibt. Ausfälle liefert `getVehicleMap` nicht, das Dashboard *HVV Netz: S- und U-Bahn* zeigt daher nur Verspätungen.
- Ist der Aufruf für den Account gesperrt (HTTP 403), merkt sich der Collector das und überspringt ihn; `NETWORK = None` in der Konfiguration schaltet ihn ab.

### API-Nutzung (Anfragen pro Sekunde)

Der GTI-Client merkt sich jeden Aufruf (Endpunkt, Startzeit, Latenz, Antwortgröße, Status), und der Collector schreibt sie mit den übrigen Punkten als `hvv_api_request` (Tags `endpoint`, `status`; Felder `latency_ms`, `bytes`). Fehlgeschlagene Aufrufe zählen mit; bei einem Influx-Ausfall bleiben die Punkte (bis 5000) für den nächsten Zyklus erhalten. Das Dashboard *HVV API-Nutzung* teilt die Zahl der Punkte je Fenster (1 min bis 1 h, wählbar) durch die Fensterlänge und zeigt dazu Anteil am Limit, Latenz, Fehlerquote und eine Tabelle je Endpunkt.

Erwartet werden rund 0,12 Anfragen pro Sekunde: etwa 13 Aufrufe je Zyklus (9 `departureList`, 4 `getVehicleMap`, alle 5 Zyklen `getAnnouncements`) brauchen bei ~3 s Latenz etwa 40 s, dazu kommen 60 s Pause. Das Limit der GTI liegt bei 1 pro Sekunde im Schnitt. Eine Anfrage pro Zyklus mehr sind etwa 0,01 mehr.

### Grund der Verspätung

Die GTI nennt **keinen Verspätungsgrund je Fahrt**. `departureList` hat nur `delay`, `cancelled` und `extra`. Was es gibt, sind zwei grobe Quellen, die der Collector beide mitschreibt:

- **Hinweise an der Abfahrt** (`attributes` in `departureList`): Freitext und Typ, aber nur in wenigen Fällen, z. B. „Prognose ungenau - Fahrzeug im Stau“ (`TRAFFIC_JAM`). Laut Handbuch sind die Typen `TRAFFIC_JAM`, `TECHNICAL_PROBLEM`, `DISPOSITIVE_ACTION` (z. B. Warten auf Anschluss) und `MISSING_UPDATE`; live kam zusätzlich `REALTIME` vor. Sie landen als Felder `reason_types` (kommagetrennt) und `reason_text` im jeweiligen `hvv_stop_event`. Reine Linieninfos (Typ nur `NORMAL`) werden nicht gespeichert. Es sind Felder und keine Tags, damit eine Abfahrt, die erst später einen Hinweis bekommt, nicht als zweite Serie doppelt zählt. Verschwindet ein Hinweis wieder, bleibt der zuletzt geschriebene stehen (Felder werden nur überschrieben, nicht gelöscht).
- **Störungsmeldungen** (`getAnnouncements`, alle 5 Minuten, `HVV_ANNOUNCEMENT_INTERVAL`, `0` schaltet ab): Pro gerade gültiger Meldung und betroffener Linie (nur S5, S7, X30, RE5, 234) eine Zeile `hvv_announcement` zum Abfragezeitpunkt. Tags `line`, `reason` (GTI-Ursache wie `ROADWORKS`, `ACCIDENT`, `VEHICLE_ON_THE_LINE`, `STAFF_ABSENCE`, `SERVICE_FAILURE`, `POLICE_REQUEST` ...), `planned` (`true` = geplant, `false` = ungeplant), `announcement_id`; Felder `summary`, `version`. Die Meldung gilt für die Linie, nicht für einen einzelnen Zug: der Zusammenhang mit einer Verspätung ist nur zeitlich. Ungeplante Ursachen findet man mit `planned=false`; die meisten Meldungen sind Baustellen und Aufzüge (`ROADWORKS`, `UNDEFINED_PROBLEM`).

`python -m hvv_gti departures ...` zeigt Hinweise der Abfahrten in eckigen Klammern.

Im Pünktlichkeits-Dashboard stehen beide Quellen im Abschnitt *Gründe*. Die Panels bleiben leer, bis der Collector mit dem neuen Stand läuft (`sudo docker compose up -d --build`); „aktiv“ heißt, die Meldung wurde in den letzten 11 Minuten abgefragt, *Erstmals gesehen* ist der Beginn der Erfassung und nicht der der Störung.

### Strecken

Stand 20.10.2026 (live gemessen). Messpunkt ist immer die Abfahrt an der genannten Haltestelle. Ankunftstafeln sind für den Account gesperrt, die Verspätung am Streckenende lässt sich also nicht direkt messen.

| Linie, Richtung (Tags `route`, `direction`) | Messpunkt | Hinweis |
| --- | --- | --- |
| S5, Richtung Neugraben | Abfahrt Stade | am Start meist nahe 0 |
| S5, Richtung Stade | Abfahrt Neugraben Richtung Stade/Buxtehude | enthält näherungsweise die Verspätung, mit der der Zug aus Stade ankam (Wendezeit nicht herausgerechnet) |
| S5, Richtung Elbgaustraße | Abfahrt Neugraben und Hauptbahnhof | die S5 fährt geteilt, es gibt keinen durchgehenden Zug Stade–Hbf |
| S5, Richtung Neugraben | Abfahrt Hauptbahnhof Richtung Neugraben | |
| S7, Richtung Aumühle | Abfahrt Bergedorf | die S7 fährt derzeit nur Bergedorf–Aumühle |
| S7, Richtung Hamburg | Abfahrt Bergedorf, Linie `S7` (Zug) und `S7-SEV` (Ersatzbus nach Berliner Tor) | Züge Richtung Hamburg gibt es derzeit nicht, der Ersatzbus trägt dieselbe Strecke |
| X30, Richtung Harburg | Abfahrt Bergedorf | |
| X30, Richtung Bergedorf | Abfahrt Harburg Rathaus | |
| 234, Richtung Leuschnerstraße | Abfahrt Bergedorf (alle Fahrten der Linie 234, auch die Kurzfahrten nach S Nettelnburg) | „Leuschnerstraße“ ist in der GTI nur eine Adresse. Die Route von Bergedorf führt mit der 234 bis Lohbrügger Weg und dann 3 Minuten zu Fuß. Die 234 meldet derzeit keine Echtzeit, zählt also über den Echtzeit-Anteil statt über die Pünktlichkeit |
| 4, Richtung Überseequartier | Abfahrt Rathausmarkt Richtung Osakaallee (Überseequartier) | Hochbahn-Linie 4 (Eidelstedt – Eimsbüttel – Dammtor – Jungfernstieg – Überseequartier). Messpunkt in der Linienmitte, anderer Punkt: `RATHAUSMARKT` in `hvv_gti/config.py` ersetzen |
| 4, Richtung Eidelstedt | Abfahrt Rathausmarkt Richtung Wildacker oder Langenfelder Damm (Kurzfahrt) | |
| RE5, Richtung Hamburg | Abfahrt Stade Richtung Hamburg (Ziel Hamburg Hbf oder Hamburg-Harburg) | die Züge beginnen in Cuxhaven, die Verspätung enthält also den Abschnitt Cuxhaven–Stade |
| RE5, Richtung Cuxhaven | Abfahrt Stade Richtung Cuxhaven | |

Ändert sich der Fahrplan (Ende der Bauarbeiten), taucht der Verkehr an den Messpunkten mit einem anderen Ziel auf. Der Collector schreibt ihn dann unter der Linie mit dem GTI-Ziel als `direction` und warnt einmal im Log. Dann `ROUTES` anpassen. Bei vollen Abfragelisten (`total >= max_list`) warnt er ebenfalls.

## Live-Karte

Kurz: machbar, aber ohne GPS, nur mit berechneten Positionen auf Minutenniveau. Details, Varianten und Messplan in [docs/live-map-evaluation.md](docs/live-map-evaluation.md). Zum Messen gegen die echte API:

```bash
python3 -m hvv_gti probe-vehicles --types S_BAHN --line S5
```

## Nutzungsbedingungen

Die GTI-Daten sind für kostenlose Fahrgastinformation freigegeben, ein Link zu hvv.de gehört dazu (steht im Hinweis-Panel des Dashboards). Mehr als im Schnitt 1 Aufruf pro Sekunde führt zu einer vorübergehenden Sperre; `HVV_MIN_REQUEST_INTERVAL` erzwingt den Abstand. Bedingungen bitte selbst gegenlesen, bevor etwas öffentlich gezeigt wird.

## Was geprüft wurde und was nicht

Geprüft:

- 118 Unit- und Integrationstests (HMAC-Signatur gegen RFC-2202-Vektor, Umlaute im Body, gzip, Teilausfälle, Influx-Fehler, CLI, Zuglauf-Berechnung).
- Line Protocol und Schreibpfad gegen eine echte InfluxDB 2.9.1, Idempotenz durch Überschreiben.
- Alle Dashboard-Abfragen gegen echtes Grafana 13.2.2 und InfluxDB, Ergebnisse stimmen mit einer Handrechnung überein (Schwellen 3 und 6 Minuten, mehrere Filter, Strecken mit und ohne Echtzeitwerte). Die Testdaten wurden danach wieder gelöscht.
- Echter Lauf gegen die GTI und die InfluxDB am 20.10.2026 (`collect --once`): 215 Datenpunkte, davon Abfahrten mit den neuen Tags und Zuglauf-Punkte für 4 RE5- und 18 S5-Fahrten. `journeyID` blieb über Abrufe im Abstand von 2 Minuten stabil. Die Einheit von `realtimeDelay` (Minuten) und der Zeitbezug der Segmente (enthalten die Verspätung) wurden gegen die Abfahrtstafel geprüft (siehe `docs/live-map-evaluation.md`).
- Beide Dashboards sind im echten Grafana (13.2.2, 20.10.2026) importiert; alle Panel- und Variablenabfragen laufen über `/api/ds/query` ohne Fehler und liefern Werte. Dabei fiel auf, dass `map` mit nur `_value` bei lauter Nullen leer zurückgibt (Pünktlichkeit und Ausfallquote zeigten dann „No data“ statt 0 %); die Abfragen behalten jetzt `_time`.
- Netz- und API-Nutzungs-Dashboard (28.10.2026): alle Panel-Abfragen laufen über `/api/ds/query` mit Probe-Messungen (`*_probe`, danach gelöscht) und auf dem echten Bucket ohne Fehler; beide sind in Grafana importiert. Ein `collect --dry-run --once` gegen die echte GTI lieferte nachts 12 Fahrten und 18 Halte des Netzes.

Nicht geprüft:

- Ankünfte sind mit dem vorhandenen Account nicht erlaubt (HTTP 403). In `getVehicleMap` fehlt für die S5 der Abschnitt Stade – Dollern, die S5-Züge erscheinen also erst ab Dollern (siehe `docs/live-map-evaluation.md`).
- Die Messungen `hvv_network_stop` und `hvv_api_request` enthalten erst Daten, wenn der Container mit dem neuen Stand läuft; die Last bei vollem Tagesverkehr (viele Fahrten je Aufruf) ist noch nicht gemessen.
- Darstellung der Panels im Browser (kein Browser vorhanden). Importiert und abgefragt wurde über die Grafana-API. Besonders unsicher: die Stationsnamen statt Zahlen auf den Achsen von *Bildfahrplan* und *Verspätung entlang der Strecke* (Wertzuordnung); die Tabellen *Züge unterwegs* und *Verspätung je Zug und Halt* sind davon unabhängig lesbar.

## Entwicklung

```bash
python3 -m unittest discover -s tests -t .
```
