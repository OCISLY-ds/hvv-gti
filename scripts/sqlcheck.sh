#!/usr/bin/env bash
# sqlcheck.sh [Optionen fuer sqlcheck.py] < abfrage.sql   - startet die Pruefung im Container gegen die Testdatenbank (hvv-dbtest, Port 5544)
C=/home/florian/projects/hvv-gti/docker-compose.yml
exec sudo -n docker compose -f $C run --rm --no-deps -T -v /home/florian/projects/hvv-gti/scripts:/app/scripts:ro -v /home/florian/projects/hvv-gti/hvv_gti:/app/hvv_gti:ro \
  -e HVV_TEST_DB=postgresql://postgres:test@127.0.0.1:5544/hvv --entrypoint python hvv-collector /app/scripts/sqlcheck.py "$@" 2>&1 | grep -v "^ Container\|kernel does not support"
