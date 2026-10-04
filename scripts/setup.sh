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
VENV="$PROJECT_ROOT/.venv"

printf 'Installing challenge tools...\n'
cd "$PROJECT_ROOT"

if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
elif command -v python3.13 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.13)"
else
  printf 'Error: Meta Ads CLI requires Python 3.12 or 3.13.\n' >&2
  exit 1
fi

VENV_SUPPORTED=false
if [ -x "$VENV/bin/python" ]; then
  VENV_SUPPORTED="$($VENV/bin/python -c 'import sys; print(str(sys.version_info[:2] in {(3, 12), (3, 13)}).lower())')"
fi
if [ "$VENV_SUPPORTED" != "true" ]; then
  "$PYTHON_BIN" -m venv --clear "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt

"$VENV/bin/meta" --version
"$VENV/bin/skool-data" version
printf 'Tool installation complete.\n'
