#!/usr/bin/env bash
set -Eeuo pipefail

HERMES_HOME="${HERMES_HOME:-/home/lighthouse/.hermes}"
APP_DIR="${HERMES_APP_DIR:-$HERMES_HOME/hermes-agent}"
PYTHON="${HERMES_PYTHON:-$APP_DIR/venv/bin/python}"
USER_NAME="${HERMES_USER:-lighthouse}"
LOG_FILE="$HERMES_HOME/logs/agent.log"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ -x "$PYTHON" ]] || fail "missing Python executable: $PYTHON"
[[ -d "$APP_DIR" ]] || fail "missing Hermes app directory: $APP_DIR"
[[ -d "$HERMES_HOME" ]] || fail "missing Hermes home: $HERMES_HOME"

"$PYTHON" -m hermes_cli.main --version >/dev/null 2>&1 || fail "Hermes CLI is not importable"

if command -v pgrep >/dev/null 2>&1; then
  pgrep -u "$USER_NAME" -f 'hermes_cli.main gateway run' >/dev/null 2>&1 \
    || fail "gateway process is not running for user $USER_NAME"
fi

[[ -f "$LOG_FILE" ]] || fail "missing agent log: $LOG_FILE"

printf 'OK: Hermes CLI, gateway process, and log path are present\n'
