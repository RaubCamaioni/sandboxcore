import asyncio
import os
import struct

from sandboxcore.host.trace import TraceChannel, TraceRegistry, read_trace


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


def test_channel_queue_is_bounded():
    ch = TraceChannel()
    for n in range(1, 1500):
        ch.put(n)
    assert ch.queue.qsize() == 1000 and ch.queue.get_nowait() == 500


def test_channel_close_marks_end():
    ch = TraceChannel()
    ch.put(3)
    ch.close()
    assert ch.closed and ch.queue.get_nowait() == 3 and ch.queue.get_nowait() is None
    ch.put(4)  # ignored after close
    assert ch.queue.empty()


def test_registry_replaces_and_expires():
    reg = TraceRegistry()
    a = reg.register("tok")
    b = reg.register("tok")
    assert a.closed and reg.get("tok") is b
    reg.expire(now=b.created + 61)
    assert reg.get("tok") is None


def test_registry_close_and_expire_closed():
    reg = TraceRegistry()
    ch = reg.register("tok")
    reg.close("tok")
    assert ch.closed and reg.get("tok") is ch  # still readable by a late client for a while
    reg.expire(now=ch.created + 11)
    assert reg.get("tok") is None


def test_channel_attach_supersedes_earlier_stream():
    ch = TraceChannel()
    first = ch.attach()
    second = ch.attach()
    assert first != second and ch.ticket == second
