"""Integration tests for the sandbox runner.

They need the inner podman and the sandbox image, so they run inside the app container:

    SANDBOX_INTEGRATION=1 python -m pytest -m integration tests/test_integration_sandbox.py -v
"""

import asyncio
import os
import shutil
import textwrap
import time
from pathlib import Path

import pytest

from containerwebcore import serializer
from containerwebcore.host.podman import Outcome, PodmanRunner
from containerwebcore.host.config import SandboxConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SANDBOX_INTEGRATION") != "1", reason="needs the inner podman and the sandbox image"),
]


@pytest.fixture(scope="module")
def config():
    return SandboxConfig.from_environment(os.environ)


@pytest.fixture
async def runner(config):
    r = PodmanRunner(config)
    await r.start()
    assert r.available, r.unavailable_reason
    yield r
    await r.stop()


@pytest.fixture
def make_run(config):
    """Create a run directory the way routes/tools.py does; clean up afterwards."""
    created = []

    def _make(name, code, args=None):
        run_dir = config.run_dir / f"itest-{name}-{time.monotonic_ns()}"
        run_dir.mkdir(parents=True)
        run_dir.chmod(0o777)
        tool = run_dir / f"{name}.py"
        tool.write_text(textwrap.dedent(code))
        with open(run_dir / "args.json", "w") as f:
            serializer.dump(args or {}, f)
        created.append(run_dir)
        return tool, run_dir

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


LOWER_CASE_TEXT = """
    from pathlib import Path

    def lower_case_text(input: Path) -> Path:
        output = Path("output.txt")
        output.write_text(input.read_text().lower())
        return output
"""


async def test_happy_path_lower_case_text(runner, make_run):
    tool, run_dir = make_run("lower_case_text", LOWER_CASE_TEXT)
    (run_dir / "in.txt").write_text("HeLLo")
    with open(run_dir / "args.json", "w") as f:
        serializer.dump({"input": run_dir / "in.txt"}, f)
    r = await runner.run(tool, run_dir)
    assert r.outcome is Outcome.OK, r
    assert isinstance(r.results, Path) and r.results.read_text() == "hello"


async def test_run_dir_owned_by_app_user_after_run(runner, make_run):
    tool, run_dir = make_run("whoami", "import os\ndef whoami() -> int:\n    return os.getuid()\n")
    await runner.run(tool, run_dir)
    assert (run_dir / "result.json").stat().st_uid == os.getuid()


async def test_sandbox_runs_as_unprivileged_user(runner, make_run):
    code = """
        import os
        def whoami() -> list:
            return [os.getuid(), os.path.exists("/run/secrets/pywebtools.env")]
    """
    tool, run_dir = make_run("whoami", code)
    r = await runner.run(tool, run_dir)
    assert r.outcome is Outcome.OK and r.results == [1001, False]


async def test_memory_bomb_reports_memory_limit(runner, make_run, config):
    tool, run_dir = make_run("membomb", "def membomb() -> int:\n    return len(bytearray(1500 * 1024 * 1024))\n")
    r = await runner.run(tool, run_dir)
    assert r.outcome is Outcome.OOM and r.message == f"Tool exceeded the {config.memory_label} memory limit"


async def test_infinite_loop_reports_timeout(runner, make_run, config):
    tool, run_dir = make_run("spin", "def spin() -> int:\n    while True:\n        pass\n")
    started = time.monotonic()
    r = await runner.run(tool, run_dir)
    assert r.outcome is Outcome.TIMEOUT and r.message == f"Tool exceeded the {config.timeout_seconds:g} second limit"
    assert time.monotonic() - started < config.timeout_seconds + 2.5


async def test_fork_bomb_hits_pids_limit(runner, make_run):
    code = """
        import subprocess
        def forks() -> int:
            procs = [subprocess.Popen(["sleep", "5"]) for _ in range(100)]
            return len(procs)
    """
    tool, run_dir = make_run("forks", code)
    r = await runner.run(tool, run_dir)
    assert not (r.outcome is Outcome.OK and r.results == 100), r


async def test_network_is_unreachable(runner, make_run):
    code = """
        import socket
        def net() -> str:
            socket.create_connection(("1.1.1.1", 53), timeout=3)
            return "reached"
    """
    tool, run_dir = make_run("net", code)
    r = await runner.run(tool, run_dir)
    assert not (r.outcome is Outcome.OK and r.results == "reached"), r


async def test_three_concurrent_runs_all_succeed(runner, make_run):
    runs = [make_run(f"quick{i}", f"def quick{i}() -> int:\n    return {i}\n") for i in range(3)]
    results = await asyncio.gather(*(runner.run(tool, run_dir) for tool, run_dir in runs))
    assert [r.outcome for r in results] == [Outcome.OK] * 3
    assert sorted(r.results for r in results) == [0, 1, 2]
