import time

import pytest


def _wait_for_alert(client, tx_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        alerts = client.get("/alerts").json()
        hit = [a for a in alerts if a["tx_id"] == tx_id]
        if hit:
            return hit[0]
        time.sleep(0.05)
    raise AssertionError(f"no alert for {tx_id} within {timeout}s")


def test_health_and_model(client):
    assert client.get("/health").json()["status"] == "ok"
    info = client.get("/model").json()
    assert info["version"] == "test"
    assert len(info["feature_names"]) == 31


def test_score_legit_under_100ms(client, legit_tx):
    client.post("/score", json=legit_tx.model_dump())  # warm-up
    t0 = time.perf_counter()
    r = client.post("/score", json=legit_tx.model_dump())
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tx_id"] == "legit-1"
    assert 0.0 <= body["score"] <= 1.0
    assert body["is_alert"] is False
    assert body["latency_ms"] < 100
    assert elapsed_ms < 100, f"end-to-end {elapsed_ms:.1f} ms"


def test_score_fraud_creates_alert_and_feedback_flow(client, fraud_tx):
    r = client.post("/score", json=fraud_tx.model_dump())
    assert r.status_code == 200
    assert r.json()["is_alert"] is True
    alert = _wait_for_alert(client, "fraud-1")
    assert alert["feedback"] is None
    assert alert["true_label"] == 1

    detail = client.get(f"/alerts/{alert['id']}").json()
    assert detail["features"]["V14"] == pytest.approx(fraud_tx.v[13], rel=1e-5)

    r = client.post(
        f"/alerts/{alert['id']}/feedback", json={"label": "false_alarm", "note": "known customer"}
    )
    assert r.status_code == 200
    assert r.json()["feedback"] == "false_alarm"
    assert r.json()["feedback_note"] == "known customer"

    # the owner can change their mind
    r = client.post(f"/alerts/{alert['id']}/feedback", json={"label": "fraud"})
    assert r.json()["feedback"] == "fraud"

    assert client.get("/alerts", params={"status": "fraud"}).json()[0]["id"] == alert["id"]
    assert client.get("/alerts", params={"status": "unreviewed"}).json() == []

    stats = client.get("/stats").json()
    assert stats["alerts_total"] >= 1
    assert stats["feedback_fraud"] == 1
    assert stats["precision_on_feedback"] == 1.0
    assert stats["latency_p99_ms"] is not None and stats["latency_p99_ms"] < 100

    csv = client.get("/labels/export.csv").text.splitlines()
    assert csv[0].startswith("Time,V1,")
    assert len(csv) == 2 and csv[1].split(",")[-3] == "1"  # Class column == 1


def test_feedback_validation(client):
    assert client.post("/alerts/999999/feedback", json={"label": "fraud"}).status_code == 404
    assert client.post("/alerts/1/feedback", json={"label": "maybe"}).status_code == 422


def test_enqueue_is_scored_by_in_process_consumer(client, fraud_tx):
    tx = fraud_tx.model_copy(update={"tx_id": "queued-fraud"})
    r = client.post("/transactions", json=tx.model_dump())
    assert r.status_code == 202
    assert r.json()["queued"] is True
    alert = _wait_for_alert(client, "queued-fraud")
    assert alert["score"] >= alert["threshold"]


def test_invalid_transaction_rejected(client, legit_tx):
    bad = legit_tx.model_dump()
    bad["v"] = bad["v"][:10]
    assert client.post("/score", json=bad).status_code == 422
    bad = legit_tx.model_dump()
    bad["amount"] = -5
    assert client.post("/score", json=bad).status_code == 422


def test_drift_endpoint_and_metrics(client, legit_tx):
    rep = client.get("/drift").json()
    assert rep["status"] in {"insufficient_data", "ok", "warn", "drift"}
    for i in range(250):
        client.post("/score", json=legit_tx.model_copy(update={"tx_id": f"d{i}"}).model_dump())
    rep = client.get("/drift").json()
    assert rep["observed"] >= 250
    assert len(rep["features"]) == 31
    text = client.get("/metrics").text
    assert "fraudstream_scored_total" in text
    assert "fraudstream_drift_psi" in text


def test_sse_stream_delivers_alert(client, fraud_tx):
    tx = fraud_tx.model_copy(update={"tx_id": "sse-fraud"})
    with client.stream("GET", "/alerts/stream") as stream:
        lines = stream.iter_lines()
        # first event is "ready"
        got_ready = False
        for line in lines:
            if line.startswith("event: ready"):
                got_ready = True
            if got_ready and line == "":
                break
        client.post("/score", json=tx.model_dump())
        payload = None
        for line in lines:
            if line.startswith("data:") and "sse-fraud" in line:
                payload = line
                break
        assert payload is not None
