"""Single-process demo: API + consumer + replay with the in-memory queue.

No Redis, Postgres or Docker needed - handy on a laptop or in CI:

    python -m fraudstream.demo --data ../data/creditcard.csv --speed 200 --offset 227846

Then open the dashboard (``npm run dev`` in dashboard/) or curl http://localhost:8000/stats.
``--offset 227846`` skips the first 80 % of the dataset (the training window) so
the stream only contains transactions the model has never seen.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from .api import create_app
from .config import Settings
from .replay import load_dataset, replay_to_queue

log = logging.getLogger("fraudstream.demo")


async def run(a: argparse.Namespace) -> None:
    settings = Settings(
        queue_backend="memory",
        run_consumer=True,
        model_dir=a.model_dir,
        database_url=a.database_url,
        api_port=a.port,
        _env_file=None,
    )
    app = create_app(settings)
    config = uvicorn.Config(app, host=a.host, port=a.port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not getattr(app.state, "queue", None):
        if server_task.done():
            server_task.result()
        await asyncio.sleep(0.05)
    log.info("API ready on http://%s:%d (memory queue, in-process consumer)", a.host, a.port)

    df = load_dataset(a.data, limit=a.limit, offset=a.offset)
    log.info("replaying %d transactions at %sx", len(df), a.rate or a.speed)
    try:
        await replay_to_queue(
            df,
            app.state.queue,
            settings.tx_topic,
            speed=None if a.rate else a.speed,
            rate=a.rate,
            loop=a.loop,
        )
        log.info("replay finished; API stays up (Ctrl+C to stop)")
        await server_task
    finally:
        server.should_exit = True
        await asyncio.gather(server_task, return_exceptions=True)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--data", default="../data/creditcard.csv")
    p.add_argument("--model-dir", default="../models")
    p.add_argument("--database-url", default="sqlite:///./fraudstream.db")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--speed", type=float, default=120.0)
    p.add_argument("--rate", type=float, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--loop", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
