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


class StatsAggregator:
    """Merges this process's RollingStats with heartbeats from other scoring processes.

    In a multi-process deployment the API serves HTTP while separate consumer
    processes do the scoring, so the API's own counters would read zero. Each
    standalone consumer publishes a heartbeat (``consumer.stats_heartbeat``)
    with its counters, latency percentiles and drift report; the API ingests
    them here and reports fleet-wide numbers on /stats and /drift.
    """

    def __init__(self, own: RollingStats, stale_after: float = 10.0):
        self.own = own
        self.stale_after = stale_after
        self._remote: dict[str, tuple[dict, float]] = {}
        self._lock = threading.Lock()

    def ingest(self, heartbeat: dict) -> None:
        source = heartbeat.get("source")
        if not source:
            return
        with self._lock:
            self._remote[source] = (dict(heartbeat), time.time())

    def _remotes(self) -> list[tuple[dict, bool]]:
        now = time.time()
        with self._lock:
            return [(hb, now - at <= self.stale_after) for hb, at in self._remote.values()]

    def snapshot(self) -> dict[str, float | int | None]:
        own = self.own.snapshot()
        remotes = self._remotes()
        scored = int(own["scored_total"] or 0)
        alerts = int(own["alerts_total"] or 0)
        throughput = float(own["throughput_per_s"] or 0.0)
        p50: list[tuple[float, int]] = []
        p99: list[float] = []
        if own["latency_p50_ms"] is not None:
            p50.append((float(own["latency_p50_ms"]), max(scored, 1)))
            p99.append(float(own["latency_p99_ms"]))
        workers = 1 if scored else 0
        for hb, fresh in remotes:
            n = int(hb.get("scored_total") or 0)
            scored += n
            alerts += int(hb.get("alerts_total") or 0)
            if fresh:
                throughput += float(hb.get("throughput_per_s") or 0.0)
                workers += 1
            if hb.get("latency_p50_ms") is not None:
                p50.append((float(hb["latency_p50_ms"]), max(n, 1)))
                p99.append(float(hb["latency_p99_ms"]))
        weight = sum(w for _, w in p50)
        return {
            "scored_total": scored,
            "alerts_total": alerts,
            "throughput_per_s": round(throughput, 2),
            "latency_p50_ms": round(sum(v * w for v, w in p50) / weight, 3) if weight else None,
            "latency_p99_ms": round(max(p99), 3) if p99 else None,
            "workers": workers,
        }

    def remote_drift_reports(self) -> list[dict]:
        """Latest drift report from every fresh remote worker."""
        return [hb["drift"] for hb, fresh in self._remotes() if fresh and hb.get("drift")]


def publish_drift(report) -> None:
    for f in report.features:
        DRIFT_PSI.labels(feature=f.name).set(f.psi)
    if report.score_psi is not None:
        DRIFT_PSI.labels(feature="__score__").set(report.score_psi)
    DRIFT_STATUS.set(_STATUS_CODE[report.status])
