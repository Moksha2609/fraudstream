import numpy as np

from fraudstream.drift import SCORE_KEY, DriftMonitor, histogram_reference, psi, psi_from_values


def test_psi_zero_for_identical_distributions():
    f = np.array([0.1, 0.2, 0.3, 0.4])
    assert psi(f, f) == 0.0


def test_psi_detects_shift():
    rng = np.random.default_rng(0)
    ref = histogram_reference(rng.normal(0, 1, 20_000))
    same = psi_from_values(ref, rng.normal(0, 1, 5_000))
    shifted = psi_from_values(ref, rng.normal(1.5, 1, 5_000))
    assert same < 0.05
    assert shifted > 0.5


def test_histogram_reference_handles_constant_feature():
    ref = histogram_reference(np.zeros(100))
    assert len(ref["edges"]) >= 2
    assert abs(sum(ref["freq"]) - 1.0) < 1e-9


def test_monitor_reports_insufficient_then_ok_then_drift():
    rng = np.random.default_rng(1)
    reference = {
        "a": histogram_reference(rng.normal(0, 1, 10_000)),
        "b": histogram_reference(rng.normal(5, 2, 10_000)),
        SCORE_KEY: histogram_reference(rng.beta(1, 20, 10_000)),
    }
    mon = DriftMonitor(reference, window=1000, min_observations=100, threshold=0.5)
    assert mon.report().status == "insufficient_data"

    for _ in range(500):
        mon.observe({"a": rng.normal(0, 1), "b": rng.normal(5, 2)}, score=rng.beta(1, 20))
    rep = mon.report()
    assert rep.status == "ok", rep
    assert rep.observed == 500
    assert {f.name for f in rep.features} == {"a", "b"}
    assert rep.score_psi is not None and rep.score_psi < 0.1

    # feature "a" drifts upward; window rolls over so the shift dominates
    for _ in range(1000):
        mon.observe({"a": rng.normal(3, 1), "b": rng.normal(5, 2)}, score=rng.beta(1, 20))
    rep = mon.report()
    assert rep.status == "drift"
    assert rep.drifted_features == ["a"]
    assert rep.features[0].name == "a"  # sorted by psi desc


def test_monitor_is_thread_safe_under_concurrent_observe():
    import threading

    reference = {"a": histogram_reference(np.random.default_rng(2).normal(0, 1, 1000))}
    mon = DriftMonitor(reference, window=100, min_observations=10)

    def work():
        for i in range(500):
            mon.observe({"a": float(i % 7)}, score=0.1)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert mon.report().observed == 2000
