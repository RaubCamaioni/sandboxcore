from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# must not import `app`: app/__init__.py builds a SandboxConfig at import time

_UNITS = {"g": "GB", "m": "MB", "k": "KB"}


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    max_concurrent: int
    queue_seconds: float
    timeout_seconds: float
    memory: str
    cpus: str
    pids: int
    run_dir: Path
    max_output_mb: int = 64  # run directory size after a run; larger output is discarded

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "SandboxConfig":
        return cls(
            image=env.get("SANDBOX_IMAGE", "docker.io/raubcamaioni/pywebtools-sandbox:latest"),
            max_concurrent=int(env.get("SANDBOX_MAX_CONCURRENT", "2")),
            queue_seconds=float(env.get("SANDBOX_QUEUE_SECONDS", "10")),
            timeout_seconds=float(env.get("SANDBOX_TIMEOUT_SECONDS", "10")),
            memory=env.get("SANDBOX_MEMORY", "1g"),
            cpus=env.get("SANDBOX_CPUS", "0.5"),
            pids=int(env.get("SANDBOX_PIDS", "64")),
            run_dir=Path(env.get("SANDBOX_RUN_DIR", "/tmp/pywebtools-runs")),
            max_output_mb=int(env.get("SANDBOX_MAX_OUTPUT_MB", "64")),
        )

    @property
    def memory_label(self) -> str:
        """podman size ("1g", "512m") as user-facing text ("1 GB", "512 MB")."""
        value, unit = self.memory[:-1], self.memory[-1:].lower()
        if unit in _UNITS and value.isdigit():
            return f"{value} {_UNITS[unit]}"
        return self.memory
