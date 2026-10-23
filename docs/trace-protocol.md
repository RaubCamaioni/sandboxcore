# Line trace protocol, version 1

The tool page highlights the line of the uploaded file that is running, live. The host knows nothing
about the language. It reads line numbers from a FIFO. Each language runtime decides *how* it learns
which line runs (Python: `sys.monitoring`; Go: source instrumentation), and every runtime writes the
same records described here. A runtime that follows this document works with every app on the core.

`containerwebcore.conformance` checks a runtime against these rules (see [Checking a runtime](#checking-a-runtime)).
Every **must** below is one of its checks.

## Roles

- **Host**: the web app (`containerwebcore.host`). It creates the FIFO, starts the container, reads
  records, and forwards them to the page.
- **Runtime**: the program the sandbox image runs. It runs the uploaded tool and writes records.

## Discovery

The host passes the FIFO's absolute path in the environment:

| Variable | Meaning |
|---|---|
| `CONTAINERWEBCORE_TRACE` | Path of the trace FIFO. Read this one. |
| `PYWEBTOOLS_TRACE` | The same path under its original name. The host sets both, so sandbox images built before containerwebcore keep tracing. A runtime may read it as a fallback. |

When neither is set, or the value is empty, the run is not traced. The runtime **must** then write
nothing, open nothing and run the tool normally. The path arrives in the environment, not as a flag:
an image that predates a variable ignores it, where an unknown flag would fail the run.

## Transport

The FIFO lies in the run directory and is mode 0666, because the sandbox user is a different uid.
Before the container starts, the host opens the FIFO's read end *and* a write end of its own. Because
the host holds a write end:

- the runtime's open never waits for a reader;
- the host sees EOF only when it closes its own write end, after the container has exited, so a
  runtime that opens late, closes early or never opens is harmless.

The runtime opens the path **write-only and non-blocking** (`O_WRONLY | O_NONBLOCK`). If the open
fails for any reason (missing path, no reader: `ENXIO`, permissions), the runtime **must** run the
tool untraced and succeed as it would have otherwise.

## Records

A record is one unsigned 32-bit integer, **little-endian**, 4 bytes: a 1-based line number of the
tool file as uploaded. Lines of the runtime's own code, of libraries, or of generated code never
appear. If a runtime rewrites the tool (instrumentation), it reports the *original* line numbers.

The runtime writes each record with a single `write(2)` of 4 bytes. 4 is less than `PIPE_BUF`, so a
record is never split or interleaved, even when several threads or goroutines write at once.

## Semantics

- A record means "this line is about to run". Emit one each time execution reaches a line that has a
  statement, including each loop iteration. Repeats are expected.
- Records follow execution order. Out of the box, Python reports every line, including a loop
  header's on every iteration. A runtime may be coarser (statement starts only), but **must** report
  at least each statement line that runs, at least once.
- Pace: the runtime does not rate-limit. The host keeps only the latest line and forwards at most
  60 per second, and it drops any record outside `1..lines in the tool file`.

## Never slow or fail the tool

The trace is a nicety. A runtime **must**:

- write without blocking. When the pipe is full (`EAGAIN`), drop the record and carry on;
- ignore every write error (`EPIPE`, `EBADF`, ...). A broken pipe must not end the process
  (Python ignores `SIGPIPE`; Go's runtime only exits on `SIGPIPE` for fds 1 and 2);
- never raise or print into the tool's output because of the trace.

The FIFO's buffer is 64 KB (16,384 records). A tight loop fills it within milliseconds while the
host drains it, so dropping is normal, not an error.

## End of trace

The runtime closes the write end when the tool finishes, or simply exits. The host ends the page's
stream once the container has exited and the FIFO has drained, whatever the runtime did.

## Changing the protocol

Never change the meaning of version 1. Images and apps are released separately, and the server
refreshes sandbox images without restarting the app. A new record format gets a new variable (for
example `CONTAINERWEBCORE_TRACE_V2`) that old runtimes ignore. The host offers both until no image
in use needs version 1.

## Implementing a runtime

1. Find the trace path: `CONTAINERWEBCORE_TRACE`, else `PYWEBTOOLS_TRACE`; empty or unset means no trace.
2. Open it `O_WRONLY | O_NONBLOCK | O_CLOEXEC`. On failure, run untraced.
3. Get a callback, or insert a call, at each statement of the tool file, carrying its original line.
4. In it: `write(fd, uint32_le(line))`, ignoring every error.
5. Run the tool; close the fd (or exit).

Steps 1, 2, 4 and 5 are about 30 lines in any language. Step 3 is the language-specific part:

| Language kind | How to get line events | Reference |
|---|---|---|
| Python 3.12+ | `sys.monitoring` LINE events, filtered to the tool file's code objects | `containerwebcore.python.tracer` |
| Go and other compiled languages | Rewrite the source before compiling: insert `trace.Line(n);` in front of every statement in a statement list, on the same line so positions don't move | `runtimes/go` |
| Other interpreters | The interpreter's debug or line hook (JavaScript: an instrumenting transform; Ruby: `TracePoint(:line)`; Lua: `debug.sethook`) | |

The writer half for Python is `containerwebcore.trace_writer.TraceWriter`, and for Go it is
`runtimes/go/trace`. Use one of them as the template for a new language.

## Checking a runtime

`containerwebcore.conformance` runs any runtime command against a real FIFO, the way the host does, and
checks every **must** above:

```python
from containerwebcore.conformance import Runtime, check_runtime

runtime = Runtime(
    name="go",
    suffix=".go",
    command=lambda tool, workdir: ["cwc-go-run", "--file", str(tool), "--workdir", str(workdir)],
    probe=GO_PROBE,      # a tool whose statement lines are known
    probe_lines=[6, 7, 9],
    flood=GO_FLOOD,      # a tool that runs one line hundreds of thousands of times
)
check_runtime(runtime, tmp_path)  # AssertionError naming the rule a runtime breaks
```

`tests/conformance/` runs it for every runtime in this repository.
