import asyncio
import os

import pytest

from fraudstream.queue.memory import MemoryQueue
from fraudstream.queue.redis_stream import RedisStreamQueue


async def _collect(aiter, n: int, timeout: float = 3.0):
    out = []

    async def run():
        async for m in aiter:
            out.append(m)
            if len(out) >= n:
                break

    await asyncio.wait_for(run(), timeout)
    return out


async def test_memory_queue_work_sharing_and_ack():
    q = MemoryQueue()
    await q.publish("tx", {"a": 1})
    await q.publish("tx", {"a": 2})
    msgs = await _collect(q.consume("tx", "g", "c1"), 2)
    assert [m.payload["a"] for m in msgs] == [1, 2]
    await q.ack("tx", "g", msgs[0].id)
    assert msgs[0].id in q.acked


async def test_memory_queue_fanout_only_sees_new_messages():
    q = MemoryQueue()
    await q.publish("alerts", {"old": True})
    sub = q.subscribe("alerts")
    task = asyncio.create_task(_collect(sub, 1))
    await asyncio.sleep(0.05)
    await q.publish("alerts", {"new": True})
    msgs = await task
    assert msgs[0].payload == {"new": True}


@pytest.fixture
def fake_redis_queue():
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.aioredis.FakeRedis()
    return RedisStreamQueue(client=client, batch=10)


async def test_redis_stream_roundtrip(fake_redis_queue):
    q = fake_redis_queue
    ids = [await q.publish("tx", {"i": i}) for i in range(5)]
    assert all("-" in i for i in ids)  # redis stream ids look like 1700000000000-0
    msgs = await _collect(q.consume("tx", "scorer", "c1", block_ms=100), 5)
    assert [m.payload["i"] for m in msgs] == [0, 1, 2, 3, 4]
    for m in msgs:
        await q.ack("tx", "scorer", m.id)
    pending = await q._r.xpending("tx", "scorer")
    assert pending["pending"] == 0


async def test_redis_stream_redelivers_unacked_on_restart(fake_redis_queue):
    q = fake_redis_queue
    await q.publish("tx", {"i": 1})
    first = await _collect(q.consume("tx", "scorer", "c1", block_ms=100), 1)
    assert first[0].payload["i"] == 1
    # consumer "c1" crashes without acking -> on restart it gets the message again
    again = await _collect(q.consume("tx", "scorer", "c1", block_ms=100), 1)
    assert again[0].id == first[0].id


async def test_redis_stream_group_is_idempotent(fake_redis_queue):
    q = fake_redis_queue
    await q._ensure_group("tx", "g")
    await q._ensure_group("tx", "g")  # BUSYGROUP must be swallowed


@pytest.mark.redis
@pytest.mark.skipif(not os.getenv("FS_REDIS_URL"), reason="needs a real redis (FS_REDIS_URL)")
async def test_real_redis_roundtrip():
    q = RedisStreamQueue(os.environ["FS_REDIS_URL"])
    topic = f"test-{os.getpid()}"
    await q.publish(topic, {"x": 1})
    msgs = await _collect(q.consume(topic, "g", "c", block_ms=100), 1)
    assert msgs[0].payload == {"x": 1}
    await q.ack(topic, "g", msgs[0].id)
    await q._r.delete(topic)
    await q.close()
