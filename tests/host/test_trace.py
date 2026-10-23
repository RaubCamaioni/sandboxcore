import asyncio
import os
import struct

from sandboxcore.host.trace import read_trace


def records(*lines):
    return b"".join(struct.pack("<I", n) for n in lines)


async def test_reader_forwards_valid_lines_in_order():
    r, w = os.pipe()
    seen = []
    task = asyncio.create_task(read_trace(r, lines=10, on_line=seen.append, max_per_second=10_000))
    for n in (1, 2, 3):  # one record per tick: records sharing a tick collapse to the latest
        os.write(w, records(n))
        await asyncio.sleep(0.02)
    os.close(w)
    await task
    assert seen == [1, 2, 3]


async def test_reader_drops_invalid_and_partial_records():
    r, w = os.pipe()
    seen = []
    task = asyncio.create_task(read_trace(r, lines=10, on_line=seen.append, max_per_second=10_000))
    os.write(w, records(0, 11, 7) + b"\x01\x02")
    os.close(w)
    await task
    assert seen == [7]


async def test_reader_rate_limits_to_latest_line_per_tick():
    r, w = os.pipe()
    seen = []
    now = [0.0]

    async def sleep(s):
        now[0] += s

    task = asyncio.create_task(
        read_trace(r, lines=1000, on_line=seen.append, max_per_second=60, clock=lambda: now[0], sleep=sleep)
    )
    os.write(w, records(*range(1, 601)))
    os.close(w)
    await task
    assert len(seen) <= 60 and seen[-1] == 600


async def test_reader_survives_writer_that_never_connects():
    r, w = os.pipe()
    os.close(w)
    await read_trace(r, lines=5, on_line=lambda n: None)  # EOF immediately, returns


async def test_reader_never_raises_from_callback():
    r, w = os.pipe()

    def boom(n):
        raise RuntimeError("page gone")

    task = asyncio.create_task(read_trace(r, lines=5, on_line=boom))
    os.write(w, records(1))
    os.close(w)
    await task
