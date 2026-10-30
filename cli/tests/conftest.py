"""Shared fixtures. Synthetic footage is generated with ffmpeg; nothing is downloaded."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ytclip.config import Config
from ytclip.db import Database
from ytclip.models import Job
from ytclip.pipeline import SourceMedia
from ytclip.repository import JobRepository
from ytclip.validation import validate_clip

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every XDG location at a temp dir so tests never touch real user data."""
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("YTCLIP_CONFIG", raising=False)
    return tmp_path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config(output_dir=tmp_path / "out", data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    cfg.video_preset = "ultrafast"
    cfg.min_free_space_mb = 1
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def db(config: Config):
    database = Database(config.db_path)
    yield database
    database.close()


@pytest.fixture
def repo(db: Database) -> JobRepository:
    return JobRepository(db)


def clip(**overrides) -> dict:
    data = {"url": URL, "start": "00:00:01", "end": "00:00:03", "aspectRatio": "9:16",
            "cropMode": "smart", "resolution": "480p"}
    data.update(overrides)
    return data


@pytest.fixture
def make_spec():
    return lambda **kw: validate_clip(clip(**kw))


# ---- synthetic media -----------------------------------------------------------------

def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def make_moving_box(path: Path, duration: float = 4.0, size: str = "640x360", audio: bool = True,
                    x_expr: str = "20+t*130", fps: int = 30) -> Path:
    """Dark background with a bright box moving horizontally, plus a sine tone."""
    w, h = (int(v) for v in size.split("x"))
    inputs = ["-f", "lavfi", "-i", f"color=c=0x202020:s={size}:r={fps}:d={duration}",
              "-f", "lavfi", "-i", f"color=c=white:s={w // 10}x{h // 3}:r={fps}:d={duration}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
    graph = f"[0][1]overlay=x='{x_expr}':y={h // 3}:shortest=1[v]"
    args = [*inputs, "-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", "2:a", "-c:a", "aac"]
    _ffmpeg(*args, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", str(duration), str(path))
    return path


def make_static(path: Path, duration: float = 3.0, size: str = "640x360") -> Path:
    """Featureless footage: nothing to detect, nothing moves."""
    _ffmpeg("-f", "lavfi", "-i", f"color=c=0x303030:s={size}:r=25:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path))
    return path


def make_scene_cut(path: Path, size: str = "640x360") -> Path:
    """Two 3s scenes with different backgrounds: box on the left, then on the right."""
    w, h = (int(v) for v in size.split("x"))
    bw, bh = w // 10, h // 3
    graph = (
        f"color=c=0x203040:s={size}:r=30:d=3[a];color=c=0x604020:s={size}:r=30:d=3[b];"
        f"color=c=white:s={bw}x{bh}:r=30:d=3[box1];color=c=white:s={bw}x{bh}:r=30:d=3[box2];"
        f"[a][box1]overlay=x='60+10*sin(t*3)':y={h // 3}[s1];"
        f"[b][box2]overlay=x='{w - 60 - bw}+10*sin(t*3)':y={h // 3}[s2];"
        "[s1][s2]concat=n=2:v=1:a=0[v]"
    )
    _ffmpeg("-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(path))
    return path


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def moving_box(media_dir: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    return make_moving_box(media_dir / "moving.mp4")


@pytest.fixture(scope="session")
def moving_box_silent(media_dir: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    return make_moving_box(media_dir / "silent.mp4", audio=False)


@pytest.fixture(scope="session")
def static_video(media_dir: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    return make_static(media_dir / "static.mp4")


@pytest.fixture(scope="session")
def scene_cut_video(media_dir: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    return make_scene_cut(media_dir / "cut.mp4")


class LocalSource:
    """Source provider that serves a local file instead of downloading."""

    def __init__(self, path: Path, title: str | None = "Synthetic") -> None:
        self.path, self.title, self.calls = path, title, 0

    def fetch(self, job: Job, on_progress: Callable[[float], None]) -> SourceMedia:
        self.calls += 1
        on_progress(1.0)
        return SourceMedia(self.path, None, self.title, cached=self.calls > 1)


@pytest.fixture
def no_detector():
    """An empty detector: smart crop then relies on motion saliency, as with no models."""
    from ytclip.smartcrop.detectors import CompositeDetector

    return lambda: CompositeDetector([])


def subject_center_x(path: Path, times: list[float]) -> list[float | None]:
    """Horizontal centre (0..1) of bright pixels in output frames at the given times."""
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(path))
    fps = capture.get(cv2.CAP_PROP_FPS)
    wanted = {int(round(t * fps)) for t in times}
    found: dict[int, float | None] = {}
    index = 0
    while wanted - found.keys():
        ok, frame = capture.read()
        if not ok:
            break
        if index in wanted:
            cols = np.where((frame.max(axis=2) > 200).any(axis=0))[0]
            found[index] = ((cols.min() + cols.max()) / 2 / frame.shape[1]) if cols.size else None
        index += 1
    capture.release()
    return [found.get(int(round(t * fps))) for t in times]
