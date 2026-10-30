# ytclip — YouTube clip queue with smart auto-crop

Mark clips while you browse YouTube in Chrome, then render them all locally with one
command. Each clip is trimmed and re-framed to **9:16**, **16:9** or **1:1**. The default
**smart** mode follows the detected subject (faces, people, animals and some objects)
instead of cutting the centre blindly.

```
Chrome extension ──native messaging──▶ ytclip host ──▶ SQLite queue
                                                         │
                       ytclip run ◀──────────────────────┘
                          │  yt-dlp download (cached per video)
                          │  ffprobe → sample frames → detect → track → smooth
                          ▼  ffmpeg trim + crop/scale (+ blur canvas) → verify
                     ~/Videos/ytclip/*.mp4
```

> **Use it only for videos you own or are authorised to download.** ytclip uses yt-dlp
> to fetch what YouTube serves to an anonymous viewer. It never circumvents DRM, sign-in,
> age or region restrictions, and it does not read browser cookies. You are responsible
> for complying with YouTube's Terms of Service and copyright law.

## Contents

- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Using the extension](#using-the-extension)
- [CLI reference](#cli-reference)
- [Crop modes](#crop-modes)
- [Files, directories and cache](#files-directories-and-cache)
- [Configuration](#configuration)
- [Development and tests](#development-and-tests)
- [Project layout](#project-layout)
- [Limitations](#limitations)

Further documentation: [installation](docs/installation.md) ·
[usage](docs/usage.md) · [smart crop algorithm](docs/crop-algorithm.md) ·
[architecture](docs/architecture.md) · [troubleshooting](docs/troubleshooting.md)

## Quick start

```bash
sudo apt install python3 python3-venv ffmpeg nodejs npm   # once
git clone <this repo> ytclip && cd ytclip
./scripts/install.sh
```

The installer creates a virtualenv in `~/.local/share/ytclip/venv`, links
`~/.local/bin/ytclip` and downloads the detection models (about 36 MB). It also
registers the Chrome native messaging host, builds the extension and runs
`ytclip install-check`. It does not use `sudo` and can be re-run to upgrade.

Then load the extension:

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select `extension/dist`.
3. Pin **ytclip** to the toolbar.

Use it:

1. Open a YouTube video (watch page, Short or youtu.be link) and click the ytclip icon.
2. Set **Start** and **End**. The clock buttons insert the current playback position.
3. Choose the aspect ratio, crop mode and resolution, then click **Add to queue**.
4. Keep browsing and add more clips from any video.
5. Later, in a terminal, run:

```bash
ytclip run
```

Finished MP4 files appear in `~/Videos/ytclip/`.

## Requirements

| Component | Version | Notes |
|---|---|---|
| Ubuntu | 24.04 (tested) | 22.04 needs Python 3.11+ (e.g. deadsnakes PPA); untested |
| Python | 3.11+ | `python3-venv` is needed for the installer |
| FFmpeg / FFprobe | tested on 6.1 | Must include `libx264` and `aac` (Ubuntu's package does) |
| Chrome / Chromium / Brave | 116+ | Manifest V3 |
| Node.js + npm | 18+ | Only needed to build the extension |
| yt-dlp | installed automatically | Also uses a JS runtime (deno, node or bun) if one is installed |

## Using the extension

- **Video detection.** The side panel reads the active tab's URL and title. It accepts
  `youtube.com/watch`, `/shorts/`, `/live/`, `m.youtube.com` and `youtu.be` links;
  other pages show a notice but still let you manage the queue.
- **Times.** Enter `HH:MM:SS`, `MM:SS` or plain seconds, with optional `.mmm`
  fractions. The end time must be after the start time, and the clip must last between
  0.5 seconds and 3 hours. The side panel also warns when the end time is past the end of the
  video.
- **Next clip.** After a clip is saved, its end time becomes the next clip's start time.
- **Defaults.** New clips use 9:16, smart crop and 1080p, or whatever you used last.
- **Duplicates.** If an identical clip (same video, times, ratio, mode and resolution)
  is already queued, the side panel says so and offers **Add anyway**.
- **Queue.** The side panel shows each job's status (pending, downloading, processing,
  completed or failed) with live progress while `ytclip run` works, plus the error message
  for failed jobs. You can **Edit** and **Remove** pending or failed jobs and **Retry**
  failed ones. **This video** filters the list to the current video. **Clear** removes
  every job that is not currently running (downloaded files are kept), and
  **Download** starts `ytclip run` in the background.
- **Offline.** If the native host is not installed or not reachable, the side panel shows the
  setup commands. **Save for later** keeps the clip in browser storage and sends it
  automatically the next time the side panel connects. Unsent form input is kept too.

The SQLite database managed by the CLI is the source of truth. The extension only caches
the last job list it saw, so it can show the queue while offline.

## CLI reference

```text
ytclip install-check            Check Python, ffmpeg, yt-dlp, models, dirs, database, native host
ytclip list [-s STATUS] [--json] List queued and recent jobs
ytclip status                   Job counts and whether a run is in progress
ytclip show ID [--json]         Details, crop statistics and history of one job
ytclip run                      Process every pending job (also ones added meanwhile)
ytclip run --id ID [--id ID]    Process specific jobs (a failed job is re-queued first)
ytclip retry [--id ID] [--force] [--queue-only]
                                Re-queue failed jobs with attempts left, then process them
ytclip remove ID [-y]           Remove a job (the rendered file is kept)
ytclip add URL -s START -e END [-r 9:16|16:9|1:1] [-m smart|center|blur|custom --rect X,Y,W,H]
           [--resolution 1080p|720p|480p] [--name FILE] [--allow-duplicate]
                                Queue a clip from the terminal
ytclip config [--init] [--path] Show effective settings / create the config file
ytclip cache info | clean [--all] [--older-than DAYS]
ytclip models list | download [--force]
ytclip native install [--extension-id ID] | uninstall
ytclip --debug <command>        Verbose logs, including the ffmpeg and yt-dlp commands
```

`ytclip run` shows one progress bar per job, then a summary. It exits with status 1 if
any job failed. If you press Ctrl-C, the current job goes back to the queue and the next
`ytclip run` resumes it. See [docs/usage.md](docs/usage.md) for worked examples.

## Crop modes

| Mode | What it does |
|---|---|
| **smart** | Samples the clip (3 frames per second by default) and detects faces (YuNet) and people, animals and some objects (YOLOX-S). It splits the clip at scene cuts, tracks the main subject in each scene and builds a smoothed crop path that keeps faces in frame. FFmpeg then applies that path frame by frame. If nothing is found in the whole clip, it falls back to `center` or `blur` (configurable). |
| **center** | A fixed crop of the requested ratio from the middle of the frame. |
| **blur** | Keeps the whole frame, scaled to fit, over a blurred and slightly darkened copy that fills the canvas. |
| **custom** | Uses a fixed area you choose: pick **Custom** in the crop dropdown on the YouTube page and drag the box over the video (move it, or drag a corner to resize; it keeps the chosen aspect ratio). The area is stored as fractions of the frame, so it works at any download resolution. From the terminal: `--mode custom --rect 0.3,0,0.4,1` (x, y, width, height). |

Resolution sets the length of the short side: 9:16 at 1080p is 1080×1920, 16:9 at
720p is 1280×720, and 1:1 at 480p is 480×480. Output is H.264 High (yuv420p) with
48 kHz stereo AAC, plus `+faststart` for web playback. Audio is kept whenever the source
has it.

The full algorithm, its parameters and its limitations are in
[docs/crop-algorithm.md](docs/crop-algorithm.md).

## Files, directories and cache

| Path | Contents | Safe to delete? |
|---|---|---|
| `~/Videos/ytclip/` (`output_dir`) | Final MP4 files | Your files; ytclip never deletes them |
| `~/.config/ytclip/config.toml` | Settings | Yes (defaults are used) |
| `~/.local/share/ytclip/jobs.db` | Queue and history (SQLite, WAL mode) | Deletes the queue |
| `~/.local/share/ytclip/models/` | ONNX model weights | Yes; re-download with `ytclip models download` |
| `~/.local/share/ytclip/logs/` | `ytclip.log` and `native-host.log` (JSON lines, rotated) | Yes |
| `~/.local/share/ytclip/venv/`, `bin/` | Installed program and native host launcher | Use `scripts/uninstall.sh` |
| `~/.cache/ytclip/downloads/<video id>/` | Cached source videos, shared by all clips of a video | Yes |
| `~/.cache/ytclip/work/job-<id>/` | Intermediate files during a render | Yes, when no run is active |

**Cache policy.** Each video is downloaded once, capped at `max_download_height`
(1080p by default), and reused for every clip from it. Partial downloads resume. After
each `ytclip run`, sources unused for `cache_keep_days` (7) are deleted, unless a pending
or failed job still needs them. Use `ytclip cache clean --all` to empty the cache
immediately. Work directories are deleted after each job.

**Output safety.** A render is written to the work directory and verified: dimensions,
duration within tolerance, audio present when the source has audio, and a clean decode of
the tail. Only then is it moved into `output_dir` through a temporary file and an atomic
rename. Filenames are sanitised and confined to `output_dir`. Existing files follow the
`overwrite` policy: `rename` (default, adds `-1`, `-2` and so on), `skip` (fail the job)
or `overwrite`.

## Configuration

Run `ytclip config --init` to create a commented `~/.config/ytclip/config.toml`. The
settings are:

```toml
output_dir = "~/Videos/ytclip"
overwrite = "rename"          # rename | skip | overwrite
max_attempts = 3              # for `ytclip retry`
max_download_height = 1080
cache_keep_days = 7
min_free_space_mb = 1024
video_crf = 20
video_preset = "medium"
audio_bitrate = "192k"

[smart]
sample_fps = 3.0
max_samples = 360
fallback = "center"           # center | blur, used when no subject is found
use_motion = true
deadzone = 0.06
smoothing_seconds = 0.8
max_pan_per_second = 0.35
scene_threshold = 0.45
```

Set `YTCLIP_CONFIG=/path/to/config.toml` to use a different file. `ytclip native
install` copies this variable into the native host launcher.

## Development and tests

```bash
./scripts/dev-setup.sh      # .venv with an editable install + extension node_modules
./scripts/test.sh           # all Python and extension tests
```

- **Python** (`cli/tests`, pytest): 213 tests. They cover timestamp parsing,
  validation, filename safety, geometry for every ratio, trajectory smoothing, subject
  tracking, scene cuts and fallback, the SQLite repository (duplicates, concurrent
  claims, retries, recovery), native messaging framing and dispatch (including a real
  host subprocess), and the CLI commands. The integration tests render **real ffmpeg
  output from synthetic footage** generated during the test run: every mode × ratio,
  trim accuracy, audio preservation, smart crop tracking a moving subject (compared
  against centre crop), interrupted-job recovery and overwrite policies. No test downloads
  from YouTube.
- **Extension** (`extension/src/**/*.test.*`, Vitest + Testing Library): 49 tests for
  timestamp and URL parsing, validation, the native client's response and error handling,
  the offline outbox, and side panel flows (queueing, validation, duplicates, offline save,
  draft restore).

Run `ytclip --debug run` to see the exact ffmpeg and yt-dlp commands.

## Project layout

```
cli/                    Python package (pyproject.toml)
  src/ytclip/
    cli.py              Typer commands
    config.py           XDG paths + TOML config
    models.py           Job/ClipSpec types and enums
    validation.py       single source of truth for clip validation
    youtube.py          URL recognition / canonicalisation
    timeutil.py filenames.py
    db.py repository.py SQLite schema, transactions, state transitions
    downloader.py       yt-dlp wrapper + per-video cache
    pipeline.py         one job: fetch → probe → plan → render → verify → publish
    runner.py           single-instance lock, recovery, per-job error isolation
    cache.py            cleanup policy
    checks.py           install-check
    modelstore.py       model registry, download, SHA-256 verification
    media/              ffprobe, ffmpeg runner, geometry, render, verify
    smartcrop/          detectors, sampler, scenes, tracker, trajectory, planner
    native/             protocol framing, request handlers, host entry, registration
  tests/
extension/              Chrome MV3 extension (React + TypeScript + Vite)
  public/manifest.json  includes a public key so the extension ID is stable
  src/                  App, components/, lib/ (native client, storage, validation)
scripts/                install.sh, uninstall.sh, dev-setup.sh, test.sh
docs/                   installation, usage, crop algorithm, architecture, troubleshooting
```

## Limitations

- Smart crop sees what its models can detect. That covers frontal and near-profile faces,
  people, common animals and a few objects (cars, balls and so on). Logos, text, slides,
  screen recordings and products are usually not detected. Without detections, a moving
  region can still guide the crop, and otherwise the configured fallback applies. See
  [docs/crop-algorithm.md#limitations](docs/crop-algorithm.md#limitations).
- The crop window follows one subject or a group that fits within the frame. When two
  people stand far apart in a 9:16 crop, only one of them is followed.
- Scene cuts are found at the sampling rate, so the jump to a new framing can land up to
  about one sampling interval (≈0.17 s at 3 fps) away from the real cut.
- Processing is sequential, one job at a time. Smart-crop analysis costs about
  70 ms per sampled frame on the 8-core test machine (≈4 s per minute of clip at 3 fps).
- HDR sources are converted to 8-bit SDR yuv420p without tone mapping, so colours can
  look flat.
- ytclip relies on yt-dlp. When YouTube changes, update yt-dlp with
  `~/.local/share/ytclip/venv/bin/python -m pip install -U yt-dlp` (or re-run the
  installer).
