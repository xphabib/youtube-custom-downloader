"""Native Messaging framing: a 4-byte native-endian length followed by UTF-8 JSON."""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO

MAX_INCOMING_BYTES = 64 * 1024  # requests are small; refuse anything bigger
MAX_OUTGOING_BYTES = 1024 * 1024  # Chrome's limit for host -> extension messages
_HEADER = struct.Struct("=I")


class ProtocolError(ValueError):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Return the next message, or None at a clean end of stream."""
    header = _read_exact(stream, _HEADER.size)
    if not header:
        return None
    if len(header) < _HEADER.size:
        raise ProtocolError("Truncated message header")
    (length,) = _HEADER.unpack(header)
    if length == 0 or length > MAX_INCOMING_BYTES:
        raise ProtocolError(f"Message length {length} outside 1..{MAX_INCOMING_BYTES} bytes")
    body = _read_exact(stream, length)
    if len(body) != length:
        raise ProtocolError("Truncated message body")
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Message is not valid UTF-8 JSON: {exc}") from None
    if not isinstance(message, dict):
        raise ProtocolError("Message must be a JSON object")
    return message


def encode_message(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_OUTGOING_BYTES:
        raise ProtocolError("Response exceeds Chrome's 1 MB native message limit")
    return _HEADER.pack(len(body)) + body


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    stream.write(encode_message(message))
    stream.flush()
