"""Shared fixtures: a synthetic dataset, a small trained model and an API client
wired to the in-memory queue + a temporary SQLite database. No services needed."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fraudstream.config import Settings
from fraudstream.features import row_to_transaction
from fraudstream.schemas import Transaction
from fraudstream.synthetic import make_synthetic
from ml.train import train


@pytest.fixture(scope="session")
def dataset() -> pd.DataFrame:
    return make_synthetic(n=12_000, fraud_rate=0.01, seed=1)


@pytest.fixture(scope="session")
def data_csv(tmp_path_factory, dataset) -> Path:
    p = tmp_path_factory.mktemp("data") / "creditcard.csv"
    dataset.to_csv(p, index=False)
    return p


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory, data_csv) -> Path:
    out = tmp_path_factory.mktemp("models")
    summary = train(data_csv, out, name="fraud_xgb", rounds=60, early_stopping=20, version="test")
    assert summary["test"]["auc_pr"] > 0.5, summary
    return out


@pytest.fixture
def settings(model_dir, tmp_path) -> Settings:
    # make sure a developer's .env does not leak into the tests
    for k in list(os.environ):
        if k.startswith("FS_"):
            del os.environ[k]
    return Settings(
        queue_backend="memory",
        model_dir=str(model_dir),
        model_name="fraud_xgb",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        run_consumer=True,
        drift_window=500,
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    from fraudstream.api import create_app

    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def legit_tx(dataset) -> Transaction:
    row = dataset[dataset["Class"] == 0].iloc[0]
    return row_to_transaction(row, "legit-1")


@pytest.fixture
def fraud_tx(dataset) -> Transaction:
    # the synthetic generator shifts V14 strongly for fraud; pick an extreme one
    row = dataset[dataset["Class"] == 1].sort_values("V14").iloc[0]
    return row_to_transaction(row, "fraud-1")
