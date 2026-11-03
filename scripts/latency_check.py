"""Measure end-to-end POST /score latency against a running API.

    python scripts/latency_check.py --url http://localhost:8000 --n 1000 --concurrency 8

Uses the standard-library HTTP client with keep-alive connections (one per
worker thread) so the numbers reflect the server, not a heavy client stack.
Prints client-observed p50 / p95 / p99 / max in milliseconds and the
server-side model latency reported in each response.
"""

from __future__ import annotations

import argparse
import http.client
import json
import random
import statistics
import threading
import time
from urllib.parse import urlparse


def sample_tx(i: int) -> dict:
    rng = random.Random(i)
    return {
        "tx_id": f"lat-{i}",
        "time": float(rng.randint(0, 172_800)),
        "amount": round(rng.lognormvariate(3.0, 1.2), 2),
        "v": [rng.gauss(0, 1) for _ in range(28)],
    }


def worker(host: str, port: int, ids: list[int], out: list, server: list) -> None:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
    for i in ids:
        body = json.dumps(sample_tx(i))
        t0 = time.perf_counter()
        conn.request("POST", "/score", body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        out.append((time.perf_counter() - t0) * 1000)
        if resp.status != 200:
            raise RuntimeError(f"{resp.status}: {data[:200]!r}")
        server.append(json.loads(data)["latency_ms"])
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--concurrency", type=int, default=8)
    a = p.parse_args()
    u = urlparse(a.url)
    host, port = u.hostname or "localhost", u.port or 80

    out: list[float] = []
    server: list[float] = []
    worker(host, port, [-1], [], [])  # warm-up
    chunks = [list(range(k, a.n, a.concurrency)) for k in range(a.concurrency)]
    threads = [threading.Thread(target=worker, args=(host, port, c, out, server)) for c in chunks]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    q = statistics.quantiles(out, n=100)
    sq = statistics.quantiles(server, n=100)
    print(
        f"requests={a.n} concurrency={a.concurrency} wall={wall:.2f}s "
        f"throughput={a.n / wall:.0f} req/s"
    )
    print(f"client   p50={q[49]:.2f}ms p95={q[94]:.2f}ms p99={q[98]:.2f}ms max={max(out):.2f}ms")
    print(
        f"model    p50={sq[49]:.3f}ms p95={sq[94]:.3f}ms p99={sq[98]:.3f}ms max={max(server):.3f}ms"
    )
    print("PASS (p99 < 100 ms)" if q[98] < 100 else "FAIL (p99 >= 100 ms)")


if __name__ == "__main__":
    main()
