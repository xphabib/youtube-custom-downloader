"""``ytclip`` command-line interface."""

from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Column, Table

from ytclip import __version__
from ytclip.cache import clean_cache, dir_size
from ytclip.checks import FAIL, OK, WARN, run_checks
from ytclip.config import Config, ConfigError, config_path, load_config, write_default_config
from ytclip.db import Database
from ytclip.downloader import MediaCache
from ytclip.logging_setup import setup_logging
from ytclip.models import AspectRatio, CropMode, Job, JobStatus, Resolution
from ytclip.modelstore import MODELS, ModelDownloadError, download_model, model_path
from ytclip.native import install as native_install
from ytclip.pipeline import JobProcessor, YouTubeSource
from ytclip.repository import DuplicateJobError, JobNotFoundError, JobRepository, JobStateError
from ytclip.runner import Runner, RunnerBusyError, lock_is_held, run_lock
from ytclip.smartcrop.detectors import build_detector
from ytclip.timeutil import format_timestamp
from ytclip.validation import ValidationError, validate_clip

console = Console()
err_console = Console(stderr=True)
log = logging.getLogger("ytclip")

app = typer.Typer(
    help="Queue YouTube clips from Chrome, then download, trim and smart-crop them locally.\n\n"
         "Only process videos you own or are authorised to download.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
)
cache_app = typer.Typer(help="Inspect or clean downloaded sources and temporary files.")
models_app = typer.Typer(help="Manage optional detection model weights for smart crop.")
native_app = typer.Typer(help="Register the Chrome native messaging host.")
app.add_typer(cache_app, name="cache")
app.add_typer(models_app, name="models")
app.add_typer(native_app, name="native")

STATUS_STYLE = {
    JobStatus.PENDING: "yellow",
    JobStatus.DOWNLOADING: "cyan",
    JobStatus.PROCESSING: "blue",
    JobStatus.COMPLETED: "green",
    JobStatus.FAILED: "red",
}


@dataclass
class State:
    debug: bool = False
    _config: Config | None = None
    _repo: JobRepository | None = None

    @property
    def config(self) -> Config:
        if self._config is None:
            try:
                self._config = load_config()
            except ConfigError as exc:
                fail(f"Configuration error: {exc}", f"Edit {config_path()}")
            self._config.ensure_dirs()
            setup_logging(self._config.log_dir, debug=self.debug)
        return self._config

    @property
    def repo(self) -> JobRepository:
        if self._repo is None:
            self._repo = JobRepository(Database(self.config.db_path))
        return self._repo

    @property
    def lock_path(self) -> Path:
        return self.config.data_dir / "run.lock"


state = State()


def fail(message: str, hint: str | None = None, code: int = 1) -> None:
    err_console.print(f"[bold red]Error:[/] {message}")
    if hint:
        err_console.print(f"[dim]Hint: {hint}[/]")
    raise typer.Exit(code)


def _version(value: bool) -> None:
    if value:
        console.print(f"ytclip {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    debug: Annotated[bool, typer.Option("--debug", help="Verbose logs, including ffmpeg/yt-dlp commands.")] = False,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True,
                                          help="Show version and exit.")] = False,
) -> None:
    state.debug = debug


# ---- formatting -------------------------------------------------------------------

def _status(job: Job) -> str:
    text = job.status.value
    if job.status.is_active:
        text += f" {job.progress:.0%}"
    return f"[{STATUS_STYLE[job.status]}]{text}[/]"


def _span(job: Job) -> str:
    return f"{format_timestamp(job.spec.start_ms)} → {format_timestamp(job.spec.end_ms)}"


def _jobs_table(jobs: list[Job]) -> Table:
    table = Table(show_edge=False, header_style="bold")
    table.add_column("ID", no_wrap=True, min_width=2)
    table.add_column("Status", no_wrap=True, min_width=9)
    table.add_column("Video", no_wrap=True, overflow="ellipsis", max_width=28)
    table.add_column("Clip", no_wrap=True, min_width=19)
    table.add_column("Format", no_wrap=True, overflow="ellipsis")
    table.add_column("Output / error", overflow="fold")
    for job in jobs:
        s = job.spec
        detail = job.error if job.status is JobStatus.FAILED else (job.output_path or "")
        table.add_row(str(job.id), _status(job), s.title or s.video_id, _span(job),
                      f"{s.aspect_ratio.value} {s.crop_mode.value} {s.resolution.value}",
                      f"[red]{detail}[/]" if job.status is JobStatus.FAILED else detail)
    return table


def _get_job(job_id: int) -> Job:
    try:
        return state.repo.get(job_id)
    except JobNotFoundError as exc:
        fail(str(exc), "ytclip list")
        raise  # unreachable; keeps type checkers happy


# ---- queue commands ---------------------------------------------------------------

@app.command("list")
def list_jobs(
    status: Annotated[Optional[list[JobStatus]], typer.Option("--status", "-s", help="Filter by status (repeatable).")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Maximum rows.")] = 30,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List queued and recent jobs (newest first)."""
    jobs = state.repo.list(status, limit)
    if as_json:
        console.print_json(json.dumps([j.to_dict() for j in jobs]))
        return
    if not jobs:
        console.print("Queue is empty. Add clips from the Chrome extension or with [bold]ytclip add[/].")
        return
    console.print(_jobs_table(jobs))


@app.command()
def status() -> None:
    """Show job counts and whether a run is in progress."""
    counts = state.repo.counts()
    busy = lock_is_held(state.lock_path)
    parts = [f"[{STATUS_STYLE[s]}]{s.value}: {counts[s.value]}[/]" for s in JobStatus]
    console.print("  ".join(parts))
    console.print("Runner: " + ("[cyan]processing[/]" if busy else "idle"))
    for job in state.repo.list([JobStatus.DOWNLOADING, JobStatus.PROCESSING]):
        console.print(f"  #{job.id} {job.stage or job.status.value} {job.progress:.0%} — "
                      f"{job.spec.title or job.spec.video_id}")
    if counts["pending"] and not busy:
        console.print("Run [bold]ytclip run[/] to process pending jobs.")


@app.command()
def show(
    job_id: Annotated[int, typer.Argument(help="Job ID.")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Display details and history of one job."""
    job = _get_job(job_id)
    events = state.repo.events(job_id)
    if as_json:
        console.print_json(json.dumps({**job.to_dict(), "metadata": job.metadata, "events": events}))
        return
    s = job.spec
    rows = [
        ("Status", _status(job) + (f" ({job.stage})" if job.stage else "")),
        ("Title", s.title or "—"),
        ("URL", s.url),
        ("Clip", f"{_span(job)} ({s.duration_ms / 1000:.1f}s)"),
        ("Format", f"{s.aspect_ratio.value}, {s.crop_mode.value} crop, {s.resolution.value}"),
        *([("Crop area", "x={x}, y={y}, w={width}, h={height}".format(**s.crop_rect.to_dict()))]
          if s.crop_rect else []),
        ("Filename", s.output_name or "(automatic)"),
        ("Output", job.output_path or "—"),
        ("Attempts", str(job.attempts)),
        ("Created", job.created_at),
        ("Completed", job.completed_at or "—"),
    ]
    if job.error:
        rows.append(("Error", f"[red]{job.error}[/]"))
    if applied := job.metadata.get("applied_mode"):
        if applied != s.crop_mode.value:
            rows.append(("Crop used", f"{applied} ({job.metadata.get('fallback_reason', '')})"))
    if smart := job.metadata.get("smart"):
        rows.append(("Smart crop", ", ".join(f"{k}={v}" for k, v in smart.items())))
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(f"[bold]{key}[/]", value)
    console.print(table)
    if events:
        console.print("\n[bold]History[/]")
        for event in events[-15:]:
            style = {"error": "red", "warning": "yellow"}.get(event["level"], "dim")
            console.print(f"  [{style}]{event['at']}  {event['message']}[/]")


@app.command()
def add(
    url: Annotated[str, typer.Argument(help="YouTube watch, Shorts or youtu.be URL.")],
    start: Annotated[str, typer.Option("--start", "-s", help="Start time, e.g. 00:01:20.")],
    end: Annotated[str, typer.Option("--end", "-e", help="End time, e.g. 00:02:45.")],
    ratio: Annotated[AspectRatio, typer.Option("--ratio", "-r")] = AspectRatio.PORTRAIT,
    mode: Annotated[CropMode, typer.Option("--mode", "-m")] = CropMode.SMART,
    resolution: Annotated[Resolution, typer.Option("--resolution")] = Resolution.P1080,
    name: Annotated[Optional[str], typer.Option("--name", help="Output filename (without folder).")] = None,
    title: Annotated[Optional[str], typer.Option("--title", help="Video title for the default filename.")] = None,
    allow_duplicate: Annotated[bool, typer.Option("--allow-duplicate", help="Queue even if an identical clip exists.")] = False,
    rect: Annotated[Optional[str], typer.Option(
        "--rect", help="Crop area for --mode custom as x,y,width,height fractions, e.g. 0.3,0,0.4,1.")] = None,
) -> None:
    """Queue a clip from the terminal (the extension does the same via native messaging)."""
    clip = {"url": url, "start": start, "end": end, "aspectRatio": ratio.value,
            "cropMode": mode.value, "resolution": resolution.value, "outputName": name, "title": title}
    if rect is not None:
        try:
            x, y, w, h = (float(part) for part in rect.split(","))
        except ValueError:
            fail("--rect must be four numbers: x,y,width,height (fractions of the frame).")
        clip["cropRect"] = {"x": x, "y": y, "width": w, "height": h}
    try:
        job = state.repo.add(validate_clip(clip), allow_duplicate=allow_duplicate)
    except ValidationError as exc:
        fail(str(exc))
    except DuplicateJobError as exc:
        fail(str(exc), "Pass --allow-duplicate to queue it anyway.")
    console.print(f"Queued job [bold]#{job.id}[/]: {_span(job)} {ratio.value} {mode.value}")


@app.command()
def remove(
    job_id: Annotated[int, typer.Argument(help="Job ID.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Remove a job from the queue. Rendered output files are kept."""
    job = _get_job(job_id)
    if job.status.is_active and lock_is_held(state.lock_path):
        fail(f"Job #{job_id} is being processed right now.", "Wait for it to finish or stop `ytclip run`.")
    if not yes and not typer.confirm(f"Remove job #{job_id} ({job.status.value}, {_span(job)})?"):
        raise typer.Exit()
    try:
        if job.status.is_active:  # stale active state from a crashed run
            state.repo.recover_interrupted()
        state.repo.remove(job_id)
    except JobStateError as exc:
        fail(str(exc))
    console.print(f"Removed job #{job_id}." + (f" Output kept at {job.output_path}" if job.output_path else ""))


# ---- processing -------------------------------------------------------------------

class ConsoleReporter:
    """Rich progress bar per job."""

    def __init__(self, bar: Progress) -> None:
        self.bar = bar
        self.tasks: dict[int, TaskID] = {}

    def _task(self, job: Job) -> TaskID:
        if job.id not in self.tasks:
            label = job.spec.title or job.spec.video_id
            self.tasks[job.id] = self.bar.add_task(f"#{job.id} {label}", total=1.0, stage="starting")
        return self.tasks[job.id]

    def stage(self, job: Job, stage: str) -> None:
        self.bar.update(self._task(job), stage=stage)

    def progress(self, job: Job, fraction: float) -> None:
        self.bar.update(self._task(job), completed=fraction)


def _processor(reporter: ConsoleReporter) -> JobProcessor:
    cfg = state.config
    source = YouTubeSource(MediaCache(state.repo, cfg.downloads_dir, cfg.max_download_height))
    return JobProcessor(cfg, state.repo, source, lambda: build_detector(cfg.models_dir), reporter)


def _process(job_ids: list[int] | None) -> None:
    # Fixed-width, truncated label: a line wider than the terminal wraps and every
    # refresh then prints a new line instead of redrawing in place.
    label = Column(width=24, no_wrap=True, overflow="ellipsis")
    progress = Progress(
        TextColumn("{task.description}", table_column=label), BarColumn(bar_width=None),
        TextColumn("{task.percentage:>3.0f}%"), TextColumn("[dim]{task.fields[stage]}[/]"),
        TimeElapsedColumn(), console=console, transient=False,
    )
    reporter = ConsoleReporter(progress)
    runner = Runner(state.repo, _processor(reporter), state.lock_path)
    try:
        with progress:
            summary = runner.run(job_ids)
    except RunnerBusyError as exc:
        fail(str(exc), "Check progress with `ytclip status`.")
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/] The current job was returned to the queue; "
                          "run `ytclip run` to resume.")
        raise typer.Exit(130) from None

    if summary.recovered:
        console.print(f"[yellow]Recovered {len(summary.recovered)} interrupted job(s).[/]")
    for job_id in summary.completed:
        console.print(f"[green]✔[/] #{job_id} → {state.repo.get(job_id).output_path}")
    for job_id in summary.failed:
        console.print(f"[red]✘[/] #{job_id}: {state.repo.get(job_id).error}")
    if not summary.completed and not summary.failed:
        console.print("Nothing to do: no pending jobs.")
    elif summary.failed:
        console.print("Fix the problem and run [bold]ytclip retry[/], or see [bold]ytclip show <id>[/].")
    _auto_clean()
    if summary.failed:
        raise typer.Exit(1)


def _auto_clean() -> None:
    try:
        with run_lock(state.lock_path):
            result = clean_cache(state.config, state.repo)
    except RunnerBusyError:
        return
    if result.freed_bytes > 50 * 1024 * 1024:
        console.print(f"[dim]Cache cleanup freed {result.freed_bytes / 1024**2:.0f} MB.[/]")


@app.command()
def run(
    job_id: Annotated[Optional[list[int]], typer.Option("--id", help="Process only this job (repeatable).")] = None,
) -> None:
    """Process all pending jobs (or specific ones with --id)."""
    if job_id:
        for jid in job_id:
            job = _get_job(jid)
            if job.status is JobStatus.COMPLETED:
                fail(f"Job #{jid} is already completed ({job.output_path}).",
                     "Queue it again from the extension or with `ytclip add --allow-duplicate`.")
            if job.status is JobStatus.FAILED:
                state.repo.retry_failed(state.config.max_attempts, [jid], force=True)
    _process(job_id or None)


@app.command()
def retry(
    job_id: Annotated[Optional[list[int]], typer.Option("--id", help="Retry only this job (repeatable).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Ignore the max_attempts limit.")] = False,
    queue_only: Annotated[bool, typer.Option("--queue-only", help="Mark as pending without processing now.")] = False,
) -> None:
    """Retry failed jobs that have attempts left, then process them."""
    ids = state.repo.retry_failed(state.config.max_attempts, job_id or None, force=force or bool(job_id))
    if not ids:
        exhausted = [j.id for j in state.repo.list([JobStatus.FAILED])]
        hint = f"Jobs {exhausted} reached max_attempts; use --force." if exhausted else None
        console.print("No failed jobs eligible for retry.")
        if hint:
            console.print(f"[dim]{hint}[/]")
        return
    console.print(f"Re-queued job(s): {', '.join(f'#{i}' for i in ids)}")
    if not queue_only:
        _process(ids)


# ---- configuration & environment -------------------------------------------------

@app.command("config")
def show_config(
    init: Annotated[bool, typer.Option("--init", help="Create a commented config file if none exists.")] = False,
    path_only: Annotated[bool, typer.Option("--path", help="Print only the config file path.")] = False,
) -> None:
    """Show effective configuration and where the config file lives."""
    path = config_path()
    if path_only:
        console.print(str(path), soft_wrap=True)
        return
    if init:
        created = not path.exists()
        write_default_config(path)
        console.print(("Created " if created else "Config already exists: ") + str(path))
    cfg = state.config
    table = Table.grid(padding=(0, 2))
    table.add_row("[bold]Config file[/]", f"{path} " + ("" if path.exists() else "[dim](not created; defaults in use — `ytclip config --init`)[/]"))
    for key in ("output_dir", "data_dir", "cache_dir", "overwrite", "max_attempts", "max_download_height",
                "cache_keep_days", "min_free_space_mb", "video_crf", "video_preset", "audio_bitrate"):
        table.add_row(f"[bold]{key}[/]", str(getattr(cfg, key)))
    for key, value in vars(cfg.smart).items():
        table.add_row(f"[bold]smart.{key}[/]", str(value))
    table.add_row("[bold]database[/]", str(cfg.db_path))
    table.add_row("[bold]logs[/]", str(cfg.log_dir))
    console.print(table)
    console.print(f"\nEdit with: [bold]{os.environ.get('EDITOR', 'nano')} {shlex.quote(str(path))}[/]")


@app.command("install-check")
def install_check() -> None:
    """Check Python, FFmpeg, yt-dlp, models, directories, database and native host."""
    icons = {OK: "[green]✔[/]", WARN: "[yellow]![/]", FAIL: "[red]✘[/]"}
    results = run_checks(state.config)
    table = Table.grid(padding=(0, 2))
    for r in results:
        table.add_row(icons[r.status], f"[bold]{r.name}[/]", r.detail)
        if r.hint and r.status != OK:
            table.add_row("", "", f"[dim]→ {r.hint}[/]")
    console.print(table)
    failures = [r for r in results if r.status == FAIL]
    console.print("\n" + ("[red]Some checks failed.[/]" if failures else "[green]Ready.[/]"))
    if failures:
        raise typer.Exit(1)


# ---- cache ------------------------------------------------------------------------

@cache_app.command("info")
def cache_info() -> None:
    """Show cache size and cached source videos."""
    cfg = state.config
    console.print(f"Downloads: {cfg.downloads_dir} ({dir_size(cfg.downloads_dir) / 1024**2:.0f} MB)")
    console.print(f"Work files: {cfg.work_dir} ({dir_size(cfg.work_dir) / 1024**2:.0f} MB)")
    console.print(f"Policy: sources unused for {cfg.cache_keep_days} days are removed after each run.")
    for media in state.repo.list_media():
        size = (media.size_bytes or 0) / 1024**2
        console.print(f"  {media.video_id} {media.variant} {size:.0f} MB  last used {media.last_used_at}  "
                      f"{(media.title or '')[:40]}")


@cache_app.command("clean")
def cache_clean(
    remove_all: Annotated[bool, typer.Option("--all", help="Remove every cached source, even if jobs still need it.")] = False,
    older_than: Annotated[Optional[int], typer.Option("--older-than", help="Days unused before removal.")] = None,
) -> None:
    """Delete cached downloads and temporary files. Final outputs are never deleted."""
    try:
        with run_lock(state.lock_path):
            result = clean_cache(state.config, state.repo, older_than, remove_all)
    except RunnerBusyError as exc:
        fail(str(exc), "Clean the cache after the run finishes.")
    console.print(f"Removed {len(result.removed)} item(s), freed {result.freed_bytes / 1024**2:.1f} MB.")


# ---- models -----------------------------------------------------------------------

@models_app.command("list")
def models_list() -> None:
    """Show model files and whether they are installed."""
    for key, spec in MODELS.items():
        installed = model_path(state.config.models_dir, key)
        mark = "[green]installed[/]" if installed else "[yellow]missing[/]"
        console.print(f"{key:6} {mark}  {spec.description}, {spec.size_bytes / 1e6:.1f} MB, {spec.license}")
    console.print(f"[dim]Directory: {state.config.models_dir}[/]")


@models_app.command("download")
def models_download(
    force: Annotated[bool, typer.Option("--force", help="Download again even if present.")] = False,
) -> None:
    """Download YuNet (face) and YOLOX-S (person/object) ONNX weights, verifying SHA-256."""
    for key, spec in MODELS.items():
        with console.status(f"Downloading {spec.filename} ({spec.size_bytes / 1e6:.1f} MB)…"):
            try:
                path = download_model(state.config.models_dir, key, force)
            except ModelDownloadError as exc:
                fail(str(exc), "Check your network connection, or download the file manually into "
                               f"{state.config.models_dir}")
        console.print(f"[green]✔[/] {key}: {path}")


# ---- native host ------------------------------------------------------------------

@native_app.command("install")
def native_install_cmd(
    extension_id: Annotated[Optional[list[str]], typer.Option("--extension-id", help="Extra allowed extension ID (repeatable).")] = None,
) -> None:
    """Register the native host for Chrome (and Chromium/Brave if present)."""
    try:
        locations = native_install.install(state.config.data_dir / "bin", extension_id)
    except ValueError as exc:
        fail(str(exc))
    for location in locations:
        console.print(f"[green]✔[/] {location.browser}: {location.path}")
    console.print("Reload the extension (or restart Chrome) for it to connect.")


@native_app.command("uninstall")
def native_uninstall_cmd() -> None:
    """Remove the native host registration."""
    removed = native_install.uninstall()
    console.print("\n".join(f"Removed {p}" for p in removed) or "Nothing to remove.")


def main() -> None:
    app()
