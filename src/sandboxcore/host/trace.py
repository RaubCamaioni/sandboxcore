"""Execution trace: line numbers streamed out of the sandbox to the tool page.

Design: docs/design.md; history in docs/history/2026-10-19-execution-trace-design.md

The sandbox writes 4-byte little-endian line numbers to a FIFO in its run directory.
`read_trace` validates them against the tool's line count and forwards at most
`max_per_second` of them (latest wins) to a callback; `TraceChannel`/`TraceRegistry`
hand them to the page's server-sent-events stream under a per-run token.
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
QUEUE_SIZE = 1000
# an unstarted channel is forgotten after this long; a finished one shortly after its end marker
UNSTARTED_TTL = 60.0
CLOSED_TTL = 10.0


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


class TraceChannel:
    """Line numbers of one run, consumed by one server-sent-events stream. `None` marks the end."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.closed = False
        self.created = time.monotonic()
        self.closed_at: Optional[float] = None
        self.ticket = 0  # the stream holding the latest ticket is the only one served

    def attach(self) -> int:
        self.ticket += 1
        return self.ticket

    def _put(self, item):
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest: a late browser only needs recent lines
        self.queue.put_nowait(item)

    def put(self, line: int) -> None:
        if not self.closed:
            self._put(line)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.closed_at = time.monotonic()
            self._put(None)


class TraceRegistry:
    """Channels by run token. A token is single use: registering it again supersedes the old channel."""

    def __init__(self):
        self.channels: dict[str, TraceChannel] = {}

    def register(self, token: str) -> TraceChannel:
        old = self.channels.get(token)
        if old is not None:
            old.close()
        channel = TraceChannel()
        self.channels[token] = channel
        return channel

    def get(self, token: str) -> Optional[TraceChannel]:
        return self.channels.get(token)

    def close(self, token: str) -> None:
        channel = self.channels.get(token)
        if channel is not None:
            channel.close()

    def expire(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        for token, channel in list(self.channels.items()):
            if channel.closed:
                stale = now - channel.closed_at > CLOSED_TTL
            else:
                stale = now - channel.created > UNSTARTED_TTL
            if stale:
                del self.channels[token]
