"""Kafka backend built on aiokafka.

Work-sharing maps onto a Kafka consumer group with manual commits; fan-out
uses a throw-away group id that starts from the latest offset.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition

from .base import Message, QueueBackend, decode, encode

log = logging.getLogger(__name__)


class KafkaQueue(QueueBackend):
    def __init__(self, bootstrap: str = "localhost:9092"):
        self._bootstrap = bootstrap
        self._producer: AIOKafkaProducer | None = None
        self._consumers: dict[tuple[str, str], AIOKafkaConsumer] = {}

    async def _get_producer(self) -> AIOKafkaProducer:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap, linger_ms=5, acks="all"
            )
            await self._producer.start()
        return self._producer

    async def ping(self) -> bool:
        try:
            await self._get_producer()
            return True
        except Exception:  # pragma: no cover - needs a broker
            return False

    async def publish(self, topic: str, payload: dict[str, Any]) -> str:
        producer = await self._get_producer()
        meta = await producer.send_and_wait(topic, encode(payload))
        return f"{meta.partition}:{meta.offset}"

    async def consume(
        self, topic: str, group: str, consumer: str, block_ms: int = 1000
    ) -> AsyncIterator[Message]:
        c = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=group,
            client_id=consumer,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await c.start()
        self._consumers[(topic, group)] = c
        try:
            while True:
                batches = await c.getmany(timeout_ms=block_ms, max_records=256)
                for _tp, records in batches.items():
                    for rec in records:
                        yield Message(id=f"{rec.partition}:{rec.offset}", payload=decode(rec.value))
        finally:
            await c.stop()
            self._consumers.pop((topic, group), None)

    async def ack(self, topic: str, group: str, message_id: str) -> None:
        c = self._consumers.get((topic, group))
        if c is None:
            return
        partition, offset = (int(x) for x in message_id.split(":"))
        await c.commit({TopicPartition(topic, partition): offset + 1})

    async def subscribe(self, topic: str, block_ms: int = 1000) -> AsyncIterator[Message]:
        c = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=f"sub-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await c.start()
        try:
            async for rec in c:
                yield Message(id=f"{rec.partition}:{rec.offset}", payload=decode(rec.value))
        finally:
            await c.stop()

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
