"""Build and run the ffmpeg command for one clip.

Every mode trims with input seeking (``-ss`` before ``-i`` plus ``-t``), which is
frame-accurate when re-encoding, then resets timestamps so audio and video both start
at zero.

Smart crop is applied with ``sendcmd`` + a named ``crop`` filter: for every output frame
a command sets the crop's ``x``/``y``. This keeps arbitrary trajectories out of a
single (length-limited) ffmpeg expression and is exactly reproducible from the plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ytclip.media.ffmpeg import ProgressCallback, run_ffmpeg
from ytclip.media.geometry import Size, center_offset, crop_size, custom_window, even
from ytclip.media.probe import MediaInfo
from ytclip.models import AspectRatio, CropMode, CropRect
from ytclip.smartcrop.planner import CropPlan

SMART_CROP_FILTER = "crop@smart"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class EncodeSettings:
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"


@dataclass(frozen=True)
class RenderRequest:
    source: Path
    info: MediaInfo
    start_s: float
    duration_s: float
    aspect: AspectRatio
    output: Size
    destination: Path
    mode: CropMode
    plan: CropPlan | None = None
    sendcmd_path: Path | None = None
    rect: CropRect | None = None


def _finish(size: Size) -> str:
    return f"scale={size.width}:{size.height}:flags=lanczos,setsar=1,format=yuv420p"


def center_filter(info: MediaInfo, aspect: AspectRatio, output: Size) -> str:
    crop = crop_size(info.size, aspect)
    x, y = center_offset(info.size, crop)
    return f"[0:v]setpts=PTS-STARTPTS,crop={crop.width}:{crop.height}:{x}:{y},{_finish(output)}[v]"


def custom_filter(info: MediaInfo, aspect: AspectRatio, rect: CropRect, output: Size) -> str:
    crop, x, y = custom_window(info.size, rect, aspect)
    return f"[0:v]setpts=PTS-STARTPTS,crop={crop.width}:{crop.height}:{x}:{y},{_finish(output)}[v]"


def blur_filter(output: Size) -> str:
    small = Size(even(output.width / 4), even(output.height / 4))
    return (
        "[0:v]setpts=PTS-STARTPTS,split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={small.width}:{small.height}:force_original_aspect_ratio=increase,"
        f"crop={small.width}:{small.height},boxblur=8:2,eq=brightness=-0.06,"
        f"scale={output.width}:{output.height}[bg];"
        f"[fgsrc]scale={output.width}:{output.height}:force_original_aspect_ratio=decrease:"
        "flags=lanczos,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
    )


def smart_filter(plan: CropPlan, sendcmd_path: Path, output: Size) -> str:
    """``sendcmd_path`` is referenced by bare name; ffmpeg runs with its parent as cwd."""
    x, y = plan.offset_at(0.0)
    if not SAFE_NAME.match(sendcmd_path.name):
        raise ValueError(f"Unsafe sendcmd filename: {sendcmd_path.name}")
    return (
        f"[0:v]setpts=PTS-STARTPTS,sendcmd=f={sendcmd_path.name},"
        f"{SMART_CROP_FILTER}=w={plan.crop.width}:h={plan.crop.height}:x={x}:y={y},"
        f"{_finish(output)}[v]"
    )


def write_sendcmd(plan: CropPlan, fps: float, duration_s: float, path: Path) -> int:
    """Write one crop command per output frame; returns the number of commands."""
    frame_count = max(1, int(duration_s * fps + 0.5))
    lines = []
    last: tuple[int, int] | None = None
    for i in range(frame_count + 1):
        t = i / fps
        offset = plan.offset_at(t)
        if offset == last:
            continue
        last = offset
        lines.append(f"{t:.4f} {SMART_CROP_FILTER} x {offset[0]}, {SMART_CROP_FILTER} y {offset[1]};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def build_filter(request: RenderRequest) -> str:
    if request.mode is CropMode.BLUR:
        return blur_filter(request.output)
    if request.mode is CropMode.SMART:
        if request.plan is None or request.sendcmd_path is None:
            raise ValueError("smart mode requires a crop plan and a sendcmd path")
        return smart_filter(request.plan, request.sendcmd_path, request.output)
    if request.mode is CropMode.CUSTOM:
        if request.rect is None:
            raise ValueError("custom mode requires a crop rectangle")
        return custom_filter(request.info, request.aspect, request.rect, request.output)
    return center_filter(request.info, request.aspect, request.output)


def build_args(request: RenderRequest, encode: EncodeSettings) -> list[str]:
    args = [
        "-y", "-ss", f"{request.start_s:.3f}", "-i", str(request.source),
        "-t", f"{request.duration_s:.3f}",
        "-filter_complex", build_filter(request), "-map", "[v]",
    ]
    if request.info.has_audio:
        args += ["-map", "0:a:0", "-af", "asetpts=PTS-STARTPTS",
                 "-c:a", "aac", "-b:a", encode.audio_bitrate, "-ar", "48000", "-ac", "2"]
    args += [
        "-c:v", "libx264", "-preset", encode.preset, "-crf", str(encode.crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-f", "mp4", str(request.destination),
    ]
    return args


def render(request: RenderRequest, encode: EncodeSettings,
           on_progress: ProgressCallback | None = None) -> None:
    cwd = None
    if request.mode is CropMode.SMART and request.plan and request.sendcmd_path:
        write_sendcmd(request.plan, request.info.fps, request.duration_s, request.sendcmd_path)
        cwd = request.sendcmd_path.parent
    run_ffmpeg(build_args(request, encode), request.duration_s, on_progress, cwd=cwd)
