#!/usr/bin/env bash
# Database backup script (pg_dump).
# Usage: ./backup.sh [output_file]
# Requires: POSTGRES_HOST, POSTGRES_USER, POSTGRES_DB (and optionally POSTGRES_PASSWORD).

set -e
OUT_FILE="${1:-backup-$(date +%Y%m%d-%H%M%S).sql}"
export PGPASSWORD="${POSTGRES_PASSWORD:-defi}"
pg_dump -h "${POSTGRES_HOST:-localhost}" -U "${POSTGRES_USER:-defi}" -d "${POSTGRES_DB:-defi_vault}" -f "$OUT_FILE"
echo "Backup written to $OUT_FILE"
