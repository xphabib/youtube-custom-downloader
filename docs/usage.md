# Usage

## Typical session

1. Browse YouTube and click the ytclip icon on a video. ytclip opens in Chrome's side
   panel, which stays open while you click around the page and follows you across tabs.
2. The panel fills **Start** from the current playback position. Scrub to the end of the
   moment and click the clock button next to **End**. You can also use the **Start** and
   **End** buttons that ytclip adds to YouTube's button row (left of Like and Share): each
   click takes the current playback time, shows it on the button, and fills the panel
   even when it is closed. The same row has dropdowns for aspect ratio, crop mode and
   resolution, and an **Add** button, so you can queue clips without opening
   the panel.
   Choosing **Custom** crop puts a box over the video: drag it to move, drag a corner to
   resize (the aspect ratio stays locked), then press **Done**. **Area** reopens it.
3. Pick **9:16 · Smart · 1080p**, or whatever you need, and click **Add to queue**. The
   next clip starts where this one ended.
4. Repeat on any number of videos.
5. Click **Download N clips** at the bottom of the panel. This starts `ytclip run` in the
   background (output in `~/.local/share/ytclip/logs/extension-run.log`). Or, in a
   terminal:

```console
$ ytclip status
pending: 3  downloading: 0  processing: 0  completed: 0  failed: 0
Runner: idle
Run ytclip run to process pending jobs.

$ ytclip run
#1 Interview with the team   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% done 0:00:41
#2 Interview with the team   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% done 0:00:09
#3 Demo day                  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% done 0:00:37
✔ #1 → /home/you/Videos/ytclip/Interview_with_the_team_01m05s-01m35s_9x16.mp4
✔ #2 → /home/you/Videos/ytclip/Interview_with_the_team_04m10s-04m30s_9x16.mp4
✔ #3 → /home/you/Videos/ytclip/Demo_day_00m12s-00m40s_1x1.mp4
```

Job #2 was faster because the source video was already cached from job #1.

While `ytclip run` works, the panel shows each job's stage (downloading, analysing,
rendering, verifying) and progress.

## Examples

Queue a clip without the extension:

```bash
ytclip add "https://www.youtube.com/watch?v=VIDEO_ID" --start 1:20 --end 2:45
ytclip add "https://youtu.be/VIDEO_ID" -s 00:10:00 -e 00:10:30.5 -r 1:1 -m blur --resolution 720p
ytclip add "https://www.youtube.com/shorts/VIDEO_ID" -s 0 -e 15 -r 16:9 -m center --name "intro"
```

Inspect the queue:

```bash
ytclip list                         # newest first
ytclip list -s failed -s pending    # filter by status
ytclip list --json | jq '.[].status'
ytclip show 12                      # times, format, output, smart-crop stats, history
```

`ytclip show` for a smart job reports the crop statistics. For example
`samples=90, sample_fps=3.0, scenes=2, detected_ratio=0.93, detector=yunet+yolox` means
a subject was found in 93% of the 90 analysed frames across two scenes. If nothing was
found, the **Crop used** line shows the fallback that was applied.

Process selectively:

```bash
ytclip run --id 12 --id 14     # only these jobs (a failed one is re-queued first)
```

Handle failures:

```bash
ytclip list -s failed          # see the error message per job
ytclip retry                   # re-queue failed jobs with attempts left (< max_attempts), then run
ytclip retry --id 7 --force    # ignore the attempt limit for job 7
ytclip retry --queue-only      # just mark them pending
```

Jobs that cannot succeed as they are, such as a clip past the end of the video, stay
failed. Fix the times with **Edit** in the side panel, which re-queues the job, or remove the
job.

Remove jobs:

```bash
ytclip remove 7            # asks for confirmation; -y to skip
```

A job that is being processed cannot be removed. Removing a completed job keeps its
output file.

Cache:

```bash
ytclip cache info              # sizes and cached videos
ytclip cache clean             # apply the keep-days policy now
ytclip cache clean --older-than 0
ytclip cache clean --all       # remove every cached source
```

Configuration:

```bash
ytclip config          # effective settings and the config file location
ytclip config --init   # create a commented config file
ytclip config --path
```

Debugging:

```bash
ytclip --debug run --id 3      # prints ffmpeg/yt-dlp commands and ffmpeg stderr
tail -f ~/.local/share/ytclip/logs/ytclip.log | jq .
```

## Interrupting and resuming

- **Ctrl-C during `ytclip run`** stops the encoder and puts the current job back to
  *pending*. The next `ytclip run` starts it again. Downloads resume from their partial
  file.
- **Crash, kill or power loss.** When `ytclip run` starts, it finds jobs left in
  *downloading* or *processing* and returns them to the queue, recorded as "recovered" in
  the job history. It is safe to assume that no other runner owns them, because only one
  `ytclip run` can hold the queue lock at a time; a second one exits with "Another `ytclip
  run` is already processing the queue."
- A job is marked **completed** only after its output is verified and moved into place,
  so a half-written file is never reported as done.

## Output naming

- With a filename set: `<sanitised name>.mp4`.
- Otherwise: `<title>_<start>-<end>_<ratio>.mp4`, for example
  `Big_Buck_Bunny_01m05s-01m15s_9x16.mp4`. If no title is known, the video ID is used.
- Names are reduced to letters, digits, `.`, `-` and `_`. Path separators and `..` are
  removed, so a name can never point outside `output_dir`.
- If the file already exists, the `overwrite` policy decides: `rename` (default) gives
  `name-1.mp4`, `skip` fails the job, and `overwrite` replaces the file.
