# sandboxcore

Run code that strangers upload without it becoming their computer. sandboxcore takes untrusted code
and untrusted inputs, runs them in a rootless podman container with no network and hard limits, and
returns a result whose every file it has checked. It also sets up and checks the system that makes
this safe.

It holds no web code. Routes, forms, sessions, databases, streaming to a browser and how results look
belong to the apps that use it: [PyTools](https://github.com/RaubCamaioni/PyTools) and the
[CadQuery Catalog](https://github.com/RaubCamaioni/cadquery-catalog).

| | |
|---|---|
| **Taking code** | `accept_source`: size cap, strict UTF-8, no NUL, identifier entrypoint. `Sandbox.inspect`: the entrypoint's signature is read *inside the sandbox*; the host checks it against an allow-list. Nothing on the host parses or evaluates uploaded code. |
| **Taking inputs** | `Signature.bind`: form strings are converted by kind (str, int, float, file, choice), never evaluated. Uploaded files are staged under cleaned names, with a size cap. |
| **Running** | One container per run: no network, read-only root, no capabilities, CPU, memory, pids and time limits, an unprivileged uid that owns nothing on the host. The runner refuses all runs until a startup self-test proves the limits are enforced. |
| **Returning** | Every path in the result becomes an `OutputFile` (a regular file inside the run, reached without symlinks) or the run is refused. `result.json` is read without blocking or trusting its size. Downloads go through `Sandbox.output`. |
| **Line trace** | A language-neutral protocol ([docs/trace-protocol.md](docs/trace-protocol.md)) for showing which line runs, with a conformance kit. Runtimes: Python, and a Go reference. |
| **System** | The `sandboxcore-host` Debian package, a base image for app containers, and `sandboxcore-doctor` ([system/](system/README.md)). |

Standard library only, Python 3.12+. Design and trust boundary: [docs/design.md](docs/design.md).

**Languages.** The container, the args/result contract, the checks and the system setup do not depend
on the language of the uploaded code; each language needs a runtime inside the image. Python is the
first full runtime (`sandboxcore.python`). Go has the trace reference so far; its runtime will compile
inside the sandbox, under the same limits, never on the host.

## Install

Pin a release tag. The tarball needs no git in an image build:

```toml
# the app's server and sandbox pyproject.toml
dependencies = [
    "sandboxcore @ https://github.com/RaubCamaioni/sandboxcore/archive/refs/tags/v0.1.0.tar.gz",
]
```

## Use

```python
import os
from sandboxcore.host import (
    InputFile, Job, Outcome, Sandbox, SandboxBusy, SandboxConfig, SandboxUnavailable,
    Signature, SignatureError, SourceError, JobError, accept_source,
)

config = SandboxConfig.from_environment(os.environ, image="docker.io/me/my-sandbox:latest")
sandbox = Sandbox(config)
sandbox.start_in_background()          # app startup; await sandbox.stop() at shutdown

# an upload: checked, then inspected in the sandbox
source = accept_source(uploaded_bytes, uploaded_filename)       # SourceError -> 400
signature = await sandbox.inspect(source)                       # SignatureError -> 400
store(source.code, signature.to_arguments())                    # the app's database

# a run: bind the submitted form, run, use the vetted result
signature = Signature.from_arguments("python", name, stored_arguments)   # checked again
arguments, files = signature.bind({"width": "12", "photo": InputFile(filename, data)})
result = await sandbox.run(Job(source, arguments, files), on_line=page_cursor)
if result.outcome is Outcome.OK:
    result.value                       # files in it are OutputFile(run, name, size, path)
else:
    result.message                     # user-facing: timeout, memory limit, crash

# a download route
out = sandbox.output(run, name)        # None unless it is a file of a live run
```

The sandbox image installs the same package and runs its runner: the `sandboxcore-python` console
script, or a wrapper that passes a `finish` hook (the catalog exports CAD models with it):

```python
from sandboxcore.python.runner import main as run
from sandbox.export import export_model

def main():
    run(finish=export_model, finish_label="Export Error")
```

The image must keep a `python3` on PATH (the self-test runs `--entrypoint python3`) and run tools as
uid `SandboxConfig.sandbox_uid` (1001). `containers/Containerfile.sandbox` is a minimal working image.

Logs go to the `sandboxcore` logger.

## Develop

```sh
./run-tests.sh -q              # unit, Go and trace conformance tests in a container (needs podman; WSL works)
./run-integration.sh -v        # real containers: builds the reference sandbox image first
sh system/debian/build.sh      # the host package
```

Release: bump `version` in `pyproject.toml` and `__version__` in `src/sandboxcore/__init__.py`, add a
CHANGELOG entry, merge, and tag `v<version>`. CI refuses a tag that doesn't match, publishes the base
image, and builds the .deb. Then bump the pinned tag in both apps (server and sandbox).
