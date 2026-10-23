# Changelog

## 0.1.0 (2026-10-23)

Extracted from PyTools v0.3.6 and cadquery-catalog v0.4.2, which carried the same sandbox code
(first as `containerwebcore`; renamed before the first release: the core holds no web code).

The core's boundary: untrusted code and inputs in, a checked result out; no web code.

- **Job API** (`sandboxcore.host`): `Sandbox.run(Job)` stages code, plain arguments and uploaded bytes,
  runs the tool, and turns every path it returned into an `OutputFile` (a regular file inside the run,
  reached without symlinks, not the run's own files), or refuses the run. `Sandbox.output(run, name)`
  serves downloads. Apps no longer create run directories, write args.json or trust result paths.
- **Taking code**: `accept_source` (size, UTF-8, NUL, identifier entrypoint). `Sandbox.inspect` reads the
  entrypoint's signature inside the sandbox (the runtime's `--inspect`), and `sandboxcore.signature`
  checks the report against the allow-list. `Signature.bind` converts form values by kind. The web app
  no longer parses uploaded code.
- **System**: `system/` holds the `sandboxcore-host` Debian package (rootless podman, cgroup delegation,
  `sandboxcore-adduser`), the base image the app containers build from (the nested-podman setup both
  apps repeated), and `sandboxcore-doctor`, which checks the system and self-tests the limits.
- Run directories are removed on `stop()` even when their cleanup task never started. The apps' own
  shutdown code had this gap.
- Moved out to the apps: the browser trace stream (`TraceChannel`/`TraceRegistry`) and the download path
  check (`resolve_run_file`, replaced by `Sandbox.output`).

Changes from the copies in the apps:

- **Security:** a FIFO, symlink or oversized file named `result.json` is refused through `read_run_file`,
  which opens it without blocking. Before, the host opened result.json with a blocking open, so a tool
  that ran `os.mkfifo("result.json")` hung the web app's event loop forever: nothing could ever open
  the write end, because the container was already gone.
- **Robustness:** result.json that is too deeply nested for `json` (`[[[[...`) is a crashed run instead of
  an unhandled `RecursionError` (an HTTP 500), and so is invalid UTF-8.
- `SandboxConfig.from_environment(env, image=..., run_dir=...)`: each app passes its own default image and
  run directory. With no image from either the environment or the app, it raises instead of falling back
  to PyTools' image. The default run directory is `/tmp/sandboxcore-runs`.
- `SandboxConfig.sandbox_uid` (default 1001) replaces the `SANDBOX_UID` constant.
- Run-directory helpers shared by both apps: `make_run_dir` (replaces the catalog's `PodmanRunner.new_run_dir`,
  a leftover of its warm pool), `delete_later`, `upload_path`, `resolve_run_file`, `read_run_file` (the
  catalog's `read_build_file`).
- The Python runner takes an optional `finish` hook and `finish_label` (the catalog's export step), and
  `main(argv)` accepts arguments for in-process use.
- File names and the trace record live in `sandboxcore.contract`; host and runtime no longer
  keep separate copies of the serializer or the record format.
- Logs go to the `sandboxcore` logger instead of `uvicorn.error` (see the README for routing it).
- The line trace is a written, language-neutral protocol ([docs/trace-protocol.md](docs/trace-protocol.md)):
  - the host passes the FIFO in `SANDBOXCORE_TRACE` and still in `PYWEBTOOLS_TRACE`, so images built
    before sandboxcore keep tracing; runtimes read the new name first;
  - `sandboxcore.trace_writer.TraceWriter` is the runtime's writer, apart from the Python event source
    (`python.tracer.LineTracer(tool, writer)`; `open_trace` is gone);
  - `sandboxcore.conformance` checks any runtime command against the protocol;
  - `runtimes/go`: a Go reference (writer, source instrumenter, `sandboxcore-go-run`), experimental: trace only,
    no args.json/result.json contract for Go yet.
