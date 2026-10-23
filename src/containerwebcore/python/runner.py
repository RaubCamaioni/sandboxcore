from containerwebcore import serializer
from containerwebcore.python.tracer import LineTracer, open_trace

from typing import Any, Callable
from functools import partial
from pathlib import Path
import importlib.util
import traceback
import sys
import os


def runner(file: Path, workdir: Path, trace: str | None = None) -> Any:
    os.chdir(workdir)

    # optional line trace: 4-byte line numbers of the tool file to a FIFO the host reads
    tracer = None
    if trace is not None:
        fd = open_trace(trace)
        if fd is not None:
            tracer = LineTracer(file, fd)
            tracer.start()
    try:
        _run(file)
    finally:
        if tracer is not None:
            tracer.stop()
            os.close(tracer.fd)


def _run(file: Path) -> None:

    with open("args.json", "r") as f:
        args = serializer.load(f)

    module_name = file.stem
    spec = importlib.util.spec_from_file_location(module_name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, module_name):
        raise ValueError(f"no function named: {module_name}")

    func: Callable[[Any], Any] = getattr(module, module_name, None)

    try:
        results = partial(func, **args)()
    except Exception as e:
        _, _, exc_traceback = sys.exc_info()
        frames = traceback.extract_tb(exc_traceback)

        trace = []
        for frame in frames:
            trace.append(f"  {frame.name}: {frame.lineno}")
        trace = "\n".join(trace)
        results = f"Runtime Error: {file.stem} {type(e).__name__}\n{trace}"

        print(results)

    with open("result.json", "w") as f:
        serializer.dump(results, f)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", type=str, required=True)
    parser.add_argument("--workdir", "-w", type=str, required=True)
    # the host passes the trace FIFO in the environment; the flag exists for local use
    parser.add_argument("--trace", "-t", type=str, default=os.environ.get("PYWEBTOOLS_TRACE"))
    args = parser.parse_args()

    file = Path(args.file)
    workdir = Path(args.workdir)

    runner(file, workdir, args.trace)


if __name__ == "__main__":
    main()
