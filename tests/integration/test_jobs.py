"""The whole path through real podman: accept, inspect in the sandbox, bind, run, vet, serve."""

import pytest

from sandboxcore.host import InputFile, Job, Outcome, Sandbox, accept_source
from tests.integration.test_sandbox import config, pytestmark  # noqa: F401  (fixture, markers)

UPPER = b"""from pathlib import Path


def upper(data: Path, times: int = 1) -> Path:
    out = Path("out.txt")
    out.write_text(data.read_text().upper() * times)
    return out.absolute()
"""


@pytest.fixture
async def sandbox(config):  # noqa: F811
    s = Sandbox(config)
    await s.start()
    assert s.available, s.unavailable_reason
    yield s
    await s.stop()


async def test_inspect_bind_run_and_serve(sandbox):
    source = accept_source(UPPER, "upper.py")
    signature = await sandbox.inspect(source)
    assert [(p.name, p.kind) for p in signature.parameters] == [("data", "file"), ("times", "int")]
    arguments, files = signature.bind({"data": InputFile("in.txt", b"abc"), "times": "2"})
    r = await sandbox.run(Job(source, arguments, files))
    assert r.outcome is Outcome.OK, r
    assert r.value.read(100) == b"ABCABC" and sandbox.output(r.run, "out.txt") == r.value


async def test_a_tool_returning_a_host_path_is_refused(sandbox):
    source = accept_source(b"from pathlib import Path\n\ndef leak() -> Path:\n    return Path('/etc/passwd')\n", "leak.py")
    r = await sandbox.run(Job(source))
    assert r.outcome is Outcome.CRASHED and r.message == "Tool returned a path that is not a file it made"
