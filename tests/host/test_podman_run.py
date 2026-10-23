import asyncio
import json
import sys

import pytest

from containerwebcore.host.podman import Outcome, PodmanRunner, SandboxBusy, SandboxUnavailable
from containerwebcore.host.config import SandboxConfig
from tests import IMAGE


class FakePodman:
    """Records argv and timeouts; `responses` maps subcommand -> (rc, out, err) or an exception."""

    def __init__(self, responses=None, run_delay=0.0):
        self.calls, self.responses, self.run_delay = [], responses or {}, run_delay
        self.timeouts = []

    async def __call__(self, *argv, timeout=None):
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
        sub = argv[1]
        if sub == "run":
            # a run longer than the caller's timeout behaves like a hung `podman run`
            if timeout is not None and self.run_delay > timeout:
                raise asyncio.TimeoutError
            await asyncio.sleep(self.run_delay)
        resp = self.responses.get(sub, (0, "", ""))
        if isinstance(resp, Exception):
            raise resp
        return resp

    def subcommands(self):
        return [c[1] for c in self.calls]


def cfg(**kw):
    return SandboxConfig.from_environment({k: str(v) for k, v in kw.items()}, image=IMAGE)


def ready_runner(config, exec):
    """A runner as it is after a successful start(): a fresh one refuses runs until then."""
    runner = PodmanRunner(config, exec=exec)
    runner.mark_available()
    return runner


async def test_run_happy_path_sequence_and_result(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps([1, 2]))
    fake = FakePodman({"inspect": (0, "0 false", "")})
    r = await ready_runner(cfg(), fake).run(tmp_path / "t.py", tmp_path)
    assert r.outcome is Outcome.OK and r.results == [1, 2]
    assert fake.subcommands() == ["run", "inspect", "logs", "rm", "unshare"]
    assert fake.calls[-1] == ["podman", "unshare", "chown", "-R", "0:0", str(tmp_path)]
    assert fake.calls[-2][:4] == ["podman", "rm", "-f", f"run-{tmp_path.name}"]


async def test_run_oom_from_inspect(tmp_path):
    fake = FakePodman({"inspect": (0, "137 true", "")})
    r = await ready_runner(cfg(), fake).run(tmp_path / "t.py", tmp_path)
    assert r.outcome is Outcome.OOM


async def test_run_safety_net_kills_once_and_cleans_up(tmp_path):
    # run_delay > timeout makes the fake raise asyncio.TimeoutError, like a hung `podman run`
    fake = FakePodman({"inspect": (0, "137 false", "")}, run_delay=5.0)
    r = await ready_runner(cfg(SANDBOX_TIMEOUT_SECONDS=0.1), fake).run(tmp_path / "t.py", tmp_path)
    assert r.outcome is Outcome.TIMEOUT
    assert fake.subcommands().count("kill") == 1 and fake.subcommands().count("rm") == 1
    assert fake.subcommands()[-1] == "unshare"


async def test_run_inspect_failure_is_crashed(tmp_path):
    fake = FakePodman({"inspect": (125, "", "no such container")})
    r = await ready_runner(cfg(), fake).run(tmp_path / "t.py", tmp_path)
    assert r.outcome is Outcome.CRASHED and "rm" in fake.subcommands()


async def test_run_post_run_calls_have_timeouts(tmp_path):
    fake = FakePodman({"inspect": (0, "0 false", "")})
    await ready_runner(cfg(), fake).run(tmp_path / "t.py", tmp_path)
    post_run = dict(zip(fake.subcommands(), fake.timeouts))
    for sub in ("inspect", "logs", "rm", "unshare"):
        assert post_run[sub] is not None and post_run[sub] <= 30, sub


async def test_run_survives_hanging_cleanup(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps("ok"))
    fake = FakePodman({"inspect": (0, "0 false", ""), "rm": asyncio.TimeoutError()})
    r = await ready_runner(cfg(), fake).run(tmp_path / "t.py", tmp_path)
    assert r.outcome is Outcome.OK and fake.subcommands()[-1] == "unshare"


async def test_run_survives_kill_failure(tmp_path):
    fake = FakePodman({"inspect": (0, "-1 false", ""), "kill": RuntimeError("podman kill blew up")}, run_delay=5.0)
    r = await ready_runner(cfg(SANDBOX_TIMEOUT_SECONDS=0.1), fake).run(tmp_path / "t.py", tmp_path)
    assert r.outcome is Outcome.TIMEOUT and "rm" in fake.subcommands() and "unshare" in fake.subcommands()


async def test_run_podman_bounds_captured_output():
    from containerwebcore.host.podman import OUTPUT_CAP_BYTES, run_podman

    code = "import sys; sys.stdout.write('x' * 3_000_000); sys.stderr.write('y' * 3_000_000)"
    rc, out, err = await run_podman(sys.executable, "-c", code)
    assert rc == 0
    # both streams are ring buffers: a flooding tool cannot grow the app's memory
    assert 0 < len(out) <= OUTPUT_CAP_BYTES and 0 < len(err) <= OUTPUT_CAP_BYTES


async def test_run_busy_after_queue_wait(tmp_path):
    fake = FakePodman({"inspect": (0, "0 false", "")}, run_delay=0.5)
    runner = ready_runner(cfg(SANDBOX_MAX_CONCURRENT=1, SANDBOX_QUEUE_SECONDS=0.05), fake)
    first = asyncio.create_task(runner.run(tmp_path / "a.py", tmp_path))
    await asyncio.sleep(0.01)
    with pytest.raises(SandboxBusy):
        await runner.run(tmp_path / "b.py", tmp_path)
    await first


async def test_run_unavailable_raises_without_calling_podman(tmp_path):
    fake = FakePodman()
    runner = PodmanRunner(cfg(), exec=fake)
    runner.mark_unavailable("self-test failed")
    with pytest.raises(SandboxUnavailable):
        await runner.run(tmp_path / "t.py", tmp_path)
    assert fake.calls == []
