#!/usr/bin/env bash
# Developer setup: editable install in ./.venv plus extension dependencies.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e "cli[dev]"
(cd extension && npm install --no-audit --no-fund --loglevel=error)
echo "Ready. Run ./scripts/test.sh to run all tests."
