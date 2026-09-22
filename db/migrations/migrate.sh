#!/bin/sh
set -eu

psql --dbname "$APP_DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  checksum text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

for migration in /migrations/V*.sql; do
  version=$(basename "$migration" .sql)
  checksum=$(sha256sum "$migration" | awk '{print $1}')
  stored=$(psql --dbname "$APP_DB_NAME" -Atqc "SELECT checksum FROM schema_migrations WHERE version = '$version'")
  if [ -n "$stored" ]; then
    [ "$stored" = "$checksum" ] || { echo "checksum mismatch for $version" >&2; exit 1; }
    continue
  fi
  psql --dbname "$APP_DB_NAME" -v ON_ERROR_STOP=1 -1 -f "$migration"
  psql --dbname "$APP_DB_NAME" -v ON_ERROR_STOP=1 -c "INSERT INTO schema_migrations(version, checksum) VALUES ('$version', '$checksum')"
done
