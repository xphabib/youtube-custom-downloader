"""Cache cleanup policy for downloaded sources and intermediate work files.

Rules:
* Final outputs are never touched.
* A source is kept while any pending/failed job still needs it (unless ``--all``).
* Otherwise a source is removed once unused for ``cache_keep_days``.
* Work directories are always disposable when no runner is active.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ytclip.config import Config
from ytclip.repository import JobRepository


@dataclass
class CleanResult:
    removed: list[Path] = field(default_factory=list)
    freed_bytes: int = 0


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _remove(path: Path, result: CleanResult) -> None:
    size = dir_size(path) if path.is_dir() else path.stat().st_size
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    result.removed.append(path)
    result.freed_bytes += size


def clean_cache(config: Config, repo: JobRepository, keep_days: int | None = None,
                remove_all: bool = False) -> CleanResult:
    """Caller must hold the runner lock so no job is using these files."""
    result = CleanResult()
    keep_days = config.cache_keep_days if keep_days is None else keep_days
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    needed = set() if remove_all else repo.media_needed_by_pending()
    downloads = config.downloads_dir.resolve()

    known: set[Path] = set()
    for media in repo.list_media():
        path = Path(media.path).resolve()
        known.add(path)
        stale = remove_all or (
            media.video_id not in needed and datetime.fromisoformat(media.last_used_at) < cutoff
        )
        if stale:
            if path.is_relative_to(downloads) and path.exists():
                _remove(path, result)
            repo.delete_media(media.id)

    # Orphans: partial downloads and files whose DB record is gone.
    age_limit = 0 if remove_all else keep_days * 86400
    if downloads.exists():
        for file in downloads.rglob("*"):
            if file.is_file() and file.resolve() not in known and time.time() - file.stat().st_mtime >= age_limit:
                _remove(file, result)
        for folder in sorted(downloads.glob("*"), reverse=True):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()

    if config.work_dir.exists():
        for folder in config.work_dir.iterdir():
            _remove(folder, result)
    return result
