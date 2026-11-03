import numpy as np

from fraudstream.features import (
    FEATURE_COLUMNS,
    features_to_dict,
    frame_to_features,
    row_to_transaction,
    transaction_to_features,
)


def test_online_and_offline_features_match(dataset):
    """The single-row (serving) path must produce exactly the frame (training) path."""
    sample = dataset.head(50)
    offline = frame_to_features(sample).to_numpy(dtype=np.float32)
    for i, (_, row) in enumerate(sample.iterrows()):
        tx = row_to_transaction(row, f"t{i}")
        online = transaction_to_features(tx)
        assert online.shape == (1, len(FEATURE_COLUMNS))
        np.testing.assert_allclose(online[0], offline[i], rtol=1e-5, atol=1e-6)


def test_feature_names_are_stable():
    assert FEATURE_COLUMNS[:28] == [f"V{i}" for i in range(1, 29)]
    assert FEATURE_COLUMNS[28:] == ["Amount", "log_amount", "hour_of_day"]


def test_hour_of_day_wraps(legit_tx):
    tx = legit_tx.model_copy(update={"time": 86400.0 + 3600.0 * 5.5})
    feats = features_to_dict(transaction_to_features(tx))
    assert abs(feats["hour_of_day"] - 5.5) < 1e-5


def test_row_to_transaction_carries_label(dataset):
    row = dataset[dataset["Class"] == 1].iloc[0]
    tx = row_to_transaction(row, "x")
    assert tx.true_label == 1
    assert len(tx.v) == 28
