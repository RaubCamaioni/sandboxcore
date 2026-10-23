"""Test doubles for apps that use sandboxcore. NOT FOR PRODUCTION: nothing here is sandboxed.

`UnsandboxedRunner` stands in for `PodmanRunner` in an app's unit tests: it runs the real runtime as a
local subprocess, with no container and no limits, and classifies the run with PodmanRunner's rules.
`Sandbox(config, runner=UnsandboxedRunner(config))` then behaves like production except for
isolation: staging, `--inspect`, the runtime and the result vetting are all real.
"""

import asyncio
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from sandboxcore.host.config import SandboxConfig
from sandboxcore.host.podman import PodmanRunner, RunResult, SandboxUnavailable

PYTHON_RUNTIME = (sys.executable, "-m", "sandboxcore.python.runner")


class UnsandboxedRunner:
    def __init__(self, config: SandboxConfig, runtime: Sequence[str] = PYTHON_RUNTIME):
        self.config = config
        self.runtime = tuple(runtime)
        self.available = True
        self.unavailable_reason: Optional[str] = None
        self.calls: list[tuple[Path, Path, tuple]] = []
        self._classifier = PodmanRunner(config)

    def start_in_background(self):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    async def run(
        self,
        tool: Path,
        run_dir: Path,
        on_line: Optional[Callable[[int], None]] = None,
        runtime_args: Sequence[str] = (),
    ) -> RunResult:
        if not self.available:
            raise SandboxUnavailable(self.unavailable_reason)
        self.calls.append((tool, run_dir, tuple(runtime_args)))
        proc = await asyncio.create_subprocess_exec(
            *self.runtime,
            "--file",
            str(tool),
            "--workdir",
            str(run_dir),
            *runtime_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), self.config.timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return self._classifier.classify(run_dir, None, False, self.config.timeout_seconds, "", timed_out=True)
        return self._classifier.classify(run_dir, proc.returncode, False, 0.0, err.decode(errors="replace"))
