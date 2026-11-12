#!/usr/bin/env bash
# Baut map/data/hamburg.pmtiles fuer Hamburg und das Umland (Stade, Buxtehude, Pinneberg, Elmshorn, Kaltenkirchen,
# Ahrensburg, Lueneburg ...) aus den Geofabrik-Extrakten Hamburg, Niedersachsen und Schleswig-Holstein.
# Aufruf: sudo ./build.sh (braucht Docker). Anderer Ausschnitt: sudo BBOX=lon1,lat1,lon2,lat2 ./build.sh
# tilemaker-Konfiguration/Lua stammen aus FreiFahren/FreiFahren (MIT), packages/tile-server.
set -euo pipefail
cd "$(dirname "$0")"
OUT="$(cd .. && pwd)/data"
BBOX="${BBOX:-8.6,53.1,10.65,53.95}" # lon1,lat1,lon2,lat2 (WGS84); Hamburg liegt bei 9.7-10.35 / 53.4-53.75
REGIONS="hamburg niedersachsen schleswig-holstein"
GEOFABRIK="https://download.geofabrik.de/europe/germany"
TAG="${BBOX//,/_}"
mkdir -p "$OUT" work
# osmium braucht fuer seine ID-Mengen rund 1,5 GB unabhaengig von der Dateigroesse (Node-IDs sind ueber den ganzen ID-Raum
# gestreut), der Pi hat weniger frei. Deshalb waehrend des Baus eine Swap-Datei auf der SSD (Prioritaet unter zram).
SWAP="$PWD/work/swapfile"
cleanup() { swapoff "$SWAP" 2>/dev/null || true; rm -f "$SWAP"; rm -rf work/store; }
trap cleanup EXIT
cleanup # Rest eines abgebrochenen Laufs
fallocate -l 6G "$SWAP" 2>/dev/null || dd if=/dev/zero of="$SWAP" bs=1M count=6144 status=none
chmod 600 "$SWAP"
mkswap -q "$SWAP"
swapon -p 5 "$SWAP"
rm -f work/*.part.part work/part-*.part work/umland-*.part # Reste eines abgebrochenen Laufs

# Quellen werden nur geladen, wenn sie fehlen (zum Aktualisieren work/*-latest.osm.pbf loeschen). Abbrueche setzt curl fort.
for region in $REGIONS; do
  src="work/$region-latest.osm.pbf"
  if [ ! -f "$src" ]; then
    echo "lade $region ..."
    curl -fL -C - -o "$src.part" "$GEOFABRIK/$region-latest.osm.pbf"
    mv "$src.part" "$src"
  fi
done

# osmium-tool in einem kleinen Debian-Image (ohne Build-Kontext, work/ ist mehrere GB gross)
docker build -q --network host -t hvv-osmium - <<'EOF'
FROM debian:stable-slim
RUN apt-get update && apt-get install -y --no-install-recommends osmium-tool && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["osmium"]
EOF
osmium() { docker run --rm -v "$PWD/work:/w" -w /w hvv-osmium "$@"; }

# Ausschnitt je Land; complete_ways nimmt Wege vollstaendig mit (smart braeuchte zusaetzlich Speicher fuer Relationen)
parts=()
for region in $REGIONS; do
  part="work/part-$region-$TAG.osm.pbf"
  if [ ! -f "$part" ]; then
    echo "schneide $region auf $BBOX ..."
    osmium extract -v --bbox "$BBOX" --strategy complete_ways --overwrite -f pbf -o "${part#work/}.part" "$region-latest.osm.pbf"
    mv "$part.part" "$part"
  fi
  parts+=("${part#work/}")
done

MERGED="work/umland-$TAG.osm.pbf"
if [ ! -f "$MERGED" ]; then
  echo "fuege zusammen ..."
  osmium merge -v --overwrite -f pbf -o "${MERGED#work/}.part" "${parts[@]}"
  mv "$MERGED.part" "$MERGED"
fi

# --store legt Zwischendaten auf die Platte, damit der Pi (Pi 4, 2 GB RAM) mit wenig Speicher auskommt
echo "baue Kacheln (das dauert auf dem Pi laenger) ..."
docker run --rm -v "$PWD:/src" -v "$OUT:/out" -w /src ghcr.io/systemed/tilemaker:master \
  --input "$MERGED" --output /out/hamburg-new.pmtiles \
  --config config.json --process process.lua --store work/store --threads 2
# erst jetzt ersetzen, damit eine laufende Karte nie eine halbfertige Datei liest
# tilemaker uebernimmt die Grenzen aus dem Bbox-Header der Eingabe, den osmium merge nicht schreibt: sonst bleibt die Karte leer
python3 fix_header.py "$OUT/hamburg-new.pmtiles" "$BBOX"
chown "${SUDO_UID:-$(id -u)}:${SUDO_GID:-$(id -g)}" "$OUT/hamburg-new.pmtiles"
mv -f "$OUT/hamburg-new.pmtiles" "$OUT/hamburg.pmtiles"
# Zwischenstaende (Ausschnitte, Zusammenfuehrung) loeschen, die Quellen bleiben fuer den naechsten Lauf
rm -f work/part-*.osm.pbf work/umland-*.osm.pbf
echo "fertig: $OUT/hamburg.pmtiles"
