"""The runtime's half of the line trace protocol (docs/trace-protocol.md), independent of any language.

A runtime finds the FIFO with `trace_path`, opens it with `TraceWriter.open`, and calls `line(n)` from
whatever tells it a line of the tool runs. This module is the Python template; `runtimes/go/trace` is
the same thing in Go.
"""

import os
import struct
from typing import Mapping, Optional

from containerwebcore.contract import TRACE_ENV, TRACE_ENV_LEGACY, TRACE_RECORD


def trace_path(env: Mapping[str, str] = os.environ) -> Optional[str]:
    """The trace FIFO the host offers, or None when the run is not traced."""
    return env.get(TRACE_ENV) or env.get(TRACE_ENV_LEGACY) or None


class TraceWriter:
    """Writes line records without ever blocking or raising: a full or closed pipe drops them."""

    def __init__(self, fd: int):
        self.fd = fd

    @classmethod
    def open(cls, path: str) -> Optional["TraceWriter"]:
        """The write end of the trace FIFO, opened without blocking; None when it can't be opened
        (missing, no reader waiting, ...), in which case the tool runs untraced."""
        try:
            return cls(os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC))
        except OSError:
            return None

    def line(self, line: int) -> None:
        try:
            os.write(self.fd, TRACE_RECORD.pack(line))  # 4 bytes < PIPE_BUF: one atomic record
        except (OSError, struct.error):  # full (EAGAIN), closed (EPIPE), a line beyond uint32
            pass

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
