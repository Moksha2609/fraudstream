import asyncio

import pandas as pd

from fraudstream.config import Settings
from fraudstream.consumer import build_pipeline, consume_forever, stats_heartbeat
from fraudstream.queue.memory import MemoryQueue
from fraudstream.replay import iter_transactions, load_dataset, replay_to_queue


async def test_replay_paces_by_dataset_time(dataset):
    df = dataset.head(5).copy()
    df["Time"] = [0, 10, 20, 30, 40]  # 40 dataset-seconds
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    txs = [tx async for tx in iter_transactions(df, speed=400.0)]  # -> ~0.1 wall seconds
    elapsed = loop.time() - t0
    assert len(txs) == 5
    assert 0.08 <= elapsed < 1.0
    assert txs[0].tx_id != txs[1].tx_id


async def test_replay_fixed_rate(dataset):
    df = dataset.head(20)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    n = len([tx async for tx in iter_transactions(df, rate=200.0)])
    assert n == 20
    assert loop.time() - t0 >= 0.08


def test_load_dataset_falls_back_to_synthetic(tmp_path):
    df = load_dataset(tmp_path / "missing.csv", limit=100)
    assert len(df) == 100
    assert "Class" in df.columns


async def test_end_to_end_queue_to_alert(model_dir, dataset, tmp_path):
    settings = Settings(
        queue_backend="memory",
        model_dir=str(model_dir),
        database_url=f"sqlite:///{tmp_path / 'e2e.db'}",
        _env_file=None,
    )
    q = MemoryQueue()
    pipeline = build_pipeline(settings, q)

    # stream 300 transactions including every fraud row of the sample
    sample = pd.concat(
        [dataset[dataset["Class"] == 1].head(10), dataset[dataset["Class"] == 0].head(290)]
    ).sort_values("Time")
    n_fraud = int(sample["Class"].sum())
    published = await replay_to_queue(sample, q, settings.tx_topic, speed=None, rate=None)
    assert published == len(sample)

    processed = await asyncio.wait_for(
        consume_forever(pipeline, q, settings, max_messages=len(sample)), timeout=20
    )
    assert processed == len(sample)
    assert pipeline.rolling.scored == len(sample)

    alerts = pipeline.store.list_alerts(limit=1000)
    assert alerts, "expected at least one alert from the fraud rows"
    caught = sum(1 for a in alerts if a.true_label == 1)
    assert caught >= n_fraud * 0.5
    # alerts were also published to the alert topic for dashboards
    assert q.pending(settings.alert_topic) == len(alerts)


async def test_consumer_drops_malformed_messages(model_dir, tmp_path):
    settings = Settings(
        queue_backend="memory",
        model_dir=str(model_dir),
        database_url=f"sqlite:///{tmp_path / 'bad.db'}",
        _env_file=None,
    )
    q = MemoryQueue()
    pipeline = build_pipeline(settings, q)
    await q.publish(settings.tx_topic, {"garbage": True})
    good = {"tx_id": "ok", "time": 1.0, "amount": 10.0, "v": [0.0] * 28}
    await q.publish(settings.tx_topic, good)
    n = await asyncio.wait_for(consume_forever(pipeline, q, settings, max_messages=1), timeout=10)
    assert n == 1
    assert len(q.acked) == 2  # bad message acked (dropped), good one acked after scoring


async def test_stats_heartbeat_publishes_counters_and_drift(model_dir, tmp_path, dataset):
    settings = Settings(
        queue_backend="memory",
        model_dir=str(model_dir),
        database_url=f"sqlite:///{tmp_path / 'hb.db'}",
        _env_file=None,
    )
    q = MemoryQueue()
    pipeline = build_pipeline(settings, q)
    sample = dataset.head(20)
    await replay_to_queue(sample, q, settings.tx_topic, speed=None, rate=None)
    await consume_forever(pipeline, q, settings, max_messages=len(sample))

    stop = asyncio.Event()
    task = asyncio.create_task(stats_heartbeat(pipeline, q, settings, "worker-A", stop, 0.02))
    await asyncio.sleep(0.1)
    stop.set()
    await task

    assert q.pending(settings.stats_topic) >= 2
    hb = (await q._work[settings.stats_topic].get()).payload
    assert hb["source"] == "worker-A"
    assert hb["scored_total"] == len(sample)
    assert hb["latency_p99_ms"] is not None
    assert hb["drift"]["observed"] == len(sample)
