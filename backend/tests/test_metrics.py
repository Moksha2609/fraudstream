import time

from fraudstream.drift import merge_reports
from fraudstream.metrics import RollingStats, StatsAggregator
from fraudstream.schemas import DriftFeature, DriftReport


def _hb(source, scored, alerts, thr, p50, p99):
    return {
        "source": source,
        "scored_total": scored,
        "alerts_total": alerts,
        "throughput_per_s": thr,
        "latency_p50_ms": p50,
        "latency_p99_ms": p99,
    }


def test_aggregator_merges_own_and_remote_workers():
    own = RollingStats()
    own.record(1.0, False)
    own.record(3.0, True)
    agg = StatsAggregator(own, stale_after=10.0)
    agg.ingest(_hb("w1", 100, 2, 50.0, 0.5, 2.0))
    agg.ingest(_hb("w2", 300, 1, 150.0, 0.7, 5.0))
    agg.ingest({"no_source": True})  # ignored

    snap = agg.snapshot()
    assert snap["scored_total"] == 402
    assert snap["alerts_total"] == 4
    assert snap["throughput_per_s"] == 200.0 + own.snapshot()["throughput_per_s"]
    assert snap["latency_p99_ms"] == 5.0  # worst worker
    assert 0.5 < snap["latency_p50_ms"] < 2.0  # weighted towards the busy worker
    assert snap["workers"] == 3


def test_aggregator_stale_worker_keeps_counts_but_not_throughput():
    agg = StatsAggregator(RollingStats(), stale_after=0.05)
    agg.ingest(_hb("old", 500, 5, 100.0, 1.0, 2.0))
    time.sleep(0.1)
    agg.ingest(_hb("fresh", 10, 0, 7.0, 1.0, 2.0))
    snap = agg.snapshot()
    assert snap["scored_total"] == 510
    assert snap["throughput_per_s"] == 7.0
    assert snap["workers"] == 1


def _report(observed, psi_a, psi_b, score_psi=0.05, status="ok"):
    feats = [
        DriftFeature(name="a", psi=psi_a, status="ok"),
        DriftFeature(name="b", psi=psi_b, status="ok"),
    ]
    return DriftReport(
        window_size=1000,
        observed=observed,
        status=status,
        score_psi=score_psi,
        alert_rate=0.001,
        reference_alert_rate=0.001,
        features=feats,
        drifted_features=[],
        computed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


def test_merge_reports_weights_by_window_fill_and_flags_drift():
    merged = merge_reports([_report(1000, 0.30, 0.01), _report(1000, 0.10, 0.01)])
    assert merged.observed == 2000
    by_name = {f.name: f for f in merged.features}
    assert abs(by_name["a"].psi - 0.20) < 1e-6
    assert by_name["a"].status == "drift"
    assert merged.drifted_features == ["a"]
    assert merged.status == "drift"


def test_merge_reports_ignores_workers_without_data():
    empty = _report(10, 0.0, 0.0, score_psi=None, status="insufficient_data")
    merged = merge_reports([empty, _report(500, 0.05, 0.02)])
    assert merged.observed == 510
    assert merged.status == "ok"
    only_empty = merge_reports([empty])
    assert only_empty.status == "insufficient_data"
