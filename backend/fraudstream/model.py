"""Loading and serving the trained XGBoost fraud model.

A model artifact is a pair of files in the model directory:
  <name>.json       - the XGBoost booster (format-stable, version-portable)
  <name>.meta.json  - metadata: version, threshold, feature names, metrics and
                      the reference distributions used by the drift monitor.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

from .features import FEATURE_COLUMNS, transaction_to_features
from .schemas import ScoreResponse, Transaction


@dataclass
class ModelMeta:
    version: str
    threshold: float
    feature_names: list[str]
    trained_at: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    # per-feature reference histograms {name: {"edges": [...], "freq": [...]}}
    reference: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    reference_alert_rate: float | None = None
    n_train: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelMeta:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "threshold": self.threshold,
            "feature_names": self.feature_names,
            "trained_at": self.trained_at,
            "metrics": self.metrics,
            "reference": self.reference,
            "reference_alert_rate": self.reference_alert_rate,
            "n_train": self.n_train,
            "params": self.params,
        }


class FraudModel:
    """Thin wrapper around an XGBoost booster with a decision threshold."""

    def __init__(self, booster: xgb.Booster, meta: ModelMeta, threshold: float | None = None):
        if list(meta.feature_names) != FEATURE_COLUMNS:
            raise ValueError(
                "Model feature list does not match features.FEATURE_COLUMNS; "
                "retrain the model with the current feature code."
            )
        self.booster = booster
        self.meta = meta
        self.threshold = float(threshold if threshold is not None else meta.threshold)
        # warm-up: the first inplace_predict call allocates thread-local buffers
        self.booster.inplace_predict(np.zeros((1, len(FEATURE_COLUMNS)), dtype=np.float32))

    # -- persistence ------------------------------------------------------
    @classmethod
    def load(cls, model_dir: str | Path, name: str, threshold: float | None = None) -> FraudModel:
        model_dir = Path(model_dir)
        booster = xgb.Booster()
        booster.load_model(str(model_dir / f"{name}.json"))
        booster.set_param({"nthread": 1})  # single-row scoring is fastest single-threaded
        meta = ModelMeta.from_dict(json.loads((model_dir / f"{name}.meta.json").read_text()))
        return cls(booster, meta, threshold)

    @staticmethod
    def save(booster: xgb.Booster, meta: ModelMeta, model_dir: str | Path, name: str) -> None:
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        booster.save_model(str(model_dir / f"{name}.json"))
        (model_dir / f"{name}.meta.json").write_text(json.dumps(meta.to_dict(), indent=2))

    # -- inference --------------------------------------------------------
    @property
    def version(self) -> str:
        return self.meta.version

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Fraud probability for a (n, n_features) float32 array."""
        return self.booster.inplace_predict(X)

    def score_features(self, row: np.ndarray) -> float:
        return float(self.booster.inplace_predict(row)[0])

    def score_transaction(self, tx: Transaction) -> tuple[ScoreResponse, np.ndarray]:
        """Score one transaction; returns the response and the feature row used."""
        t0 = time.perf_counter()
        row = transaction_to_features(tx)
        score = self.score_features(row)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return (
            ScoreResponse(
                tx_id=tx.tx_id,
                score=score,
                is_alert=score >= self.threshold,
                threshold=self.threshold,
                model_version=self.version,
                latency_ms=round(latency_ms, 3),
            ),
            row,
        )
