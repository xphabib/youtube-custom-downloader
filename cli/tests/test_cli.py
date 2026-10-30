"""CLI commands and error handling, driven through Typer's test runner."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from conftest import URL, LocalSource, requires_ffmpeg
from ytclip import cli
from ytclip.pipeline import JobProcessor
from ytclip.smartcrop.detectors import CompositeDetector

runner = CliRunner()


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "state", cli.State())
    monkeypatch.setenv("COLUMNS", "200")


def invoke(*args: str, input: str | None = None):
    return runner.invoke(cli.app, list(args), input=input)


def plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def add_clip(*extra: str):
    return invoke("add", URL, "--start", "00:00:01", "--end", "00:00:03", *extra)


def test_help_lists_commands() -> None:
    result = invoke("--help")
    assert result.exit_code == 0
    for command in ("install-check", "list", "run", "retry", "status", "show", "remove", "config"):
        assert command in result.output


def test_version() -> None:
    assert "ytclip 0.1.0" in invoke("--version").output


def test_add_list_show_status_remove() -> None:
    result = add_clip("--ratio", "1:1", "--mode", "blur", "--name", "my clip")
    assert result.exit_code == 0 and "Queued job #1" in result.output

    listed = invoke("list")
    assert "pending" in listed.output and "1:1 blur 1080p" in plain(listed.output)

    data = json.loads(invoke("list", "--json").output)
    assert data[0]["outputName"] == "my clip"

    shown = plain(invoke("show", "1").output)
    assert "00:00:01 → 00:00:03 (2.0s)" in shown and "Job added to queue" in shown
    assert json.loads(invoke("show", "1", "--json").output)["events"][0]["stage"] == "queued"

    status = plain(invoke("status").output)
    assert "pending: 1" in status and "Runner: idle" in status

    assert invoke("remove", "1", "--yes").exit_code == 0
    assert "Queue is empty" in invoke("list").output


def test_duplicates_and_validation_errors() -> None:
    add_clip()
    dup = add_clip()
    assert dup.exit_code == 1 and "already queued" in dup.output and "--allow-duplicate" in dup.output
    assert add_clip("--allow-duplicate").exit_code == 0

    bad_time = invoke("add", URL, "--start", "00:00:05", "--end", "00:00:02")
    assert bad_time.exit_code == 1 and "End time must be greater" in bad_time.output
    bad_url = invoke("add", "https://example.com/v", "--start", "1", "--end", "2")
    assert bad_url.exit_code == 1 and "not a YouTube address" in bad_url.output
    bad_ratio = invoke("add", URL, "--start", "1", "--end", "2", "--ratio", "4:3")
    assert bad_ratio.exit_code != 0


def test_unknown_job_ids() -> None:
    for args in (("show", "42"), ("remove", "42", "-y"), ("run", "--id", "42")):
        result = invoke(*args)
        assert result.exit_code == 1 and "does not exist" in result.output


def test_remove_asks_for_confirmation() -> None:
    add_clip()
    assert invoke("remove", "1", input="n\n").exit_code == 0
    assert "pending" in invoke("list").output


def test_config_command(isolated_home) -> None:
    result = invoke("config")
    assert result.exit_code == 0 and "output_dir" in result.output and "not created" in plain(result.output)
    init = invoke("config", "--init")
    path = isolated_home / "config" / "ytclip" / "config.toml"
    assert path.exists() and "Created" in init.output
    assert invoke("config", "--path").output.strip().replace("\n", "") == str(path)


def test_bad_config_is_reported(isolated_home) -> None:
    path = isolated_home / "config" / "ytclip" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('overwrite = "sometimes"\n')
    result = invoke("list")
    assert result.exit_code == 1 and "Configuration error" in result.output


def test_install_check_reports_missing_native_host() -> None:
    result = invoke("install-check")
    out = plain(result.output)
    assert "ffmpeg" in out and "Database" in out and "Native host" in out
    assert "ytclip native install" in out
    assert result.exit_code == 1  # native host not registered in the isolated home


def test_native_install_then_install_check(isolated_home) -> None:
    result = invoke("native", "install")
    assert result.exit_code == 0 and "Google Chrome" in result.output
    manifest = isolated_home / "home" / ".config" / "google-chrome" / "NativeMessagingHosts" / "com.ytclip.host.json"
    assert manifest.exists()
    check = plain(invoke("install-check").output)
    assert re.search(r"✔\s+Native host", check)
    assert "Removed" in invoke("native", "uninstall").output


def test_retry_without_failed_jobs() -> None:
    assert "No failed jobs" in invoke("retry").output


@requires_ffmpeg
def test_run_and_retry_commands(monkeypatch, moving_box) -> None:
    def local_processor(reporter):
        state = cli.state
        return JobProcessor(state.config, state.repo, LocalSource(moving_box),
                            lambda: CompositeDetector([]), reporter)

    monkeypatch.setattr(cli, "_processor", local_processor)
    monkeypatch.setattr(cli.state.config, "video_preset", "ultrafast", raising=False)
    add_clip("--mode", "center", "--resolution", "480p")
    invoke("add", URL, "--start", "00:00:02", "--end", "00:00:50")  # past the 4 s video

    result = invoke("run")
    out = plain(result.output)
    assert result.exit_code == 1  # one job failed
    assert "✔ #1" in out and "✘ #2" in out and "only 00:00:04 long" in out
    assert "ytclip retry" in out

    assert "Nothing to do" in invoke("run").output
    retried = plain(invoke("retry", "--id", "2").output)
    assert "Re-queued job(s): #2" in retried and "✘ #2" in retried
    completed = invoke("run", "--id", "1")
    assert completed.exit_code == 1 and "already completed" in completed.output
