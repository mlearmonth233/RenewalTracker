#!/usr/bin/env bash
# macOS double-click launcher: Finder opens .command files in Terminal.
# It simply hands over to start.sh in the same folder.
cd "$(dirname "$0")"
chmod +x start.sh 2>/dev/null || true
exec ./start.sh "$@"
