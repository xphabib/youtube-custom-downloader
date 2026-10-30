# Troubleshooting

Start with:

```bash
ytclip install-check
ytclip --debug run --id <job>       # shows ffmpeg/yt-dlp commands and errors
ytclip show <job>                   # error message and job history
less ~/.local/share/ytclip/logs/ytclip.log          # JSON lines
less ~/.local/share/ytclip/logs/native-host.log
```

## Extension shows "ytclip is not connected"

| Chrome message | Fix |
|---|---|
| `Specified native messaging host not found.` | Run `ytclip native install`, then close and reopen the side panel. For Chromium or Brave, make sure the browser has been started once (so its profile directory exists) before running the command. |
| `Access to the specified native messaging host is forbidden.` | The extension ID is not in `allowed_origins`. Check the ID on `chrome://extensions`. It is `fbajbpimmccmfkjgmhcdcoelbkgnkmdm` unless you removed `key` from the manifest. Run `ytclip native install --extension-id <id>`. |
| `Native host has exited.` / `Error when communicating…` | The host crashed on start. Run the launcher by hand: `~/.local/share/ytclip/bin/ytclip-native-host < /dev/null; echo $?`. It should print nothing and exit 0. Check `native-host.log`; typical causes are a broken config file or a moved or deleted virtualenv (re-run `./scripts/install.sh`). |

Clips you save while disconnected are kept in the browser and sent automatically once
the host responds.

## `ytclip: command not found`

`~/.local/bin` is not on your `PATH`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

## Download failures

| Error shown | Meaning / action |
|---|---|
| Network error … run `ytclip retry` | Temporary connectivity problem; retry. |
| YouTube is rate limiting downloads (HTTP 429) | Wait (tens of minutes), then `ytclip retry`. |
| YouTube is asking for bot verification | Wait and retry; update yt-dlp (below). ytclip does not use your browser cookies. |
| This video is private / members-only / age-restricted / DRM-protected | Not supported by design. |
| The video is unavailable | Removed, region-locked or a wrong URL. |
| No downloadable video format was found | Update yt-dlp. |
| `yt-dlp failed: …` (anything else) | Update yt-dlp first; then run with `--debug`. |

Update yt-dlp, which YouTube changes often break:

```bash
~/.local/share/ytclip/venv/bin/python -m pip install -U "yt-dlp[default]"
```

yt-dlp prints `No supported JavaScript runtime could be found` when none of deno, node or
bun is on the `PATH` of the terminal running `ytclip run`. Some formats are then missing.
Install one of them, for example `sudo apt install nodejs`.

## Processing failures

- **"Clip ends at … but the video is only … long"**: fix the times with **Edit** in the
  side panel. This error is not retried automatically.
- **"Only N MB free"**: free disk space or run `ytclip cache clean`. The threshold is
  `min_free_space_mb`.
- **"Output … expected WxH" / "Output lasts …"**: the render didn't match after encoding;
  this is rare and usually means a broken source. Run `ytclip cache clean --all` and then
  `ytclip retry`. If it persists, report the `--debug` output.
- **"Source media is corrupt or incomplete"**: run `ytclip cache clean --all` and then
  `ytclip retry`.
- **"… already exists (overwrite policy is 'skip')"**: choose another filename or change
  `overwrite` in the config.
- **"Another `ytclip run` is already processing the queue."**: only one runner at a time.
  `ytclip status` shows what it is doing.

## Smart crop doesn't follow the subject

Look at `ytclip show <id>`:

- `detected_ratio` near 0 and **Crop used: center (no subject detected)**: nothing
  recognisable was found (see the limitations in [crop-algorithm.md](crop-algorithm.md)).
  Set `smart.fallback = "blur"` to keep the whole frame instead, or choose `blur` for
  that clip.
- `detector=` shows something other than `yunet+yolox`: the models are missing. Run
  `ytclip models download`.
- The frame follows the wrong person: when several people are far apart, the largest and
  most confident subject wins, preferring one with a visible face.
- Movement is too sluggish or too jumpy: tune `smart.smoothing_seconds`,
  `smart.max_pan_per_second` and `smart.deadzone`.
- Cuts are handled late in fast edits: raise `smart.sample_fps` (for example 6).

## Jobs stuck in "downloading" or "processing"

If no `ytclip run` is active (`ytclip status` shows `Runner: idle`), the previous run was
killed. The next `ytclip run` recovers those jobs automatically. You can also remove one
with `ytclip remove <id>`.

## Start over

```bash
ytclip cache clean --all              # cached sources and work files
rm ~/.local/share/ytclip/jobs.db*     # the whole queue and history
```
