#!/usr/bin/env bash
# Run every automated test: Python (unit + ffmpeg integration) and the extension.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"

echo "== Python tests"
(cd "$ROOT/cli" && "$PY" -m pytest -q "$@")

echo "== Extension typecheck and tests"
(cd "$ROOT/extension" && npx tsc --noEmit && npx vitest run)
