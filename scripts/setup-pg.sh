#!/usr/bin/env bash
set -euo pipefail

echo "=== Setting up PostgreSQL for OfferPilot ==="

# Ensure PostgreSQL is running
sudo pg_ctlcluster 16 main start 2>/dev/null || true

# Create user and database
sudo -u postgres psql -v ON_ERROR_STOP=0 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'offerpilot') THEN
    CREATE ROLE offerpilot WITH LOGIN PASSWORD 'offerpilot' CREATEDB;
  END IF;
END
$$;
SQL

sudo -u postgres psql -v ON_ERROR_STOP=0 <<'SQL'
SELECT 'CREATE DATABASE offerpilot OWNER offerpilot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'offerpilot')\gexec
SQL

# Apply init schema if exists
INIT_SQL="/mnt/e/0找工作/0大模型全栈知识库/OfferPilot/backend/sql/init_db.sql"
if [[ -f "$INIT_SQL" ]]; then
  echo "Applying init_db.sql..."
  PGPASSWORD=offerpilot psql -h 127.0.0.1 -U offerpilot -d offerpilot -f "$INIT_SQL" 2>/dev/null || true
fi

# Verify
echo ""
echo "=== Verification ==="
PGPASSWORD=offerpilot psql -h 127.0.0.1 -U offerpilot -d offerpilot -c "SELECT 'PostgreSQL OK' AS status;"
echo "Done."
