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
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt

"$VENV/bin/meta" --version
"$VENV/bin/skool-data" version
printf 'Tool installation complete.\n'
