import json

from sandboxcore.host.podman import Outcome, PodmanRunner
from sandboxcore.host.config import SandboxConfig
from tests import IMAGE

CFG = SandboxConfig.from_environment({}, image=IMAGE)


def classify(tmp_path, **kw):
    kw = {"exit_code": 0, "oom_killed": False, "elapsed": 0.5, "stderr": ""} | kw
    return PodmanRunner(CFG).classify(tmp_path, **kw)


def test_result_json_means_ok_even_with_nonzero_exit(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps("hello"))
    r = classify(tmp_path, exit_code=1)
    assert r.outcome is Outcome.OK and r.results == "hello" and r.message is None


def test_oom_killed(tmp_path):
    r = classify(tmp_path, exit_code=137, oom_killed=True)
    assert r.outcome is Outcome.OOM and r.message == "Tool exceeded the 1 GB memory limit"


def test_timeout_by_elapsed(tmp_path):
    r = classify(tmp_path, exit_code=137, elapsed=10.2)
    assert r.outcome is Outcome.TIMEOUT and r.message == "Tool exceeded the 10 second limit"


def test_timeout_by_podman_exit_code(tmp_path):
    # inspect reports ExitCode -1 when podman's own --timeout killed the container
    r = classify(tmp_path, exit_code=-1, elapsed=0.3)
    assert r.outcome is Outcome.TIMEOUT


def test_timeout_by_safety_net_flag(tmp_path):
    assert classify(tmp_path, exit_code=None, elapsed=0.0, timed_out=True).outcome is Outcome.TIMEOUT


def test_crash_keeps_stderr_tail(tmp_path):
    r = classify(tmp_path, exit_code=1, stderr="\n".join(f"line{i}" for i in range(40)))
    assert r.outcome is Outcome.CRASHED and r.message == "Tool failed to run"
    assert r.stderr_tail.splitlines() == [f"line{i}" for i in range(20, 40)]


def test_classify_malformed_result_is_crashed(tmp_path):
    (tmp_path / "result.json").write_text("{not json")
    assert classify(tmp_path).outcome is Outcome.CRASHED


# result.json is written by untrusted code: anything but a modest regular file is a crash, never a 500
def test_result_json_directory_is_crashed(tmp_path):
    (tmp_path / "result.json").mkdir()
    assert classify(tmp_path).outcome is Outcome.CRASHED


def test_result_json_symlink_is_crashed(tmp_path):
    (tmp_path / "real.json").write_text(json.dumps("x"))
    (tmp_path / "result.json").symlink_to(tmp_path / "real.json")
    assert classify(tmp_path).outcome is Outcome.CRASHED


def test_result_json_fifo_is_crashed_without_blocking(tmp_path):
    # the container is gone, so nothing will ever open the write end: a blocking open would hang the app
    import os
    import threading

    os.mkfifo(tmp_path / "result.json")
    got = []
    worker = threading.Thread(target=lambda: got.append(classify(tmp_path)), daemon=True)
    worker.start()
    worker.join(5)
    assert got and got[0].outcome is Outcome.CRASHED


def test_result_json_nested_too_deep_is_crashed(tmp_path):
    (tmp_path / "result.json").write_text("[" * 100_000 + "]" * 100_000)
    assert classify(tmp_path).outcome is Outcome.CRASHED


def test_result_json_invalid_utf8_is_crashed(tmp_path):
    (tmp_path / "result.json").write_bytes(bytes([34, 255, 34]))  # a JSON string holding byte 0xff
    assert classify(tmp_path).outcome is Outcome.CRASHED


def test_result_json_oversized_is_crashed(tmp_path):
    from sandboxcore.host.podman import RESULT_MAX_BYTES

    (tmp_path / "result.json").write_bytes(b'"' + b"x" * RESULT_MAX_BYTES + b'"')
    assert classify(tmp_path).outcome is Outcome.CRASHED


def test_oversized_output_directory_is_crashed_and_emptied(tmp_path):
    small = SandboxConfig.from_environment({"SANDBOX_MAX_OUTPUT_MB": "1"}, image=IMAGE)
    (tmp_path / "result.json").write_text(json.dumps("ok"))
    (tmp_path / "big.bin").write_bytes(b"\0" * (2 * 1024 * 1024))
    r = PodmanRunner(small).classify(tmp_path, exit_code=0, oom_killed=False, elapsed=0.5, stderr="")
    assert r.outcome is Outcome.CRASHED and r.message == "Tool output exceeded the 1 MB limit"
    assert list(tmp_path.iterdir()) == []
