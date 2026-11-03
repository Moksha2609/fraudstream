"""Dataset loading / splitting helpers shared by the training scripts."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from fraudstream.features import LABEL_COLUMN, RAW_COLUMNS, frame_to_features

log = logging.getLogger(__name__)


def load_raw(path: str | Path) -> pd.DataFrame:
    """Load creditcard.csv (Kaggle or OpenML copy) and normalise the label column."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Run scripts/download_data.sh, or "
            "`python -m fraudstream.synthetic --out ../data/creditcard.csv` for synthetic data."
        )
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype(str).str.strip("'\"").astype(int)
    missing = [c for c in [*RAW_COLUMNS, LABEL_COLUMN] if c not in df.columns]
    if missing:
        raise ValueError(f"dataset missing columns: {missing}")
    return df.sort_values("Time").reset_index(drop=True)


def load_feedback(paths: list[str | Path]) -> pd.DataFrame:
    """Owner-labelled rows exported from GET /labels/export.csv."""
    frames = []
    for p in paths:
        f = pd.read_csv(p)
        if len(f):
            frames.append(f[[*RAW_COLUMNS, LABEL_COLUMN]])
    if not frames:
        return pd.DataFrame(columns=[*RAW_COLUMNS, LABEL_COLUMN])
    out = pd.concat(frames, ignore_index=True)
    log.info("loaded %d feedback-labelled rows (%d fraud)", len(out), int(out[LABEL_COLUMN].sum()))
    return out


def time_split(df: pd.DataFrame, test_frac: float = 0.2, val_frac: float = 0.1):
    """Chronological split - the model is evaluated on *later* transactions than it
    was trained on, which mirrors production and avoids leaking future patterns."""
    n = len(df)
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    train = df.iloc[: n - n_test - n_val]
    val = df.iloc[n - n_test - n_val : n - n_test]
    test = df.iloc[n - n_test :]
    return train, val, test


def xy(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    return frame_to_features(df), df[LABEL_COLUMN].to_numpy(dtype=np.float32)
