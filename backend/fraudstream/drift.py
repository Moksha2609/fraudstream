"""Model drift monitoring with the Population Stability Index (PSI).

For every feature (and for the model score itself) we keep a sliding window of
the most recent live values and compare their histogram with the histogram of
the training data, using the bin edges stored in the model metadata.

PSI rule of thumb:  < 0.1 stable · 0.1-0.2 moderate shift · > 0.2 significant
shift -> investigate / retrain.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime

import numpy as np

from .schemas import DriftFeature, DriftReport

SCORE_KEY = "__score__"
_EPS = 1e-6


def histogram_reference(values: np.ndarray, bins: int = 10) -> dict[str, list[float]]:
    """Quantile-based bin edges + relative frequencies for a reference sample."""
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    qs = np.linspace(0, 1, bins + 1)
    edges = np.unique(np.quantile(values, qs))
    if len(edges) < 3:  # degenerate feature: fall back to a linear grid
        lo, hi = float(values.min()), float(values.max())
        edges = np.linspace(lo, hi if hi > lo else lo + 1.0, bins + 1)
    counts, _ = np.histogram(values, bins=edges)
    freq = counts / max(counts.sum(), 1)
    return {"edges": edges.tolist(), "freq": freq.tolist()}


def psi(expected_freq: np.ndarray, actual_freq: np.ndarray) -> float:
    e = np.clip(np.asarray(expected_freq, dtype=np.float64), _EPS, None)
    a = np.clip(np.asarray(actual_freq, dtype=np.float64), _EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_from_values(reference: dict[str, list[float]], values: np.ndarray) -> float:
    edges = np.asarray(reference["edges"], dtype=np.float64)
    counts, _ = np.histogram(np.asarray(values, dtype=np.float64), bins=edges)
    # values outside the reference range are attributed to the outer bins
    vals = np.asarray(values, dtype=np.float64)
    counts[0] += int((vals < edges[0]).sum())
    counts[-1] += int((vals > edges[-1]).sum())
    actual = counts / max(counts.sum(), 1)
    return psi(np.asarray(reference["freq"]), actual)


class DriftMonitor:
    """Thread-safe sliding-window drift detector."""

    def __init__(
        self,
        reference: dict[str, dict[str, list[float]]],
        window: int = 5000,
        warn: float = 0.1,
        alert: float = 0.2,
        min_observations: int = 200,
        reference_alert_rate: float | None = None,
        threshold: float = 0.5,
    ):
        self.reference = reference
        self.window = window
        self.warn = warn
        self.alert = alert
        self.min_observations = min_observations
        self.reference_alert_rate = reference_alert_rate
        self.threshold = threshold
        self._lock = threading.Lock()
        self._values: dict[str, deque[float]] = {
            k: deque(maxlen=window) for k in reference if k != SCORE_KEY
        }
        self._scores: deque[float] = deque(maxlen=window)
        self._observed = 0

    def observe(self, features: dict[str, float], score: float) -> None:
        with self._lock:
            for name, dq in self._values.items():
                v = features.get(name)
                if v is not None:
                    dq.append(float(v))
            self._scores.append(float(score))
            self._observed += 1

    def _status(self, value: float) -> str:
        if value >= self.alert:
            return "drift"
        if value >= self.warn:
            return "warn"
        return "ok"

    def report(self) -> DriftReport:
        with self._lock:
            snapshot = {k: np.fromiter(dq, dtype=np.float64) for k, dq in self._values.items()}
            scores = np.fromiter(self._scores, dtype=np.float64)
            observed = self._observed
        now = datetime.now(UTC)
        if len(scores) < self.min_observations:
            return DriftReport(
                window_size=self.window,
                observed=observed,
                status="insufficient_data",
                score_psi=None,
                alert_rate=float((scores >= self.threshold).mean()) if len(scores) else None,
                reference_alert_rate=self.reference_alert_rate,
                features=[],
                drifted_features=[],
                computed_at=now,
            )
        feats: list[DriftFeature] = []
        for name, vals in snapshot.items():
            if len(vals) == 0:
                continue
            value = psi_from_values(self.reference[name], vals)
            feats.append(DriftFeature(name=name, psi=round(value, 4), status=self._status(value)))
        feats.sort(key=lambda f: f.psi, reverse=True)
        score_psi = None
        if SCORE_KEY in self.reference:
            score_psi = round(psi_from_values(self.reference[SCORE_KEY], scores), 4)
        worst = max([f.psi for f in feats] + [score_psi or 0.0])
        drifted = [f.name for f in feats if f.status == "drift"]
        return DriftReport(
            window_size=self.window,
            observed=observed,
            status=self._status(worst),  # type: ignore[arg-type]
            score_psi=score_psi,
            alert_rate=float((scores >= self.threshold).mean()),
            reference_alert_rate=self.reference_alert_rate,
            features=feats,
            drifted_features=drifted,
            computed_at=now,
        )
