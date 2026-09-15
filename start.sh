#!/usr/bin/env bash
# One-click launcher for macOS / Linux.
# First run: creates a virtual environment and installs dependencies.
# Every run: starts RenewalTracker and opens it in your browser.
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "RenewalTracker needs Python 3.11 or newer. Install it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Enter to close..." _ || true
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up a private Python environment (this takes a minute)..."
  "$PY" -m venv .venv
fi

# Re-install only when requirements.txt changed since last time.
STAMP=.venv/.requirements.sha
CURRENT=$(shasum -a 256 requirements.txt 2>/dev/null || sha256sum requirements.txt)
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$CURRENT" ]; then
  echo "Installing dependencies..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  echo "$CURRENT" > "$STAMP"
fi

exec .venv/bin/python run.py "$@"
