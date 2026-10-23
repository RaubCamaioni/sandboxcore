"""Run one Python tool inside the sandbox: call its entrypoint with args.json, write result.json.

The entrypoint is the function named like the file (`lower.py` -> `lower`). An app whose results need
work in the sandbox after the call (the CadQuery catalogue turns a model into STL/STEP files) passes a
`finish` callable to `main`; whatever it returns becomes the result, and an exception it raises
becomes an error string labelled `finish_label`.
"""

from containerwebcore import serializer
from containerwebcore.contract import ARGS_FILE, RESULT_FILE
from containerwebcore.python.tracer import LineTracer
from containerwebcore.trace_writer import TraceWriter, trace_path

from typing import Any, Callable, Optional
from functools import partial
from pathlib import Path
import importlib.util
import traceback
import sys
import os

Finish = Callable[[Any], Any]


def runner(
    file: Path,
    workdir: Path,
    trace: str | None = None,
    finish: Optional[Finish] = None,
    finish_label: str = "Finish Error",
) -> Any:
    os.chdir(workdir)

    # optional line trace (docs/trace-protocol.md): line numbers of the tool file to a FIFO the host reads
    tracer = None
    writer = TraceWriter.open(trace) if trace else None
    if writer is not None:
        tracer = LineTracer(file, writer)
        tracer.start()
    try:
        _run(file, finish, finish_label)
    finally:
        if tracer is not None:
            tracer.stop()
            writer.close()


def _run(file: Path, finish: Optional[Finish], finish_label: str) -> None:

    with open(ARGS_FILE, "r") as f:
        args = serializer.load(f)

    module_name = file.stem
    spec = importlib.util.spec_from_file_location(module_name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, module_name):
        raise ValueError(f"no function named: {module_name}")

    func: Callable[[Any], Any] = getattr(module, module_name, None)

    failed = False
    try:
        results = partial(func, **args)()
    except Exception as e:
        failed = True
        _, _, exc_traceback = sys.exc_info()
        frames = traceback.extract_tb(exc_traceback)

        trace = []
        for frame in frames:
            trace.append(f"  {frame.name}: {frame.lineno}")
        trace = "\n".join(trace)
        results = f"Runtime Error: {file.stem} {type(e).__name__}\n{trace}"

        print(results)

    # the app's own step on a successful result (e.g. exporting a CAD model to files); a tool error
    # string is reported as it is
    if finish is not None and not failed:
        try:
            results = finish(results)
        except Exception as e:
            results = f"{finish_label}: {type(e).__name__}: {e}"
            print(results)

    with open(RESULT_FILE, "w") as f:
        serializer.dump(results, f)


def main(argv: Optional[list[str]] = None, finish: Optional[Finish] = None, finish_label: str = "Finish Error"):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", type=str, required=True)
    parser.add_argument("--workdir", "-w", type=str, required=True)
    # the host passes the trace FIFO in the environment; the flag exists for local use
    parser.add_argument("--trace", "-t", type=str, default=trace_path())
    args = parser.parse_args(argv)

    file = Path(args.file)
    workdir = Path(args.workdir)

    runner(file, workdir, args.trace, finish, finish_label)


if __name__ == "__main__":
    main()
