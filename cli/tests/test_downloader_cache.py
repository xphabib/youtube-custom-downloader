"""yt-dlp command construction, progress/error parsing, media cache reuse and cleanup.

No network: yt-dlp itself is never invoked here.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from conftest import VIDEO_ID, requires_ffmpeg
from ytclip.cache import clean_cache
from ytclip.downloader import DownloadError, MediaCache, build_command, classify_error, parse_progress
from ytclip.models import JobStatus


def test_build_command_is_an_argument_list_with_canonical_url(tmp_path: Path) -> None:
    cmd = build_command(VIDEO_ID, tmp_path / "x", 720)
    assert cmd[-2:] == ["--", f"https://www.youtube.com/watch?v={VIDEO_ID}"]
    assert "--no-playlist" in cmd and "--no-config" in cmd
    assert "--progress" in cmd  # --print implies --quiet, which would hide progress lines
    assert "height<=720" in cmd[cmd.index("-f") + 1]
    assert cmd[cmd.index("-o") + 1] == f"{tmp_path / 'x'}.%(ext)s"
    for forbidden in ("--cookies-from-browser", "--cookies", "--exec", "--allow-unplayable-formats"):
        assert forbidden not in cmd


def test_build_command_rejects_invalid_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_command("$(rm -rf ~)", tmp_path / "x", 720)


@pytest.mark.parametrize("line, expected", [
    ("ytclip-progress 500 1000 NA", 0.5),
    ("ytclip-progress 500 NA 2000", 0.25),
    ("ytclip-progress 500 NA NA", None),
    ("ytclip-progress 5000 1000 NA", 1.0),
    ("[download] 50%", None),
])
def test_parse_progress(line: str, expected: float | None) -> None:
    assert parse_progress(line) == expected


@pytest.mark.parametrize("stderr, fragment, retryable", [
    ("ERROR: [youtube] x: Private video. Sign in", "private", False),
    ("ERROR: This video is DRM protected", "DRM", False),
    ("ERROR: Sign in to confirm your age", "age-restricted", False),
    ("ERROR: Video unavailable. This video has been removed", "unavailable", False),
    ("ERROR: HTTP Error 429: Too Many Requests", "rate limiting", True),
    ("ERROR: Unable to download webpage: <urlopen error [Errno -3]>", "Network error", True),
    ("ERROR: something new", "yt-dlp failed: ERROR: something new", True),
])
def test_classify_error(stderr: str, fragment: str, retryable: bool) -> None:
    error = classify_error(stderr)
    assert isinstance(error, DownloadError) and fragment in str(error) and error.retryable is retryable


@requires_ffmpeg
def test_cache_reuses_existing_download(config, repo, moving_box, monkeypatch) -> None:
    cache = MediaCache(repo, config.downloads_dir, 1080)
    target = config.downloads_dir / VIDEO_ID / f"{VIDEO_ID}_h1080.mp4"
    target.parent.mkdir(parents=True)
    calls = []

    def fake_download(video_id, stem, on_progress):
        calls.append(video_id)
        shutil.copy(moving_box, target)
        return target, "Fetched title"

    monkeypatch.setattr(cache, "_download", fake_download)
    first = cache.ensure(VIDEO_ID, None)
    second = cache.ensure(VIDEO_ID, None)
    assert calls == [VIDEO_ID] and not first.cached and second.cached
    assert second.media.title == "Fetched title" and second.path == target

    target.write_bytes(b"corrupt")  # damaged cache entry is discarded and re-fetched
    third = cache.ensure(VIDEO_ID, None)
    assert calls == [VIDEO_ID, VIDEO_ID] and not third.cached


def test_cache_never_deletes_outside_downloads(config, repo, tmp_path) -> None:
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"user file")
    repo.upsert_media(VIDEO_ID, "h1080", str(outside), None, 1.0, 9)
    assert MediaCache(repo, config.downloads_dir, 1080).cached(VIDEO_ID) is None
    assert outside.exists()


def test_clean_cache_policy(config, repo, make_spec) -> None:
    def cached_file(video_id: str, days_old: int) -> Path:
        path = config.downloads_dir / video_id / f"{video_id}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
        media = repo.upsert_media(video_id, "h1080", str(path), None, 1.0, 1000)
        with repo.db.transaction() as conn:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - days_old * 86400))
            conn.execute("UPDATE media SET last_used_at = ? WHERE id = ?", (stamp, media.id))
        return path

    old_unused = cached_file("aaaaaaaaaaa", 30)
    old_needed = cached_file(VIDEO_ID, 30)
    recent = cached_file("bbbbbbbbbbb", 1)
    repo.add(make_spec())  # pending job still needs VIDEO_ID
    orphan = config.downloads_dir / "ccccccccccc" / "x.mp4.part"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"partial")
    os.utime(orphan, (time.time() - 30 * 86400,) * 2)
    work = config.work_dir / "job-9"
    work.mkdir(parents=True)
    output = config.output_dir / "final.mp4"
    output.write_bytes(b"keep me")

    result = clean_cache(config, repo)
    assert not old_unused.exists() and not orphan.exists() and not work.exists()
    assert old_needed.exists() and recent.exists() and output.exists()
    assert result.freed_bytes >= 1000 + len(b"partial")

    clean_cache(config, repo, remove_all=True)
    assert not old_needed.exists() and not recent.exists() and output.exists()
    assert repo.list_media() == []
    assert repo.get(1).status is JobStatus.PENDING
