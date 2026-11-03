"""Retrain with the business owner's feedback labels pulled from the live API.

    python -m ml.retrain --api http://localhost:8000 \
        --data ../data/creditcard.csv --out-dir ../models

Downloads /labels/export.csv, merges it with the base dataset (feedback rows are
up-weighted), trains a new version, and prints a before/after comparison. Point
FS_MODEL_NAME at the new artifact (or overwrite fraud_xgb) and restart the API
to promote it.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import httpx

from ml.train import train

log = logging.getLogger("ml.retrain")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--data", default="../data/creditcard.csv")
    ap.add_argument("--out-dir", default="../models")
    ap.add_argument("--name", default="fraud_xgb")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--feedback-weight", type=float, default=5.0)
    ap.add_argument(
        "--min-feedback", type=int, default=20, help="refuse to retrain with fewer labels"
    )
    a = ap.parse_args(argv)
    logging.basicConfig(level="INFO")

    export = Path(a.out_dir) / "feedback_labels.csv"
    r = httpx.get(f"{a.api}/labels/export.csv", timeout=30)
    r.raise_for_status()
    export.write_text(r.text)
    n_rows = max(r.text.count("\n") - 1, 0)
    log.info("downloaded %d feedback rows to %s", n_rows, export)
    if n_rows < a.min_feedback:
        raise SystemExit(f"only {n_rows} labelled alerts (< {a.min_feedback}); not retraining")

    summary = train(
        a.data,
        a.out_dir,
        a.name,
        a.device,
        feedback=[str(export)],
        feedback_weight=a.feedback_weight,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
