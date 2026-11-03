import json
import time

import numpy as np
import pytest

from fraudstream.features import FEATURE_COLUMNS, frame_to_features
from fraudstream.model import FraudModel, ModelMeta


def test_artifact_roundtrip(model_dir):
    m = FraudModel.load(model_dir, "fraud_xgb")
    assert m.version == "test"
    assert 0.0 < m.threshold < 1.0
    assert m.meta.feature_names == FEATURE_COLUMNS
    assert set(m.meta.reference) >= set(FEATURE_COLUMNS)
    meta = json.loads((model_dir / "fraud_xgb.meta.json").read_text())
    assert meta["metrics"]["test"]["auc_pr"] > 0.5


def test_model_separates_fraud_from_legit(model_dir, dataset):
    m = FraudModel.load(model_dir, "fraud_xgb")
    X = frame_to_features(dataset).to_numpy(dtype=np.float32)
    y = dataset["Class"].to_numpy()
    p = m.predict_proba(X)
    assert p[y == 1].mean() > p[y == 0].mean() + 0.3


def test_score_transaction_is_fast(model_dir, legit_tx):
    m = FraudModel.load(model_dir, "fraud_xgb")
    m.score_transaction(legit_tx)  # warm
    t0 = time.perf_counter()
    n = 200
    for _ in range(n):
        resp, _ = m.score_transaction(legit_tx)
    per_call_ms = (time.perf_counter() - t0) * 1000 / n
    assert resp.latency_ms < 100
    assert per_call_ms < 20, f"{per_call_ms:.2f} ms per score is too slow"


def test_threshold_override(model_dir, fraud_tx):
    strict = FraudModel.load(model_dir, "fraud_xgb", threshold=0.999999)
    resp, _ = strict.score_transaction(fraud_tx)
    assert resp.threshold == pytest.approx(0.999999)


def test_feature_mismatch_is_rejected(model_dir):
    m = FraudModel.load(model_dir, "fraud_xgb")
    bad = ModelMeta(version="x", threshold=0.5, feature_names=FEATURE_COLUMNS[:-1])
    with pytest.raises(ValueError):
        FraudModel(m.booster, bad)
