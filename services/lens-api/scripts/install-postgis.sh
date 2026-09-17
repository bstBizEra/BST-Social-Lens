#!/usr/bin/env bash
# Install PostGIS + pg_trgm on the bizera-wsl local PostgreSQL cluster (001D G1) and grant them to LensDB.
# Run inside WSL: bash services/lens-api/scripts/install-postgis.sh   (needs sudo; reads POSTGRES_DB from .env)
set -euo pipefail
cd "$(dirname "$0")/.."
DB=$(sed -n 's/^POSTGRES_DB=//p' .env | tr -d '\r"' | head -1); DB=${DB:-lens}
PGV=$(psql --version | grep -oE '[0-9]+' | head -1)
echo "PostgreSQL major: $PGV, database: $DB"
if ! dpkg -l | grep -q "postgresql-${PGV}-postgis"; then
  sudo apt-get update -q
  sudo apt-get install -y -q "postgresql-${PGV}-postgis-3" "postgresql-${PGV}-postgis-3-scripts"
fi
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -tAc "select extname||' '||extversion from pg_extension where extname in ('postgis','pg_trgm')" -d "$DB"
echo "restart lens-api to apply schema_postgis.sql: sudo systemctl restart bst-lens-api"
