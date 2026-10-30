# Smart crop algorithm

Source: `cli/src/ytclip/smartcrop/` (detection, tracking and trajectory) and
`cli/src/ytclip/media/render.py` (applying the trajectory with ffmpeg).

## Overview

```
probe ─▶ crop size ─▶ sample frames ─▶ detect ─▶ scene cuts ─▶ track subject ─▶ smooth path ─▶ sendcmd ─▶ ffmpeg
         (geometry)   (ffmpeg pipe)   (DNN /    (histogram)   (per scene)      (per axis)     (per frame)
                                       motion)
```

All analysis runs in **normalised coordinates** (0..1 of the source frame), so it does
not depend on the analysis resolution.

## 1. Probe and crop geometry

`ffprobe` provides width, height (swapped for 90°/270° rotation metadata), frame rate,
duration and audio presence.

`crop_size` returns the largest even-sized window with the target ratio that fits in the
source. From 1920×1080, that is 608×1080 for 9:16, 1080×1080 for 1:1 and the full frame
for 16:9. The window is later scaled to the output size: 1080p means a short side of
1080 px, so 9:16 → 1080×1920.

If the window covers the full frame on an axis, that axis is fixed at the centre. For
example, a 16:9 output from a 16:9 source does not move.

## 2. Frame sampling

A single ffmpeg process decodes only the clip interval (`-ss`/`-t`) and emits raw BGR
frames at `smart.sample_fps` (default 3), scaled to `analysis_width` (640 px). The rate
is lowered automatically so that a clip produces at most `smart.max_samples` frames
(360). Frames are processed as they stream in, so memory use does not grow with clip
length.

Analysing every frame would cost 10× more for little gain: the crop path is smoothed
over about a second anyway (section 6).

## 3. Detection

| Tier | Faces | Bodies and objects | When used |
|---|---|---|---|
| DNN (default after `ytclip models download`) | YuNet, score ≥ 0.7 | YOLOX-S on a 640×640 letterbox, score ≥ 0.45, NMS 0.45 | Model files present |
| Built-in | Haar frontal-face cascade | HOG people detector | Models missing, OpenCV 4.x |
| Motion | — | Background subtraction (below) | Samples with no detections, if `smart.use_motion` |

YOLOX classes and their weights: person 1.0; animals (bird, cat, dog, horse, sheep, cow,
elephant, bear, zebra, giraffe) 0.7; sports ball 0.5; bicycle, car, motorcycle and
skateboard 0.4; bus, TV and laptop 0.3. Everything else is ignored.

**Motion saliency.** Each scene gets a background image: the per-pixel median of that
scene's samples, in 160 px grayscale. For a sample with no detections, the largest region
that differs from the background becomes a low-weight (0.35) "motion" subject. Frame
differencing would mark both where the subject was and where it is; the median
background marks only where it is. Samples where nothing moves, or where over 60% of the
frame changes (a camera pan), are skipped.

## 4. Scene cuts

The planner compares a 16×16 hue/saturation histogram of each sample with the previous
one using the Bhattacharyya distance. A distance above `smart.scene_threshold` (0.45)
starts a new scene. Tracking, smoothing and motion backgrounds are computed **per
scene**, so the virtual camera cuts with the edit instead of panning across it. The
switch happens halfway between the last sample of the old scene and the first sample of
the new one.

## 5. Subject selection and tracking (`tracker.py`)

For each sample:

1. **Build subjects.** A face is attached to the smallest person or animal box whose
   upper half contains the face's centre. A face with no body becomes a subject on its
   own, with an estimated head-and-shoulders box. Motion regions count only when nothing
   else was found.
2. **Score.**

   ```
   score = class_weight × confidence × (0.4 + min(1, 2·√area)) × (1.3 if it has a face)
   ```

   Larger, more confident subjects with a visible face win.
3. **Continuity.** After the first sample of a scene, each candidate's score is
   multiplied by `1 + 2·similarity` to the previously tracked box. Similarity is the
   larger of the IoU and a Gaussian of centre distance (σ = 0.12). A briefly
   higher-scoring passer-by therefore does not steal the frame.
4. **Groups.** Other subjects scoring at least half of the primary's are added to the
   framed region, strongest first, while the union still fits inside 95% of the crop
   window. Two people side by side stay together; people at opposite edges of a 16:9
   frame do not fit a 9:16 window, so only the primary one is followed.
5. **Focus point.**
   - x is the centre of the group if there is one.
   - Otherwise x is `0.6·face + 0.4·body` when a face is known (a slight bias toward the
     face).
   - Otherwise x is the centre of the box.
   - y matters only when the crop is shorter than the frame (for example 16:9 from a
     portrait source). It puts the face about 38% from the top, leaving headroom.
   - The face, or the group, is recorded as the **keep region**.

## 6. Trajectory (`trajectory.py`), per axis

| Step | What it does | Parameter |
|---|---|---|
| `fill_gaps` | Interpolates samples without a subject linearly and holds the ends | — |
| `apply_deadzone` | Hysteresis: the camera moves only once the target leaves ±deadzone of the current position | `deadzone` × crop width (0.06) |
| `gaussian_smooth` | Zero-phase Gaussian smoothing with odd-reflection padding, so linear motion is not flattened at clip edges and nothing lags | σ = `smoothing_seconds` × sample fps (0.8 s) |
| `limit_velocity` | Averages a forward and a backward pass of a rate limiter, so sudden subject switches become pans centred on the change | `max_pan_per_second` (0.35 frame widths/s) |
| `enforce_keep` | Shifts the window just enough that the keep region (face or group) is inside it, when it fits | 1% margin |
| `clamp_center` | Keeps the window inside the frame | — |

If a scene has no subject at all, its axis stays centred. If **no** scene has a subject,
the plan reports `subject_found = false` and the job uses `smart.fallback` (`center` or
`blur`). `ytclip show` then reports which crop was actually used and why.

## 7. Applying the path with ffmpeg

The plan is a set of per-scene keyframe tracks (sample time → crop centre).
`CropPlan.offset_at(t)` interpolates linearly within a scene and converts to a clamped
top-left offset in source pixels.

`write_sendcmd` calls it at **every output frame time**. It writes one line whenever the
offset changes:

```
0.0000 crop@smart x 656, crop@smart y 0;
0.0333 crop@smart x 658, crop@smart y 0;
...
```

The filter graph is:

```
[0:v]setpts=PTS-STARTPTS,sendcmd=f=crop.cmd,crop@smart=w=608:h=1080:x=656:y=0,
     scale=1080:1920:flags=lanczos,setsar=1,format=yuv420p[v]
```

`setpts=PTS-STARTPTS` makes filter time start at 0 at the clip start. That matches the
times in `crop.cmd`, because the input is opened with `-ss <start>` and `-t <duration>`.
Audio is trimmed by the same options and reset with `asetpts`, so the two streams stay in
sync.

sendcmd is used instead of one large `if(lt(t,…))` expression. It has no length or
nesting limit, works for clips of any length, and the file can be inspected (it is kept
in `~/.cache/ytclip/work/job-<id>/` while the job runs). ffmpeg is started with that
directory as its working directory, so the filter graph contains only a fixed, safe
filename.

**Blur mode** uses a separate graph:

```
split → [bg] scale to cover at ¼ size, crop, boxblur, darken, scale up
      → [fg] scale to fit
→ overlay centred
```

## 8. Verification

After rendering, the output must have exactly the expected width and height. Its
duration must be within max(0.25 s, 3 frames, 1%) of the requested clip. If the source
had audio, the output must contain audio. The last 3 seconds must decode without errors.
Only then is the file moved into the output directory.

## Tests

`cli/tests/test_smartcrop.py` covers each trajectory stage, tracking, group framing,
scene cuts, fallback and frame-bounds invariants, using synthetic detections.
`cli/tests/test_media.py` renders synthetic ffmpeg footage: a bright box moving across a
dark frame, plus a two-scene clip with the box on opposite sides. It then measures where
the box lands in the **output** frames:

- Smart crop keeps the box near the centre throughout.
- Centre crop loses it.
- At a scene cut, the window jumps and the box stays visible.

## Limitations

- **Detection coverage.** Only faces, people, the listed animals and a few object classes
  are recognised. Text, slides, logos, products, screen recordings, illustrations and
  heavily stylised animation are usually not. Cartoon characters may or may not be
  detected. Motion saliency helps only when the camera is static.
- **Small or unusual faces.** Faces smaller than about 10 px at the 640 px analysis
  width, heavily occluded faces or strong profiles may be missed. The person box still
  guides the crop.
- **One focus.** When several important subjects are spread wider than the crop window,
  one is followed. There is no split-screen and no speaker detection; the system does not
  know who is talking.
- **Fast action.** Pans are deliberately capped (0.35 frame widths/s by default). A
  subject crossing the frame faster can briefly leave the window, but `enforce_keep`
  still pulls the face back in when it fits. Raise `max_pan_per_second` or lower
  `smoothing_seconds` for sports.
- **Cut timing.** Cuts are found at the sampling rate, so the new framing may start up to
  half a sampling interval early or late. Raise `sample_fps` for fast-cut edits. Very
  similar-looking scenes may not register as a cut.
- **Edge pans.** The zero-lag rate limiter can start a pan slightly before a subject
  moves. When that happens near the start or end of a clip, the first or last frames can
  be marginally off target.
- **CPU time.** About 70 ms per sampled frame with YOLOX-S on the test machine: a
  1-minute clip at 3 fps takes ~13 s of analysis before encoding.
