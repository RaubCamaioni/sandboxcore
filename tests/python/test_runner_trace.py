import os
import struct
import subprocess
import sys
import threading

RUNNER = [sys.executable, "-m", "sandboxcore.python.runner"]


def write_tool(tmp_path):
    tool = tmp_path / "lower.py"
    tool.write_text("def lower(input: str) -> str:\n    return input.lower()\n")
    (tmp_path / "args.json").write_text('{"input": "HeLLo"}')
    return tool


def test_runner_writes_trace_to_fifo(tmp_path):
    tool = write_tool(tmp_path)
    fifo = tmp_path / "trace"
    os.mkfifo(fifo)
    # like the app: hold the read end open before the sandbox starts; a FIFO with no writer reads
    # as EOF at once, so keep a dummy write end until the sandbox has finished
    rfd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    os.set_blocking(rfd, True)
    wfd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    got = bytearray()

    def reader():
        while chunk := os.read(rfd, 4096):
            got.extend(chunk)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    subprocess.run(RUNNER + ["--file", str(tool), "--workdir", str(tmp_path), "--trace", str(fifo)], check=True, timeout=30)
    os.close(wfd)  # the sandbox's writer is closed too: the reader now sees EOF
    t.join(10)
    os.close(rfd)
    assert (tmp_path / "result.json").read_text() == '"hello"'
    lines = [struct.unpack("<I", got[i : i + 4])[0] for i in range(0, len(got) - len(got) % 4, 4)]
    assert 2 in lines and max(lines) <= 2


def test_runner_without_trace_is_unchanged(tmp_path):
    tool = write_tool(tmp_path)
    subprocess.run(RUNNER + ["--file", str(tool), "--workdir", str(tmp_path)], check=True, timeout=30)
    assert (tmp_path / "result.json").read_text() == '"hello"'
    assert not (tmp_path / "trace").exists()


def test_runner_takes_the_trace_path_from_the_environment(tmp_path):
    tool = write_tool(tmp_path)
    fifo = tmp_path / "trace"
    os.mkfifo(fifo)
    rfd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    wfd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    subprocess.run(
        RUNNER + ["--file", str(tool), "--workdir", str(tmp_path)],
        check=True,
        timeout=30,
        env={**os.environ, "SANDBOXCORE_TRACE": str(fifo)},
    )
    os.close(wfd)
    assert len(os.read(rfd, 4096)) >= 4  # at least one line record arrived
    os.close(rfd)
