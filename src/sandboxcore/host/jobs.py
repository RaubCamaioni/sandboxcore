"""Run untrusted code and get back a result whose files the core has checked.

`Sandbox` is the interface an app uses. It takes a `Job` (a `Source` plus plain argument values and
uploaded file bytes) and returns a `Result`: the outcome, the tool's return value with every file it
named replaced by a vetted `OutputFile`, and the run's id. The app never creates a run directory,
writes args.json or trusts a path that came out of a run; it serves a download with `Sandbox.output`.

Run directories are removed `keep_seconds` after their run, and at once on `stop()`.
"""

import asyncio
import logging
import math
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from sandboxcore import serializer
from sandboxcore.contract import ARGS_FILE, RESULT_FILE, TRACE_FILE
from sandboxcore.host.config import SandboxConfig
from sandboxcore.host.podman import Outcome, PodmanRunner, SandboxUnavailable
from sandboxcore.host.rundir import (
    KEEP_SECONDS,
    RUN_DIR_CHARS,
    RUN_DIR_NAME_LENGTH,
    RunFileError,
    delete_later,
    make_run_dir,
    read_run_file,
    upload_path,
)
from sandboxcore.source import ENTRYPOINT, Source, check_source

logger = logging.getLogger("sandboxcore")

# what a tool may receive as a plain argument; files arrive as InputFile
PLAIN = (str, int, float, bool, type(None))


class JobError(ValueError):
    """A job the core refuses to run; the message can be shown to the visitor."""


@dataclass(frozen=True)
class InputFile:
    filename: Optional[str]  # the visitor's name for it; cleaned before it reaches the run directory
    data: bytes


@dataclass(frozen=True)
class Job:
    source: Source
    arguments: Mapping[str, Any] = field(default_factory=dict)  # plain values: str, int, float, bool, None
    files: Mapping[str, Optional[InputFile]] = field(default_factory=dict)  # file arguments; None: no file


@dataclass(frozen=True)
class OutputFile:
    run: str  # the run's id
    name: str  # the file's path inside the run directory, e.g. "out.png" or "inputs/data/photo.png"
    size: int
    path: Path  # checked: a regular file inside the run directory, reached without symlinks

    @property
    def filename(self) -> str:
        return self.path.name

    def read(self, limit: int) -> bytes:
        """The file's bytes; RunFileError when it is larger than `limit` or no longer a plain file."""
        return read_run_file(self.path, limit)


@dataclass
class Result:
    outcome: Outcome
    run: str  # pass it to Sandbox.output to fetch a file of this run until it expires
    value: Any = None  # the tool's return value when outcome is OK; its files are OutputFile
    message: Optional[str] = None  # user-facing text for every other outcome
    stderr_tail: str = ""
    files: list[OutputFile] = field(default_factory=list)  # every OutputFile in `value`


# files of the run's own machinery: never a tool's output and never served
_MACHINERY = {ARGS_FILE, RESULT_FILE, TRACE_FILE}


class _NotAnOutput(Exception):
    pass


class Sandbox:
    def __init__(self, config: SandboxConfig, runner=None, keep_seconds: float = KEEP_SECONDS):
        self.config = config
        self.runner = runner if runner is not None else PodmanRunner(config)
        self.keep_seconds = keep_seconds
        self._runs: dict[str, tuple[Path, str]] = {}  # run id -> (run directory, tool file name)
        self._cleanups: dict[str, asyncio.Task] = {}

    # --- lifecycle -------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self.runner.available

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self.runner.unavailable_reason

    def start_in_background(self):
        """Start the runner (readiness, image, self-test) without delaying the app's startup."""
        self.runner.start_in_background()

    async def start(self):
        await self.runner.start()

    async def stop(self):
        """Stop the runner and remove every run directory still kept."""
        tasks = list(self._cleanups.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # a cleanup task cancelled before its first step never ran its finally: remove what is left here
        for run, (run_dir, _) in list(self._runs.items()):
            shutil.rmtree(run_dir, ignore_errors=True)
            self._runs.pop(run, None)
        await self.runner.stop()

    # --- one job ---------------------------------------------------------------------

    async def run(self, job: Job, on_line: Optional[Callable[[int], None]] = None) -> Result:
        """Run `job`. JobError when the job itself is refused; SandboxBusy and SandboxUnavailable
        as PodmanRunner.run. With `on_line`, the tool's executed line numbers arrive as they happen."""
        if not self.available:
            raise SandboxUnavailable(self.unavailable_reason)
        source = _checked(job)
        run_dir = make_run_dir(self.config.run_dir)
        run = run_dir.name
        self._runs[run] = (run_dir, source.filename)
        try:
            tool = run_dir / source.filename
            tool.write_text(source.code, encoding="utf-8")
            arguments = dict(job.arguments)
            budget = self.config.max_input_mb * 1024 * 1024
            for key, upload in job.files.items():
                if upload is None:
                    arguments[key] = None
                    continue
                budget -= len(upload.data)
                if budget < 0:
                    raise JobError(f"Uploaded files are larger than {self.config.max_input_mb} MB")
                try:
                    target = upload_path(run_dir, key, upload.filename)
                except RunFileError as e:
                    raise JobError(str(e)) from None
                with open(target, "xb") as f:
                    f.write(upload.data)
                arguments[key] = target
            with open(run_dir / ARGS_FILE, "w") as f:
                serializer.dump(arguments, f)

            # !DANGER! user submitted code
            raw = await self.runner.run(tool, run_dir, on_line=on_line)
        except BaseException:
            self._runs.pop(run, None)
            shutil.rmtree(run_dir, ignore_errors=True)  # nothing to download from a run that never ran
            raise
        self._schedule_cleanup(run, run_dir)

        if raw.outcome is not Outcome.OK:
            return Result(raw.outcome, run, message=raw.message, stderr_tail=raw.stderr_tail)
        files: list[OutputFile] = []
        try:
            value = self._vet(raw.results, run, run_dir, source.filename, files)
        except _NotAnOutput as e:
            logger.warning(f"run {run[:6]}: {e}")
            return Result(
                Outcome.CRASHED, run, message="Tool returned a path that is not a file it made",
                stderr_tail=raw.stderr_tail,
            )
        return Result(Outcome.OK, run, value=value, stderr_tail=raw.stderr_tail, files=files)

    def output(self, run: str, name: str) -> Optional[OutputFile]:
        """A file of a run this sandbox made and still keeps, by the names in its OutputFile; None for
        anything else (unknown or expired run, a path out of the run directory, a symlink, a directory,
        the run's own machinery)."""
        if len(run) != RUN_DIR_NAME_LENGTH or not set(run) <= set(RUN_DIR_CHARS) or run not in self._runs:
            return None
        run_dir, tool = self._runs[run]
        try:
            return _output_file(run, run_dir, run_dir / name, tool)
        except _NotAnOutput:
            return None

    # --- internals -------------------------------------------------------------------

    def _vet(self, value, run: str, run_dir: Path, tool: str, files: list):
        if isinstance(value, Path):
            out = _output_file(run, run_dir, value, tool)
            files.append(out)
            return out
        if isinstance(value, list):
            return [self._vet(v, run, run_dir, tool, files) for v in value]
        if isinstance(value, dict):
            return {k: self._vet(v, run, run_dir, tool, files) for k, v in value.items()}
        return value

    def _schedule_cleanup(self, run: str, run_dir: Path):
        async def cleanup():
            try:
                await delete_later(run_dir, self.keep_seconds)
            finally:
                self._runs.pop(run, None)
                self._cleanups.pop(run, None)

        self._cleanups[run] = asyncio.create_task(cleanup(), name=f"sandboxcore-delete-{run[:6]}")


def _checked(job: Job) -> Source:
    source = check_source(job.source)
    names = list(job.arguments) + list(job.files)
    if len(set(names)) != len(names):
        raise JobError("An argument is given both as a value and as a file")
    for name in names:
        if not isinstance(name, str) or not ENTRYPOINT.fullmatch(name):
            raise JobError(f"Argument names must be plain identifiers, not {name!r}")
    for name, value in job.arguments.items():
        if not isinstance(value, PLAIN) or (isinstance(value, float) and not math.isfinite(value)):
            raise JobError(f"Argument {name} must be a string, a number, true/false or empty")
    return source


def _output_file(run: str, run_dir: Path, path: Path, tool: str) -> OutputFile:
    """`path` as an OutputFile if it is a regular file inside `run_dir` reached without symlinks."""
    if not path.is_absolute():
        raise _NotAnOutput(f"relative path {path}")
    root = run_dir.resolve()
    normal = Path(os.path.normpath(path))
    try:
        relative = normal.relative_to(run_dir)
    except ValueError:
        try:
            relative = normal.relative_to(root)
        except ValueError:
            raise _NotAnOutput(f"{path} is outside the run directory") from None
    if not relative.parts or relative.as_posix() in _MACHINERY | {tool}:
        raise _NotAnOutput(f"{path} is not an output")
    # walk it component by component with lstat: a symlink anywhere on the way is refused
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            raise _NotAnOutput(f"{path} does not exist") from None
        if stat.S_ISLNK(info.st_mode):
            raise _NotAnOutput(f"{path} goes through a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise _NotAnOutput(f"{path} is not a regular file")
    return OutputFile(run=run, name=relative.as_posix(), size=info.st_size, path=current)
