#!/usr/bin/env sh
# Online backup of the SQLite database + recent reports, keeping the newest N backups.
# Usage: scripts/backup.sh [data_dir] [backup_dir] [keep]
# For PostgreSQL use: pg_dump --format=custom chanakya > backup.dump (schedule nightly).
set -eu
DATA_DIR="${1:-./data}"
BACKUP_DIR="${2:-./backups}"
KEEP="${3:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
python3 - "$DATA_DIR/chanakya.db" "$BACKUP_DIR/chanakya_$STAMP.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)  # consistent snapshot even while Chanakya is writing (WAL mode)
print(f"backup written: {dst}")
PY
tar -czf "$BACKUP_DIR/reports_$STAMP.tar.gz" -C "$DATA_DIR" reports 2>/dev/null || true
ls -1t "$BACKUP_DIR"/chanakya_*.db 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
ls -1t "$BACKUP_DIR"/reports_*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
