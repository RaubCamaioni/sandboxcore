"""Check a sandbox runtime, in any language, against the line trace protocol (docs/trace-protocol.md).

Describe the runtime with `Runtime` (how to run a tool file, plus two small tools written in its
language), then call `check_runtime`, or one check from `CHECKS` at a time. Each check sets up the
FIFO the way the host does: the read end and a write end of its own are open before the runtime
starts. It runs the command on this machine, so wrap it in `podman run` to check an image.
A broken rule raises AssertionError naming it.
"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from sandboxcore.contract import TRACE_ENV, TRACE_ENV_LEGACY, TRACE_FILE, TRACE_RECORD

READ_CHUNK = 65536


@dataclass(frozen=True)
class Runtime:
    name: str
    suffix: str  # the tool file's suffix, e.g. ".py"
    # (tool file, run directory) -> argv; the runtime is run with the run directory as its cwd
    command: Callable[[Path, Path], list[str]]
    # a tool file named probe<suffix> that exits successfully and runs these statement lines, first in this order
    probe: str
    probe_lines: Sequence[int]
    # a tool file named flood<suffix> that runs a line at least 100,000 times, then exits successfully
    flood: str
    # anything the runtime needs in the run directory besides the tool (Python: args.json)
    prepare: Callable[[Path], None] = field(default=lambda run_dir: None)
    timeout: float = 120.0


@dataclass
class Capture:
    returncode: int
    records: list[int]
    stdout: str
    stderr: str
    seconds: float


def run_traced(
    runtime: Runtime,
    run_dir: Path,
    tool: str = "probe",
    *,
    variables: Sequence[str] = (TRACE_ENV,),
    trace: Optional[str] = None,
    reader: str = "draining",
) -> Capture:
    """Run tool `tool` ("probe" or "flood") the way the host would and collect what it writes.

    `variables` names the environment variables that carry the trace path (none: an untraced run),
    `trace` overrides the path they carry, and `reader` is "draining" (the host reads as it goes),
    "stuck" (the host holds the FIFO open but never reads) or "none" (nobody opened the FIFO).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    source = runtime.probe if tool == "probe" else runtime.flood
    tool_file = run_dir / f"{tool}{runtime.suffix}"
    tool_file.write_text(source)
    runtime.prepare(run_dir)

    fifo = run_dir / TRACE_FILE
    os.mkfifo(fifo, 0o666)
    rfd = wfd = None
    if reader != "none":
        rfd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        wfd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)  # the host's own write end: no early EOF
    got = bytearray()
    drain = None
    if reader == "draining":
        os.set_blocking(rfd, True)

        def read_all():
            while chunk := os.read(rfd, READ_CHUNK):
                got.extend(chunk)

        drain = threading.Thread(target=read_all, daemon=True)
        drain.start()

    env = {k: v for k, v in os.environ.items() if k not in (TRACE_ENV, TRACE_ENV_LEGACY)}
    env.update({name: trace if trace is not None else str(fifo) for name in variables})
    started = time.monotonic()
    try:
        proc = subprocess.run(
            runtime.command(tool_file, run_dir),
            cwd=run_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=runtime.timeout,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(f"{runtime.name}: did not finish within {runtime.timeout:g} s") from None
    finally:
        seconds = time.monotonic() - started
        if wfd is not None:
            os.close(wfd)  # the runtime is gone: this delivers EOF to the reader
        if drain is not None:
            drain.join(10)
        if rfd is not None:
            os.close(rfd)
    whole = len(got) - len(got) % TRACE_RECORD.size
    records = [TRACE_RECORD.unpack_from(got, i)[0] for i in range(0, whole, TRACE_RECORD.size)]
    return Capture(proc.returncode, records, proc.stdout, proc.stderr, seconds)


def _succeeded(runtime: Runtime, capture: Capture, rule: str):
    assert capture.returncode == 0, (
        f"{runtime.name}: {rule}: exited {capture.returncode}\n{capture.stderr[-2000:]}"
    )


def _line_count(source: str) -> int:
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def check_probe(runtime: Runtime, run_dir: Path) -> Capture:
    """Records are line numbers of the tool file, and every statement line that runs is reported, in order."""
    c = run_traced(runtime, run_dir)
    _succeeded(runtime, c, "a traced run must succeed")
    lines = _line_count(runtime.probe)
    assert c.records, f"{runtime.name}: a traced run must write records"
    stray = sorted({r for r in c.records if not 1 <= r <= lines})
    assert not stray, f"{runtime.name}: records must be lines 1..{lines} of the tool file, got {stray[:10]}"
    first = []
    for r in c.records:
        if r in runtime.probe_lines and r not in first:
            first.append(r)
    assert first == list(runtime.probe_lines), (
        f"{runtime.name}: statement lines {list(runtime.probe_lines)} must each be reported, in execution order; "
        f"first reported {first}"
    )
    return c


def check_legacy_variable(runtime: Runtime, run_dir: Path) -> Capture:
    """The trace path in PYWEBTOOLS_TRACE alone is honoured (images and hosts from before sandboxcore)."""
    c = run_traced(runtime, run_dir, variables=(TRACE_ENV_LEGACY,))
    _succeeded(runtime, c, "a run traced through the legacy variable must succeed")
    assert set(runtime.probe_lines) <= set(c.records), f"{runtime.name}: {TRACE_ENV_LEGACY} must be read too"
    return c


def check_untraced(runtime: Runtime, run_dir: Path) -> Capture:
    """Without a trace variable nothing is written, and the tool runs normally."""
    c = run_traced(runtime, run_dir, variables=())
    _succeeded(runtime, c, "an untraced run must succeed")
    assert c.records == [], f"{runtime.name}: an untraced run must write nothing, wrote {len(c.records)} records"
    return c


def check_missing_fifo(runtime: Runtime, run_dir: Path) -> Capture:
    """A trace path that does not exist means an untraced run, not a failed one."""
    c = run_traced(runtime, run_dir, trace=str(run_dir / "no-such-fifo"))
    _succeeded(runtime, c, "a run whose trace path is missing must succeed untraced")
    return c


def check_no_reader(runtime: Runtime, run_dir: Path) -> Capture:
    """A FIFO nobody reads must not block the open: the runtime opens it non-blocking and runs untraced."""
    c = run_traced(runtime, run_dir, reader="none")
    _succeeded(runtime, c, "a run whose FIFO has no reader must succeed untraced")
    return c


def check_stuck_reader(runtime: Runtime, run_dir: Path) -> Capture:
    """A full pipe drops records: a tool that floods a FIFO nobody drains still finishes."""
    c = run_traced(runtime, run_dir, "flood", reader="stuck")
    _succeeded(runtime, c, "a run whose trace pipe is full must succeed (drop records, never block)")
    return c


CHECKS: dict[str, Callable[[Runtime, Path], Capture]] = {
    "probe": check_probe,
    "legacy_variable": check_legacy_variable,
    "untraced": check_untraced,
    "missing_fifo": check_missing_fifo,
    "no_reader": check_no_reader,
    "stuck_reader": check_stuck_reader,
}


def check_runtime(runtime: Runtime, work_dir: Path) -> None:
    """Every check, each in its own run directory under `work_dir`."""
    for name, check in CHECKS.items():
        check(runtime, work_dir / name)
