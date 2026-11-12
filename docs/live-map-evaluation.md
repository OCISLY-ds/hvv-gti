# Live-Karte mit Bussen und Bahnen: Machbarkeit

Stand: Oktober 2026. Grundlage sind die GTI-Dokumentation und OpenAPI-Spezifikation (Version 63), die Open-Source-Integration von Home Assistant für HVV und das Projekt *HVVMap*.
Ein echter `getVehicleMap`-Aufruf wurde am 19.10.2026 gegen den GTI-Account des Projektinhabers gemessen (Abschnitt „Messung“). Was daraus noch nicht folgt, ist unten als offen markiert.

## Kurzfazit

| Frage | Antwort |
| --- | --- |
| Geht eine Live-Karte mit GTI-Daten? | Ja, aber **ohne GPS**: Die GTI liefert Fahrten mit Abschnitten (Start-/Endhalt, Zeiten, Verspätung). Positionen werden aus diesen Zeiten entlang des Gleis-/Straßenverlaufs **berechnet** (interpoliert). |
| Genauigkeit | Auf Minutenniveau. Zwischen zwei Halten wird gleichmäßige Fahrt angenommen; bei Störungen oder Halt auf freier Strecke liegt die Anzeige daneben. Für Busse im Stadtverkehr ist das gröber als für die S-Bahn. |
| Rate-Limit | Im Schnitt mehr als 1 Aufruf pro Sekunde führt zu einer vorübergehenden Sperre. Eine Karte mit einem Abruf alle 30 bis 60 s liegt weit darunter. |
| In Grafana? | Mit dem Geomap-Panel ja, als „alle 15 bis 30 s neu gezeichnete Punkte“. Flüssige Bewegung ist dort nicht möglich. |
| Empfehlung | Variante A (nur S-Bahn oder ausgewählte Linien in Grafana); die Messung (siehe unten) spricht nicht dagegen. Eine eigene Webkarte (B) nur, wenn flüssige Bewegung oder das ganze Netz gewünscht ist. |

## Was die API liefert

- `getVehicleMap`: Eingabe sind Bounding-Box (WGS84), Zeitfenster (`periodBegin`/`periodEnd`, Unix-Sekunden), `realtime`, `withoutCoords` und optional `vehicleTypes` (`S_BAHN`, `U_BAHN`, `METROBUS`, `REGIONALBUS`, `SCHIFF` usw.).
  Ausgabe sind `journeys`: je Fahrt `journeyID`, `line`, `vehicleType`, `realtime` und `segments`. Ein Segment hat `startStationName/-Key`, `startDateTime`, `endStationName/-Key`, `endDateTime`, `destination`, `realtimeDelay` (**Minuten**, siehe „Messung 20.10.2026“) und optional `track` (Polylinie).
- `getTrackCoordinates`: liefert die Polylinien (`tracks`) zu Haltepunkt-Paaren. Sie sind statisch und lassen sich dauerhaft zwischenspeichern.
- Mit `withoutCoords=true` bleibt die Antwort klein; die Polylinien holt man einmalig separat. Das Projekt HVVMap hat `getVehicleMap` nach eigener Aussage wegen der Anzahl der Requests wieder entfernt und kommt mit Minutengenauigkeit aus. Das passt zu der Einschätzung hier.

### Messung (19.10.2026, 13:10 Uhr)

`python -m hvv_gti probe-vehicles --types S_BAHN --line S5`, Bounding-Box Großraum Hamburg, 2-Minuten-Fenster:

| Wert | Messung |
| --- | --- |
| Freischaltung | `getVehicleMap` funktioniert mit dem Account (Ankunftstafeln dagegen nicht, HTTP 403) |
| Latenz | 3,2 s (alle GTI-Aufrufe brauchen etwa 3 s) |
| Größe | 48 KiB entpackt, rund 830 Byte pro Fahrt |
| Umfang | 59 S-Bahn-Fahrten mit 93 Segmenten, alle mit Echtzeit (`realtime=true`) |
| `realtimeDelay` | Median 0, Maximum 10 (zu ruhige Stichprobe; Einheit Minuten, nicht Sekunden, siehe unten) |
| Linie S5 | 12 Fahrten gleichzeitig, jeweils Ziel Neugraben oder Elbgaustraße |

Folgerungen: Die Antwortgröße ist unkritisch (bei 30 s Abstand rund 140 MB Transfer pro Tag). Die Latenz heißt, dass ein Abruf ein paar Sekunden dauert und nicht mehr als alle 15 bis 30 s sinnvoll ist. Die Standard-Bounding-Box endet östlich von Stade; für den Stade-Zweig muss sie nach Westen erweitert werden (`--bbox 9.40,53.35,10.35,53.75`).

### Messung 20.10.2026: Einheit und Zeitbezug

Bei verspäteten Zügen (darunter ein RE5 mit über 30 Minuten) wurden die Segmentzeiten mit der Abfahrtstafel (`departureList`, Planzeit plus `delay`) verglichen. Das Ergebnis, bei 8 von 9 Vergleichen übereinstimmend:

- `realtimeDelay` ist in **Minuten** (die Tafel liefert `delay` in Sekunden).
- `startDateTime`/`endDateTime` **enthalten die Verspätung bereits**: Planzeit = `startDateTime - realtimeDelay * 60`. Die Verspätung darf also nicht noch einmal aufaddiert werden.
- `journeyID` ist innerhalb eines Abrufs eindeutig, bei gleichem Zeitfenster auch über Abrufe stabil (24 von 24 Fahrten bei zwei Abrufen im Abstand von 2 Minuten). **Bei gleitendem Zeitfenster ist sie es nicht** (Messung 05.11.2026, Abrufe im Abstand von 30 s): Die Zähler im Suffix (`subline`, `sdIndex`, `Fahrt`) beziehen sich auf das Fenster; nur 69 % der Fahrten behielten ihre ID, die übrigen wurden umbenannt oder bekamen die ID des Zuges davor/dahinter. Außerdem erscheinen rund 7 % der Fahrten doppelt unter zwei IDs mit identischen Abschnitten. Eine Fahrt erkennt man stattdessen an Linie, Haltepunktpaar und planmäßiger Startzeit der Abschnitte (siehe `TripIds` in `hvv_gti/web.py`). Mit der `serviceId` der Abfahrtstafel ist die ID nicht identisch.
- Mit einem Zeitfenster von 90 Minuten zurück und voraus liefert `getVehicleMap` je Fahrt alle Abschnitte (bis zu 43 bei der U-Bahn); die Zeilen `line.origin` und `line.direction` nennen Ursprung und Ziel. Über alle vier Fahrzeugarten sind das rund 1,9 MB ohne Koordinaten (4,6 s), deshalb holt die Karte diesen Abruf nur bei einem Klick auf einen Zug und cacht ihn.
- Ein weites Zeitfenster (90 Minuten zurück bis 90 Minuten voraus) liefert ganze Fahrten mit allen Abschnitten, auch den noch bevorstehenden; ein 2-Minuten-Fenster nur die gerade fahrenden Abschnitte.
- `vehicleTypes` ist Pflicht: ohne den Parameter kommt nur `{"returnCode":"OK"}`, `R_BAHN` liefert den RE5, `S_BAHN` die S5, `ZUG` ergibt HTTP 400.
- Für die S5 fehlt der Abschnitt Stade – Dollern in der Antwort: Züge Richtung Neugraben beginnen in Dollern, Züge Richtung Stade enden dort. Auch mit größerer Bounding-Box und `withoutCoords=false` ändert sich das nicht (erneut gemessen 07.11.2026). Die Karte ergänzt den Abschnitt selbst (`hvv_gti/extensions.py`).
- **Geisterfahrten (07.11.2026):** Für verspätete Züge liefert `getVehicleMap` zusätzlich eine Fahrt zur Planzeit mit `realtimeDelay` 0 (gleiche Abschnitte, gleiche Planstart-Minute, Reihenfolge wechselt). Beispiel: S3 am Hauptbahnhof, Tafel +360 s, Karte: eine Fahrt +6 (Abfahrt 20:12) und eine +0 (20:06). `hvv_gti/dedupe.py` behält die mit der größten Verspätung.
- **Auflösung:** `realtimeDelay` ist in ganzen Minuten; auch `departureList.delay` war in allen Stichproben ein Vielfaches von 60 s (0, 180, 240, 360). Ein Abfahrtssignal, eine Fahrzeugposition oder ein Ist-Zeitstempel in Sekunden kommt weder in `getVehicleMap` noch in `departureList` vor. Eine öffentliche GTFS-Realtime-Quelle mit Fahrzeugpositionen für den HVV wurde nicht gefunden (der Community-Server `hamburg-gtfs-rt-server` ist veraltet und verweist selbst auf `getVehicleMap`).

Umgesetzt ist das im Collector als Zuglauf Stade – Hamburg (`hvv_gti/trains.py`, Dashboard `hvv-zuglauf-stade-hamburg`).

### Noch offen

1. **Echtzeit-Anteil bei Bussen** (die S-Bahn ist zu 100 % echtzeitfähig, Busse nicht zwingend): `probe-vehicles --types METROBUS`. Die Abfahrtstafel liefert für einzelne Busfahrten keinen `delay`-Wert (im Trockenlauf vom 19.10.2026 2 von 22 Zeilen, beide beim Ersatzbus S7-SEV). Der Collector erfasst das als fehlenden Wert (siehe README).
2. **Verhalten bei Störungen**, wenn Fahrten ausfallen oder Echtzeit abreißt.

## Positionsberechnung

Für jede Fahrt zum Zeitpunkt *t*:

1. Segment finden mit `start <= t < end` (die Zeiten enthalten die Verspätung schon).
2. Anteil `f = (t - start) / (end - start)` bilden.
3. Entlang der Polylinie des Segments den Punkt bei `f * Länge` bestimmen (Haversine oder lokale Ebene).
4. Zwischen zwei Segmenten (Halt am Bahnsteig) die Position des Halts verwenden.

Je Fahrzeug fällt dabei nur eine Handvoll Arithmetik an. Der Rechenaufwand ist auch für einige hundert Fahrzeuge auf dem Raspberry Pi vernachlässigbar.

## Varianten

### A. Grafana Geomap (empfohlen als Start)

- Neuer Befehl im Collector (`vehicles`), der alle 30 s `getVehicleMap` abruft, Positionen berechnet und `hvv_vehicle` nach InfluxDB schreibt (Felder `lat`, `lon`, `delay_s`; Tags `line`, `vehicle_type`, `journey_id`, `destination`).
- Geomap-Panel mit Markers-Layer, Datenmodus „Last point“, Farbe nach Verspätung, Beschriftung mit Linie, Auto-Refresh 15 bis 30 s.
- **Speicher beachten:** 600 Fahrzeuge alle 30 s sind rund 1,7 Mio. Punkte pro Tag, zu viel für die wackelige USB-SSD. Deshalb eigener Bucket `hvv_live` mit Retention von 2 bis 6 Stunden und zunächst nur `S_BAHN` (grob 60 Fahrzeuge).
- Aufwand: klein bis mittel. Kein neuer Dienst, nur ein weiterer Befehl und ein Dashboard.
- Grenze: Punkte springen alle 30 s, keine Linienführung auf der Karte, nur Rasterkarte mit Grafanas Standard-Basiskarte.

### B. Eigene Webkarte

- Kleiner Dienst (z. B. FastAPI oder reines `http.server`) mit demselben Abruf. Er liefert JSON mit Segmenten, Zeiten und Polylinien. Die Interpolation läuft **im Browser** per `requestAnimationFrame`, die Bewegung ist flüssig, und der Abruf bleibt bei 30 bis 60 s.
- Karte mit MapLibre oder Leaflet. **Kacheln:** Der öffentliche OSM-Tile-Server ist für Dauerbetrieb nicht gedacht; entweder einen erlaubten Anbieter mit eigenem Schlüssel oder selbst gehostete Kacheln verwenden.
- In Grafana nur als Link oder iframe einbindbar.
- Aufwand: mittel bis hoch. Lohnt sich, wenn das ganze Netz oder eine „schöne“ Karte gewünscht ist.

### C. Einfachste Alternative: Abfahrtstafel statt Karte

`departureList` ist bereits im Collector. Eine Tabelle „nächste S5 ab Stade/Hbf mit Verspätung“ braucht keine Geometrie und keinen zusätzlichen Aufruf.

## Rate-Limit und Rechenbeispiel

Stand 28.10.2026 fragt der Collector je Zyklus 9 `departureList`, 2 `getVehicleMap` für den Zuglauf, 1 `getVehicleMap` für das ganze S- und U-Bahn-Netz und alle 5 Zyklen `getAnnouncements` ab. Das sind etwa 11 Aufrufe in ~34 s plus 60 s Pause, im Schnitt rund 0,12 Aufrufe pro Sekunde (gemessen wird das im Dashboard *HVV API-Nutzung*, Messung `hvv_api_request`). Das ganze S- und U-Bahn-Netz kostet dabei nur einen Aufruf pro Zyklus, weil `getVehicleMap` alle Fahrten im Rechteck liefert; Abfahrtstafeln für die rund 150 Stationen einzeln wären über 100 Aufrufe je Zyklus gewesen. Frühere Rechnung: 2 Haltestellen mit Abfahrten und Ankünften waren 4 Aufrufe pro Minute (rund 0,07 pro Sekunde). `HVV_MIN_REQUEST_INTERVAL=1.2` ist ein harter Abstand zwischen zwei Aufrufen, auch für künftige Befehle. Eine Live-Karte mit einem Abruf alle 30 s kommt mit 2 Aufrufen pro Minute dazu. Beides zusammen liegt bei unter 0,1 Aufruf pro Sekunde.
Mehrere Browser-Tabs dürfen nie je einen eigenen API-Abruf auslösen. Der Abruf gehört in den Collector, die Browser lesen nur dessen Ergebnis.

## Nutzungsbedingungen

- Die Daten dürfen laut Bedingungen der GTI für kostenlose Fahrgastinformation verwendet werden. Auf der Seite muss ein Link zu hvv.de stehen (das Dashboard hat ihn bereits im Hinweis-Panel). Die genauen Bedingungen bitte beim Freischalten des Zugangs noch einmal selbst lesen, vor allem bei öffentlicher Darstellung.
- Kein Weiterverkauf, kein Dauer-Polling in kurzen Abständen.

## Risiken

- **Baustellen 2026:** Am 19.10.2026 fährt die S5 geteilt (Stade–Neugraben und Neugraben–Elbgaustraße über Hbf, live gemessen). Es gibt keinen durchgehenden Zug Stade–Hbf; Pünktlichkeit und Karte müssen beide Zweige betrachten, und Fahrplanänderungen können die Tags (`direction`/`origin`) verschieben.
- **Freischaltung:** Der vorhandene GTI-Account darf keine Ankunftstafeln abfragen (HTTP 403). `getVehicleMap` kann ebenfalls gesperrt sein; das zeigt erst `probe-vehicles`.
- Positionsfehler bei Störungen (siehe oben), Busse oft zu ungenau, um daran „wo ist mein Bus“ zu entscheiden.
- Speicher und RAM des Raspberry Pi (1,8 GB): Karte A mit Retention begrenzen, eine eigene Webkarte (B) nur als schlanken Dienst betreiben.

## Messplan

```bash
# S-Bahn im Großraum Hamburg, 2-Minuten-Fenster, Linie S5 auflisten
python -m hvv_gti probe-vehicles --types S_BAHN --line S5

# gesamtes Netz inkl. Busse (Größe und Latenz einschätzen)
python -m hvv_gti probe-vehicles

# nur Fahrplan statt Echtzeit (Vergleich)
python -m hvv_gti probe-vehicles --types S_BAHN --schedule
```

Entscheidungshilfe: Die Messung (48 KiB, 59 Fahrten) liegt weit unter den Schwellen: Variante A ist ohne weitere Optimierung machbar. Für Busse gilt Entsprechendes erst nach einer eigenen Messung (`--types METROBUS`), weil dort deutlich mehr Fahrzeuge anfallen.
