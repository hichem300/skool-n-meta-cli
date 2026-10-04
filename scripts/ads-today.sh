#!/usr/bin/env bash
set -euo pipefail
# Resolve symlink chain so global wrapper (e.g. /root/.local/bin/ads-today) works.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  TARGET="$(readlink -- "$SOURCE")"
  case "$TARGET" in
    /*) SOURCE="$TARGET" ;;
    *) SOURCE="$(dirname -- "$SOURCE")/$TARGET" ;;
  esac
done
SCRIPT_DIR="$(cd -- "$(dirname -- "$SOURCE")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
exec "$PROJECT_ROOT/.venv/bin/python" "$SCRIPT_DIR/today_report.py" "$@"
