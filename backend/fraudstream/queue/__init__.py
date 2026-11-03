"""Pluggable message-queue backends (in-memory, Redis Streams, Kafka)."""

from __future__ import annotations

from ..config import Settings
from .base import Message, QueueBackend


def make_queue(settings: Settings) -> QueueBackend:
    """Instantiate the backend named by FS_QUEUE_BACKEND."""
    if settings.queue_backend == "memory":
        from .memory import MemoryQueue

        return MemoryQueue()
    if settings.queue_backend == "redis":
        from .redis_stream import RedisStreamQueue

        return RedisStreamQueue(settings.redis_url, batch=settings.consume_batch)
    if settings.queue_backend == "kafka":
        from .kafka import KafkaQueue

        return KafkaQueue(settings.kafka_bootstrap)
    raise ValueError(f"unknown queue backend {settings.queue_backend!r}")


__all__ = ["Message", "QueueBackend", "make_queue"]
