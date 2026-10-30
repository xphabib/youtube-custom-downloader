# Installation (Ubuntu)

## 1. System packages

```bash
sudo apt update
sudo apt install python3 python3-venv ffmpeg nodejs npm
python3 --version   # must be 3.11 or newer (Ubuntu 24.04 ships 3.12)
ffmpeg -hide_banner -encoders | grep -E "libx264|aac"   # both must be listed
```

Node.js and npm are only needed to build the extension. If you already have Node
through `nvm`, you can use that instead.

Optional: yt-dlp needs a JavaScript runtime for YouTube. It uses `deno` if it is
installed, and otherwise ytclip passes `node` or `bun` from your `PATH`.

## 2. One-step install

From the repository root:

```bash
./scripts/install.sh
```

| Option | Effect |
|---|---|
| `--no-models` | Skip downloading the detection models (smart crop falls back, see below) |
| `--skip-extension` | Don't build the extension |
| `--extension-id ID` | Also allow another extension ID (for example a re-keyed fork) |

The installer:

1. Checks the system packages above.
2. Creates `~/.local/share/ytclip/venv` and installs `cli/` into it, together with
   typer, rich, numpy, opencv-python-headless and yt-dlp.
3. Links `~/.local/bin/ytclip`. It refuses to replace an existing file that is not a
   symlink. If `~/.local/bin` is not on your `PATH`, it prints the line to add to
   `~/.bashrc`.
4. Writes a commented `~/.config/ytclip/config.toml` if none exists.
5. Downloads the detection models (see [Model setup](#model-setup)).
6. Registers the native messaging host (see [Native host](#native-messaging-host)).
7. Builds the extension into `extension/dist` with `npm ci && npm run build`.
8. Runs `ytclip install-check`.

Re-run it at any time to upgrade. Your queue, config and outputs are kept.

## 3. Load the Chrome extension

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose `<repo>/extension/dist`.
4. Pin **ytclip** using the puzzle-piece menu.

`extension/public/manifest.json` contains a public `key`, which gives the unpacked
extension the fixed ID **`fbajbpimmccmfkjgmhcdcoelbkgnkmdm`** wherever you load it from.
The native host only accepts requests from this ID.

To rebuild after changing the extension:

```bash
cd extension
npm ci
npm run build          # typecheck + production build into dist/
npm run dev            # or: rebuild on every change
```

Then click the reload icon on the extension's card in `chrome://extensions`.

## Native messaging host

Chrome starts `ytclip-native-host` when the extension sends a message. Registration is a
JSON manifest:

```bash
ytclip native install                  # Chrome, plus Chromium/Brave if their profiles exist
ytclip native install --extension-id abcdefghijklmnopabcdefghijklmnop   # allow another ID too
ytclip native uninstall
```

It writes:

- `~/.local/share/ytclip/bin/ytclip-native-host`, a small shell launcher that runs
  `python -m ytclip.native.host` with the virtualenv's Python. If `YTCLIP_CONFIG` is set
  during install, the launcher exports it too.
- `~/.config/google-chrome/NativeMessagingHosts/com.ytclip.host.json` (and the
  Chromium/Brave equivalents):

```json
{
  "name": "com.ytclip.host",
  "description": "ytclip local clip queue",
  "path": "/home/you/.local/share/ytclip/bin/ytclip-native-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://fbajbpimmccmfkjgmhcdcoelbkgnkmdm/"]
}
```

No network port is opened. Chrome talks to the host over stdin/stdout, and only the
listed extension is allowed.

## Model setup

Smart crop works best with two small ONNX models from
[OpenCV Zoo](https://github.com/opencv/opencv_zoo). They run on the CPU through OpenCV's
DNN module, with no GPU and no extra runtime.

| Model | Purpose | Size | License |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` | Face detection (YuNet) | 0.23 MB | MIT |
| `object_detection_yolox_2022nov.onnx` | People, animals and some objects (YOLOX-S, COCO) | 35.9 MB | Apache-2.0 |

```bash
ytclip models download      # fetches from Hugging Face (GitHub as mirror), verifies SHA-256
ytclip models list
```

The files are stored in `~/.local/share/ytclip/models/` and are not committed to the
repository. To install them by hand, for example on an offline machine, copy files with
exactly these names into that directory. `ytclip models download` checks the SHA-256 of
anything it downloads; files you copy in are recognised by name and size.

**Why these models?** Both are permissively licensed, small, CPU-fast (about 65 ms per
frame for YOLOX-S at 640×640, and a few ms for YuNet) and run with OpenCV, which is
already a dependency. Alternatives were rejected for these reasons:

- Ultralytics YOLOv8: AGPL-3.0 licence and a PyTorch dependency.
- MediaPipe: an extra large dependency with Python version constraints.
- MobileNet-SSD: lower accuracy.

**Without the models**, smart crop still runs:

- On OpenCV 4.x it uses OpenCV's built-in Haar face and HOG person detectors (weaker).
- OpenCV 5 moved those detectors to opencv-contrib, so only **motion saliency**
  (background subtraction) is used.

If neither finds anything, the configured `smart.fallback` applies. `ytclip
install-check` shows which tier is active.

## Verify

```bash
ytclip install-check
```

Every line should show ✔. A ✘ comes with the command that fixes it.

## Uninstall

```bash
./scripts/uninstall.sh          # remove program + Chrome registration, keep data
./scripts/uninstall.sh --purge  # also remove queue DB, models, config and cache
```

Rendered videos are never removed. Remove the extension in `chrome://extensions`.
