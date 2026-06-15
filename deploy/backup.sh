#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

HERMES_HOME="${HERMES_HOME:-/home/lighthouse/.hermes}"
BACKUP_DIR="${HERMES_BACKUP_DIR:-/home/lighthouse/hermes-backups}"
STAMP="$(date +%F-%H%M%S)"
WORK_DIR="$(mktemp -d)"
ARCHIVE="$BACKUP_DIR/hermes-backup-$STAMP.tar.gz"
STATE_BACKUP="$BACKUP_DIR/hermes-state-$STAMP.db.gz"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"

if [[ -f "$HERMES_HOME/state.db" ]] && command -v sqlite3 >/dev/null 2>&1; then
  mkdir -p "$WORK_DIR/sqlite"
  sqlite3 "$HERMES_HOME/state.db" ".backup '$WORK_DIR/sqlite/state.db'"
fi

tar \
  --exclude='hermes-agent/venv' \
  --exclude='hermes-agent/node_modules' \
  --exclude='audio_cache' \
  --exclude='image_cache' \
  --exclude='sandboxes' \
  -czf "$ARCHIVE" \
  -C "$(dirname "$HERMES_HOME")" "$(basename "$HERMES_HOME")"

if [[ -f "$WORK_DIR/sqlite/state.db" ]]; then
  gzip -c "$WORK_DIR/sqlite/state.db" > "$STATE_BACKUP"
  chmod 600 "$STATE_BACKUP"
  printf 'SQLite state backup written: %s\n' "$STATE_BACKUP"
fi

chmod 600 "$ARCHIVE"
printf 'Backup written: %s\n' "$ARCHIVE"
