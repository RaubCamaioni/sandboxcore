"""Line trace: write the executed line numbers of the tool file as 4-byte records.

Only code objects whose filename is the tool file are traced, so library code
(numpy, cadquery, ...) is never reported. Every record is ``contract.TRACE_RECORD``
written non-blocking; a full pipe or a missing reader drops records silently so the
trace can never slow down or fail the tool.
"""

import os
import sys
from pathlib import Path

from containerwebcore.contract import TRACE_RECORD

TOOL_ID = sys.monitoring.PROFILER_ID


def open_trace(path: str) -> int | None:
    """Open the write end of the trace FIFO without blocking; None when no reader waits."""
    try:
        return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return None


class LineTracer:
    def __init__(self, tool_file: Path, fd: int):
        self.filename = str(tool_file)
        self.fd = fd

    def _on_line(self, code, line):
        if code.co_filename != self.filename:
            return sys.monitoring.DISABLE  # never called again for this code object
        try:
            os.write(self.fd, TRACE_RECORD.pack(line))
        except (BlockingIOError, BrokenPipeError, OSError):
            pass

    def start(self):
        sys.monitoring.use_tool_id(TOOL_ID, "sandbox-trace")
        sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.LINE, self._on_line)
        sys.monitoring.set_events(TOOL_ID, sys.monitoring.events.LINE)

    def stop(self):
        sys.monitoring.set_events(TOOL_ID, 0)
        sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(TOOL_ID)
