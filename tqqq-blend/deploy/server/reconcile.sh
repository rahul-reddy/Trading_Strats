#!/usr/bin/env bash
# Post-close reconcile for every paper bucket, one after another (trading-reconcile.service).
# Each `reconcile` connects read-only and does nothing when its bucket has no pending orders.
# A failure in one bucket is recorded in its reconcile.jsonl and doesn't skip the others; the exit
# status is non-zero if any of them failed, so the unit shows as failed.
set -uo pipefail
cd /opt/trading/app/deploy
status=0
services="tqqq-paper tqqq-paper-mix"
if docker image inspect sleeves:latest >/dev/null 2>&1; then
  services="$services sleeves-rotation sleeves-sectors sleeves-quality"
fi
for svc in $services; do
  echo "== $svc"
  /usr/bin/docker compose run --rm "$svc" reconcile || status=1
done
exit $status
