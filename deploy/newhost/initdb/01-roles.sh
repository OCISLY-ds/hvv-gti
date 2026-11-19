#!/bin/bash
# Laeuft einmal beim ersten Start der Datenbank: Nur-Lese-Rolle fuer den Routenplaner (sieht auch spaeter angelegte Tabellen).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
create role hvv_ro login password '${HVV_RO_PASSWORD}';
grant connect on database ${POSTGRES_DB} to hvv_ro;
grant usage on schema public to hvv_ro;
alter default privileges in schema public grant select on tables to hvv_ro;
SQL
