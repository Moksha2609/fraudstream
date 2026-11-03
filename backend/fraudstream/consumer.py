"""Queue consumer: pulls transactions, scores them, publishes alerts.

Run standalone (``python -m fraudstream.consumer``) to scale scoring
independently of the API, or in-process inside the API with FS_RUN_CONSUMER=1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time

from pydantic import ValidationError

from . import metrics
from .config import Settings, get_settings
from .drift import SCORE_KEY, DriftMonitor
from .model import FraudModel
from .queue import QueueBackend, make_queue
from .schemas import Transaction
from .scoring import ScoringPipeline
from .store import AlertStore

log = logging.getLogger(__name__)


def build_pipeline(settings: Settings, queue: QueueBackend | None) -> ScoringPipeline:
    model = FraudModel.load(settings.model_dir, settings.model_name, settings.alert_threshold)
    store = AlertStore(settings.database_url)
    reference = {k: v for k, v in model.meta.reference.items()}
    drift = DriftMonitor(
        reference=reference,
        window=settings.drift_window,
        warn=settings.drift_psi_warn,
        alert=settings.drift_psi_alert,
        reference_alert_rate=model.meta.reference_alert_rate,
        threshold=model.threshold,
    )
    if SCORE_KEY not in reference:
        log.warning("model metadata has no score reference distribution; score PSI disabled")
    return ScoringPipeline(
        model=model,
        store=store,
        drift=drift,
        queue=queue,
        alert_topic=settings.alert_topic,
        rolling=metrics.RollingStats(),
    )


def resolve_consumer_name(settings: Settings) -> str:
    """The configured consumer name, or a host+pid default that is unique per process."""
    if settings.consumer_name == "scorer-1":
        return f"{socket.gethostname()}-{os.getpid()}"
    return settings.consumer_name


async def stats_heartbeat(
    pipeline: ScoringPipeline,
    queue: QueueBackend,
    settings: Settings,
    source: str,
    stop: asyncio.Event,
    interval: float | None = None,
) -> None:
    """Publish this worker's counters, latency and drift report every ``interval`` s.

    The API subscribes to ``settings.stats_topic`` and merges heartbeats from
    all workers (see ``metrics.StatsAggregator``)."""
    interval = settings.stats_interval_s if interval is None else interval
    while not stop.is_set():
        payload = {
            "source": source,
            "ts": time.time(),
            **pipeline.rolling.snapshot(),
            "drift": pipeline.drift.report().model_dump(mode="json"),
        }
        try:
            await queue.publish(settings.stats_topic, payload, maxlen=1000)
        except Exception:  # broker hiccup: never let stats kill the worker
            log.warning("stats heartbeat failed", exc_info=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def consume_forever(
    pipeline: ScoringPipeline,
    queue: QueueBackend,
    settings: Settings,
    stop: asyncio.Event | None = None,
    max_messages: int | None = None,
    consumer_name: str | None = None,
) -> int:
    """Consume until ``stop`` is set (or ``max_messages`` processed). Returns count."""
    consumer_name = consumer_name or resolve_consumer_name(settings)
    log.info(
        "consumer %s joining group %s on %s",
        consumer_name,
        settings.consumer_group,
        settings.tx_topic,
    )
    n = 0
    bad = 0
    async for msg in queue.consume(settings.tx_topic, settings.consumer_group, consumer_name):
        if stop is not None and stop.is_set():
            break
        try:
            tx = Transaction.model_validate(msg.payload)
        except ValidationError as e:
            bad += 1
            log.warning("dropping malformed message %s: %s", msg.id, e.errors()[:1])
            await queue.ack(settings.tx_topic, settings.consumer_group, msg.id)
            continue
        sent_at = msg.payload.get("_sent_at")
        if sent_at:
            metrics.QUEUE_LAG.set(max(0.0, time.time() - float(sent_at)))
        resp, alert = await pipeline.process(tx, path="queue")
        await queue.ack(settings.tx_topic, settings.consumer_group, msg.id)
        n += 1
        if alert is not None:
            log.info("ALERT tx=%s score=%.3f amount=%.2f", tx.tx_id, resp.score, tx.amount)
        if n % 1000 == 0:
            log.info("scored %d messages (%d malformed)", n, bad)
        if max_messages is not None and n >= max_messages:
            break
    return n


async def _main_async() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    queue = make_queue(settings)
    pipeline = build_pipeline(settings, queue)
    stop = asyncio.Event()
    name = resolve_consumer_name(settings)
    tasks: list[asyncio.Task] = []
    if settings.stats_interval_s > 0:
        tasks.append(
            asyncio.create_task(
                stats_heartbeat(pipeline, queue, settings, name, stop), name="stats-heartbeat"
            )
        )
    try:
        await consume_forever(pipeline, queue, settings, stop, consumer_name=name)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await queue.close()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
