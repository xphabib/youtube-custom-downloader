"""Configuration: XDG-based locations plus a user-editable TOML file.

Precedence: built-in defaults < ``config.toml`` < environment (``YTCLIP_CONFIG``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "ytclip"
OVERWRITE_POLICIES = ("rename", "skip", "overwrite")
FALLBACKS = ("center", "blur")


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def config_path() -> Path:
    override = os.environ.get("YTCLIP_CONFIG")
    if override:
        return Path(override)
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME / "config.toml"


def default_data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP_NAME


def default_cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_NAME


@dataclass
class SmartCropConfig:
    sample_fps: float = 3.0
    max_samples: int = 360
    analysis_width: int = 640
    fallback: str = "center"
    use_motion: bool = True
    deadzone: float = 0.06
    smoothing_seconds: float = 0.8
    max_pan_per_second: float = 0.35
    scene_threshold: float = 0.45


@dataclass
class Config:
    output_dir: Path = field(default_factory=lambda: Path.home() / "Videos" / APP_NAME)
    data_dir: Path = field(default_factory=default_data_dir)
    cache_dir: Path = field(default_factory=default_cache_dir)
    overwrite: str = "rename"
    max_attempts: int = 3
    max_download_height: int = 1080
    cache_keep_days: int = 7
    min_free_space_mb: int = 1024
    video_crf: int = 20
    video_preset: str = "medium"
    audio_bitrate: str = "192k"
    smart: SmartCropConfig = field(default_factory=SmartCropConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def downloads_dir(self) -> Path:
        return self.cache_dir / "downloads"

    @property
    def work_dir(self) -> Path:
        return self.cache_dir / "work"

    def ensure_dirs(self) -> None:
        for path in (self.output_dir, self.data_dir, self.models_dir, self.log_dir,
                     self.downloads_dir, self.work_dir):
            path.mkdir(parents=True, exist_ok=True)


class ConfigError(ValueError):
    pass


def _apply(target: Any, values: dict[str, Any], prefix: str = "") -> None:
    known = {f.name: f for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            raise ConfigError(f"Unknown config key '{prefix}{key}'")
        current = getattr(target, key)
        if isinstance(current, SmartCropConfig):
            if not isinstance(value, dict):
                raise ConfigError(f"'{prefix}{key}' must be a table")
            _apply(current, value, f"{key}.")
        elif isinstance(current, Path):
            setattr(target, key, Path(os.path.expanduser(str(value))))
        elif isinstance(current, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"'{prefix}{key}' must be true or false")
            setattr(target, key, value)
        elif isinstance(current, str):
            if not isinstance(value, str):
                raise ConfigError(f"'{prefix}{key}' must be text")
            setattr(target, key, value)
        else:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"'{prefix}{key}' must be a number")
            setattr(target, key, type(current)(value))


def _check(config: Config) -> None:
    if config.overwrite not in OVERWRITE_POLICIES:
        raise ConfigError(f"overwrite must be one of {OVERWRITE_POLICIES}")
    if config.smart.fallback not in FALLBACKS:
        raise ConfigError(f"smart.fallback must be one of {FALLBACKS}")
    if not 0.2 <= config.smart.sample_fps <= 30:
        raise ConfigError("smart.sample_fps must be between 0.2 and 30")
    if config.max_attempts < 1:
        raise ConfigError("max_attempts must be at least 1")


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    config = Config()
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        _apply(config, data)
    _check(config)
    return config


DEFAULT_CONFIG_TEMPLATE = """\
# ytclip configuration. Remove a line (or the whole file) to use the default.

# Final MP4 files are written here.
# output_dir = "~/Videos/ytclip"

# What to do if an output file already exists: "rename" (add -1, -2 ...),
# "skip" (fail the job, keep the existing file) or "overwrite".
# overwrite = "rename"

# Failed jobs are retried by `ytclip retry` until they reach this many attempts.
# max_attempts = 3

# Highest source resolution yt-dlp should fetch.
# max_download_height = 1080

# Cached source downloads unused for this many days are removed by `ytclip cache clean`.
# cache_keep_days = 7

# Refuse to start a job when less free space than this remains.
# min_free_space_mb = 1024

# x264 quality (lower = better/bigger) and preset.
# video_crf = 20
# video_preset = "medium"
# audio_bitrate = "192k"

[smart]
# Frames analysed per second of clip, capped by max_samples.
# sample_fps = 3.0
# max_samples = 360
# What to do when no subject is found anywhere in a clip: "center" or "blur".
# fallback = "center"
# Use motion as a weak saliency signal when no person/face/object is detected.
# use_motion = true
# Ignore subject movement smaller than this fraction of the crop width.
# deadzone = 0.06
# Temporal smoothing window, in seconds.
# smoothing_seconds = 0.8
# Fastest allowed pan, as a fraction of the source width per second.
# max_pan_per_second = 0.35
# Histogram distance (0-1) above which consecutive samples count as a scene cut.
# scene_threshold = 0.45
"""


def write_default_config(path: Path | None = None) -> Path:
    path = path or config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return path
