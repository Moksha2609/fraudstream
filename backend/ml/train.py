"""Train the XGBoost fraud model and write the serving artifact.

    python -m ml.train --data ../data/creditcard.csv --out-dir ../models
    python -m ml.train --device cuda                      # GPU (RAPIDS / CUDA build)
    python -m ml.train --feedback feedback_labels.csv     # retrain with owner feedback

Outputs <out-dir>/<name>.json (booster) and <name>.meta.json (threshold, metrics,
feature list and the reference histograms used by the drift monitor).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)

from fraudstream.drift import SCORE_KEY, histogram_reference
from fraudstream.features import FEATURE_COLUMNS
from fraudstream.model import FraudModel, ModelMeta
from ml.data import load_feedback, load_raw, time_split, xy

log = logging.getLogger("ml.train")

DEFAULT_PARAMS: dict = {
    "objective": "binary:logistic",
    "eval_metric": ["aucpr", "logloss"],
    "tree_method": "hist",
    "max_depth": 6,
    "eta": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 3,
    "gamma": 0.1,
    "reg_lambda": 1.0,
    "max_bin": 256,
}


def pick_threshold(y: np.ndarray, p: np.ndarray, min_precision: float | None = None) -> float:
    """Threshold maximising F1 on validation (optionally subject to a precision floor)."""
    prec, rec, thr = precision_recall_curve(y, p)
    prec, rec = prec[:-1], rec[:-1]
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-9, None)
    if min_precision is not None:
        ok = prec >= min_precision
        if ok.any():
            f1 = np.where(ok, f1, -1)
    return float(thr[int(np.argmax(f1))])


def evaluate(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = (p >= threshold).astype(int)
    return {
        "auc_pr": round(float(average_precision_score(y, p)), 4),
        "auc_roc": round(float(roc_auc_score(y, p)), 4),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
        "alerts": int(pred.sum()),
        "n": int(len(y)),
        "positives": int(y.sum()),
    }


def train(
    data: str | Path,
    out_dir: str | Path,
    name: str = "fraud_xgb",
    device: str = "cpu",
    rounds: int = 600,
    early_stopping: int = 50,
    feedback: list[str] | None = None,
    feedback_weight: float = 5.0,
    min_precision: float | None = None,
    version: str | None = None,
    seed: int = 42,
    params: dict | None = None,
) -> dict:
    t_start = time.perf_counter()
    df = load_raw(data)
    train_df, val_df, test_df = time_split(df)
    log.info("split train=%d val=%d test=%d", len(train_df), len(val_df), len(test_df))

    X_tr, y_tr = xy(train_df)
    w_tr = np.ones(len(X_tr), dtype=np.float32)
    if feedback:
        fb = load_feedback(feedback)
        if len(fb):
            X_fb, y_fb = xy(fb)
            X_tr = pd.concat([X_tr, X_fb], ignore_index=True)
            y_tr = np.concatenate([y_tr, y_fb])
            w_tr = np.concatenate([w_tr, np.full(len(X_fb), feedback_weight, dtype=np.float32)])
    X_va, y_va = xy(val_df)
    X_te, y_te = xy(test_df)

    pos = float(y_tr.sum())
    neg = float(len(y_tr) - pos)
    p = {**DEFAULT_PARAMS, **(params or {})}
    p["device"] = device
    p["seed"] = seed
    # sqrt of the imbalance ratio: enough to learn the minority class without
    # pushing every score to 1.0 (the threshold search handles the rest)
    p.setdefault("scale_pos_weight", round(float(np.sqrt(neg / max(pos, 1))), 2))

    dtrain = xgb.DMatrix(X_tr, label=y_tr, weight=w_tr, feature_names=FEATURE_COLUMNS)
    dval = xgb.DMatrix(X_va, label=y_va, feature_names=FEATURE_COLUMNS)
    dtest = xgb.DMatrix(X_te, label=y_te, feature_names=FEATURE_COLUMNS)

    t_fit = time.perf_counter()
    booster = xgb.train(
        p,
        dtrain,
        num_boost_round=rounds,
        evals=[(dval, "val")],
        early_stopping_rounds=early_stopping,
        verbose_eval=50,
    )
    fit_s = time.perf_counter() - t_fit
    best_iter = int(booster.best_iteration) + 1 if booster.best_iteration is not None else rounds
    booster = booster[:best_iter]  # keep only the best trees

    p_va = booster.predict(dval)
    p_te = booster.predict(dtest)
    threshold = pick_threshold(y_va, p_va, min_precision)
    metrics = {
        "validation": evaluate(y_va, p_va, threshold),
        "test": evaluate(y_te, p_te, threshold),
        "best_iteration": best_iter,
        "fit_seconds": round(fit_s, 2),
        "device": device,
        "feedback_rows": int((w_tr != 1.0).sum()),
    }
    log.info("threshold=%.4f test=%s", threshold, metrics["test"])

    # reference distributions for the drift monitor: features from the training
    # window, scores from the held-out validation window (unbiased)
    reference = {c: histogram_reference(X_tr[c].to_numpy()) for c in FEATURE_COLUMNS}
    reference[SCORE_KEY] = histogram_reference(p_va, bins=10)

    meta = ModelMeta(
        version=version or datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        threshold=threshold,
        feature_names=FEATURE_COLUMNS,
        trained_at=datetime.now(UTC).isoformat(timespec="seconds"),
        metrics=metrics,
        reference=reference,
        reference_alert_rate=float((p_va >= threshold).mean()),
        n_train=int(len(X_tr)),
        params={k: v for k, v in p.items() if k != "eval_metric"},
    )
    FraudModel.save(booster, meta, out_dir, name)
    # sanity check: the serving wrapper must load what we just wrote
    FraudModel.load(out_dir, name)
    summary = {
        "model": str(Path(out_dir) / f"{name}.json"),
        "version": meta.version,
        "threshold": threshold,
        "total_seconds": round(time.perf_counter() - t_start, 2),
        **metrics,
    }
    (Path(out_dir) / f"{name}.metrics.json").write_text(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", default="../data/creditcard.csv")
    ap.add_argument("--out-dir", default="../models")
    ap.add_argument("--name", default="fraud_xgb")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--rounds", type=int, default=600)
    ap.add_argument("--early-stopping", type=int, default=50)
    ap.add_argument(
        "--feedback", nargs="*", default=None, help="CSV exports from /labels/export.csv"
    )
    ap.add_argument("--feedback-weight", type=float, default=5.0)
    ap.add_argument("--min-precision", type=float, default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    summary = train(
        a.data,
        a.out_dir,
        a.name,
        a.device,
        a.rounds,
        a.early_stopping,
        a.feedback,
        a.feedback_weight,
        a.min_precision,
        a.version,
        a.seed,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
