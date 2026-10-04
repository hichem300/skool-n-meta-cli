#!/usr/bin/env bash
set -euo pipefail

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
cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m scripts.doctor "$@"
