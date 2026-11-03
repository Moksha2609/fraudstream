"""Queue consumer: pulls transactions, scores them, publishes alerts.

Run standalone (``python -m fraudstream.consumer``) to scale scoring
independently of the API, or in-process inside the API with FS_RUN_CONSUMER=1.
"""

from __future__ import annotations

import asyncio
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


async def consume_forever(
    pipeline: ScoringPipeline,
    queue: QueueBackend,
    settings: Settings,
    stop: asyncio.Event | None = None,
    max_messages: int | None = None,
) -> int:
    """Consume until ``stop`` is set (or ``max_messages`` processed). Returns count."""
    consumer_name = settings.consumer_name
    if consumer_name == "scorer-1":  # default -> make unique per host/pid
        consumer_name = f"{socket.gethostname()}-{os.getpid()}"
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
    try:
        await consume_forever(pipeline, queue, settings)
    finally:
        await queue.close()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
