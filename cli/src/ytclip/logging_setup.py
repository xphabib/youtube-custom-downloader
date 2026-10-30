"""Structured JSON-lines logging to a rotating file, plus optional console output."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(log_dir: Path, debug: bool = False, console: bool = True,
                  filename: str = "ytclip.log") -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / filename
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3)
    file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    if console:
        from rich.logging import RichHandler

        console_handler = RichHandler(show_time=False, show_path=debug, markup=False)
        console_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
        root.addHandler(console_handler)
    return log_file
