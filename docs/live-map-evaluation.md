# Live-Karte mit Bussen und Bahnen: Machbarkeit

Stand: Oktober 2026. Grundlage sind die GTI-Dokumentation und OpenAPI-Spezifikation (Version 63), die Open-Source-Integration von Home Assistant für HVV und das Projekt *HVVMap*.
**Nicht belegt:** Es gab keinen API-Zugang, daher wurde kein einziger echter `getVehicleMap`-Aufruf ausgeführt. Alles, was von echten Antworten abhängt, ist unten als offen markiert und lässt sich mit `python -m hvv_gti probe-vehicles` in zwei Minuten klären.

## Kurzfazit

| Frage | Antwort |
| --- | --- |
| Geht eine Live-Karte mit GTI-Daten? | Ja, aber **ohne GPS**: Die GTI liefert Fahrten mit Abschnitten (Start-/Endhalt, Zeiten, Verspätung). Positionen werden aus diesen Zeiten entlang des Gleis-/Straßenverlaufs **berechnet** (interpoliert). |
| Genauigkeit | Auf Minutenniveau. Zwischen zwei Halten wird gleichmäßige Fahrt angenommen; bei Störungen oder Halt auf freier Strecke liegt die Anzeige daneben. Für Busse im Stadtverkehr ist das gröber als für die S-Bahn. |
| Rate-Limit | Im Schnitt mehr als 1 Aufruf pro Sekunde führt zu einer vorübergehenden Sperre. Eine Karte mit einem Abruf alle 30 bis 60 s liegt weit darunter. |
| In Grafana? | Mit dem Geomap-Panel ja, als „alle 15 bis 30 s neu gezeichnete Punkte“. Flüssige Bewegung ist dort nicht möglich. |
| Empfehlung | Zuerst `probe-vehicles` messen. Danach Variante A (nur S-Bahn oder ausgewählte Linien in Grafana). Eine eigene Webkarte (B) nur, wenn flüssige Bewegung oder das ganze Netz gewünscht ist. |

## Was die API liefert

- `getVehicleMap`: Eingabe sind Bounding-Box (WGS84), Zeitfenster (`periodBegin`/`periodEnd`, Unix-Sekunden), `realtime`, `withoutCoords` und optional `vehicleTypes` (`S_BAHN`, `U_BAHN`, `METROBUS`, `REGIONALBUS`, `SCHIFF` usw.).
  Ausgabe sind `journeys`: je Fahrt `journeyID`, `line`, `vehicleType`, `realtime` und `segments`. Ein Segment hat `startStationName/-Key`, `startDateTime`, `endStationName/-Key`, `endDateTime`, `destination`, `realtimeDelay` (Sekunden) und optional `track` (Polylinie).
- `getTrackCoordinates`: liefert die Polylinien (`tracks`) zu Haltepunkt-Paaren. Sie sind statisch und lassen sich dauerhaft zwischenspeichern.
- Mit `withoutCoords=true` bleibt die Antwort klein; die Polylinien holt man einmalig separat. Das Projekt HVVMap hat `getVehicleMap` nach eigener Aussage wegen der Anzahl der Requests wieder entfernt und kommt mit Minutengenauigkeit aus. Das passt zu der Einschätzung hier.

### Offene Punkte (mit `probe-vehicles` prüfen)

1. **Größe und Latenz** der Antwort für ganz Hamburg und für den Zuschnitt auf S-Bahn. Ausgegeben werden Bytes pro Fahrt und Gesamtgröße.
2. **Enthalten `startDateTime`/`endDateTime` die Verspätung schon, oder muss `realtimeDelay` addiert werden?** Der Vergleich mit `departures` für dieselbe Fahrt klärt das.
3. **Anteil der Fahrten mit `realtime=true`**, besonders bei Bussen.
4. Ob das **GTI-Konto** `getVehicleMap` überhaupt freischaltet (Rechte hängen am Zugang).

## Positionsberechnung

Für jede Fahrt zum Zeitpunkt *t*:

1. Segment finden mit `start + delay <= t <= end + delay` (siehe offener Punkt 2).
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

Der Collector für die Pünktlichkeit nutzt bei 60 s Intervall und zwei Haltestellen mit Abfahrten und Ankünften 4 Aufrufe pro Minute (rund 0,07 pro Sekunde). `HVV_MIN_REQUEST_INTERVAL=1.2` ist ein harter Abstand zwischen zwei Aufrufen, auch für künftige Befehle. Eine Live-Karte mit einem Abruf alle 30 s kommt mit 2 Aufrufen pro Minute dazu. Beides zusammen liegt bei unter 0,1 Aufruf pro Sekunde.
Mehrere Browser-Tabs dürfen nie je einen eigenen API-Abruf auslösen. Der Abruf gehört in den Collector, die Browser lesen nur dessen Ergebnis.

## Nutzungsbedingungen

- Die Daten dürfen laut Bedingungen der GTI für kostenlose Fahrgastinformation verwendet werden. Auf der Seite muss ein Link zu hvv.de stehen (das Dashboard hat ihn bereits im Hinweis-Panel). Die genauen Bedingungen bitte beim Freischalten des Zugangs noch einmal selbst lesen, vor allem bei öffentlicher Darstellung.
- Kein Weiterverkauf, kein Dauer-Polling in kurzen Abständen.

## Risiken

- **Baustellen 2026:** Wegen der Arbeiten am Harburger Tunnel weichen Fahrpläne und Ersatzverkehr ab. Schienenersatzverkehr taucht nicht zwingend als Linie `S5` auf; Lücken in den Pünktlichkeitsdaten sind dann kein Fehler im Collector.
- Positionsfehler bei Störungen (siehe oben), Busse oft zu ungenau, um daran „wo ist mein Bus“ zu entscheiden.
- Speicher und RAM des Raspberry Pi (1,8 GB): Karte A mit Retention begrenzen, eine eigene Webkarte (B) nur als schlanken Dienst betreiben.

## Messplan

```bash
# S-Bahn im Großraum Hamburg, 2-Minuten-Fenster, Linie S5 auflisten
python -m hvv_gti probe-vehicles --types S_BAHN --line S5

# gesamtes Netz (Größe und Latenz einschätzen)
python -m hvv_gti probe-vehicles

# nur Fahrplan statt Echtzeit (Vergleich)
python -m hvv_gti probe-vehicles --types S_BAHN --schedule
```

Entscheidungshilfe: Liegt die Antwort für `S_BAHN` unter etwa 200 KiB und unter 3 s Latenz, ist Variante A ohne weitere Optimierung machbar. Ist sie deutlich größer, wird `withoutCoords=true` (Standard) geprüft und das Gebiet auf eine Bounding-Box entlang der S5 eingeschränkt.
