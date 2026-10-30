# Architecture

## Components

```
┌──────────── Chrome ────────────┐        ┌──────────────── ytclip (Python) ─────────────────┐
│ Side panel (React)             │ stdio  │ native/host.py  ─ protocol.py (framing, limits)  │
│  ├ tab.ts   active tab, player │ ─────▶ │                 ─ handlers.py (fixed operations) │
│  ├ native.ts NativeClient      │ ◀───── │                        │                         │
│  ├ storage.ts drafts, outbox,  │        │                        ▼                         │
│  │            cached queue     │        │  validation.py ─▶ repository.py ─▶ SQLite (WAL)  │
│  └ components/                 │        │                        ▲                         │
└────────────────────────────────┘        │  cli.py ─▶ runner.py ──┘ (lock, recovery)        │
                                          │             └▶ pipeline.py (one job)             │
                                          │                  ├ downloader.py  (yt-dlp, cache)│
                                          │                  ├ smartcrop/     (plan)         │
                                          │                  └ media/         (ffmpeg, verify)│
                                          └──────────────────────────────────────────────────┘
```

- **Extension.** It collects clip settings, does instant validation and shows the queue.
  It holds no authoritative state. It reads only the active YouTube tab's URL and title,
  plus the `<video>` element's position, through `chrome.scripting`, and only when the
  side panel is open. It opens in Chrome's side panel rather than a popup, so it stays
  open while you scrub the video.
- **Native host** (`ytclip.native.host`). Chrome starts a short-lived process per
  message. It validates each request and reads or writes SQLite. The one exception is
  `start_run` (the extension's Download button), which launches `ytclip run` as a
  detached process if no runner holds the lock. Its output goes to
  `logs/extension-run.log`.
- **CLI.** `ytclip run` is the only thing that downloads and renders. It runs when you
  start it in your terminal or press Download in the extension.

## Data model (SQLite, `jobs.db`)

```sql
jobs(id PK, url, video_id, title, start_ms, end_ms CHECK(end_ms > start_ms),
     aspect_ratio, crop_mode, resolution, output_name, dedupe_key,
     status, progress, stage, output_path, error, attempts, worker_pid,
     media_id → media.id, metadata JSON, created_at, updated_at, started_at, completed_at)
  INDEX (status, id), INDEX (dedupe_key), INDEX (video_id)

media(id PK, video_id, variant, path, title, duration_s, width, height, has_audio,
      size_bytes, created_at, last_used_at, UNIQUE(video_id, variant))

job_events(id PK, job_id → jobs.id ON DELETE CASCADE, at, level, stage, message, detail)
  INDEX (job_id, id)
```

- **Status lifecycle:**

  ```
  pending → downloading → processing → completed
                 └────────────┴──────→ failed → (retry) → pending
  ```

- **Edits.** Editing a pending or failed job resets it to pending.
- **Duplicates.** `dedupe_key` is `video_id|start_ms|end_ms|ratio|mode|resolution`.
  Adding a clip whose key matches any existing job returns a `duplicate` error with that
  job, unless `allowDuplicate` is set.
- **History.** `job_events` records stage changes, errors, retries and recoveries for
  `ytclip show`.
- **Metadata.** The `metadata` JSON stores the requested and applied crop mode, the
  fallback reason, smart-crop statistics, and the source and output stream facts.
- **Migrations.** `PRAGMA user_version` holds the schema version, and `db.py` applies
  migrations in order.

### Concurrency

- **WAL mode and timeouts.** WAL plus `busy_timeout=15000` lets the native host insert
  jobs while `ytclip run` updates progress.
- **Atomic writes.** Every write is a short `BEGIN IMMEDIATE` transaction. That makes
  read-modify-write steps atomic across processes: the duplicate check plus insert, and
  the claim (`UPDATE … WHERE status = 'pending'`). A test runs eight concurrent claimers
  and exactly one wins.
- **Single runner.** `ytclip run` holds an `flock` on `~/.local/share/ytclip/run.lock`.
  The kernel releases the lock when the process dies, so a crash never leaves a stale
  lock. While holding it, the runner can safely reset any job left in an active state
  (`recover_interrupted`).

## Native messaging protocol

Framing is Chrome's: a 4-byte native-endian length, then UTF-8 JSON.

- **Requests.** At most **64 KiB** (`protocol.py`). Anything else, malformed JSON, or a
  non-object ends the session with a `protocol` error.
- **Responses.** Limited to Chrome's 1 MiB.

```jsonc
// request
{"id": "lx2-1", "type": "add_job", "payload": {"clip": {...}, "allowDuplicate": false}}
// success
{"id": "lx2-1", "ok": true, "data": {"job": {...}}}
// failure
{"id": "lx2-1", "ok": false, "error": {"code": "duplicate", "message": "...", "existingJob": {...}}}
```

| type | payload | data |
|---|---|---|
| `ping` | `{}` | `{version, outputDir, smartCrop: "dnn" \| "basic"}` |
| `add_job` | `{clip, allowDuplicate?}` | `{job}` |
| `list_jobs` | `{limit? 1..200}` | `{jobs, counts}` |
| `get_job` | `{id}` | `{job}` |
| `update_job` | `{id, clip, allowDuplicate?}` | `{job}` |
| `remove_job` | `{id}` | `{removed}` |
| `retry_job` | `{id}` | `{job}` |

- **Error codes:** `invalid_request`, `unknown_type`, `validation` (includes `field`),
  `duplicate`, `not_found`, `invalid_state`, `protocol`, `host_config`, `internal`.
- **Unknown keys.** Rejected at every level, so the extension cannot smuggle in options
  such as paths or commands.
- **Clip fields.** `url`, `title`, `start`, `end`, `aspectRatio`, `cropMode`,
  `resolution` and `outputName` are validated by `validation.py`, which the CLI's
  `ytclip add` also uses.
- **URL handling.** URLs are reduced to a video ID and rebuilt as
  `https://www.youtube.com/watch?v=<id>`.

## Processing pipeline (`pipeline.py`)

| Step | Progress | What happens |
|---|---|---|
| Check free space | | Fail early if below `min_free_space_mb` |
| Download | 0–35% | `MediaCache.ensure`: reuse a cached and probe-verified file, or run yt-dlp; `--continue` resumes partial downloads |
| Probe | | Check the clip against the real duration: fail if it starts past the end; clamp an end up to 0.5 s past the end |
| Analyse (smart only) | 35–55% | Build the crop plan, or pick the fallback |
| Render | 55–95% | ffmpeg into `work/job-<id>/render.mp4`, progress from `-progress pipe:1` |
| Verify | 96% | Dimensions, duration, audio, tail decode |
| Publish | | Apply the overwrite policy; move to `output_dir/.name.partial`, then atomic `os.replace` |
| Complete | 100% | Mark the job completed with metadata; delete the work directory |

Error handling:

- Errors are converted into `JobError(message, retryable)` with a user-facing message.
  The runner records the message, and the job's history keeps the details.
- An unexpected exception fails only that job, and the queue continues.
- `KeyboardInterrupt` returns the job to pending and re-raises.

**Source abstraction.** `JobProcessor` takes a `SourceProvider`: `YouTubeSource` in
production, `LocalSource` in tests. This lets the full pipeline run against synthetic
footage with no network and no mocking of ffmpeg.

## Security model

- **No listening sockets.** There is no HTTP server; the only channel is Chrome's native
  messaging, restricted by `allowed_origins` to the extension's fixed ID.
- **Fixed operations.** The host exposes no "run command" or "read file" request. All
  requests are schema-validated, and their size and length are bounded. `start_run`
  takes no arguments and can only launch `ytclip run` with the host's own interpreter.
- **Safe subprocesses.** Every subprocess uses an argument list. The yt-dlp URL is
  rebuilt from a validated 11-character ID and passed after `--`. yt-dlp runs with
  `--no-config`, so no user config can inject `--exec`. The sendcmd file is referenced by
  a regex-checked bare filename.
- **Confined files.** Output names are sanitised to a single path component and checked
  with `confined_path`. Cache cleanup deletes only files that resolve inside the cache
  directory. Outputs are never deleted.
- **No browser data.** No cookies or credentials are read. A content script on youtube.com
  only adds the Start/End buttons and reads the `<video>` position when you click one. The extension asks only for
  `nativeMessaging`, `storage`, `activeTab`, `scripting`, `sidePanel` and YouTube host
  permissions.
- **No access-control bypass.** Private, members-only, age-restricted and DRM videos fail
  with an explanation instead.
