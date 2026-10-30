"""Registry and downloader for optional detection model weights.

Weights are not committed to the repository. ``ytclip models download`` fetches them
into ``<data_dir>/models`` and verifies their SHA-256 checksums.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    sha256: str
    size_bytes: int
    license: str
    description: str
    urls: tuple[str, ...]


MODELS: dict[str, ModelSpec] = {
    "yunet": ModelSpec(
        key="yunet",
        filename="face_detection_yunet_2023mar.onnx",
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        size_bytes=232_589,
        license="MIT",
        description="YuNet face detector (OpenCV Zoo)",
        urls=(
            "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        ),
    ),
    "yolox": ModelSpec(
        key="yolox",
        filename="object_detection_yolox_2022nov.onnx",
        sha256="c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
        size_bytes=35_858_002,
        license="Apache-2.0",
        description="YOLOX-S COCO person/object detector (OpenCV Zoo)",
        urls=(
            "https://huggingface.co/opencv/object_detection_yolox/resolve/main/object_detection_yolox_2022nov.onnx",
            "https://github.com/opencv/opencv_zoo/raw/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx",
        ),
    ),
}


class ModelDownloadError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_path(models_dir: Path, key: str) -> Path | None:
    """Path of an installed model, or None. Size is checked; the hash is checked on download."""
    spec = MODELS[key]
    path = models_dir / spec.filename
    return path if path.is_file() and path.stat().st_size == spec.size_bytes else None


def download_model(models_dir: Path, key: str, force: bool = False) -> Path:
    spec = MODELS[key]
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / spec.filename
    if not force and model_path(models_dir, key) and sha256_file(target) == spec.sha256:
        return target
    partial = target.with_suffix(".part")
    errors = []
    for url in spec.urls:
        try:
            log.info("Downloading %s from %s", spec.filename, url)
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
            actual = sha256_file(partial)
            if actual != spec.sha256:
                raise ModelDownloadError(f"checksum mismatch (got {actual[:12]}…)")
            partial.replace(target)
            return target
        except (OSError, ModelDownloadError) as exc:
            errors.append(f"{url}: {exc}")
            partial.unlink(missing_ok=True)
    raise ModelDownloadError(f"Could not download {spec.filename}:\n  " + "\n  ".join(errors))
