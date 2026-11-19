# Anweisung für Claude auf der neuen Maschine

Diese Datei komplett in eine neue Claude-Code-Sitzung auf der neuen Maschine einfügen (im Ordner, in dem das Repo liegen soll).
Vorher auf dem Pi erledigen: MIGRATION.md Schritte 1 und 2 (Tokens, Spool + Shipper, `push-assets`, `export-history`).

````markdown
Du richtest auf dieser Maschine den HVV-Routenplaner mit TimescaleDB ein. Repo: github.com/OCISLY-ds/hvv-gti (privat, Branch `dev`).

## Rahmen
- Der Raspberry Pi des Nutzers bleibt unverändert (Collector, InfluxDB, Grafana). Fasse ihn nicht an.
- Hier gibt es NUR: TimescaleDB, Importer, Routenplaner (+3D-Bahnhöfe), optional Cloudflare-Tunnel. Kein Grafana, kein Influx.
- Die Maschinen sprechen nie direkt miteinander. Der Weg ist: Pi -> GitHub-Releases -> Importer hier.
- Geheimnisse (Tokens, Passwörter, .env) niemals ausgeben, committen oder in Commit-Texte schreiben. Fehlende Tokens frag beim Nutzer ab.
- Das GTI-Limit ist 1 Aufruf/s für das Konto, der Pi nutzt ca. 0,2/s. `HVV_MIN_REQUEST_INTERVAL` nie unter 1.2 setzen.

## Schritte
1. **Maschine prüfen:** `docker compose version`, `git`, freier RAM (mindestens 4 GB, besser 8 GB+), freier Plattenplatz (mindestens 20 GB), Benutzer mit UID 1000 (sonst `user:` in `deploy/newhost/docker-compose.yml` anpassen). Berichte kurz, was fehlt, und installiere nichts Großes ohne Rückfrage.
2. **Repo holen:** `git clone git@github.com:OCISLY-ds/hvv-gti.git && cd hvv-gti && git checkout dev`. Falls der Zugriff fehlt, bitte den Nutzer um einen SSH-Key oder Deploy-Key, rate nichts.
3. **Lesen:** `deploy/newhost/MIGRATION.md` komplett, danach `deploy/newhost/docker-compose.yml`.
4. **GitHub prüfen:** Gibt es im Repo die Releases `assets-map`, `data-history` und `data-YYYY-MM` (nutzt den Read-only-Token des Nutzers: `curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/OCISLY-ds/hvv-gti/releases`)? Fehlen sie, STOPP: sage dem Nutzer genau, dass die Pi-Schritte 1 und 2 aus MIGRATION.md noch fehlen, und mach nicht weiter.
5. **.env anlegen:** `cd deploy/newhost && cp .env.example .env`. Erzeuge `POSTGRES_PASSWORD` und `HVV_RO_PASSWORD` mit `openssl rand -base64 24 | tr -d '/+='`. Frage den Nutzer nach `GITHUB_TOKEN` (fine-grained, nur dieses Repo, Contents: Read-only), `HVV_GTI_USER`, `HVV_GTI_PASSWORD`. Setze `DB_MEMORY` auf etwa die Hälfte des RAM und `DB_CPUS` auf die Kernzahl. `TUNNEL_TOKEN` bleibt leer.
6. **Datenbank und Dateien:**
   `mkdir -p ../../data ../../map/data`
   `docker compose up -d --build timescaledb dbinit`
   `docker compose run --rm importer pull-assets` (Kacheln, Gleise, Logos)
7. **Alles starten:** `docker compose up -d --build`, dann `docker compose logs -f importer`. Der Importer liest erst `data-history`, dann alle `data-YYYY-MM`. Warte, bis nur noch der 5-Minuten-Takt läuft (keine Fehler).
8. **Prüfen** (Befehle stehen in MIGRATION.md, Abschnitt 3):
   - `hvv_import_log`: jedes Paket vorhanden
   - `select m, count(*), min(time), max(time) from hvv_stop group by m`: Daten für die letzten ca. 30 Tage
   - `curl -s localhost:8090/healthz` und `curl -s 'localhost:8090/api/stations?q=Hbf'`
   - eine echte Routenabfrage im Planer mit Zuverlässigkeits-Prozenten (Station Stade nach Hamburg Hbf)
9. **Tunnel nur nach Rückfrage:** Erst wenn der Nutzer den Planer geprüft hat und ein `TUNNEL_TOKEN` gibt: `docker compose --profile tunnel up -d tunnel`. Den Tunnel auf dem Pi stoppt der Nutzer selbst.

## Ergebnis
Kurzer Bericht: was läuft, Zeilenzahlen je Messung, Zeitraum der Daten, Fehler oder offene Punkte, Ports. Nichts davon in Git committen.
````
