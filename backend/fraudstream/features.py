"""Feature engineering shared by training and serving.

Keeping this in one module guarantees the model sees identical features online
and offline (no training/serving skew)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schemas import Transaction

V_COLUMNS = [f"V{i}" for i in range(1, 29)]
RAW_COLUMNS = ["Time", *V_COLUMNS, "Amount"]
FEATURE_COLUMNS = [*V_COLUMNS, "Amount", "log_amount", "hour_of_day"]
LABEL_COLUMN = "Class"


def frame_to_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the model feature frame from a raw dataset frame (Time, V1..V28, Amount)."""
    out = pd.DataFrame(index=df.index)
    for c in V_COLUMNS:
        out[c] = df[c].astype("float32")
    amount = df["Amount"].astype("float32")
    out["Amount"] = amount
    out["log_amount"] = np.log1p(amount).astype("float32")
    out["hour_of_day"] = ((df["Time"].astype("float64") % 86400.0) / 3600.0).astype("float32")
    return out[FEATURE_COLUMNS]


def transaction_to_features(tx: Transaction) -> np.ndarray:
    """Vectorise a single transaction into a (1, n_features) float32 array."""
    row = np.empty((1, len(FEATURE_COLUMNS)), dtype=np.float32)
    row[0, :28] = tx.v
    row[0, 28] = tx.amount
    row[0, 29] = np.log1p(tx.amount)
    row[0, 30] = (tx.time % 86400.0) / 3600.0
    return row


def features_to_dict(row: np.ndarray) -> dict[str, float]:
    return {name: float(v) for name, v in zip(FEATURE_COLUMNS, row.ravel(), strict=True)}


def row_to_transaction(row: pd.Series | dict, tx_id: str) -> Transaction:
    """Convert one dataset row (Time, V1..V28, Amount[, Class]) into a Transaction."""
    label = row.get(LABEL_COLUMN)
    return Transaction(
        tx_id=tx_id,
        time=float(row["Time"]),
        amount=float(row["Amount"]),
        v=[float(row[c]) for c in V_COLUMNS],
        true_label=None if label is None or pd.isna(label) else int(label),
    )
