#!/usr/bin/env bash
set -Eeuo pipefail

HERMES_HOME="${HERMES_HOME:-/home/lighthouse/.hermes}"
APP_DIR="${HERMES_APP_DIR:-$HERMES_HOME/hermes-agent}"
BRANCH="${HERMES_UPDATE_BRANCH:-main}"
PYTHON="$APP_DIR/venv/bin/python"

cd "$APP_DIR"

OLD_COMMIT="$(git rev-parse --short HEAD)"
printf 'Current commit: %s\n' "$OLD_COMMIT"

"$APP_DIR/deploy/backup.sh"

git fetch --prune origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PYTHON" -e '.[all]'
else
  "$PYTHON" -m ensurepip --upgrade
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -e '.[all]'
fi

"$PYTHON" -m hermes_cli.main --version
printf 'Updated from %s to %s\n' "$OLD_COMMIT" "$(git rev-parse --short HEAD)"
printf 'Restart with: systemctl --user restart hermes-gateway.service\n'
