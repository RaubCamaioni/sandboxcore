# sandboxcore design

## Why this exists

PyTools (`RaubCamaioni/PyTools`) and the CadQuery Catalog (`RaubCamaioni/cadquery-catalog`) both run
code that anyone with a GitHub login can upload. The catalog started as a copy of PyTools v0.3.2, so
both carried the same sandbox: a podman runner, its limits and outcome rules, the line-trace channel,
the args.json/result.json contract, the entrypoint-annotation allow-list and the in-sandbox runner.
By October 2026 the copies differed only in the default image name, the catalog's `make_run_dir`
and its export step, and security fixes were being made twice (PyTools v0.3.5 / catalog v0.4.1 were
the same annotation fix). This package is that shared code, so a fix lands once and both apps take
it by bumping a pinned version.

## What is in it

```
sandboxcore/
  contract.py      file names and the trace record: what the host and every runtime agree on
  serializer.py    JSON for args.json and result.json (Path values travel as {"__path__": ...})
  host/            the web app's side; language neutral
    config.py      SandboxConfig: limits from SANDBOX_* variables, with the app's default image
    podman.py      PodmanRunner: startup self-test, one container per run, outcome classification
    trace.py       read_trace, TraceChannel, TraceRegistry: line numbers to a server-sent-events stream
    rundir.py      make_run_dir, delete_later, upload_path, resolve_run_file, read_run_file
  trace_writer.py  sandbox side, any language's template: open the trace FIFO, write line records
  conformance.py   checks a runtime (any language) against the line trace protocol
  python/          the Python language
    annotations.py host side: the entrypoint parameter allow-list, never evaluated
    runner.py      sandbox side: call the entrypoint, write result.json, optional `finish` hook
    tracer.py      sandbox side: sys.monitoring LINE events into a TraceWriter
runtimes/go/       Go reference (experimental): trace writer, source instrumenter, sandboxcore-go-run
```

It is all standard library, so the same distribution installs in the app image and in each sandbox
image. What stays in the apps: routes, templates, the database, rendering of results, the tool/model
contracts beyond "a function named like its file" (for example the catalog's `export.py` and
`build_files.checked_build`), and each app's Containerfiles.

Another language later is a sibling of `python/` (its annotation rules for the host, and a runtime
the sandbox image runs) plus a sandbox image; `host/` does not change. The runtime may be written in
that language. It only has to honour `contract.py` and the line trace protocol
([trace-protocol.md](trace-protocol.md)), which `sandboxcore.conformance` checks. `runtimes/go`
shows the trace for a compiled language: the tool's source is instrumented before it is built.

## The contract

A run directory has the same absolute path on both sides of the container boundary (it is mounted
at its own path, `-v run_dir:run_dir:U`), so paths in args.json and result.json mean the same thing in
the app and in the sandbox. The host writes the tool file, `args.json` and `inputs/<argument>/<name>`,
and creates the `trace` FIFO. The runtime writes `result.json` and whatever files the tool makes.
The sandbox entrypoint takes `--file <tool> --workdir <run_dir>`, and the trace FIFO path arrives in
`PYWEBTOOLS_TRACE` (an image that predates the trace ignores the variable).

## Trust boundary

Everything in a run directory after a run was written by untrusted code, including result.json,
because the tool runs in the same process as the runner. The host:

- reads result.json only through `read_run_file`: no symlinks (`O_NOFOLLOW`), no FIFOs or devices,
  never a blocking open (`O_NONBLOCK`), at most 16 MB, and JSON that is too deeply nested counts as a
  crash, not a server error;
- serves downloads only through `resolve_run_file`: the path is resolved (symlinks included) and must
  lie inside a run directory;
- writes uploads only through `upload_path`: the visitor's name is reduced to its last component
  and plain characters, under `inputs/<argument>/`, so it can't overwrite args.json or the tool;
- treats the result's *paths* as hints at most. An app that copies or stores a run's files (the
  catalog's default builds) opens fixed names with `read_run_file`, never the paths result.json names;
- range-checks every trace record against the tool's line count and rate-limits them.

The container limits and why each one is there are in
[history/2026-10-19-podman-sandbox-runner-design.md](history/2026-10-19-podman-sandbox-runner-design.md);
the trace channel in [history/2026-10-19-execution-trace-design.md](history/2026-10-19-execution-trace-design.md).
Both were written for PyTools. Names in them (`app.utility.podman_runner`, `sandbox/`) are the old
locations of this code.

## Versioning and releases

Semantic versions, tagged `v<version>` matching `pyproject.toml`. Apps pin an exact tag (see the
README). A change to `contract.py` or to what a runtime writes needs a coordinated release: app images
and sandbox images are published separately, and the server refreshes sandbox images without
restarting the app. So keep old names working, or bump the major version and release both images
together.

## Testing

- `run-tests.sh`: the unit suite, the Go module's tests and the trace conformance of every runtime, in
  a container with Python 3.12 and Go (`containers/Containerfile.test`). FIFOs, `loop.add_reader` and
  `sys.monitoring` need Linux and Python 3.12.
- `run-integration.sh`: builds `containers/Containerfile.sandbox` and runs `tests/integration` against
  a real rootless podman. It needs cgroup v2 with the memory and pids controllers delegated. On WSL,
  each podman call takes about 0.5 s; set `SANDBOX_TEST_SLACK_SECONDS=2` there, because two tests bound
  the total wall time of a timed-out run.
