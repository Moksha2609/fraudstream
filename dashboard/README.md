# FraudStream dashboard

Single-page React + Vite + TypeScript app that shows live fraud alerts and lets
the business owner mark each one as **Real fraud** or **False alarm**. Every
verdict is posted back to the scoring API and becomes a labelled example for
the next retraining run.

## What it shows

- **Header** – model version, decision threshold, API health, SSE stream state.
- **Stat tiles** – scored count, throughput (with sparkline), alerts and alert
  rate, p50/p99 scoring latency (p99 turns red above the 100 ms budget),
  unreviewed alerts, precision on owner feedback, drift status.
- **Live alerts** – newest first, streamed over Server-Sent Events from
  `/api/alerts/stream`. Filter by All / Unreviewed / Fraud / False alarm. Click a
  row to see the feature vector and, when replaying labelled data, the ground
  truth. Keys: `f` = real fraud, `x` = false alarm, `Esc` = close.
- **Model drift** – per-feature Population Stability Index (PSI) bars with the
  0.1 / 0.2 guide lines, score PSI and live vs. training alert rate.
- **Toasts** and optional browser notifications for each new alert.

## Run

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api -> http://localhost:8000
npm run typecheck  # tsc --noEmit
npm run build      # typecheck + production bundle in dist/
```

Point the dev proxy elsewhere with `VITE_API_TARGET=http://host:port npm run dev`.

## Docker

The multi-stage `Dockerfile` builds the bundle and serves it with nginx.
`nginx.conf` proxies `/api/` to `http://api:8000/` (the compose service name)
with buffering disabled so the SSE stream is delivered immediately.

## API contract

All calls go through the `/api` prefix, which the dev server and nginx strip:
`/stats`, `/drift`, `/model`, `/health`, `/alerts?limit&status`, `/alerts/{id}`,
`POST /alerts/{id}/feedback {label}`, and the SSE feed `/alerts/stream`
(events `ready`, `alert`, `ping`). Types live in `src/types.ts`.
