"""Environment diagnostics for ``ytclip install-check``."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ytclip.config import Config
from ytclip.db import Database
from ytclip.modelstore import MODELS, model_path
from ytclip.native import DEFAULT_EXTENSION_ID
from ytclip.native.install import installed_manifest
from ytclip.smartcrop.detectors import builtin_detectors_available

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    hint: str | None = None


def _version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""


def check_python() -> CheckResult:
    version = ".".join(map(str, sys.version_info[:3]))
    ok = sys.version_info >= (3, 11)
    return CheckResult("Python", OK if ok else FAIL, f"{version} ({sys.executable})",
                       None if ok else "Python 3.11 or newer is required")


def check_tool(name: str) -> CheckResult:
    path = shutil.which(name)
    if not path:
        return CheckResult(name, FAIL, "not found", "sudo apt install ffmpeg")
    line = _version([name, "-version"]) or "unknown version"
    return CheckResult(name, OK, " ".join(line.split()[:3]))


def check_encoders() -> CheckResult:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                             text=True, timeout=30, check=False).stdout
    except FileNotFoundError:
        return CheckResult("H.264/AAC encoders", FAIL, "ffmpeg missing")
    missing = [e for e in ("libx264", " aac ") if e not in out]
    if missing:
        return CheckResult("H.264/AAC encoders", FAIL, f"missing: {', '.join(m.strip() for m in missing)}",
                           "Install Ubuntu's ffmpeg package, which includes libx264 and aac")
    return CheckResult("H.264/AAC encoders", OK, "libx264, aac")


def check_ytdlp() -> CheckResult:
    version = _version([sys.executable, "-m", "yt_dlp", "--version"])
    if version is None:
        return CheckResult("yt-dlp", FAIL, "not importable in this environment",
                           f"{sys.executable} -m pip install -U yt-dlp")
    return CheckResult("yt-dlp", OK, version,
                       f"Keep it current: {sys.executable} -m pip install -U yt-dlp")


def check_models(config: Config) -> CheckResult:
    present = [k for k in MODELS if model_path(config.models_dir, k)]
    if len(present) == len(MODELS):
        return CheckResult("Detection models", OK, f"YuNet + YOLOX in {config.models_dir}")
    fallback = ("Haar/HOG built-in detectors" if builtin_detectors_available()
                else "motion saliency only")
    missing = ", ".join(k for k in MODELS if k not in present)
    return CheckResult("Detection models", WARN, f"missing {missing}; smart crop uses {fallback}",
                       "ytclip models download")


def check_dir(label: str, path: Path) -> CheckResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(label, FAIL, f"{path}: {exc}")
    if not os.access(path, os.W_OK):
        return CheckResult(label, FAIL, f"{path} is not writable")
    free_gb = shutil.disk_usage(path).free / 1024**3
    status = OK if free_gb >= 2 else WARN
    return CheckResult(label, status, f"{path} ({free_gb:.1f} GB free)",
                       None if status == OK else "Low disk space")


def check_database(config: Config) -> CheckResult:
    try:
        db = Database(config.db_path)
        count = db.query("SELECT COUNT(*) AS n FROM jobs")[0]["n"]
        db.close()
    except Exception as exc:  # noqa: BLE001 - report any DB problem
        return CheckResult("Database", FAIL, f"{config.db_path}: {exc}")
    return CheckResult("Database", OK, f"{config.db_path} ({count} jobs)")


def check_native_host() -> CheckResult:
    found = installed_manifest()
    if not found:
        return CheckResult("Native host", FAIL, "not registered with Chrome", "ytclip native install")
    path, manifest = found
    launcher = Path(manifest.get("path", ""))
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        return CheckResult("Native host", FAIL, f"{path} points to missing launcher {launcher}",
                           "ytclip native install")
    origins = manifest.get("allowed_origins", [])
    if f"chrome-extension://{DEFAULT_EXTENSION_ID}/" not in origins:
        return CheckResult("Native host", WARN, f"{path} does not allow the bundled extension ID",
                           "ytclip native install")
    return CheckResult("Native host", OK, str(path))


def run_checks(config: Config) -> list[CheckResult]:
    return [
        check_python(),
        check_tool("ffmpeg"),
        check_tool("ffprobe"),
        check_encoders(),
        check_ytdlp(),
        check_models(config),
        check_dir("Output directory", config.output_dir),
        check_dir("Cache directory", config.cache_dir),
        check_database(config),
        check_native_host(),
    ]
