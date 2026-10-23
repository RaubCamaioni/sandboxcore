import importlib.util
import os
import struct
import textwrap

from containerwebcore.python.tracer import LineTracer
from containerwebcore.trace_writer import TraceWriter


def run_tool(tmp_path, code, fd):
    tool = tmp_path / "t.py"
    tool.write_text(textwrap.dedent(code))
    spec = importlib.util.spec_from_file_location("t", tool)
    mod = importlib.util.module_from_spec(spec)
    tracer = LineTracer(tool, TraceWriter(fd))
    tracer.start()
    try:
        spec.loader.exec_module(mod)
        return mod.t()
    finally:
        tracer.stop()


def read_lines(r):
    data = os.read(r, 1 << 16)
    return [struct.unpack("<I", data[i : i + 4])[0] for i in range(0, len(data) - len(data) % 4, 4)]


def nonblocking_pipe():
    r, w = os.pipe()
    os.set_blocking(w, False)
    return r, w


def test_lines_of_the_tool_file_are_written_in_order(tmp_path):
    r, w = nonblocking_pipe()
    result = run_tool(
        tmp_path,
        """
        import json
        def t():
            x = 1
            for i in range(2):
                x += i
            return json.dumps(x)
        """,
        w,
    )
    assert result == "2"
    lines = read_lines(r)
    body = [n for n in lines if n >= 4]
    assert body[:3] == [4, 5, 6]
    assert body.count(5) >= 2  # the loop header is reached on every iteration
    assert body[-1] == 7


def test_library_frames_are_not_traced(tmp_path):
    r, w = nonblocking_pipe()
    run_tool(tmp_path, "import json\ndef t():\n    return json.dumps([1, 2])\n", w)
    lines = read_lines(r)
    assert lines and max(lines) <= 3  # json's own lines (hundreds) never appear


def test_full_pipe_drops_without_raising(tmp_path):
    r, w = nonblocking_pipe()
    # > 64 KB of records: the pipe fills and later writes must be dropped silently
    assert run_tool(tmp_path, "def t():\n    for i in range(100000):\n        pass\n    return 1\n", w) == 1

