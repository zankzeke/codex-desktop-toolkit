"""
sse_handler.py — streaming SSE line buffer and per-event ID rewriting.

The upstream SSE stream looks like:

    event: response.output_item.added\r\n
    data: {"type":"response.output_item.added", ...}\r\n
    \r\n
    data: [DONE]\r\n
    \r\n

Problems we must solve
-----------------------
1.  Raw bytes arrive in arbitrary TCP chunks; a single SSE event may be
    split across multiple chunks, or multiple events may arrive in one chunk.
2.  We must NOT buffer the entire stream before forwarding — we must forward
    each event as soon as it is complete.
3.  We must rewrite ID fields inside ``data: {...}`` lines.
4.  We must pass ``data: [DONE]`` verbatim.
5.  Lines that are not ``data:`` (e.g. ``event:``, ``id:``, ``:``) are
    forwarded verbatim.

Strategy
---------
We maintain a line buffer.  Incoming bytes are appended.  We scan for
complete lines (ending with \\n).  Complete lines are classified and
forwarded immediately.  Incomplete tail bytes stay in the buffer.

Event boundaries (blank lines) are forwarded as-is — this preserves the
SSE framing for the browser/client.
"""

from __future__ import annotations

import json
import logging
from typing import Generator

from id_rewriter import _walk_and_rewrite, rewrite_single_id

logger = logging.getLogger(__name__)


class SSELineBuffer:
    """Stateful buffer that splits raw bytes into complete SSE lines."""

    def __init__(self) -> None:
        self._buf: bytes = b""

    def feed(self, chunk: bytes) -> Generator[bytes, None, None]:
        """
        Feed raw bytes; yield each complete line (including its \\n terminator).
        Incomplete lines remain buffered.
        """
        self._buf += chunk
        while True:
            idx = self._buf.find(b"\n")
            if idx == -1:
                break
            line = self._buf[: idx + 1]  # includes the \n
            self._buf = self._buf[idx + 1 :]
            yield line

    def flush(self) -> bytes:
        """Return and clear any remaining buffered bytes (call at stream end)."""
        tail = self._buf
        self._buf = b""
        return tail


def rewrite_sse_line(
    line: bytes,
    id_map: dict[str, str],
) -> tuple[bytes, int]:
    """
    Rewrite ID fields in a single SSE ``data:`` line.

    Returns (rewritten_line_bytes, number_of_fixes).
    Passes non-data lines verbatim.
    """
    stripped = line.rstrip(b"\r\n")

    if not stripped.startswith(b"data:"):
        return line, 0

    payload = stripped[5:]  # everything after "data:"
    if payload.lstrip() == b"[DONE]":
        return line, 0

    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        # Not valid JSON — forward verbatim rather than corrupt the stream.
        logger.debug("sse: non-JSON data line, passing through")
        return line, 0

    if not isinstance(obj, dict):
        return line, 0

    before_len = len(id_map)
    _walk_and_rewrite(obj, id_map)
    fixes = len(id_map) - before_len

    # Reconstruct the line with the same line-ending style.
    ending = line[len(stripped):]  # \n or \r\n
    new_payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    new_line = b"data:" + new_payload.encode() + ending

    return new_line, fixes
