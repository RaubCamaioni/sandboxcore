"""The language-neutral runtime half of the line trace: find the FIFO, write records, never block or raise."""

import os
import struct
import time

from containerwebcore.trace_writer import TraceWriter, trace_path


def host_fifo(tmp_path):
    """A FIFO opened the way the host opens it: the read end plus a write end of its own."""
    path = tmp_path / "trace"
    os.mkfifo(path, 0o666)
    r = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    w = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    return str(path), r, w


def test_trace_path_prefers_the_new_variable():
    assert trace_path({}) is None and trace_path({"CONTAINERWEBCORE_TRACE": ""}) is None
    assert trace_path({"PYWEBTOOLS_TRACE": "/old"}) == "/old"
    assert trace_path({"PYWEBTOOLS_TRACE": "/old", "CONTAINERWEBCORE_TRACE": "/new"}) == "/new"


def test_records_are_little_endian_uint32(tmp_path):
    path, r, w = host_fifo(tmp_path)
    writer = TraceWriter.open(path)
    writer.line(7)
    writer.line(300)
    writer.close()
    assert struct.unpack("<II", os.read(r, 64)) == (7, 300)
    os.close(r)
    os.close(w)


def test_open_fails_softly_without_a_reader_or_a_file(tmp_path):
    os.mkfifo(tmp_path / "trace")
    assert TraceWriter.open(str(tmp_path / "trace")) is None  # ENXIO, never a blocking open
    assert TraceWriter.open(str(tmp_path / "missing")) is None


def test_a_full_pipe_drops_records_without_blocking(tmp_path):
    path, r, w = host_fifo(tmp_path)  # nobody drains it
    writer = TraceWriter.open(path)
    started = time.monotonic()
    for i in range(200_000):  # 800 KB into a 64 KB pipe
        writer.line(i % 50 + 1)
    assert time.monotonic() - started < 5
    writer.close()
    os.close(r)
    os.close(w)


def test_a_closed_pipe_and_out_of_range_lines_are_ignored(tmp_path):
    path, r, w = host_fifo(tmp_path)
    writer = TraceWriter.open(path)
    os.close(r)
    os.close(w)
    writer.line(1)  # EPIPE: dropped (Python ignores SIGPIPE)
    writer.line(2**32)  # not a uint32: dropped
    writer.close()
    writer.close()  # twice is harmless
