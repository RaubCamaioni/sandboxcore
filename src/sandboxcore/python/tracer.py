"""Line events for Python tools: `sys.monitoring` LINE events, written through a `TraceWriter`.

This is the Python-specific half of the line trace (docs/trace-protocol.md). Only code objects whose
filename is the tool file are traced, so library code (numpy, cadquery, ...) is never reported.
"""

import sys
from pathlib import Path

from sandboxcore.trace_writer import TraceWriter

TOOL_ID = sys.monitoring.PROFILER_ID


class LineTracer:
    def __init__(self, tool_file: Path, writer: TraceWriter):
        self.filename = str(tool_file)
        self.writer = writer

    def _on_line(self, code, line):
        if code.co_filename != self.filename:
            return sys.monitoring.DISABLE  # never called again for this code object
        self.writer.line(line)

    def start(self):
        sys.monitoring.use_tool_id(TOOL_ID, "sandbox-trace")
        sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.LINE, self._on_line)
        sys.monitoring.set_events(TOOL_ID, sys.monitoring.events.LINE)

    def stop(self):
        sys.monitoring.set_events(TOOL_ID, 0)
        sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(TOOL_ID)
