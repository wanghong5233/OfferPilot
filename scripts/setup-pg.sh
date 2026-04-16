#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DB_USER="${PULSE_PG_USER:-pulse}"
DB_PASSWORD="${PULSE_PG_PASSWORD:-pulse}"
DB_NAME="${PULSE_PG_DB:-pulse}"
INIT_SQL="${PULSE_INIT_SQL:-$PROJECT_DIR/backend/sql/init_db.sql}"

echo "=== Setting up PostgreSQL for Pulse ==="
echo "user=$DB_USER db=$DB_NAME"

# Ensure PostgreSQL is running
sudo pg_ctlcluster 16 main start 2>/dev/null || true

# Create user and database
sudo -u postgres psql -v ON_ERROR_STOP=0 <<SQL
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}' CREATEDB;
  END IF;
END
$$;
SQL

sudo -u postgres psql -v ON_ERROR_STOP=0 <<SQL
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL

# Apply init schema if exists
if [[ -f "$INIT_SQL" ]]; then
  echo "Applying init_db.sql..."
  PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -f "$INIT_SQL" 2>/dev/null || true
fi

# Verify
echo ""
echo "=== Verification ==="
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -c "SELECT 'PostgreSQL OK' AS status;"
echo "Done."
