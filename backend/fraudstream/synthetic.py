"""Synthetic stand-in for the Kaggle credit-card fraud dataset.

Produces a frame with the exact same columns (Time, V1..V28, Amount, Class) and
a similar class imbalance so that tests, CI and first-run demos work without
downloading the real data. Fraud rows are shifted on a handful of components
so a model can actually learn something.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import V_COLUMNS


def make_synthetic(n: int = 20_000, fraud_rate: float = 0.005, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_fraud = max(int(n * fraud_rate), 10)
    n_legit = n - n_fraud
    V = rng.normal(0.0, 1.0, size=(n, 28))
    y = np.zeros(n, dtype=np.int64)
    y[n_legit:] = 1
    # Fraud: shifts on V1,V3,V4,V10,V12,V14,V17 (the strong signals in the real data)
    shifts = {0: -3.0, 2: -4.0, 3: 3.5, 9: -4.0, 11: -5.0, 13: -6.0, 16: -5.0}
    for col, delta in shifts.items():
        V[n_legit:, col] += delta + rng.normal(0, 1.5, size=n_fraud)
    amount = rng.lognormal(mean=3.0, sigma=1.2, size=n).round(2)
    amount[n_legit:] = rng.lognormal(mean=3.6, sigma=1.6, size=n_fraud).round(2)
    # two days like the real set; unsorted so fraud is spread across the timeline
    time_s = rng.uniform(0, 172_800, size=n).round(0)
    df = pd.DataFrame(V, columns=V_COLUMNS).astype("float64")
    df.insert(0, "Time", time_s)
    df["Amount"] = amount
    df["Class"] = y
    return df.sort_values("Time").reset_index(drop=True)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--out", default="../data/creditcard_synthetic.csv")
    p.add_argument("--rows", type=int, default=50_000)
    a = p.parse_args()
    make_synthetic(a.rows).to_csv(a.out, index=False)
    print(f"wrote {a.rows} rows to {a.out}")
