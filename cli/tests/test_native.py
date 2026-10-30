"""Native messaging framing, request validation, dispatch, and the real host process."""

from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import URL, clip
from ytclip.config import Config
from ytclip.native import DEFAULT_EXTENSION_ID, HOST_NAME
from ytclip.native import install as native_install
from ytclip.native.handlers import HostContext, dispatch
from ytclip.native.host import serve
from ytclip.native.protocol import (
    MAX_INCOMING_BYTES, ProtocolError, encode_message, read_message, write_message,
)
from ytclip.repository import JobRepository


def frame(obj: object) -> bytes:
    body = json.dumps(obj).encode()
    return struct.pack("=I", len(body)) + body


@pytest.fixture
def ctx(config: Config, repo: JobRepository) -> HostContext:
    return HostContext(config, repo)


# ---- framing -----------------------------------------------------------------------------

def test_roundtrip_and_eof() -> None:
    buffer = io.BytesIO()
    write_message(buffer, {"type": "ping", "text": "héllo"})
    buffer.seek(0)
    assert read_message(buffer) == {"type": "ping", "text": "héllo"}
    assert read_message(buffer) is None


@pytest.mark.parametrize("raw, match", [
    (struct.pack("=I", MAX_INCOMING_BYTES + 1) + b"{}", "outside"),
    (struct.pack("=I", 0), "outside"),
    (b"\x05\x00", "header"),
    (struct.pack("=I", 10) + b"{}", "body"),
    (frame([1, 2]), "object"),
    (struct.pack("=I", 3) + b"\xff\xfe{", "UTF-8"),
    (struct.pack("=I", 5) + b"{bad}", "JSON"),
])
def test_invalid_frames(raw: bytes, match: str) -> None:
    with pytest.raises(ProtocolError, match=match):
        read_message(io.BytesIO(raw))


def test_outgoing_size_limit() -> None:
    with pytest.raises(ProtocolError):
        encode_message({"blob": "x" * (1024 * 1024 + 1)})


# ---- dispatch ----------------------------------------------------------------------------

def test_ping(ctx: HostContext) -> None:
    response = dispatch(ctx, {"id": "r1", "type": "ping"})
    assert response["ok"] and response["id"] == "r1"
    assert response["data"]["smartCrop"] in ("dnn", "basic")


def test_add_list_get_update_remove(ctx: HostContext) -> None:
    added = dispatch(ctx, {"id": 1, "type": "add_job", "payload": {"clip": clip(title="Vid")}})
    assert added["ok"], added
    job = added["data"]["job"]
    assert job["status"] == "pending" and job["url"] == URL and job["startMs"] == 1000

    listed = dispatch(ctx, {"type": "list_jobs", "payload": {"limit": 10}})
    assert [j["id"] for j in listed["data"]["jobs"]] == [job["id"]] and listed["data"]["counts"]["pending"] == 1

    assert dispatch(ctx, {"type": "get_job", "payload": {"id": job["id"]}})["data"]["job"]["title"] == "Vid"

    updated = dispatch(ctx, {"type": "update_job", "payload": {"id": job["id"], "clip": clip(end="00:00:09")}})
    assert updated["data"]["job"]["endMs"] == 9000

    assert dispatch(ctx, {"type": "remove_job", "payload": {"id": job["id"]}})["data"] == {"removed": job["id"]}
    assert dispatch(ctx, {"type": "get_job", "payload": {"id": job["id"]}})["error"]["code"] == "not_found"


def test_duplicate_response_includes_existing_job(ctx: HostContext) -> None:
    dispatch(ctx, {"type": "add_job", "payload": {"clip": clip()}})
    dup = dispatch(ctx, {"type": "add_job", "payload": {"clip": clip()}})
    assert dup["error"]["code"] == "duplicate" and dup["error"]["existingJob"]["id"] == 1
    forced = dispatch(ctx, {"type": "add_job", "payload": {"clip": clip(), "allowDuplicate": True}})
    assert forced["ok"]


def test_retry_only_failed(ctx: HostContext, repo: JobRepository) -> None:
    dispatch(ctx, {"type": "add_job", "payload": {"clip": clip()}})
    assert dispatch(ctx, {"type": "retry_job", "payload": {"id": 1}})["error"]["code"] == "invalid_state"
    repo.claim(1, 1)
    repo.fail(1, "network")
    assert dispatch(ctx, {"type": "retry_job", "payload": {"id": 1}})["data"]["job"]["status"] == "pending"


def test_clear_keeps_active_jobs(ctx: HostContext, repo: JobRepository) -> None:
    for end in ("00:00:05", "00:00:06", "00:00:07"):
        dispatch(ctx, {"type": "add_job", "payload": {"clip": clip(end=end)}})
    repo.claim(2, 1)
    assert dispatch(ctx, {"type": "clear_jobs", "payload": {}})["data"] == {"removed": 2}
    assert [j.id for j in repo.list()] == [2]


def test_start_run_launches_runner_once(ctx: HostContext, config: Config) -> None:
    launched: list[Config] = []
    ctx.launch = launched.append
    assert dispatch(ctx, {"type": "start_run", "payload": {}})["error"]["code"] == "invalid_state"
    dispatch(ctx, {"type": "add_job", "payload": {"clip": clip()}})
    assert dispatch(ctx, {"type": "start_run", "payload": {}})["data"] == {"started": True, "alreadyRunning": False}
    assert launched == [config]

    from ytclip.runner import run_lock
    with run_lock(config.data_dir / "run.lock"):
        busy = dispatch(ctx, {"type": "start_run", "payload": {}})
    assert busy["data"] == {"started": False, "alreadyRunning": True} and len(launched) == 1


@pytest.mark.parametrize("request_, code", [
    ({"type": "exec", "payload": {"cmd": "ls"}}, "unknown_type"),
    ({"type": None}, "unknown_type"),
    ({"type": "ping", "extra": 1}, "invalid_request"),
    ({"type": "ping", "payload": []}, "invalid_request"),
    ({"type": "ping", "payload": {"x": 1}}, "invalid_request"),
    ({"type": "add_job", "payload": {}}, "invalid_request"),
    ({"type": "add_job", "payload": {"clip": clip(end="00:00:00")}}, "validation"),
    ({"type": "add_job", "payload": {"clip": {**clip(), "path": "/etc"}}}, "validation"),
    ({"type": "add_job", "payload": {"clip": clip(), "allowDuplicate": "yes"}}, "invalid_request"),
    ({"type": "list_jobs", "payload": {"limit": 10_000}}, "invalid_request"),
    ({"type": "get_job", "payload": {"id": "1; DROP TABLE jobs"}}, "invalid_request"),
    ({"type": "get_job", "payload": {"id": True}}, "invalid_request"),
    ({"type": "remove_job", "payload": {"id": 5}}, "not_found"),
])
def test_invalid_requests(ctx: HostContext, request_: dict, code: str) -> None:
    response = dispatch(ctx, request_)
    assert response["ok"] is False and response["error"]["code"] == code


def test_validation_errors_name_the_field(ctx: HostContext) -> None:
    response = dispatch(ctx, {"type": "add_job", "payload": {"clip": clip(end="00:00:00")}})
    assert response["error"]["field"] == "end"


def test_overlong_request_id_is_not_echoed(ctx: HostContext) -> None:
    assert dispatch(ctx, {"id": "x" * 100, "type": "ping"})["id"] is None


def test_serve_loop_handles_multiple_messages_then_stops_on_garbage(ctx: HostContext) -> None:
    stdin = io.BytesIO(frame({"id": "a", "type": "ping"}) + frame({"id": "b", "type": "list_jobs"}) + b"\xff\xff\xff\x7f")
    stdout = io.BytesIO()
    assert serve(ctx, stdin, stdout) == 1
    stdout.seek(0)
    responses = [read_message(stdout) for _ in range(3)]
    assert [r["id"] for r in responses] == ["a", "b", None]
    assert responses[2]["error"]["code"] == "protocol"


# ---- the real host process ----------------------------------------------------------------

def test_host_process_end_to_end(isolated_home: Path) -> None:
    """Spawn the host exactly as Chrome would and talk to it over stdin/stdout."""
    payload = frame({"id": "1", "type": "add_job", "payload": {"clip": clip(title="From Chrome")}})
    payload += frame({"id": "2", "type": "list_jobs"})
    result = subprocess.run(
        [sys.executable, "-m", "ytclip.native.host", f"chrome-extension://{DEFAULT_EXTENSION_ID}/"],
        input=payload, capture_output=True, timeout=60, env=os.environ.copy(), check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    out = io.BytesIO(result.stdout)
    added, listed = read_message(out), read_message(out)
    assert added["ok"] and listed["data"]["jobs"][0]["title"] == "From Chrome"
    assert read_message(out) is None  # nothing but protocol frames on stdout
    db_path = isolated_home / "data" / "ytclip" / "jobs.db"
    assert db_path.exists()  # persisted in SQLite, not just in the browser


# ---- registration -------------------------------------------------------------------------

def test_install_writes_manifest_and_launcher(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".config" / "chromium").mkdir(parents=True)
    locations = native_install.install(tmp_path / "bin", ["abcdefghijklmnopabcdefghijklmnop"], home=home)
    assert {loc.browser for loc in locations} == {"Google Chrome", "Chromium"}
    manifest = json.loads(locations[0].path.read_text())
    assert manifest["name"] == HOST_NAME and manifest["type"] == "stdio"
    assert manifest["allowed_origins"] == [
        f"chrome-extension://{DEFAULT_EXTENSION_ID}/", "chrome-extension://abcdefghijklmnopabcdefghijklmnop/",
    ]
    launcher = Path(manifest["path"])
    assert os.access(launcher, os.X_OK) and "ytclip.native.host" in launcher.read_text()
    assert native_install.installed_manifest(home)[0] == locations[0].path
    assert len(native_install.uninstall(home)) == 2


def test_install_rejects_bad_extension_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        native_install.install(tmp_path / "bin", ["not-an-id"], home=tmp_path)


def test_manifest_extension_id_matches_key() -> None:
    """The ID the host allows must be the one Chrome derives from the manifest key."""
    import base64
    import hashlib

    manifest = Path(__file__).resolve().parents[2] / "extension" / "public" / "manifest.json"
    key = json.loads(manifest.read_text())["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    assert "".join(chr(ord("a") + int(c, 16)) for c in digest) == DEFAULT_EXTENSION_ID
