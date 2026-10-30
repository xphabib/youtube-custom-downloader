#!/usr/bin/env bash
# Install ytclip for the current user on Ubuntu (no root, no Docker).
#
#   ./scripts/install.sh [--no-models] [--skip-extension] [--extension-id ID]
#
# Creates a virtualenv in ~/.local/share/ytclip/venv, links ~/.local/bin/ytclip,
# downloads detection models, registers the Chrome native messaging host and builds
# the extension into extension/dist. Safe to re-run (it upgrades in place).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ytclip"
VENV="$DATA_DIR/venv"
BIN_DIR="$HOME/.local/bin"
MODELS=1
EXTENSION=1
EXTRA_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-models) MODELS=0 ;;
    --skip-extension) EXTENSION=0 ;;
    --extension-id) EXTRA_IDS+=("--extension-id" "$2"); shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

step "Checking system packages"
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || die "Python 3.11+ is required (found $(python3 --version 2>&1))."
python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=(python3-venv)
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
command -v ffprobe >/dev/null || missing+=(ffmpeg)
if [[ ${#missing[@]} -gt 0 ]]; then
  die "Missing packages. Install them with:  sudo apt install ${missing[*]}"
fi
echo "python3 $(python3 -c 'import platform; print(platform.python_version())'), $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"

step "Installing the ytclip Python package into $VENV"
mkdir -p "$DATA_DIR"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$ROOT/cli"
# Same version number never triggers --upgrade, so always refresh ytclip itself.
"$VENV/bin/python" -m pip install --quiet --force-reinstall --no-deps "$ROOT/cli"

step "Linking $BIN_DIR/ytclip"
mkdir -p "$BIN_DIR"
if [[ -e "$BIN_DIR/ytclip" && ! -L "$BIN_DIR/ytclip" ]]; then
  die "$BIN_DIR/ytclip exists and is not a symlink; move it away and re-run."
fi
ln -sfn "$VENV/bin/ytclip" "$BIN_DIR/ytclip"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: add $BIN_DIR to your PATH, e.g.  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac

YTCLIP="$VENV/bin/ytclip"
"$YTCLIP" config --init >/dev/null

if [[ $MODELS -eq 1 ]]; then
  step "Downloading smart-crop detection models (~36 MB)"
  "$YTCLIP" models download || echo "Model download failed; smart crop will use fallback detection. Retry with: ytclip models download"
fi

step "Registering the Chrome native messaging host"
"$YTCLIP" native install "${EXTRA_IDS[@]}"

if [[ $EXTENSION -eq 1 ]]; then
  step "Building the Chrome extension"
  if command -v npm >/dev/null; then
    (cd "$ROOT/extension" && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)
  elif [[ -f "$ROOT/extension/dist/manifest.json" ]]; then
    echo "npm not found; using the existing build in extension/dist."
  else
    die "npm is required to build the extension (sudo apt install nodejs npm), or re-run with --skip-extension."
  fi
fi

step "Checking the installation"
"$YTCLIP" install-check || true

cat <<EOF

Load the extension in Chrome:
  1. Open chrome://extensions and enable "Developer mode".
  2. Click "Load unpacked" and choose: $ROOT/extension/dist
  3. Pin "ytclip", open a YouTube video and add clips.

Then process the queue at any time with:  ytclip run
EOF
