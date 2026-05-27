#!/usr/bin/env bash
# Origin — PostgreSQL backup script
#
# Usage:
#   ./scripts/backup_db.sh
#   BACKUP_DIR=/mnt/backups ./scripts/backup_db.sh
#
# Restore:
#   psql "$DATABASE_URL" < backups/origin_20260526_0300.sql
#
# Recommended: run via cron at 3am daily
#   0 3 * * * /path/to/origin/scripts/backup_db.sh >> /var/log/origin-backup.log 2>&1

set -euo pipefail

# ── Config ────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$0")/../backups}"
DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost/origin}"
KEEP_DAYS="${KEEP_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M)
BACKUP_FILE="${BACKUP_DIR}/origin_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

# ── Dump ──────────────────────────────────────────────────────
echo "[$(date -u +%FT%TZ)] Starting backup → $BACKUP_FILE"
pg_dump "$DATABASE_URL" \
    --no-password \
    --format=plain \
    --no-owner \
    --no-acl \
    | gzip > "$BACKUP_FILE"

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "[$(date -u +%FT%TZ)] Backup complete: $BACKUP_FILE ($SIZE)"

# ── Prune old backups ─────────────────────────────────────────
PRUNED=$(find "$BACKUP_DIR" -name "origin_*.sql.gz" -mtime "+${KEEP_DAYS}" -print -delete | wc -l)
echo "[$(date -u +%FT%TZ)] Pruned $PRUNED backup(s) older than ${KEEP_DAYS} days"
