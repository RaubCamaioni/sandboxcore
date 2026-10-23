"""Sandbox: stage a job, run it, and return only files the core has checked.

`UnsandboxedRunner` (sandboxcore.testing) stands in for podman: it runs the real Python runtime in a
subprocess and classifies the run with PodmanRunner.classify, so staging, the runtime and vetting are real.
"""

import asyncio

import pytest

from sandboxcore.host import InputFile, Job, JobError, Outcome, OutputFile, Sandbox, SandboxConfig
from sandboxcore.host.podman import SandboxUnavailable
from sandboxcore.signature import SignatureError
from sandboxcore.source import Source, SourceError
from sandboxcore.testing import UnsandboxedRunner
from tests import IMAGE


@pytest.fixture
def config(tmp_path):
    return SandboxConfig.from_environment({"SANDBOX_MAX_INPUT_MB": "1"}, image=IMAGE, run_dir=str(tmp_path / "runs"))


@pytest.fixture
async def sandbox(config):
    s = Sandbox(config, runner=UnsandboxedRunner(config))
    yield s
    await s.stop()


def job(code, **kw):
    return Job(Source("python", "t", code), **kw)


async def test_a_job_runs_with_its_arguments(sandbox):
    r = await sandbox.run(job("def t(a: int, b: str) -> str:\n    return b * a\n", arguments={"a": 2, "b": "x"}))
    assert r.outcome is Outcome.OK and r.value == "xx" and r.files == []


async def test_an_uploaded_file_is_staged_under_a_clean_name(sandbox):
    code = "from pathlib import Path\ndef t(data: Path) -> list:\n    return [data.name, data.read_text()]\n"
    r = await sandbox.run(job(code, files={"data": InputFile("../../etc/evil name.txt", b"hello")}))
    assert r.value == ["evil name.txt", "hello"]


async def test_a_missing_file_argument_is_none(sandbox):
    r = await sandbox.run(job("def t(data=None):\n    return data is None\n", files={"data": None}))
    assert r.value is True


async def test_a_returned_file_becomes_a_checked_output(sandbox):
    code = (
        "from pathlib import Path\n"
        "def t():\n"
        "    Path('out').mkdir()\n"
        "    Path('out/a.txt').write_text('A')\n"
        "    return {'file': Path('out/a.txt').absolute(), 'n': 1}\n"
    )
    r = await sandbox.run(job(code))
    assert r.outcome is Outcome.OK and r.value["n"] == 1
    out = r.value["file"]
    assert isinstance(out, OutputFile) and out.name == "out/a.txt" and out.size == 1 and r.files == [out]
    assert out.read(10) == b"A" and sandbox.output(r.run, "out/a.txt") == out


HOSTILE_RETURNS = {
    "a host file": "return Path('/etc/passwd')",
    "a symlink to a host file": "Path('l').symlink_to('/etc/passwd'); return Path('l').absolute()",
    "a path through a symlinked directory": "Path('d').symlink_to('/etc'); return Path('d/passwd').absolute()",
    "a directory": "Path('d').mkdir(); return Path('d').absolute()",
    "args.json": "return Path('args.json').absolute()",
    "its own code": "return Path('t.py').absolute()",
    "a path climbing out": "return Path(str(Path.cwd()) + '/../x')",
    "a relative path": "return Path('x')",
    "a FIFO": "import os; os.mkfifo('f'); return Path('f').absolute()",
}


@pytest.mark.parametrize("returns", HOSTILE_RETURNS.values(), ids=HOSTILE_RETURNS.keys())
async def test_a_path_that_is_not_a_file_the_tool_made_is_refused(sandbox, returns):
    r = await sandbox.run(job(f"from pathlib import Path\ndef t():\n    {returns}\n"))
    assert r.outcome is Outcome.CRASHED and r.message == "Tool returned a path that is not a file it made"


async def test_output_only_serves_files_of_live_runs(sandbox, tmp_path):
    code = "from pathlib import Path\ndef t():\n    Path('o.txt').write_text('o')\n    return 1\n"
    r = await sandbox.run(job(code))
    assert sandbox.output(r.run, "o.txt") is not None  # a file the tool made, even if it did not return it
    (tmp_path / "secret").write_text("x")
    for name in ["../secret", "/etc/passwd", "args.json", "result.json", "t.py", "", "."]:
        assert sandbox.output(r.run, name) is None, name
    assert sandbox.output("x" * 32, "o.txt") is None  # not a run this sandbox made
    assert sandbox.output("../" + r.run[3:], "o.txt") is None


@pytest.mark.parametrize(
    "bad",
    [
        {"arguments": {"../x": 1}},
        {"arguments": {"a": object()}},
        {"arguments": {"a": float("nan")}},
        {"arguments": {"a": [1, 2]}},
        {"arguments": {"a": 1}, "files": {"a": None}},
        {"files": {"a": InputFile("x", b"x" * (1024 * 1024 + 1))}},
    ],
)
async def test_a_bad_job_is_refused_before_anything_runs(sandbox, config, bad):
    with pytest.raises(JobError):
        await sandbox.run(job("def t(a=None):\n    return 1\n", **bad))
    assert sandbox.runner.calls == []
    assert not config.run_dir.exists() or not any(config.run_dir.iterdir())  # nothing left behind


async def test_a_hand_built_source_is_checked(sandbox):
    with pytest.raises(SourceError):
        await sandbox.run(Job(Source("python", "../t", "x = 1\n")))


async def test_a_failed_run_still_has_its_id_and_message(sandbox):
    r = await sandbox.run(job("def t():\n    import sys; sys.exit(3)\n"))
    assert r.outcome is Outcome.CRASHED and r.message == "Tool failed to run" and len(r.run) == 32


async def test_runs_are_removed_after_keep_seconds_and_on_stop(config):
    code = "from pathlib import Path\ndef t():\n    Path('o.txt').write_text('o')\n"
    s = Sandbox(config, runner=UnsandboxedRunner(config), keep_seconds=0)
    r = await s.run(job(code))
    await asyncio.sleep(0.05)
    assert s.output(r.run, "o.txt") is None and not (config.run_dir / r.run).exists()
    s.keep_seconds = 3600
    r = await s.run(job("def t():\n    return 1\n"))
    assert (config.run_dir / r.run).exists()
    await s.stop()
    assert not (config.run_dir / r.run).exists()


async def test_an_unavailable_sandbox_refuses_before_staging(config):
    runner = UnsandboxedRunner(config)
    runner.available, runner.unavailable_reason = False, "self-test failed"
    s = Sandbox(config, runner=runner)
    with pytest.raises(SandboxUnavailable):
        await s.run(job("def t():\n    return 1\n"))
    assert not config.run_dir.exists() or not any(config.run_dir.iterdir())


async def test_inspect_reads_the_signature_in_the_sandbox(sandbox, config, tmp_path):
    marker = tmp_path / "ran"
    code = f"from typing import Literal\nopen({str(marker)!r}, 'w')\ndef t(a: int, m: Literal['x', 'y'] = 'x'):\n    pass\n"
    signature = await sandbox.inspect(Source("python", "t", code))
    assert [(p.name, p.kind, p.choices) for p in signature.parameters] == [("a", "int", ()), ("m", "choice", ("x", "y"))]
    assert not marker.exists() and not any(config.run_dir.iterdir())  # nothing ran, nothing kept


async def test_inspect_refuses_what_the_allow_list_refuses(sandbox):
    with pytest.raises(SignatureError, match="Annotation Error"):
        await sandbox.inspect(Source("python", "t", "def t(a: __import__('os').getcwd()):\n    pass\n"))
    with pytest.raises(SignatureError, match="Syntax Error"):
        await sandbox.inspect(Source("python", "t", "def t(:\n"))


async def test_inspect_then_bind_then_run(sandbox):
    source = Source("python", "t", "def t(a: int, b: float = 0.5) -> float:\n    return a + b\n")
    signature = await sandbox.inspect(source)
    arguments, files = signature.bind({"a": "2"})
    r = await sandbox.run(Job(source, arguments, files))
    assert r.value == 2.5
