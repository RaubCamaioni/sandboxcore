# containerwebcore

The sandbox core shared by [PyTools](https://github.com/RaubCamaioni/PyTools) and the
[CadQuery Catalog](https://github.com/RaubCamaioni/cadquery-catalog). Both run user-submitted code in
one rootless podman container per run. This package is the part they had in common. Fix it here and both
apps get the fix.

- **host** (web app side): `PodmanRunner` with a fail-closed startup self-test, per-run limits (no
  network, CPU, memory, pids, read-only root, no capabilities, hard timeout), outcome classification
  (OK / TIMEOUT / OOM / CRASHED), the line-trace channel, and run-directory helpers that never trust
  what a run left behind.
- **python** (language): the entrypoint-annotation allow-list (host side) and the runner and line tracer
  (sandbox side).
- **contract**: the file names and trace record format the two sides agree on. The line trace is a
  language-neutral protocol ([docs/trace-protocol.md](docs/trace-protocol.md)) with a conformance kit
  (`containerwebcore.conformance`) and a Go reference runtime in `runtimes/go`.

Standard library only, Python 3.12+. Design and trust boundary: [docs/design.md](docs/design.md).

## Install

Pin a release tag. A tarball needs no git in the image build:

```toml
# server/pyproject.toml and sandbox/pyproject.toml
dependencies = [
    "containerwebcore @ https://github.com/RaubCamaioni/containerwebcore/archive/refs/tags/v0.1.0.tar.gz",
]
```

## Use

### In the web app

```python
import os
from containerwebcore.host import PodmanRunner, SandboxConfig, Outcome, SandboxBusy, SandboxUnavailable
from containerwebcore.host import TraceRegistry, make_run_dir, delete_later, upload_path, resolve_run_file, RunFileError
from containerwebcore.python.annotations import check_annotation, resolve_annotation, AnnotationError
from containerwebcore import serializer

config = SandboxConfig.from_environment(
    os.environ, image="docker.io/raubcamaioni/pywebtools-sandbox:latest", run_dir="/tmp/pywebtools-runs"
)
runner = PodmanRunner(config)
runner.start_in_background()          # on app startup; runner.stop() on shutdown

run_dir = make_run_dir(config.run_dir)  # then asyncio.create_task(delete_later(run_dir)) when done
(run_dir / "tool.py").write_text(code)
with open(run_dir / "args.json", "w") as f:
    serializer.dump(kwargs, f)        # uploads: write them to upload_path(run_dir, key, filename)
result = await runner.run(run_dir / "tool.py", run_dir, on_line=channel.put)
if result.outcome is Outcome.OK:
    ...                               # result.results; paths in it are hints, not trusted
```

Serve downloads with `resolve_run_file(config.run_dir, requested)`. It raises `RunFileError` for
anything outside a run directory, symlinks included. Read a file a run produced with
`read_run_file(path, limit)`.

Logs go to the `containerwebcore` logger. Under uvicorn, route it next to uvicorn's own:

```python
from uvicorn.config import LOGGING_CONFIG
log_config = {**LOGGING_CONFIG, "loggers": {**LOGGING_CONFIG["loggers"],
              "containerwebcore": {"handlers": ["default"], "level": "INFO", "propagate": False}}}
uvicorn.run(app, ..., log_config=log_config)
```

### In the sandbox image

Install the package next to the tools' dependencies and make the runner the entrypoint. With no
post-processing, use the console script:

```dockerfile
ENTRYPOINT ["/venvs/sandbox/bin/containerwebcore-python"]
```

An app that turns results into files inside the sandbox wraps `main` with a `finish` hook:

```python
# sandbox/src/sandbox/runner.py in the CadQuery Catalog
from containerwebcore.python.runner import main as run
from sandbox.export import export_model

def main():
    run(finish=export_model, finish_label="Export Error")
```

The image must keep a `python3` on PATH (the startup self-test runs `--entrypoint python3`) and run as
uid `SandboxConfig.sandbox_uid` (1001). `containers/Containerfile.sandbox` is a minimal working example.

## Develop

```sh
./run-tests.sh -q              # unit, Go and trace conformance tests in a container (needs podman; WSL works)
./run-integration.sh -v        # real containers: builds the reference sandbox image first
```

Release: bump `version` in `pyproject.toml` and `__version__` in `src/containerwebcore/__init__.py`,
add a CHANGELOG entry, merge, and tag `v<version>`. CI refuses a tag that doesn't match. Then bump the
pinned tag in both apps (server and sandbox).
