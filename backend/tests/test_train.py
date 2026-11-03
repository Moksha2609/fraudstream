import numpy as np
import pandas as pd

from fraudstream.features import V_COLUMNS
from ml.data import load_raw, time_split
from ml.train import pick_threshold, train


def test_time_split_is_chronological(dataset):
    tr, va, te = time_split(dataset)
    assert tr["Time"].max() <= va["Time"].min() <= va["Time"].max() <= te["Time"].min()
    assert len(tr) + len(va) + len(te) == len(dataset)


def test_load_raw_normalises_quoted_labels(tmp_path, dataset):
    df = dataset.head(100).copy()
    df["Class"] = df["Class"].map(lambda c: f"'{c}'")  # OpenML style
    p = tmp_path / "q.csv"
    df.to_csv(p, index=False)
    out = load_raw(p)
    assert out["Class"].dtype.kind == "i"


def test_pick_threshold_prefers_high_f1():
    y = np.array([0] * 90 + [1] * 10)
    p = np.concatenate([np.linspace(0, 0.4, 90), np.linspace(0.6, 1.0, 10)])
    t = pick_threshold(y, p)
    assert 0.4 < t <= 0.6
    assert pick_threshold(y, p, min_precision=0.99) >= 0.4


def test_retrain_with_feedback_rows(data_csv, tmp_path, dataset):
    fb = dataset[dataset["Class"] == 1].head(15).copy()
    fb["source"] = "feedback"
    fb_path = tmp_path / "feedback.csv"
    fb[["Time", *V_COLUMNS, "Amount", "Class", "source"]].to_csv(fb_path, index=False)
    out = tmp_path / "models"
    summary = train(
        data_csv, out, rounds=30, early_stopping=10, feedback=[str(fb_path)], version="fb"
    )
    assert summary["feedback_rows"] == 15
    assert (out / "fraud_xgb.json").exists()
    assert (out / "fraud_xgb.meta.json").exists()
    assert pd.read_json(out / "fraud_xgb.metrics.json", typ="series")["version"] == "fb"
