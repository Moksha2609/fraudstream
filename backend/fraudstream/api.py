"""FastAPI scoring + alerting service.

Endpoints
  POST /score                     synchronous scoring (p99 target: < 100 ms end-to-end)
  POST /transactions              enqueue a transaction for asynchronous scoring
  GET  /alerts, /alerts/{id}      alert history (with ?status=unreviewed|fraud|false_alarm)
  POST /alerts/{id}/feedback      owner marks an alert as real fraud or a false alarm
  GET  /alerts/stream             Server-Sent Events feed of new alerts (dashboard)
  GET  /stats, /drift, /model     operational and model-health views
  GET  /labels/export.csv         feedback-labelled rows for retraining
  GET  /metrics, /health          Prometheus + liveness
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sse_starlette.sse import EventSourceResponse

from . import __version__, metrics
from .config import Settings, get_settings
from .consumer import build_pipeline, consume_forever
from .queue import make_queue
from .schemas import Alert, DriftReport, FeedbackRequest, ScoreResponse, Stats, Transaction
from .scoring import ScoringPipeline

log = logging.getLogger(__name__)


class AlertBroadcaster:
    """Fans alerts out to connected SSE clients."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[dict]] = set()

    def publish(self, alert: dict) -> None:
        for q in list(self._clients):
            if q.qsize() > 500:  # slow client: drop oldest rather than grow unbounded
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(alert)

    @contextlib.asynccontextmanager
    async def listen(self) -> AsyncIterator[asyncio.Queue[dict]]:
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._clients.add(q)
        try:
            yield q
        finally:
            self._clients.discard(q)

    @property
    def n_clients(self) -> int:
        return len(self._clients)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=settings.log_level.upper())
        queue = make_queue(settings)
        pipeline = build_pipeline(settings, queue)
        broadcaster = AlertBroadcaster()
        app.state.settings = settings
        app.state.queue = queue
        app.state.pipeline = pipeline
        app.state.broadcaster = broadcaster
        app.state.started_at = time.time()
        stop = asyncio.Event()
        tasks: list[asyncio.Task] = []

        async def relay_alerts() -> None:
            # every API instance mirrors the alert topic to its SSE clients
            while not stop.is_set():
                try:
                    async for msg in queue.subscribe(settings.alert_topic):
                        broadcaster.publish(msg.payload)
                        if stop.is_set():
                            break
                except asyncio.CancelledError:
                    raise
                except Exception:  # broker hiccup: back off and resubscribe
                    log.exception("alert relay failed; retrying in 2s")
                    await asyncio.sleep(2)

        tasks.append(asyncio.create_task(relay_alerts(), name="alert-relay"))
        if settings.run_consumer:
            tasks.append(
                asyncio.create_task(
                    consume_forever(pipeline, queue, settings, stop), name="consumer"
                )
            )
        log.info(
            "fraudstream api %s ready: model=%s threshold=%.3f queue=%s consumer=%s",
            __version__,
            pipeline.model.version,
            pipeline.model.threshold,
            settings.queue_backend,
            settings.run_consumer,
        )
        try:
            yield
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await queue.close()

    app = FastAPI(title="fraudstream scoring API", version=__version__, lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else []
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
    )

    def pipe(request: Request) -> ScoringPipeline:
        return request.app.state.pipeline

    # ---- scoring ----------------------------------------------------------
    @app.post("/score", response_model=ScoreResponse, tags=["scoring"])
    async def score(tx: Transaction, request: Request) -> ScoreResponse:
        """Score one transaction synchronously. Alerts are persisted and streamed."""
        t0 = time.perf_counter()
        resp, alert = await pipe(request).process(tx, path="http")
        metrics.REQUEST_LATENCY.observe(time.perf_counter() - t0)
        return resp

    @app.post("/score/batch", response_model=list[ScoreResponse], tags=["scoring"])
    async def score_batch(txs: list[Transaction], request: Request) -> list[ScoreResponse]:
        p = pipe(request)
        return [(await p.process(tx, path="http"))[0] for tx in txs]

    @app.post("/transactions", status_code=202, tags=["scoring"])
    async def enqueue(tx: Transaction, request: Request) -> dict:
        """Accept a transaction and hand it to the queue for asynchronous scoring."""
        payload = tx.model_dump()
        payload["_sent_at"] = time.time()
        msg_id = await request.app.state.queue.publish(settings.tx_topic, payload)
        return {"queued": True, "message_id": msg_id, "tx_id": tx.tx_id}

    # ---- alerts & feedback ------------------------------------------------
    @app.get("/alerts", response_model=list[Alert], tags=["alerts"])
    def list_alerts(
        request: Request,
        limit: int = Query(100, ge=1, le=1000),
        status: str | None = Query(None, pattern="^(unreviewed|fraud|false_alarm)$"),
        since_id: int | None = None,
    ) -> list[Alert]:
        return pipe(request).store.list_alerts(limit=limit, status=status, since_id=since_id)

    @app.get("/alerts/stream", tags=["alerts"])
    async def alert_stream(request: Request):
        """Server-Sent Events: one `alert` event per new alert, `ping` every 15 s."""
        broadcaster: AlertBroadcaster = request.app.state.broadcaster

        async def gen():
            async with broadcaster.listen() as q:
                yield {"event": "ready", "data": "{}"}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        alert = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield {
                            "event": "alert",
                            "data": Alert.model_validate(alert).model_dump_json(),
                        }
                    except TimeoutError:
                        yield {"event": "ping", "data": str(int(time.time()))}

        return EventSourceResponse(gen())

    @app.get("/alerts/{alert_id}", response_model=Alert, tags=["alerts"])
    def get_alert(alert_id: int, request: Request) -> Alert:
        alert = pipe(request).store.get_alert(alert_id)
        if alert is None:
            raise HTTPException(404, "alert not found")
        return alert

    @app.post("/alerts/{alert_id}/feedback", response_model=Alert, tags=["alerts"])
    def feedback(alert_id: int, body: FeedbackRequest, request: Request) -> Alert:
        """Business owner verdict. Creates a labelled example for retraining."""
        alert = pipe(request).store.set_feedback(alert_id, body.label, body.note)
        if alert is None:
            raise HTTPException(404, "alert not found")
        metrics.FEEDBACK.labels(label=body.label).inc()
        return alert

    @app.get("/labels/export.csv", tags=["alerts"])
    def export_labels(request: Request) -> Response:
        df = pipe(request).store.export_labeled()
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=feedback_labels.csv"},
        )

    # ---- health & monitoring --------------------------------------------
    @app.get("/stats", response_model=Stats, tags=["monitoring"])
    def stats(request: Request) -> Stats:
        p = pipe(request)
        snap = p.rolling.snapshot()
        counts = p.store.counts()
        reviewed = counts["fraud"] + counts["false_alarm"]
        scored = int(snap["scored_total"] or 0)
        return Stats(
            scored_total=scored,
            alerts_total=int(snap["alerts_total"] or 0),
            alert_rate=(int(snap["alerts_total"] or 0) / scored) if scored else 0.0,
            feedback_fraud=counts["fraud"],
            feedback_false_alarm=counts["false_alarm"],
            unreviewed=counts["unreviewed"],
            precision_on_feedback=(counts["fraud"] / reviewed) if reviewed else None,
            throughput_per_s=float(snap["throughput_per_s"] or 0.0),
            latency_p50_ms=snap["latency_p50_ms"],  # type: ignore[arg-type]
            latency_p99_ms=snap["latency_p99_ms"],  # type: ignore[arg-type]
            model_version=p.model.version,
            threshold=p.model.threshold,
        )

    @app.get("/drift", response_model=DriftReport, tags=["monitoring"])
    def drift(request: Request) -> DriftReport:
        report = pipe(request).drift.report()
        metrics.publish_drift(report)
        return report

    @app.get("/model", tags=["monitoring"])
    def model_info(request: Request) -> dict:
        m = pipe(request).model
        return {
            "version": m.version,
            "threshold": m.threshold,
            "trained_at": m.meta.trained_at,
            "n_train": m.meta.n_train,
            "metrics": m.meta.metrics,
            "params": m.meta.params,
            "feature_names": m.meta.feature_names,
        }

    @app.get("/health", tags=["monitoring"])
    async def health(request: Request) -> dict:
        ok = True
        with contextlib.suppress(Exception):
            ok = await request.app.state.queue.ping()
        return {
            "status": "ok" if ok else "degraded",
            "queue": settings.queue_backend,
            "queue_ok": ok,
            "uptime_s": round(time.time() - request.app.state.started_at, 1),
            "sse_clients": request.app.state.broadcaster.n_clients,
        }

    @app.get("/metrics", tags=["monitoring"])
    def prometheus() -> Response:
        metrics.publish_drift(app.state.pipeline.drift.report())
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "fraudstream.api:create_app",
        factory=True,
        host=s.api_host,
        port=s.api_port,
        log_level=s.log_level,
    )


if __name__ == "__main__":
    main()
