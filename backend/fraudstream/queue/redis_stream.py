"""Redis Streams backend (XADD / XREADGROUP / XACK).

Redis Streams give us at-least-once delivery with consumer groups and are a
single container to run, which makes them the default broker. Swap to Kafka
with ``FS_QUEUE_BACKEND=kafka`` once throughput or retention needs grow.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from .base import Message, QueueBackend, decode, encode

log = logging.getLogger(__name__)

FIELD = b"data"


class RedisStreamQueue(QueueBackend):
    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        batch: int = 64,
        maxlen: int | None = 1_000_000,
        client: aioredis.Redis | None = None,
    ):
        self._r = client or aioredis.from_url(url)
        self._batch = batch
        self._maxlen = maxlen

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def publish(self, topic: str, payload: dict[str, Any], maxlen: int | None = None) -> str:
        msg_id = await self._r.xadd(
            topic,
            {FIELD: encode(payload)},
            maxlen=self._maxlen if maxlen is None else maxlen,
            approximate=True,
        )
        return msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)

    async def _ensure_group(self, topic: str, group: str) -> None:
        try:
            await self._r.xgroup_create(topic, group, id="0", mkstream=True)
        except ResponseError as e:  # BUSYGROUP = already exists
            if "BUSYGROUP" not in str(e):
                raise

    async def consume(
        self, topic: str, group: str, consumer: str, block_ms: int = 1000
    ) -> AsyncIterator[Message]:
        await self._ensure_group(topic, group)
        # First pass: re-deliver messages this consumer read but never acked
        # (e.g. it crashed mid-batch). Then switch to new messages (">").
        cursor = "0"
        while True:
            resp = await self._r.xreadgroup(
                group, consumer, {topic: cursor}, count=self._batch, block=block_ms
            )
            if not resp:
                cursor = ">"
                continue
            for _stream, entries in resp:
                if not entries and cursor == "0":
                    cursor = ">"
                for msg_id, fields in entries:
                    yield Message(id=_s(msg_id), payload=decode(fields[FIELD]))

    async def ack(self, topic: str, group: str, message_id: str) -> None:
        await self._r.xack(topic, group, message_id)

    async def subscribe(self, topic: str, block_ms: int = 1000) -> AsyncIterator[Message]:
        last = "$"
        while True:
            resp = await self._r.xread({topic: last}, count=self._batch, block=block_ms)
            if not resp:
                continue
            for _stream, entries in resp:
                for msg_id, fields in entries:
                    last = _s(msg_id)
                    yield Message(id=last, payload=decode(fields[FIELD]))

    async def close(self) -> None:
        await self._r.aclose()


def _s(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)
