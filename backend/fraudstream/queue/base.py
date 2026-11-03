"""Abstract queue interface.

Two consumption styles are supported:

* ``consume(topic, group, consumer)`` - *work sharing*. Every message is
  delivered to exactly one consumer of the group and must be ``ack``-ed. Used by
  the scoring workers so they can be scaled horizontally.
* ``subscribe(topic)`` - *fan-out*. Every subscriber sees every message from
  the moment it subscribes; no acks. Used by API instances to push alerts to
  dashboards over Server-Sent Events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import orjson


@dataclass(slots=True)
class Message:
    id: str
    payload: dict[str, Any]


def encode(payload: dict[str, Any]) -> bytes:
    return orjson.dumps(payload)


def decode(raw: bytes | str) -> dict[str, Any]:
    return orjson.loads(raw)


class QueueBackend(ABC):
    @abstractmethod
    async def publish(self, topic: str, payload: dict[str, Any], maxlen: int | None = None) -> str:
        """Append a message; returns its id.

        ``maxlen`` caps the topic's retained length where the backend supports
        it (Redis Streams); used for high-frequency, low-value topics such as
        stats heartbeats."""

    @abstractmethod
    def consume(
        self, topic: str, group: str, consumer: str, block_ms: int = 1000
    ) -> AsyncIterator[Message]:
        """Work-sharing consumption within ``group``. Must be ack-ed."""

    @abstractmethod
    async def ack(self, topic: str, group: str, message_id: str) -> None: ...

    @abstractmethod
    def subscribe(self, topic: str, block_ms: int = 1000) -> AsyncIterator[Message]:
        """Fan-out consumption of new messages (no acks)."""

    async def close(self) -> None:  # pragma: no cover - trivial default
        return None

    async def ping(self) -> bool:  # pragma: no cover - trivial default
        return True
