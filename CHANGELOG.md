# Changelog

## 0.1.0 (2026-10-23)

Extracted from PyTools v0.3.6 and cadquery-catalog v0.4.2, which carried the same sandbox code.

Changes from the copies in the apps:

- **Security:** a FIFO, symlink or oversized file named `result.json` is refused through `read_run_file`,
  which opens it without blocking. Before, the host opened result.json with a blocking open, so a tool
  that ran `os.mkfifo("result.json")` hung the web app's event loop forever: nothing could ever open
  the write end, because the container was already gone.
- **Robustness:** result.json that is too deeply nested for `json` (`[[[[...`) is a crashed run instead of
  an unhandled `RecursionError` (an HTTP 500), and so is invalid UTF-8.
- `SandboxConfig.from_environment(env, image=..., run_dir=...)`: each app passes its own default image and
  run directory. With no image from either the environment or the app, it raises instead of falling back
  to PyTools' image. The default run directory is `/tmp/containerwebcore-runs`.
- `SandboxConfig.sandbox_uid` (default 1001) replaces the `SANDBOX_UID` constant.
- Run-directory helpers shared by both apps: `make_run_dir` (replaces the catalog's `PodmanRunner.new_run_dir`,
  a leftover of its warm pool), `delete_later`, `upload_path`, `resolve_run_file`, `read_run_file` (the
  catalog's `read_build_file`).
- The Python runner takes an optional `finish` hook and `finish_label` (the catalog's export step), and
  `main(argv)` accepts arguments for in-process use.
- File names and the trace record live in `containerwebcore.contract`; host and runtime no longer
  keep separate copies of the serializer or the record format.
- Logs go to the `containerwebcore` logger instead of `uvicorn.error` (see the README for routing it).
