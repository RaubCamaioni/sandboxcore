"""Execution trace, host side: read line numbers out of the sandbox's trace FIFO, safely.

Design: docs/trace-protocol.md; history in docs/history/2026-10-19-execution-trace-design.md

The sandbox writes 4-byte little-endian line numbers to a FIFO in its run directory.
`read_trace` validates them against the tool's line count and forwards at most
`max_per_second` of them (latest wins) to a callback. Getting them to a browser is the app's job.
"""

import asyncio
import logging
import os
import time
from typing import Callable, Optional

from sandboxcore.contract import TRACE_RECORD as RECORD

logger = logging.getLogger("sandboxcore")

READ_CHUNK = 4096
MAX_EVENTS_PER_SECOND = 60


async def read_trace(
    fd: int,
    lines: int,
    on_line: Callable[[int], None],
    *,
    max_per_second: int = MAX_EVENTS_PER_SECOND,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], "asyncio.Future"] = asyncio.sleep,
) -> None:
    """Forward validated line numbers from `fd` until EOF or error. Never raises."""
    loop = asyncio.get_running_loop()
    buf = bytearray()
    latest: Optional[int] = None
    done = asyncio.Event()
    data = asyncio.Event()

    def on_readable():
        nonlocal latest
        try:
            chunk = os.read(fd, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:
            loop.remove_reader(fd)
            done.set()
            data.set()
            return
        buf.extend(chunk)
        whole = len(buf) - len(buf) % RECORD.size
        for offset in range(0, whole, RECORD.size):
            (line,) = RECORD.unpack_from(buf, offset)
            if 1 <= line <= lines:
                latest = line
        del buf[:whole]
        data.set()

    try:
        os.set_blocking(fd, False)
        loop.add_reader(fd, on_readable)
    except OSError as e:
        logger.warning(f"trace reader not started: {e}")
        return

    interval = 1.0 / max_per_second
    last_sent = float("-inf")
    try:
        while True:
            if latest is None:
                if done.is_set():
                    break
                await data.wait()
                data.clear()
                continue
            now = clock()
            wait = last_sent + interval - now
            if wait > 0:
                await sleep(wait)  # records arriving meanwhile replace `latest`
                now = clock()
            line, latest = latest, None
            last_sent = now
            try:
                on_line(line)
            except Exception as e:
                logger.warning(f"trace callback failed: {e}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"trace reader stopped: {e}")
    finally:
        if not done.is_set():
            loop.remove_reader(fd)
