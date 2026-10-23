from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

_UNITS = {"g": "GB", "m": "MB", "k": "KB"}

DEFAULT_RUN_DIR = "/tmp/sandboxcore-runs"
# uid of the unprivileged user a sandbox image runs tools as (`useradd -u 1001` in its Containerfile)
DEFAULT_SANDBOX_UID = 1001


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
    max_input_mb: int = 64  # uploaded files of one job, together
    sandbox_uid: int = DEFAULT_SANDBOX_UID

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str],
        *,
        image: Optional[str] = None,
        run_dir: str = DEFAULT_RUN_DIR,
    ) -> "SandboxConfig":
        """Limits from SANDBOX_* variables. `image` and `run_dir` are the app's defaults for
        SANDBOX_IMAGE and SANDBOX_RUN_DIR; an image must come from one or the other."""
        image = env.get("SANDBOX_IMAGE", image)
        if not image:
            raise ValueError("no sandbox image: set SANDBOX_IMAGE or pass a default image")
        return cls(
            image=image,
            max_concurrent=int(env.get("SANDBOX_MAX_CONCURRENT", "2")),
            queue_seconds=float(env.get("SANDBOX_QUEUE_SECONDS", "10")),
            timeout_seconds=float(env.get("SANDBOX_TIMEOUT_SECONDS", "10")),
            memory=env.get("SANDBOX_MEMORY", "1g"),
            cpus=env.get("SANDBOX_CPUS", "0.5"),
            pids=int(env.get("SANDBOX_PIDS", "64")),
            run_dir=Path(env.get("SANDBOX_RUN_DIR", run_dir)),
            max_output_mb=int(env.get("SANDBOX_MAX_OUTPUT_MB", "64")),
            max_input_mb=int(env.get("SANDBOX_MAX_INPUT_MB", "64")),
        )

    @property
    def memory_label(self) -> str:
        """podman size ("1g", "512m") as user-facing text ("1 GB", "512 MB")."""
        value, unit = self.memory[:-1], self.memory[-1:].lower()
        if unit in _UNITS and value.isdigit():
            return f"{value} {_UNITS[unit]}"
        return self.memory
