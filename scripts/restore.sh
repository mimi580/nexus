#!/usr/bin/env bash
# Restore a backup made by backup.sh. Stops the worker and API while restoring.
#   scripts/restore.sh backups/nexus-20261001T021500Z.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."
file="${1:?usage: scripts/restore.sh backups/<file>.sql.gz}"
[ -f "$file" ] || { echo "no such file: $file"; exit 1; }
read -r -p "This replaces the current database with ${file}. Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "cancelled"; exit 1; }
docker compose stop worker api
gunzip -c "$file" | docker compose exec -T db psql -U nexus -d nexus -v ON_ERROR_STOP=1 >/dev/null
docker compose run --rm migrate
docker compose start api worker
echo "restored ${file}; stuck tasks are requeued automatically on the next worker cycle"
