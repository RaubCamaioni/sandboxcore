# Execution trace: live cursor line and progress bar

Date: 2026-10-19
Status: design for review
Builds on: `2026-10-19-podman-sandbox-runner-design.md` (the sandbox runner this extends)

## Goal

While a tool runs, the tool page shows the line the sandbox is actually executing (vim
cursor line in the code view) and a progress bar that only ever advances. The only
thing a sandbox can send out is a stream of line numbers; everything else about the
sandbox (no network, limits, isolation) is unchanged.

Success: running `container_with_lid` on pywebtools.com shows the cursor moving through
the real lines as CadQuery builds the model, the bar reaches 100% exactly when the
result renders, and a tool that floods the channel neither slows the app nor escapes
its own 30-second budget.

## The channel

- **Transport:** a named pipe (FIFO) at `<run dir>/trace`, created by the app before
  the container starts. The run directory is already bind-mounted at its own path, so
  the sandbox sees `/tmp/pywebtools-runs/<id>/trace`. No sockets, no network; the
  sandbox keeps `--network=none`.
- **Direction and payload:** sandbox → app only. Each record is exactly 4 bytes: an
  unsigned little-endian line number. The app never interprets anything else: records
  outside `1 ≤ line ≤ <number of lines in the tool file>` are dropped, partial records
  are discarded, and nothing in the stream can name files, paths or text.
- **Ownership:** the FIFO is created by the app, then re-owned to the sandbox user with
  the rest of the run directory (`:U`), so the sandbox can open it for writing. The app
  opens the read end non-blocking *before* `podman run`; the sandbox opens the write
  end non-blocking (`O_WRONLY|O_NONBLOCK`), which fails harmlessly if no reader exists.
- **Flooding:** the writer never blocks; when the 64 KB pipe buffer is full, records are
  dropped on the sandbox side (`EAGAIN`). The app reads in 4 KB chunks, keeps only the
  latest line per 1/60 s tick, and forwards at most 60 events per second to the browser.
  A tool emitting millions of events per second burns its own CPU budget and nothing
  else.
- **Lifetime:** the reader task is cancelled when the run ends (result, timeout, OOM or
  crash) and the FIFO is removed with the run directory by the existing cleanup. The
  trace never changes a run's outcome: any error in the trace path is logged and the
  run continues without a trace.

## Sandbox side

`sandbox/src/sandbox/runner.py` gains `--trace <path>`, defaulting to the `PYWEBTOOLS_TRACE`
environment variable, which is how the app passes it (`podman run -e PYWEBTOOLS_TRACE=<fifo>`):
an older sandbox image ignores the variable, so the two images never have to be deployed in
lock-step. When given, the runner installs
a `sys.monitoring` LINE callback (Python 3.12, cheaper than `sys.settrace`) restricted
to code objects whose filename is the tool file, so library code (numpy, cadquery…) is
not traced. The callback writes the 4-byte record with `os.write` on the non-blocking
fd and ignores `BlockingIOError`. The hook is installed before the tool module is
executed and removed after the result is written. Tools cannot see the fd (it is not
inherited as stdin/stdout, and the FIFO path is only in the run directory, which they
could write to anyway; see Flooding). Without `--trace` the runner behaves exactly as
today.

## App side

- `PodmanRunner.run(tool, run_dir, on_line: Callable[[int], None] | None = None)`:
  when `on_line` is given the runner creates the FIFO, opens the read end, passes
  `--trace <fifo>` after `--workdir`, and runs a reader task that validates records
  (against `tool` line count) and calls `on_line` at most 60 times per second with the
  latest line. The reader stops when the container exits; it is wrapped so it can never
  raise into the run.
- **Run token:** the tool page sends a random 32-character token (`crypto.getRandomValues`,
  generated per run) in the `X-Trace-Token` request header of the run POST, so it can never
  collide with a tool argument or leak into the share link. The run route
  registers a `TraceChannel` (an `asyncio.Queue` of line numbers) under that token for
  the run's duration, passes `on_line` = `channel.put`, and closes the channel with an
  `end` marker when the run finishes. Tokens are capabilities: 192 bits of randomness,
  single use, forgotten after the run or after 60 s unregistered.
- **SSE endpoint:** `GET /trace/{token}` streams `text/event-stream` events
  `line: <n>` and finally `end`. If the channel is not registered yet it waits up to
  10 s for the run to start, then ends. One connection per token; a second connection
  for the same token ends the first. Connections end when the run ends, and any stream
  attached to an already finished channel ends at once.
- **Ordering:** the page opens the EventSource on `htmx:beforeRequest` (before the POST
  is sent), so the stream is normally attached before the first line executes; events
  that arrive before the browser attaches are kept in the queue (bounded at 1,000;
  older ones dropped).

## Page behaviour

- Cursor line: on each `line` event the vim cursor moves to that line and scrolls into
  view (throttled to one move per animation frame). The `-- RUNNING --` mode stays.
- Progress bar: `width = max(line seen so far) / total lines`. It never decreases; a
  loop keeps it still until a later line is reached. On the result (htmx
  `afterRequest`) it jumps to 100% and fades out; on failure it also completes, so the
  bar never hangs.
- Fallback: if the trace stream never connects or delivers no events (sandbox older
  than this feature, FIFO error), the page falls back to today's illustrative stepping
  and indeterminate bar after 1 s without events.

## Limits and non-goals

- Only the tool's own file is traced; while a C extension or library call runs, the
  cursor stays on the calling line.
- Subprocesses spawned by a tool are not traced.
- No timing, variable or output data ever leaves the sandbox through this channel.
- Validation runs (future runtime-selection work) can reuse the same channel on the
  configure panel; not in this design.

## Testing

- **Sandbox unit** (`sandbox/tests/test_trace.py`, run in the sandbox venv): executing
  a 6-line tool under the tracer writes the expected sequence of line numbers to a pipe;
  library frames produce no records; a full pipe drops records without raising.
- **App unit:** FIFO reader with a fake writer — accepts valid records, drops
  out-of-range and partial ones, forwards ≤ 60/s (fake clock), survives a writer that
  never connects; `TraceChannel` registration/expiry; `/trace/{token}` streams `line`
  events then `end`, unknown token ends within the wait, second connection supersedes
  the first; the run route registers the token from the form and closes the channel on
  every outcome (ok, timeout, oom, crash, busy).
- **Integration** (inside the app container): a tool with a `for` loop produces
  monotonically plausible events in order and the stream ends with the result; the
  flooding tool (a `while True: pass` over ten lines) stays within the 30-second limit
  and the reader never exceeds 60 events/s.
- **Deploy smoke:** run `container_with_lid` in Chrome; the cursor moves through the
  file during the run and the bar reaches 100% with the STL result.

## Rollout

Sandbox image first (the runner gains `--trace`; old apps ignore it), then the app.
The app treats a sandbox without `--trace` support as "no events" and falls back.
