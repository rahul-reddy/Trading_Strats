#!/usr/bin/env bash
# The three sleeves paper buckets, one after another (sleeves-paper.service). A failure in one is recorded in
# its runs.jsonl and doesn't skip the others; the exit status is non-zero if any of them failed.
set -uo pipefail
cd /opt/trading/app/deploy
status=0
for svc in sleeves-rotation sleeves-sectors sleeves-quality; do
  echo "== $svc"
  /usr/bin/docker compose run --rm "$svc" run --live || status=1
done
exit $status
