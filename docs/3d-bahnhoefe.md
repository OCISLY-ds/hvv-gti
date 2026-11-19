# 3D-Bahnhöfe: Stand und was eine stärkere Maschine besser kann

Stand: 19.11.2026, Branch `dev` (Commits `2486e34`, `d848d1f`, `4b8efd5`). Kurzfassung steht im README („Bahnhöfe in 3D (Prototyp)"), hier die Einordnung.

## 1. Was es heute gibt

**Seite:** `/station.html?id=<bahnhof>&from=<bahnsteig>&to=<bahnsteig>` (`map/static/station.html/.css/.js`, 447 Zeilen JS), gezeichnet mit MapLibre `fill-extrusion`.
Der Routenplaner verlinkt sie bei Umstiegen („Umstieg in 3D ansehen"), wenn Bahnhof, Gleis und Verkehrsmittel eindeutig zu einem Bahnsteig passen (`hvv_gti/stationmatch.py`, `planner.attach_models`).

**Was man sieht:**
- Eine Bodenplatte auf Ebene 0 mit Straßen und Straßennamen, Gebäuden in ihrer Höhe, Bäumen, Grünflächen, Wasser, Orten und Eingängen; darunter die Ebenen des Bahnhofs (Bahnsteige nach Verkehrsmittel gefärbt: S grün, U blau, Regional/Fern rot; Gänge, Gleise, Räume, Treppen als Stufenblöcke, Rolltreppen, Aufzüge).
- Der Umsteigeweg als leuchtendes Band mit Zeit, Länge und Schritten. Ebenenwahl blendet die übrigen gedimmt ein, ein Regler zieht die Ebenen auseinander, `Umgebung` ist voll/gedämpft/aus, dunkel/hell folgt dem System, die Kamera fährt auf den Umstieg.

**Datenweg:** OpenStreetMap über Overpass, nicht GTI.
`scripts/build_stations.py [--offline] [id ...]` → Rohdaten in `map/data/osm-cache/` (gitignored) → `hvv_gti/stationmap.py` (Elemente klassifizieren, ebenenbewusster Fußwegegraph, Dijkstra zwischen allen Bahnsteigen) und `hvv_gti/stationctx.py` (Umgebung) → fertige Modelle in `map/seed/stations/<id>.json` und `<id>.ctx.json` (in Git, werden unter `/stations/` ausgeliefert).
`scripts/preview_station.py` zeichnet ein Modell ohne Browser als PNG.

| Bahnhof | Bahnsteige | Wege | Zustand |
|---|---|---|---|
| Hamburg Hbf | 11 | 110, alle berechnet | gut; S-Bahn Gleis 1/2 fehlt in OSM, deshalb kein Link bei S-Bahn-Umstiegen dort |
| Berliner Tor | 5 | 20 (12 berechnet, 8 nur aus der Luftlinie geschätzt) | brauchbar |
| Altona | 6 | 30 | brauchbar |
| Jungfernstieg | 2 | 2 | Bahnsteige ohne Gleisnummern, 14 „Ebenen" (OSM-Tagging), kaum nutzbar |
| Barmbek | 0 | 0 | OSM liefert keine verwertbaren Bahnsteige; nur Eingänge |
| Landungsbrücken, Ohlsdorf, Dammtor | – | – | stehen in `STATIONS`, aber nie gebaut (Overpass lieferte 502/Timeouts) |

Umgebung je Bahnhof (Beispiel Hbf): 888 Straßenstücke, 483 Gebäude, 694 Bäume, 69 Grünflächen, 70 Orte, 60 Straßennamen.

## 2. Bekannte Grenzen (heute)

- **Nie auf einem echten Gerät gesehen.** Getestet nur mit Headless-Chromium und Software-WebGL auf dem Pi (Screenshots dunkel/hell/Handy-Format). Ob es auf einem Handy flüssig läuft, ist unbekannt; die Umgebung hat einige tausend Flächen.
- **Flache Klötze.** `fill-extrusion` kann nur ausgezogene Grundrisse: keine Dächer, keine Fassaden, keine Schrägen, echte Treppen sind Blöcke, Bäume sind Quader. Das Ergebnis ist ein Diorama, kein Modell.
- **Pi-Limits im Code:** `MAX_TREES = 1200`, `MAX_POIS = 70`, 60 Straßennamen, kleine Bounding-Boxen (je rund 0,5–1 km) in `STATIONS`.
- **Qualität hängt an OSM.** Fehlende Bahnsteige, Treppen oder Aufzüge sind Lücken im Modell; `src: "estimated"` markiert Wege, die nicht auf dem Graphen berechnet sind.
- **Feste Gehzeiten:** `WALK_MS = 1.25 m/s`, `STAIR_MS = 0.7 m/s`, unabhängig von Tageszeit, Gepäck, Richtung der Rolltreppe oder Andrang.
- **Verknüpfung mit der GTI:** nur eindeutige Treffer (Bahnhof/Alias + Gleisnummer + Verkehrsmittel). Alles Mehrdeutige bekommt keinen Link.
- **Overpass-Abhängigkeit:** die öffentlichen Server sind langsam und brechen bei großen Abfragen ab; ein Neuaufbau braucht Netz (oder den Cache).

## 3. Was auf einer deutlich stärkeren Maschine besser geht

Geordnet nach Nutzen für den Aufwand.

**A. Daten (größter Hebel, ohne Frontend-Umbau)**
1. **Lokaler OSM-Auszug statt Overpass.** Hamburg-Extrakt (Geofabrik) einmal laden, mit `osmium`/`pyosmium` oder einer lokalen Overpass-Instanz abfragen. Keine Timeouts, beliebig große Bereiche, Neuaufbau in Sekunden, offline und wiederholbar. Damit fehlen Landungsbrücken, Ohlsdorf, Dammtor nicht mehr und alle S-/U-Knoten lassen sich bauen.
2. **Lücken schließen:** fehlende Bahnsteige aus `public_transport=stop_position`/`stop_area` ableiten (Hbf S-Bahn Gleis 1/2, Barmbek); zusätzlich eine kleine Korrekturdatei von Hand (`overrides/<id>.json`), damit solche Fälle nicht an OSM hängen. Parallel die Lücken in OSM selbst nachtragen.
3. **Amtliche 3D-Daten prüfen:** Hamburg stellt (nach meinem Kenntnisstand) ein 3D-Stadtmodell (LoD2) und ein Geländemodell als Open Data bereit. Das würde echte Dachformen und Höhen statt OSM-Schätzungen liefern. Lizenz und Format vor dem Einsatz prüfen.
4. **Mehr Umgebung:** `MAX_TREES`, `MAX_POIS`, Straßennamen und Radius anheben, weitere Objekte (Bänke, Masten, Zugänge zu Geschäften).

**B. Darstellung**
1. **Echte 3D-Engine statt Extrusion** (three.js oder deck.gl mit glTF): Dachformen, Treppen mit Stufen und Geländern, Bäume als Instanzen, Schatten, Umgebungsverdeckung. Dazu die Modelle serverseitig vorbauen (z. B. `trimesh`/Blender headless → eine `.glb` je Bahnhof, auch als Fallback für schwache Handys).
2. **Vorberechnete Vorschaubilder** der Umstiege (Headless-Chromium auf der starken Maschine) für die Routenliste, damit das Handy nichts rendern muss, bevor man in 3D geht.
3. **Kacheln statt Riesen-JSON:** Umgebung als PMTiles/Vektorkacheln (tippecanoe) mit Detailstufen, damit auf dem Handy nur sichtbare Teile geladen werden.

**C. Routing und Inhalt**
1. **Bessere Gehzeiten:** richtungsabhängige Rolltreppen, Wartezeit am Aufzug, Sperren/Türen, optional Gepäck-/Barrierefrei-Variante (nur Aufzüge). Ein Graphdienst (z. B. Valhalla/OpenTripPlanner mit Indoor-Routing) rechnet das auf einer stärkeren Maschine ohne Mühe.
2. **Wagenposition:** aus GTI-Gleisangabe plus Haltepunkt „Zugende" ableiten, an welcher Stelle des Bahnsteigs man aussteigt und welcher Ausgang/Treppe am nächsten ist.
3. **Live im Modell:** die Fahrzeuge aus `getVehicleMap` (Interpolation wie in `motion.js`) auf den Bahnsteigen einfahren lassen und die Abfahrten je Bahnsteig einblenden; Umstiegsweg animiert mit der geschätzten Zeit.
4. **Eigene Messdaten nutzen:** die tatsächlichen Umstiegsverläufe aus der Statistik (wie oft wird ein Anschluss mit n Minuten erreicht) neben dem Modell zeigen.

**D. Qualität und Betrieb**
1. **Echte Browser-Tests:** Playwright mit richtigem Chromium auf der starken Maschine, Screenshots aller Bahnhöfe (dunkel/hell, Handy/Desktop) als Regressionstest; Bildraten-Messung mit CPU-Drosselung für schwache Handys. Auf dem Pi geht das nur mit Software-WebGL und DevTools-Umweg.
2. **Modell-Checks nach jedem Bau:** Anzahl Bahnsteige, Zusammenhang des Graphen, Anteil `estimated`, Ebenen mit negativer Basis (wird von MapLibre still verworfen) als automatische Prüfung.
3. **Regelmäßiger Neuaufbau** der Modelle (z. B. wöchentlich aus einem frischen OSM-Auszug) mit Diff, damit man sieht, welche Bahnhöfe sich verbessern oder verschlechtern.

## 4. Praktische Hinweise für die neue Maschine

- Die fertigen Modelle (`map/seed/stations/`) liegen in Git; die neue Maschine bekommt sie mit `git clone`. Das Release `assets-map` enthält nur Kacheln, Gleise, Signale und Logos, **nicht** den `osm-cache`. Neue Modelle bauen braucht dort also Netz zu Overpass oder (besser) den lokalen OSM-Auszug.
- Reihenfolge, die ich empfehlen würde: A1 (lokaler Auszug) → A2 (Lücken) → D1 (echte Browser-Tests) → B1/B3 (Darstellung). Alles Weitere hängt davon ab, ob die 3D-Ansicht auf echten Handys überzeugt.
- Gelernt beim Bauen: `fill-extrusion-base` darf nicht negativ sein; die Umgebung muss nach dem Untergrund gezeichnet werden, sonst verdeckt der Tiefentest ihn auch bei Deckkraft 0,3; Overpass braucht `out body geom`, sonst fehlen die Knoten-IDs für den Graphen.
