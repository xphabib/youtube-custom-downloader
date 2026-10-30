"""Request dispatch for the native host.

The extension can only call the fixed operations below. None of them run arbitrary
commands or read arbitrary files; they manage queue records in SQLite. The one
exception is ``start_run``, which launches ``ytclip run`` in the background.

Request:  {"id": "<= 64 chars", "type": "<operation>", "payload": {...}}
Response: {"id": ..., "ok": true, "data": {...}}
       or {"id": ..., "ok": false, "error": {"code": ..., "message": ..., "field": ...}}
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ytclip import __version__
from ytclip.config import Config
from ytclip.modelstore import model_path
from ytclip.repository import DuplicateJobError, JobNotFoundError, JobRepository, JobStateError
from ytclip.validation import ValidationError, validate_clip

log = logging.getLogger(__name__)

MAX_LIST_LIMIT = 200
REQUEST_KEYS = {"id", "type", "payload"}


class RequestError(Exception):
    def __init__(self, code: str, message: str, field: str | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.field, self.extra = code, field, extra or {}


def launch_runner(config: Config) -> None:
    """Start ``ytclip run`` detached from the host, which Chrome stops after each reply."""
    log_file = (config.log_dir / "extension-run.log").open("ab")
    with log_file:
        subprocess.Popen(
            [sys.executable, "-m", "ytclip", "run"],
            stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )


@dataclass
class HostContext:
    config: Config
    repo: JobRepository
    launch: Callable[[Config], None] = field(default=launch_runner)


def _payload_keys(payload: dict[str, Any], allowed: set[str], required: set[str] = frozenset()) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise RequestError("invalid_request", f"Unknown payload field(s): {', '.join(sorted(unknown))}")
    missing = required - set(payload)
    if missing:
        raise RequestError("invalid_request", f"Missing payload field(s): {', '.join(sorted(missing))}")


def _job_id(payload: dict[str, Any]) -> int:
    value = payload.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RequestError("invalid_request", "id must be a positive integer", "id")
    return value


def _flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise RequestError("invalid_request", f"{key} must be true or false", key)
    return value


def handle_ping(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, set())
    models = ctx.config.models_dir
    smart = "dnn" if model_path(models, "yunet") and model_path(models, "yolox") else "basic"
    return {"version": __version__, "outputDir": str(ctx.config.output_dir), "smartCrop": smart}


def handle_add(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, {"clip", "allowDuplicate"}, {"clip"})
    spec = validate_clip(payload["clip"])
    job = ctx.repo.add(spec, allow_duplicate=_flag(payload, "allowDuplicate"))
    log.info("Queued job %s from extension", job.id, extra={"job_id": job.id})
    return {"job": job.to_dict()}


def handle_list(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, {"limit"})
    limit = payload.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise RequestError("invalid_request", f"limit must be between 1 and {MAX_LIST_LIMIT}", "limit")
    jobs = ctx.repo.list(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs], "counts": ctx.repo.counts()}


def handle_get(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, {"id"}, {"id"})
    return {"job": ctx.repo.get(_job_id(payload)).to_dict()}


def handle_update(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, {"id", "clip", "allowDuplicate"}, {"id", "clip"})
    spec = validate_clip(payload["clip"])
    job = ctx.repo.update_spec(_job_id(payload), spec, allow_duplicate=_flag(payload, "allowDuplicate"))
    return {"job": job.to_dict()}


def handle_remove(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, {"id"}, {"id"})
    job = ctx.repo.remove(_job_id(payload))
    return {"removed": job.id}


def handle_retry(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, {"id"}, {"id"})
    job_id = _job_id(payload)
    job = ctx.repo.get(job_id)
    if not ctx.repo.retry_failed(ctx.config.max_attempts, [job_id], force=True):
        raise RequestError("invalid_state", f"Job #{job_id} is {job.status.value}, not failed")
    return {"job": ctx.repo.get(job_id).to_dict()}


def handle_clear(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, set())
    removed = ctx.repo.clear()
    log.info("Cleared %d job(s) from extension", len(removed))
    return {"removed": len(removed)}


def handle_start_run(ctx: HostContext, payload: dict[str, Any]) -> dict[str, Any]:
    _payload_keys(payload, set())
    from ytclip.runner import lock_is_held  # deferred: pulls in the media pipeline

    if lock_is_held(ctx.config.data_dir / "run.lock"):
        return {"started": False, "alreadyRunning": True}
    if not ctx.repo.pending_ids():
        raise RequestError("invalid_state", "There are no pending clips to download")
    ctx.launch(ctx.config)
    log.info("Started `ytclip run` from extension")
    return {"started": True, "alreadyRunning": False}


HANDLERS: dict[str, Callable[[HostContext, dict[str, Any]], dict[str, Any]]] = {
    "ping": handle_ping,
    "add_job": handle_add,
    "list_jobs": handle_list,
    "get_job": handle_get,
    "update_job": handle_update,
    "remove_job": handle_remove,
    "retry_job": handle_retry,
    "clear_jobs": handle_clear,
    "start_run": handle_start_run,
}


def _error(request_id: Any, code: str, message: str, field: str | None = None,
           extra: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if field:
        error["field"] = field
    error.update(extra or {})
    return {"id": request_id, "ok": False, "error": error}


def dispatch(ctx: HostContext, request: dict[str, Any]) -> dict[str, Any]:
    raw_id = request.get("id")
    request_id = raw_id if isinstance(raw_id, (str, int)) and len(str(raw_id)) <= 64 else None
    try:
        if set(request) - REQUEST_KEYS:
            raise RequestError("invalid_request", "Unknown top-level field(s)")
        kind = request.get("type")
        handler = HANDLERS.get(kind) if isinstance(kind, str) else None
        if handler is None:
            raise RequestError("unknown_type", f"Unsupported request type: {str(kind)[:40]!r}")
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            raise RequestError("invalid_request", "payload must be an object")
        return {"id": request_id, "ok": True, "data": handler(ctx, payload)}
    except RequestError as exc:
        return _error(request_id, exc.code, str(exc), exc.field, exc.extra)
    except ValidationError as exc:
        return _error(request_id, "validation", str(exc), exc.field)
    except DuplicateJobError as exc:
        return _error(request_id, "duplicate", str(exc), extra={"existingJob": exc.existing.to_dict()})
    except JobNotFoundError as exc:
        return _error(request_id, "not_found", str(exc))
    except JobStateError as exc:
        return _error(request_id, "invalid_state", str(exc))
    except Exception:  # noqa: BLE001 - never crash the host on one bad request
        log.exception("Native request failed")
        return _error(request_id, "internal", "Internal error in ytclip host; see ytclip logs.")
