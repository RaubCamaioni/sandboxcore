from pathlib import Path

from containerwebcore.host.config import SandboxConfig


def test_defaults_from_empty_environment():
    cfg = SandboxConfig.from_environment({})
    assert cfg.image == "docker.io/raubcamaioni/pywebtools-sandbox:latest"
    assert (cfg.max_concurrent, cfg.queue_seconds, cfg.timeout_seconds) == (2, 10.0, 10.0)
    assert (cfg.memory, cfg.cpus, cfg.pids) == ("1g", "0.5", 64)
    assert cfg.run_dir == Path("/tmp/pywebtools-runs")
    assert cfg.max_output_mb == 64


def test_overrides_from_environment():
    cfg = SandboxConfig.from_environment(
        {"SANDBOX_MAX_CONCURRENT": "3", "SANDBOX_MEMORY": "512m", "SANDBOX_RUN_DIR": "/runs"}
    )
    assert cfg.max_concurrent == 3 and cfg.memory == "512m" and cfg.run_dir == Path("/runs")


def test_memory_label():
    assert SandboxConfig.from_environment({}).memory_label == "1 GB"
    assert SandboxConfig.from_environment({"SANDBOX_MEMORY": "512m"}).memory_label == "512 MB"

