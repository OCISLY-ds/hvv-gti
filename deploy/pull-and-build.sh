#!/usr/bin/env bash
# Zieht den Branch und baut die Karte neu, wenn es etwas Neues gibt. Gedacht fuer Cron auf dem zweiten Host:
#   */5 * * * * /opt/hvv-gti/deploy/pull-and-build.sh
# Nicht versionierte Dateien (.env, data/, map/data/, docker-compose.override.yml) bleiben unberuehrt.
# Einstellungen per Umgebungsvariable: REPO, BRANCH, SERVICES, LOG, LOCK
set -euo pipefail

main() {
  local repo="${REPO:-/opt/hvv-gti}" branch="${BRANCH:-dev}" services="${SERVICES:-hvv-map}"
  local log="${LOG:-$HOME/hvv-deploy.log}" lock="${LOCK:-/tmp/hvv-deploy.lock}"

  exec 9>"$lock"
  flock -n 9 || exit 0 # laeuft schon

  cd "$repo"
  git fetch --quiet origin "$branch"
  [ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$branch")" ] && exit 0

  if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt 1048576 ]; then
    tail -n 500 "$log" > "$log.tmp" && mv "$log.tmp" "$log"
  fi
  {
    echo "=== $(date '+%F %T') $(git rev-parse --short HEAD) -> $(git rev-parse --short "origin/$branch")"
    git reset --hard "origin/$branch"
    # shellcheck disable=SC2086 # mehrere Dienste moeglich
    docker compose up -d --build $services
    docker image prune -f
  } >> "$log" 2>&1
}

# Alles in einer Funktion, die Bash vollstaendig einliest: das Skript liegt im Repo, und `git reset --hard` darf es
# mitten im Lauf ersetzen, ohne dass Bash ab der Mitte der neuen Datei weiterliest.
main "$@"
exit
