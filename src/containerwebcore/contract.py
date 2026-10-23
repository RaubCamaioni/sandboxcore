"""What the host and a sandbox runtime agree on. Both sides import these names.

A run directory holds, at its own absolute path on both sides of the container boundary:

    <tool file>    the code to run, written by the host
    args.json      the entrypoint's keyword arguments (see `serializer`), written by the host
    inputs/        uploaded files, written by the host
    result.json    the entrypoint's return value (see `serializer`), written by the runtime
    trace          a FIFO, created by the host; the runtime writes TRACE_RECORD line numbers to it
                   (the line trace protocol: docs/trace-protocol.md)

The host trusts nothing the runtime writes: result.json is read with size and file-type checks and
trace records are range-checked. Changing a name here changes the contract between already published
sandbox images and the app, so keep old names working or release both together.
"""

import struct

ARGS_FILE = "args.json"
RESULT_FILE = "result.json"
INPUTS_DIR = "inputs"
TRACE_FILE = "trace"
# the trace FIFO travels as an environment variable: a sandbox image that predates the trace ignores it,
# where an unknown flag would fail the run. The host sets both names: images built before containerwebcore
# read only the legacy one.
TRACE_ENV = "CONTAINERWEBCORE_TRACE"
TRACE_ENV_LEGACY = "PYWEBTOOLS_TRACE"
# one executed line number of the tool file: uint32, little-endian, 1-based
TRACE_RECORD = struct.Struct("<I")
