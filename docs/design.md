# sandboxcore design

## Why this exists

PyTools (`RaubCamaioni/PyTools`) and the CadQuery Catalog (`RaubCamaioni/cadquery-catalog`) both run
code that anyone with a GitHub login can upload. The catalog started as a copy of PyTools v0.3.2, so
both carried the same sandbox, and security fixes were being made twice (PyTools v0.3.5 and catalog
v0.4.1 were the same annotation fix). This package is that shared code. A fix lands once, and both
apps take it by bumping a pinned version.

## The boundary

The core is the technical implementation of running untrusted code securely: *possibly unsafe code
and inputs in, a checked result out*. Everything on the host that touches bytes from a visitor or
from a run goes through it: uploaded code, form values, uploaded files, `result.json`, output files,
trace records. It also owns the system setup that makes the container limits real.

The apps own the website: routes, templates, sessions, the database, how results render, streaming
line numbers to a browser, tags, and formatting code for display.

## What is in it

```
sandboxcore/
  source.py        accept_source: the checks every upload passes before anything parses it
  signature.py     Signature: an --inspect report checked against the allow-list; bind() for form values
  contract.py      file names and the trace record: what the host and every runtime agree on
  serializer.py    JSON for args.json and result.json (Path values travel as {"__path__": ...})
  trace_writer.py  runtime side, any language's template: open the trace FIFO, write line records
  conformance.py   checks a runtime (any language) against the line trace protocol
  doctor.py        sandboxcore-doctor: is this system set up for safe sandboxes
  host/            the app's side; language neutral
    jobs.py        Sandbox, Job, Result, OutputFile: stage, run, vet; output() for downloads
    podman.py      PodmanRunner: startup self-test, one container per run, outcome classification
    config.py      SandboxConfig: limits from SANDBOX_* variables, with the app's default image
    trace.py       read_trace: range-checked, rate-limited line numbers from the FIFO
    rundir.py      run directories, input staging, read_run_file
  python/          the Python language
    annotations.py host side: the annotation allow-list (never evaluated) -> a language-neutral kind
    entrypoint.py  sandbox side: --inspect, the entrypoint's signature without running anything
    runner.py      sandbox side: call the entrypoint, write result.json, optional `finish` hook
    tracer.py      sandbox side: sys.monitoring LINE events into a TraceWriter
runtimes/go/       Go reference (experimental): trace writer, source instrumenter, sandboxcore-go-run
system/            sandboxcore-host .deb, the app containers' base image (see system/README.md)
```

All of it is standard library only, so the same distribution installs in the app image and in each
sandbox image.

## A run, end to end

1. **Upload.** `accept_source` bounds the size, requires strict UTF-8 without NUL, normalises newlines
   and requires the file name to be an identifier (it becomes the entrypoint and the tool's file
   name). `Sandbox.inspect` copies the code into a fresh run directory and starts the runtime with
   `--inspect`. The runtime parses the code under the sandbox's limits and reports the entrypoint's
   parameters. The host takes nothing in that report on trust: names must be identifiers, text is
   bounded, and each annotation must map through the language's allow-list to a kind (`str`, `int`,
   `float`, `file`, `choice`). The canonical form is what gets stored.
2. **Run.** The app rebuilds the `Signature` from storage (checked again) and binds the submitted
   values: strings are converted by kind, and files become `InputFile`s. `Sandbox.run` checks the job,
   creates a secret run directory, writes the code, stages uploads under `inputs/<argument>/<clean
   name>`, writes `args.json`, and hands the run to `PodmanRunner`.
3. **Contain.** One container per run: `--network=none`, `--read-only`, `--cap-drop=ALL`,
   `no-new-privileges`, CPU, memory, swap, pids and time limits, uid 1001 (a sub-uid of the
   unprivileged app user). The run directory is mounted at its own path. Afterwards the container is
   removed and the directory handed back to the app user.
4. **Return.** Everything in the run directory was written by untrusted code, `result.json` included.
   The output's total size is capped. `result.json` is read with `O_NOFOLLOW | O_NONBLOCK`, as a regular
   file of at most 16 MB; JSON nested too deeply is a crash. Every path in the value must be a regular
   file inside the run directory, reached without symlinks, and not one of the run's own files.
   Otherwise the run counts as crashed. Such paths become `OutputFile`s, and `Sandbox.output` serves
   only those, only for runs it still keeps. Run directories are removed after ten minutes and on
   `stop()`.
5. **Trace.** Line numbers come out through a FIFO as 4-byte records, range-checked against the tool's
   line count and rate-limited ([trace-protocol.md](trace-protocol.md)).

## Languages

A language is a sibling of `python/`: an allow-list that maps its annotation syntax to the neutral
kinds (`signature.KIND_OF`), and a runtime in its sandbox image that takes `--file`, `--workdir`
and `--inspect`, honours `contract.py`, and passes `sandboxcore.conformance` (the line trace
protocol). `host/` does not change. `runtimes/go` shows the trace for a compiled language: the
source is instrumented before it is built. The Go args/result contract and `--inspect` are still to do.

## The system

The self-test refuses to run anything unless the system enforces the limits, but the system has to
be set up so it can. `system/` provides the host package, the base image the app containers build
from, and `sandboxcore-doctor`, which checks every requirement and names the fix. See
[system/README.md](../system/README.md).

The container limits and why each one exists are in
[history/2026-10-19-podman-sandbox-runner-design.md](history/2026-10-19-podman-sandbox-runner-design.md),
the trace channel in [history/2026-10-19-execution-trace-design.md](history/2026-10-19-execution-trace-design.md).
Both were written for PyTools, so names in them (`app.utility.podman_runner`, `sandbox/`) are the old
locations of this code.

## Versioning and releases

Semantic versions, tagged `v<version>` to match `pyproject.toml`. Apps pin an exact tag. A change to
`contract.py`, to the runtime's command line, or to what a runtime writes needs a coordinated
release, because app images and sandbox images are published separately and the server refreshes
sandbox images without restarting the app. Keep old names working (as with `PYWEBTOOLS_TRACE`), or
bump the major version and release both images together.

## Testing

- `run-tests.sh`: the unit suite, the Go module's tests and the trace conformance of every runtime, in
  a container with Python 3.12 and Go (`containers/Containerfile.test`). The job tests run the real
  Python runtime against hostile tools.
- `run-integration.sh`: builds `containers/Containerfile.sandbox` and runs `tests/integration` against
  a real rootless podman. It needs cgroup v2 with memory and pids delegated. On WSL, each podman call
  takes about 0.5 s, so set `SANDBOX_TEST_SLACK_SECONDS=2` there.
- `system/`: build the base image, boot it with the documented flags, and run
  `sandboxcore-doctor --self-test` inside it. Install the .deb on `ubuntu:24.04` (CI does).
