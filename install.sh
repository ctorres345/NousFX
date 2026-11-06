#!/usr/bin/env bash
# Runs the NousFX sync: a dry run first, then the real run once you confirm.
# All arguments go to scripts/nousfx_sync.py, for example: ./install.sh --tools claude,agy
# This script copies nothing itself; docs/sync.md says what the sync writes.
set -euo pipefail

SYNC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/nousfx_sync.py"
PY=python3
command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' 2>/dev/null || PY=python

"$PY" -B "$SYNC" --dry-run "$@"
printf 'Apply these changes? [y/N] '
answer=
read -r answer || [ -n "$answer" ] || answer=n
case "$answer" in
  y|Y|yes|YES) "$PY" -B "$SYNC" "$@" ;;
  *) echo "Nothing written." ;;
esac
