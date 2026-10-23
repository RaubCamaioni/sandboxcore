"""Run directories: everything in them after a run was written by untrusted code."""

import asyncio
import os
import stat
import threading

import pytest

from sandboxcore.host.rundir import (
    RUN_DIR_NAME_LENGTH,
    RunFileError,
    delete_later,
    make_run_dir,
    read_run_file,
    upload_path,
)


def test_a_run_dir_is_secret_and_world_writable(tmp_path):
    a, b = make_run_dir(tmp_path / "runs"), make_run_dir(tmp_path / "runs")
    assert a != b and a.parent == tmp_path / "runs" and len(a.name) == RUN_DIR_NAME_LENGTH
    assert stat.S_IMODE(a.stat().st_mode) == 0o777


async def test_delete_later_removes_the_dir_after_the_delay(tmp_path):
    run_dir = make_run_dir(tmp_path)
    (run_dir / "out.txt").write_text("x")
    await delete_later(run_dir, delay=0)
    assert not run_dir.exists()


async def test_delete_later_removes_the_dir_at_once_when_cancelled(tmp_path):
    run_dir = make_run_dir(tmp_path)
    task = asyncio.create_task(delete_later(run_dir, delay=3600))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not run_dir.exists()


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("../../escaped.txt", "escaped.txt"),
        ("/tmp/escaped.txt", "escaped.txt"),
        ("a/../../b.txt", "b.txt"),
        ("..\\..\\win.txt", "win.txt"),
        ("..", "data"),
        ("", "data"),
        (None, "data"),
        (".bashrc", "bashrc"),
        ("args.json", "args.json"),
        ("result.json", "result.json"),
        ("photo $(id).PNG", "photo __id_.PNG"),
        ("x" * 300, "x" * 128),
    ],
)
def test_an_upload_lands_in_its_own_input_directory(tmp_path, filename, expected):
    path = upload_path(tmp_path, "data", filename)
    assert path.parent == tmp_path / "inputs" / "data" and path.name == expected


def test_an_upload_name_taken_by_a_symlink_is_refused(tmp_path):
    (tmp_path / "inputs" / "data").mkdir(parents=True)
    (tmp_path / "inputs" / "data" / "x.txt").symlink_to(tmp_path / "elsewhere.txt")
    with pytest.raises(RunFileError):
        upload_path(tmp_path, "data", "x.txt")


def test_read_run_file_reads_a_plain_file(tmp_path):
    (tmp_path / "f").write_bytes(b"abc")
    assert read_run_file(tmp_path / "f", 3) == b"abc"


def test_read_run_file_refuses_a_symlink(tmp_path):
    (tmp_path / "secret").write_text("SESSION_KEY=hunter2")
    (tmp_path / "f").symlink_to(tmp_path / "secret")
    with pytest.raises(RunFileError):
        read_run_file(tmp_path / "f", 1024)


def test_read_run_file_refuses_missing_directories_and_fifos_without_blocking(tmp_path):
    with pytest.raises(RunFileError):
        read_run_file(tmp_path / "missing", 1024)
    (tmp_path / "dir").mkdir()
    with pytest.raises(RunFileError):
        read_run_file(tmp_path / "dir", 1024)
    os.mkfifo(tmp_path / "fifo")
    raised = []

    def read():
        try:
            read_run_file(tmp_path / "fifo", 1024)
        except RunFileError:
            raised.append(True)

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(5)
    assert raised


def test_read_run_file_refuses_an_oversized_file(tmp_path):
    (tmp_path / "f").write_bytes(b"x" * 11)
    with pytest.raises(RunFileError, match="larger than 10 bytes"):
        read_run_file(tmp_path / "f", 10)
