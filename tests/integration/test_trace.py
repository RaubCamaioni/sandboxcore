"""Integration tests for the execution trace: real sandbox, real FIFO.

    SANDBOX_INTEGRATION=1 python -m pytest -m integration tests/test_integration_trace.py -v
"""

import os
import time

import pytest

from sandboxcore.host.podman import Outcome, PodmanRunner
from sandboxcore.host.config import SandboxConfig

from tests.integration.test_sandbox import SLACK, config, make_run, runner  # noqa: F401  (fixtures)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SANDBOX_INTEGRATION") != "1", reason="needs the inner podman and the sandbox image"),
]

# each iteration sits on the sleep line long enough to span several 1/60 s ticks
LOOP_TOOL = """
    import time

    def loop_tool() -> int:
        total = 0
        for i in range(3):
            total += i
            time.sleep(0.05)
        return total
"""

# ten lines of a hot loop: floods the trace for the whole run
FLOOD_TOOL = """
    def flood() -> int:
        n = 0
        while True:
            n += 1
            n -= 1
            n += 2
            n -= 2
            n += 3
            n -= 3
            n += 0
"""


async def test_loop_lines_arrive_in_order_and_run_is_ok(runner, make_run):
    tool, run_dir = make_run("loop_tool", LOOP_TOOL)
    seen = []
    r = await runner.run(tool, run_dir, on_line=seen.append)
    assert r.outcome is Outcome.OK and r.results == 3, r
    assert seen, "no trace events reached the app"
    source = tool.read_text().splitlines()
    sleep_line = source.index("        time.sleep(0.05)") + 1
    return_line = source.index("    return total") + 1
    assert all(1 <= n <= len(source) for n in seen)
    assert seen[-1] == return_line  # the return is the last line executed
    assert sleep_line in seen and len(seen) >= 3  # the sleep line is reported on every iteration


async def test_flooding_tool_is_rate_limited_and_times_out(make_run):
    short = SandboxConfig.from_environment({**os.environ, "SANDBOX_TIMEOUT_SECONDS": "5"})
    r = PodmanRunner(short)
    await r.start()
    assert r.available, r.unavailable_reason
    try:
        tool, run_dir = make_run("flood", FLOOD_TOOL)
        seen = []
        started = time.monotonic()
        result = await r.run(tool, run_dir, on_line=seen.append)
        elapsed = time.monotonic() - started
    finally:
        await r.stop()
    assert result.outcome is Outcome.TIMEOUT, result
    assert elapsed < short.timeout_seconds + 3 + SLACK
    assert seen and len(seen) / elapsed <= 60
