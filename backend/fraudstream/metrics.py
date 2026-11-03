"""Prometheus metrics + cheap in-process latency/throughput tracking."""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
from prometheus_client import Counter, Gauge, Histogram

SCORED = Counter("fraudstream_scored_total", "Transactions scored", ["path"])
ALERTS = Counter("fraudstream_alerts_total", "Alerts raised")
FEEDBACK = Counter("fraudstream_feedback_total", "Owner feedback received", ["label"])
SCORE_LATENCY = Histogram(
    "fraudstream_score_latency_seconds",
    "Model scoring latency (feature build + predict)",
    buckets=(0.0005, 0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)
REQUEST_LATENCY = Histogram(
    "fraudstream_request_latency_seconds",
    "End-to-end /score HTTP latency",
    buckets=(0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
DRIFT_PSI = Gauge("fraudstream_drift_psi", "Population stability index per feature", ["feature"])
DRIFT_STATUS = Gauge("fraudstream_drift_status", "0=ok 1=warn 2=drift 3=insufficient data")
QUEUE_LAG = Gauge("fraudstream_consumer_lag_seconds", "Age of the last consumed message")

_STATUS_CODE = {"ok": 0, "warn": 1, "drift": 2, "insufficient_data": 3}


class RollingStats:
    """Lock-protected rolling window of latencies and timestamps for /stats."""

    def __init__(self, maxlen: int = 10_000):
        self._lat: deque[float] = deque(maxlen=maxlen)
        self._ts: deque[float] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.scored = 0
        self.alerts = 0

    def record(self, latency_ms: float, is_alert: bool) -> None:
        with self._lock:
            self._lat.append(latency_ms)
            self._ts.append(time.time())
            self.scored += 1
            if is_alert:
                self.alerts += 1

    def snapshot(self) -> dict[str, float | int | None]:
        with self._lock:
            lat = np.fromiter(self._lat, dtype=np.float64)
            ts = np.fromiter(self._ts, dtype=np.float64)
            scored, alerts = self.scored, self.alerts
        now = time.time()
        recent = ts[ts > now - 10.0]
        throughput = len(recent) / 10.0 if len(ts) else 0.0
        return {
            "scored_total": scored,
            "alerts_total": alerts,
            "throughput_per_s": round(throughput, 2),
            "latency_p50_ms": round(float(np.percentile(lat, 50)), 3) if len(lat) else None,
            "latency_p99_ms": round(float(np.percentile(lat, 99)), 3) if len(lat) else None,
        }


def publish_drift(report) -> None:
    for f in report.features:
        DRIFT_PSI.labels(feature=f.name).set(f.psi)
    if report.score_psi is not None:
        DRIFT_PSI.labels(feature="__score__").set(report.score_psi)
    DRIFT_STATUS.set(_STATUS_CODE[report.status])
