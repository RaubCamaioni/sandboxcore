"""Run directories: create them, put uploads in them, read and serve what a run left in them.

Everything in a run directory after a run was written by untrusted code. It can be a symlink to any
host path, a FIFO that blocks whoever opens it, or a file of any size, and result.json can name any
host path. The helpers here are the only ways to read from a run directory (Sandbox uses them).
"""

import asyncio
import os
import re
import secrets
import shutil
import stat
import string
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Optional

from sandboxcore.contract import INPUTS_DIR

RUN_DIR_NAME_LENGTH = 32
RUN_DIR_CHARS = string.ascii_letters + string.digits + "-_"
# run directories are removed this long after their run; their files are downloadable until then
KEEP_SECONDS = 60 * 10
UPLOAD_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")
UPLOAD_NAME_MAX = 128


class RunFileError(ValueError):
    """A path or file from a run directory that must not be used."""


def make_run_dir(root: Path) -> Path:
    """A fresh, secret, world-writable run directory under `root`.

    The name is the only thing keeping one visitor's results from another's: downloads are served by
    path, so it must be unguessable. World-writable because the sandbox user is a sub-UID.
    """
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / "".join(secrets.choice(RUN_DIR_CHARS) for _ in range(RUN_DIR_NAME_LENGTH))
    os.mkdir(run_dir)
    os.chmod(run_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
    return run_dir


async def delete_later(run_dir: Path, delay: float = KEEP_SECONDS) -> None:
    """Remove `run_dir` after `delay` seconds, or at once when cancelled (app shutdown)."""
    try:
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(delay)
    finally:
        if run_dir.exists() and run_dir.is_dir():
            shutil.rmtree(run_dir, ignore_errors=True)


def upload_path(run_dir: Path, key: str, filename: Optional[str]) -> Path:
    """Where an uploaded file is written: its own directory, inputs/<argument>/, under a cleaned name.

    The name in the multipart header is the visitor's: `/x` or `../x` would place the file anywhere the
    app user can write, and a bare name would land next to result.json and args.json. Only the last
    component survives, reduced to plain characters and without a leading dot; tools still see a
    readable name (they name their output after it). RunFileError when nothing safe remains.
    """
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    name = UPLOAD_NAME_UNSAFE.sub("_", name).lstrip(". ")[:UPLOAD_NAME_MAX] or key
    directory = run_dir / INPUTS_DIR / key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if path.resolve().parent != directory.resolve():
        raise RunFileError("Bad upload filename")
    return path


def read_run_file(path: Path, limit: int) -> bytes:
    """The bytes of a regular file that is not a symlink and holds at most `limit` bytes."""
    try:
        # O_NONBLOCK: a FIFO left in the run directory must not block the event loop on open
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise RunFileError(f"{path.name} is missing or not a plain file") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RunFileError(f"{path.name} is not a plain file")
        if info.st_size > limit:
            raise RunFileError(f"{path.name} is larger than {_size_label(limit)}")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(limit + 1)
        if len(data) > limit:  # it grew after fstat
            raise RunFileError(f"{path.name} is larger than {_size_label(limit)}")
        return data
    finally:
        os.close(fd)


def _size_label(limit: int) -> str:
    if limit >= 1024 * 1024:
        return f"{limit // (1024 * 1024)} MB"
    return f"{limit} bytes"
