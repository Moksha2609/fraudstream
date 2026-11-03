"""In-process queue for tests and single-process demos (no broker required)."""

from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from .base import Message, QueueBackend


class MemoryQueue(QueueBackend):
    def __init__(self) -> None:
        self._counter = itertools.count(1)
        # work-sharing: one asyncio.Queue per topic (groups share the same queue
        # for simplicity - a single process rarely needs independent groups)
        self._work: dict[str, asyncio.Queue[Message]] = defaultdict(asyncio.Queue)
        # fan-out: one queue per subscriber
        self._subs: dict[str, list[asyncio.Queue[Message]]] = defaultdict(list)
        self.acked: set[str] = set()

    async def publish(self, topic: str, payload: dict[str, Any], maxlen: int | None = None) -> str:
        msg = Message(id=str(next(self._counter)), payload=dict(payload))
        await self._work[topic].put(msg)
        for q in self._subs[topic]:
            q.put_nowait(msg)
        return msg.id

    async def consume(
        self, topic: str, group: str, consumer: str, block_ms: int = 1000
    ) -> AsyncIterator[Message]:
        q = self._work[topic]
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=block_ms / 1000)
            except TimeoutError:
                await asyncio.sleep(0)
                continue
            yield msg

    async def ack(self, topic: str, group: str, message_id: str) -> None:
        self.acked.add(message_id)

    async def subscribe(self, topic: str, block_ms: int = 1000) -> AsyncIterator[Message]:
        q: asyncio.Queue[Message] = asyncio.Queue()
        self._subs[topic].append(q)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=block_ms / 1000)
                except TimeoutError:
                    continue
        finally:
            self._subs[topic].remove(q)

    def pending(self, topic: str) -> int:
        return self._work[topic].qsize()
