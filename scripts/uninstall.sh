#!/usr/bin/env bash
# Remove ytclip's program files and Chrome registration.
#
#   ./scripts/uninstall.sh          keep the job database, models, config and cache
#   ./scripts/uninstall.sh --purge  also delete the database, models, config and cache
#
# Rendered videos in your output directory are never deleted.
set -euo pipefail

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ytclip"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/ytclip"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ytclip"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

if [[ -x "$DATA_DIR/venv/bin/ytclip" ]]; then
  "$DATA_DIR/venv/bin/ytclip" native uninstall || true
fi
[[ -L "$HOME/.local/bin/ytclip" ]] && rm "$HOME/.local/bin/ytclip"
rm -rf "$DATA_DIR/venv" "$DATA_DIR/bin"

if [[ $PURGE -eq 1 ]]; then
  rm -rf "$DATA_DIR" "$CACHE_DIR" "$CONFIG_DIR"
  echo "Removed ytclip, its data, cache and configuration. Output videos were kept."
else
  echo "Removed ytclip. Kept $DATA_DIR (jobs, models), $CONFIG_DIR and $CACHE_DIR."
fi
echo "Remove the extension from chrome://extensions if you no longer need it."
