"""CPU vs GPU (RAPIDS) XGBoost training benchmark.

Runs the same training job on ``device=cpu`` and ``device=cuda`` and reports the
speed-up. With RAPIDS installed the data is loaded with cuDF so the whole
pipeline (CSV parse -> feature build -> DMatrix -> boosting) stays on the GPU.

    python -m ml.benchmark_gpu --data ../data/creditcard.csv --rounds 300 --replicate 4

``--replicate K`` stacks the 285k-row dataset K times; GPU speed-ups grow with
data size, so report at least one larger run. Results are written to
``docs/benchmark.json`` and printed as a Markdown table ready for the README.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from fraudstream.features import FEATURE_COLUMNS, LABEL_COLUMN, frame_to_features
from ml.data import load_raw
from ml.train import DEFAULT_PARAMS

log = logging.getLogger("ml.benchmark")

try:  # RAPIDS is optional - the benchmark degrades to "CPU only" without it
    import cudf  # type: ignore

    HAVE_CUDF = True
except Exception:  # pragma: no cover - import guard
    cudf = None
    HAVE_CUDF = False


def gpu_available() -> bool:
    """True only if XGBoost really trains on CUDA.

    XGBoost does not raise when no GPU is present - it logs a warning and
    silently falls back to CPU - so we must catch that warning explicitly."""
    if str(xgb.build_info().get("USE_CUDA", "")).lower() not in ("1", "true", "on"):
        return False
    try:
        X = np.random.rand(64, 4).astype(np.float32)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            xgb.train(
                {"device": "cuda", "tree_method": "hist"}, xgb.DMatrix(X, label=X[:, 0] > 0.5), 1
            )
        return not any("changed from GPU to CPU" in str(w.message) for w in caught)
    except xgb.core.XGBoostError:
        return False


def gpu_name() -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout else None
    except Exception:
        return None


def prepare(df: pd.DataFrame, replicate: int, use_cudf: bool):
    """Return (X, y, load_seconds) on host or device memory."""
    t0 = time.perf_counter()
    if replicate > 1:
        df = pd.concat([df] * replicate, ignore_index=True)
    if use_cudf:
        gdf = cudf.from_pandas(df)
        X = cudf.DataFrame({c: gdf[c].astype("float32") for c in FEATURE_COLUMNS[:29]})
        X["log_amount"] = (gdf["Amount"].astype("float32") + 1).log()
        X["hour_of_day"] = ((gdf["Time"].astype("float64") % 86400.0) / 3600.0).astype("float32")
        y = gdf[LABEL_COLUMN].astype("float32")
    else:
        X = frame_to_features(df)
        y = df[LABEL_COLUMN].to_numpy(dtype=np.float32)
    return X, y, time.perf_counter() - t0


def run_one(X, y, device: str, rounds: int) -> dict:
    params = {**DEFAULT_PARAMS, "device": device, "eval_metric": "aucpr"}
    t0 = time.perf_counter()
    dtrain = xgb.QuantileDMatrix(X, label=y, max_bin=params["max_bin"])
    dmatrix_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    booster = xgb.train(params, dtrain, num_boost_round=rounds)
    fit_s = time.perf_counter() - t1
    t2 = time.perf_counter()
    booster.predict(dtrain)
    predict_s = time.perf_counter() - t2
    return {
        "device": device,
        "rows": int(dtrain.num_row()),
        "rounds": rounds,
        "dmatrix_seconds": round(dmatrix_s, 3),
        "fit_seconds": round(fit_s, 3),
        "predict_seconds": round(predict_s, 3),
        "total_seconds": round(dmatrix_s + fit_s, 3),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", default="../data/creditcard.csv")
    ap.add_argument("--rounds", type=int, default=300)
    ap.add_argument("--replicate", type=int, nargs="*", default=[1, 4])
    ap.add_argument("--out", default="../docs/benchmark.json")
    ap.add_argument("--skip-cpu", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level="INFO")

    df = load_raw(a.data)
    has_gpu = gpu_available()
    log.info("cudf=%s gpu=%s (%s)", HAVE_CUDF, has_gpu, gpu_name())
    results = []
    for k in a.replicate:
        row: dict = {"replicate": k, "rows": len(df) * k}
        if not a.skip_cpu:
            X, y, load_s = prepare(df, k, use_cudf=False)
            cpu = run_one(X, y, "cpu", a.rounds)
            cpu["load_seconds"] = round(load_s, 3)
            row["cpu"] = cpu
            log.info("cpu  rows=%d fit=%.2fs", cpu["rows"], cpu["fit_seconds"])
        if has_gpu:
            X, y, load_s = prepare(df, k, use_cudf=HAVE_CUDF)
            gpu = run_one(X, y, "cuda", a.rounds)
            gpu["load_seconds"] = round(load_s, 3)
            gpu["cudf"] = HAVE_CUDF
            row["gpu"] = gpu
            log.info("cuda rows=%d fit=%.2fs", gpu["rows"], gpu["fit_seconds"])
            if "cpu" in row:
                row["speedup_fit"] = round(
                    row["cpu"]["fit_seconds"] / max(gpu["fit_seconds"], 1e-6), 1
                )
                row["speedup_total"] = round(
                    (row["cpu"]["load_seconds"] + row["cpu"]["total_seconds"])
                    / max(gpu["load_seconds"] + gpu["total_seconds"], 1e-6),
                    1,
                )
        results.append(row)

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "xgboost": xgb.__version__,
        "cudf": getattr(cudf, "__version__", None),
        "gpu": gpu_name(),
        "cpu": platform.processor() or platform.machine(),
        "rounds": a.rounds,
        "results": results,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))

    print("\n| rows | CPU fit (s) | GPU fit (s) | fit speed-up | end-to-end speed-up |")
    print("|---:|---:|---:|---:|---:|")
    for r in results:
        cpu = r.get("cpu", {}).get("fit_seconds", "-")
        gpu = r.get("gpu", {}).get("fit_seconds", "-")
        s_fit, s_tot = r.get("speedup_fit", "-"), r.get("speedup_total", "-")
        print(f"| {r['rows']:,} | {cpu} | {gpu} | {s_fit}x | {s_tot}x |")
    if not has_gpu:
        print(
            "\nNo CUDA device found: GPU column left empty. Run inside the RAPIDS image "
            "(make benchmark-gpu) on a machine with an NVIDIA GPU."
        )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
