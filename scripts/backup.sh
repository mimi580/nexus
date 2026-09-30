#!/usr/bin/env bash
# Nightly PostgreSQL backup with 14-day rotation.
# Cron (as the deploy user):  15 2 * * *  /opt/nexus/scripts/backup.sh >> /opt/nexus/backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
file="backups/nexus-${stamp}.sql.gz"
docker compose exec -T db pg_dump -U nexus --no-owner --clean --if-exists nexus | gzip > "${file}.partial"
mv "${file}.partial" "${file}"
find backups -name 'nexus-*.sql.gz' -mtime +14 -delete
echo "$(date -u +%FT%TZ) backup ok: ${file} ($(du -h "${file}" | cut -f1))"
