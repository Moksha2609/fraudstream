"""Replay a labelled fraud dataset as a live stream of transactions.

The dataset's ``Time`` column (seconds since the first transaction) drives the
pacing: with ``--speed 60`` one real second replays one dataset minute. Use
``--rate N`` for a fixed N transactions/second instead.

Examples
    python -m fraudstream.replay --data ../data/creditcard.csv --speed 120
    python -m fraudstream.replay --data ../data/creditcard.csv --rate 200 --limit 50000
    python -m fraudstream.replay --sink http --url http://localhost:8000   # bypass the queue
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pandas as pd

from .config import get_settings
from .features import RAW_COLUMNS, row_to_transaction
from .queue import QueueBackend, make_queue
from .schemas import Transaction
from .synthetic import make_synthetic

log = logging.getLogger(__name__)


def load_dataset(
    path: str | Path | None, limit: int | None = None, offset: int = 0
) -> pd.DataFrame:
    if path is None or not Path(path).exists():
        log.warning("dataset %s not found - generating synthetic data", path)
        df = make_synthetic(limit or 20_000)
    else:
        p = Path(path)
        df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
        # OpenML's copy quotes the label ('0' / '1'); normalise to int
        if "Class" in df.columns:
            df["Class"] = df["Class"].astype(str).str.strip("'\"").astype(int)
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"dataset is missing columns {missing}")
    df = df.sort_values("Time").iloc[offset:]
    if limit is not None:
        df = df.iloc[:limit]
    return df.reset_index(drop=True)


async def iter_transactions(
    df: pd.DataFrame, speed: float | None = 60.0, rate: float | None = None, loop: bool = False
) -> AsyncIterator[Transaction]:
    """Yield transactions paced by dataset time (speed x) or at a fixed rate."""
    while True:
        wall0 = time.perf_counter()
        t0 = float(df["Time"].iloc[0]) if len(df) else 0.0
        for i, row in enumerate(df.itertuples(index=False)):
            rec = row._asdict()
            tx = row_to_transaction(rec, tx_id=f"{uuid.uuid4().hex[:12]}")
            if rate:
                target = wall0 + i / rate
            elif speed:
                target = wall0 + (tx.time - t0) / speed
            else:
                target = 0.0
            delay = target - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            yield tx
        if not loop:
            return


async def replay_to_queue(
    df: pd.DataFrame,
    queue: QueueBackend,
    topic: str,
    speed: float | None = 60.0,
    rate: float | None = None,
    loop: bool = False,
    limit: int | None = None,
) -> int:
    n = 0
    async for tx in iter_transactions(df, speed=speed, rate=rate, loop=loop):
        payload = tx.model_dump()
        payload["_sent_at"] = time.time()
        await queue.publish(topic, payload)
        n += 1
        if n % 1000 == 0:
            log.info("published %d transactions", n)
        if limit is not None and n >= limit:
            break
    return n


async def replay_to_http(
    df: pd.DataFrame, url: str, speed: float | None, rate: float | None, loop: bool
) -> int:
    n = 0
    async with httpx.AsyncClient(base_url=url, timeout=5.0) as client:
        async for tx in iter_transactions(df, speed=speed, rate=rate, loop=loop):
            r = await client.post("/score", json=tx.model_dump())
            r.raise_for_status()
            n += 1
            if n % 1000 == 0:
                log.info("posted %d transactions", n)
    return n


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--data", default="../data/creditcard.csv")
    p.add_argument(
        "--speed", type=float, default=60.0, help="replay speed multiplier (dataset time)"
    )
    p.add_argument(
        "--rate", type=float, default=None, help="fixed transactions per second (overrides --speed)"
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--offset", type=int, default=0, help="skip the first N rows (e.g. the training split)"
    )
    p.add_argument("--loop", action="store_true", help="restart from the beginning when done")
    p.add_argument("--sink", choices=["queue", "http"], default="queue")
    p.add_argument("--url", default="http://localhost:8000")
    a = p.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    df = load_dataset(a.data, limit=a.limit, offset=a.offset)
    log.info(
        "replaying %d transactions (%d fraud)",
        len(df),
        int(df.get("Class", pd.Series()).sum() or 0),
    )

    async def run() -> None:
        if a.sink == "http":
            await replay_to_http(df, a.url, a.speed, a.rate, a.loop)
            return
        settings = get_settings()
        queue = make_queue(settings)
        try:
            await replay_to_queue(df, queue, settings.tx_topic, a.speed, a.rate, a.loop)
        finally:
            await queue.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
