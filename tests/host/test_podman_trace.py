import os
import stat
import struct

from containerwebcore.host.podman import Outcome

from tests.host.test_podman_run import FakePodman, cfg, ready_runner

OK = {"inspect": (0, "0 false", "")}


def tool_and_result(tmp_path):
    (tmp_path / "t.py").write_text("def t():\n    return 1\n")
    (tmp_path / "result.json").write_text("1")
    return tmp_path / "t.py"


async def test_run_creates_fifo_and_passes_trace(tmp_path):
    tool = tool_and_result(tmp_path)
    fake = FakePodman(OK)
    await ready_runner(cfg(), fake).run(tool, tmp_path, on_line=lambda n: None)
    assert f"PYWEBTOOLS_TRACE={tmp_path / 'trace'}" in fake.calls[0]
    assert stat.S_ISFIFO(os.stat(tmp_path / "trace").st_mode)


async def test_run_forwards_lines_written_to_the_fifo(tmp_path):
    tool = tool_and_result(tmp_path)
    seen = []

    class WritingPodman(FakePodman):
        async def __call__(self, *argv, timeout=None):
            if argv[1] == "run":  # the sandbox writes while the container runs
                fd = os.open(tmp_path / "trace", os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, struct.pack("<I", 2))
                os.close(fd)
            return await super().__call__(*argv, timeout=timeout)

    r = await ready_runner(cfg(), WritingPodman(OK)).run(tool, tmp_path, on_line=seen.append)
    assert r.outcome is Outcome.OK and seen == [2]


async def test_run_survives_missing_fifo(tmp_path):
    tool = tool_and_result(tmp_path)

    class DeletingPodman(FakePodman):
        async def __call__(self, *argv, timeout=None):
            if argv[1] == "run":  # the tool owns its run directory and may remove the FIFO
                os.unlink(tmp_path / "trace")
            return await super().__call__(*argv, timeout=timeout)

    r = await ready_runner(cfg(), DeletingPodman(OK)).run(tool, tmp_path, on_line=lambda n: None)
    assert r.outcome is Outcome.OK


async def test_run_without_on_line_has_no_trace(tmp_path):
    tool = tool_and_result(tmp_path)
    fake = FakePodman(OK)
    await ready_runner(cfg(), fake).run(tool, tmp_path)
    assert not any(a.startswith("PYWEBTOOLS_TRACE=") for a in fake.calls[0]) and not (tmp_path / "trace").exists()


async def test_run_proceeds_when_fifo_cannot_be_created(tmp_path):
    tool = tool_and_result(tmp_path)
    (tmp_path / "trace").mkdir()  # mkfifo fails: EEXIST
    fake = FakePodman(OK)
    r = await ready_runner(cfg(), fake).run(tool, tmp_path, on_line=lambda n: None)
    assert r.outcome is Outcome.OK and not any(a.startswith("PYWEBTOOLS_TRACE=") for a in fake.calls[0])
