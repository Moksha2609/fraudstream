#!/usr/bin/env bash
# Smoke-test a running scoring API: health, one synchronous /score call with
# its round-trip latency, and the /stats summary.
#
# Usage: API_URL=http://localhost:8000 scripts/smoke.sh
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"

echo "== GET $API_URL/health"
curl -fsS "$API_URL/health"; echo

V28=$(python3 -c "import random;print(','.join(f'{random.gauss(0,1):.4f}' for _ in range(28)))" 2>/dev/null \
      || printf '0%.0s,' $(seq 1 27) | sed 's/,$//' | sed 's/^/0,/')
BODY=$(cat <<JSON
{"tx_id":"smoke-$(date +%s)","time":12345.0,"amount":149.62,"v":[${V28}]}
JSON
)

echo "== POST $API_URL/score"
RESP=$(curl -fsS -o /dev/stdout -w '\n%{time_total}' -H 'content-type: application/json' \
       -d "$BODY" "$API_URL/score")
echo "$RESP" | head -n 1
SECS=$(echo "$RESP" | tail -n 1)
MS=$(awk -v s="$SECS" 'BEGIN{printf "%.1f", s*1000}')
echo "round-trip latency: ${MS} ms"
awk -v s="$SECS" 'BEGIN{exit !(s < 0.1)}' && echo "latency OK (< 100 ms)" || echo "WARNING: latency above 100 ms"

echo "== GET $API_URL/stats"
curl -fsS "$API_URL/stats"; echo
