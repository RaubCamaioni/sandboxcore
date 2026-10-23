from pathlib import Path

import pytest

from containerwebcore.host.config import SandboxConfig
from tests import IMAGE


def test_defaults_from_empty_environment():
    cfg = SandboxConfig.from_environment({}, image=IMAGE)
    assert cfg.image == IMAGE
    assert (cfg.max_concurrent, cfg.queue_seconds, cfg.timeout_seconds) == (2, 10.0, 10.0)
    assert (cfg.memory, cfg.cpus, cfg.pids) == ("1g", "0.5", 64)
    assert cfg.run_dir == Path("/tmp/containerwebcore-runs")
    assert cfg.max_output_mb == 64
    assert cfg.sandbox_uid == 1001


def test_overrides_from_environment():
    cfg = SandboxConfig.from_environment(
        {"SANDBOX_MAX_CONCURRENT": "3", "SANDBOX_MEMORY": "512m", "SANDBOX_RUN_DIR": "/runs"}, image=IMAGE
    )
    assert cfg.max_concurrent == 3 and cfg.memory == "512m" and cfg.run_dir == Path("/runs")


def test_app_defaults_yield_to_the_environment():
    cfg = SandboxConfig.from_environment({}, image="app/sandbox:1", run_dir="/tmp/app-runs")
    assert cfg.image == "app/sandbox:1" and cfg.run_dir == Path("/tmp/app-runs")
    cfg = SandboxConfig.from_environment({"SANDBOX_IMAGE": "other:2"}, image="app/sandbox:1")
    assert cfg.image == "other:2"


def test_an_image_is_required():
    with pytest.raises(ValueError):
        SandboxConfig.from_environment({})
    assert SandboxConfig.from_environment({"SANDBOX_IMAGE": "x:1"}).image == "x:1"


def test_memory_label():
    assert SandboxConfig.from_environment({}, image=IMAGE).memory_label == "1 GB"
    assert SandboxConfig.from_environment({"SANDBOX_MEMORY": "512m"}, image=IMAGE).memory_label == "512 MB"
