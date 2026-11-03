"""The scoring pipeline step shared by the HTTP endpoint and the queue consumer.

score -> record metrics -> update drift window -> persist + publish alert.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from . import metrics
from .drift import DriftMonitor
from .features import features_to_dict
from .model import FraudModel
from .queue import QueueBackend
from .schemas import Alert, ScoreResponse, Transaction
from .store import AlertStore

log = logging.getLogger(__name__)


@dataclass
class ScoringPipeline:
    model: FraudModel
    store: AlertStore
    drift: DriftMonitor
    queue: QueueBackend | None
    alert_topic: str
    rolling: metrics.RollingStats

    def score_sync(
        self, tx: Transaction, path: str = "http"
    ) -> tuple[ScoreResponse, dict[str, float]]:
        """CPU-only part: model inference + in-memory bookkeeping. Never blocks on I/O."""
        resp, row = self.model.score_transaction(tx)
        feats = features_to_dict(row)
        metrics.SCORED.labels(path=path).inc()
        metrics.SCORE_LATENCY.observe(resp.latency_ms / 1000.0)
        self.rolling.record(resp.latency_ms, resp.is_alert)
        self.drift.observe(feats, resp.score)
        return resp, feats

    def persist_alert(self, tx: Transaction, resp: ScoreResponse, feats: dict[str, float]) -> Alert:
        metrics.ALERTS.inc()
        return self.store.add_alert(
            tx_id=tx.tx_id,
            score=resp.score,
            threshold=resp.threshold,
            amount=tx.amount,
            tx_time=tx.time,
            model_version=resp.model_version,
            features=feats,
            true_label=tx.true_label,
        )

    async def process(
        self, tx: Transaction, path: str = "queue"
    ) -> tuple[ScoreResponse, Alert | None]:
        """Full async step used by the consumer and the HTTP endpoints."""
        resp, feats = self.score_sync(tx, path=path)
        alert: Alert | None = None
        if resp.is_alert:
            alert = await asyncio.to_thread(self.persist_alert, tx, resp, feats)
            if self.queue is not None:
                await self.queue.publish(self.alert_topic, alert.model_dump(mode="json"))
        return resp, alert
