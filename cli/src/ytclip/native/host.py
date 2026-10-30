"""Entry point Chrome launches for ``chrome.runtime.sendNativeMessage``.

stdout is reserved for protocol frames, so logging goes to a file only.
"""

from __future__ import annotations

import logging
import sys
from typing import BinaryIO

from ytclip.config import ConfigError, load_config
from ytclip.db import Database
from ytclip.logging_setup import setup_logging
from ytclip.native.handlers import HostContext, dispatch
from ytclip.native.protocol import ProtocolError, read_message, write_message
from ytclip.repository import JobRepository

log = logging.getLogger(__name__)


def serve(ctx: HostContext, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Answer requests until EOF. Returns a process exit code."""
    while True:
        try:
            request = read_message(stdin)
        except ProtocolError as exc:
            log.warning("Rejected native message: %s", exc)
            write_message(stdout, {"id": None, "ok": False,
                                   "error": {"code": "protocol", "message": str(exc)}})
            return 1  # framing is no longer trustworthy; stop reading
        if request is None:
            return 0
        write_message(stdout, dispatch(ctx, request))


def main() -> None:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    try:
        config = load_config()
        config.ensure_dirs()
        setup_logging(config.log_dir, console=False, filename="native-host.log")
        log.info("Native host started (caller %s)", sys.argv[1:2])
        db = Database(config.db_path)
    except (ConfigError, OSError, RuntimeError) as exc:
        write_message(stdout, {"id": None, "ok": False,
                               "error": {"code": "host_config", "message": str(exc)}})
        sys.exit(1)
    try:
        sys.exit(serve(HostContext(config, JobRepository(db)), stdin, stdout))
    finally:
        db.close()


if __name__ == "__main__":
    main()
