#!/usr/bin/env bash
# Builds the dashboard from /opt/trading/state and publishes it to the stack's Amplify app.
# Runs as root on the server from trading-dashboard.service, after the 16:30 state backup.
#
# Fail-closed: it uploads only if basic auth is on for both the app and the branch, and afterwards it
# checks that the live URL still answers 401 without the password. The page never leaves the server
# any other way, and the presigned upload URL is never printed.
set -euo pipefail

# shellcheck disable=SC1091
. /etc/trading.env                     # BUCKET, DASHBOARD_APP_ID (written by install.sh)
APP=${DASHBOARD_APP_ID:-}
BRANCH=main
OUT=/opt/trading/state/dashboard
[ -n "$APP" ] || { echo "No DASHBOARD_APP_ID in /etc/trading.env: the stack has no dashboard yet; nothing to publish."; exit 0; }

cd /opt/trading/app/deploy
docker compose run --rm dashboard
[ -s "$OUT/index.html" ] || { echo "The build wrote no $OUT/index.html"; exit 1; }

app_auth=$(aws amplify get-app --app-id "$APP" --query app.enableBasicAuth --output text)
branch_auth=$(aws amplify get-branch --app-id "$APP" --branch-name "$BRANCH" --query branch.enableBasicAuth --output text)
if [ "$app_auth" != True ] || [ "$branch_auth" != True ]; then
  echo "Basic auth is not on for the dashboard (app: $app_auth, branch: $branch_auth); refusing to publish."
  exit 1
fi

ZIP=$(mktemp --suffix=.zip)
trap 'rm -f "$ZIP"' EXIT
python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED).write(sys.argv[2], "index.html")' \
  "$ZIP" "$OUT/index.html"

read -r JOB UPLOAD_URL < <(aws amplify create-deployment --app-id "$APP" --branch-name "$BRANCH" \
  --query '[jobId, zipUploadUrl]' --output text)
curl -fsS -X PUT -H "Content-Type: application/zip" --upload-file "$ZIP" "$UPLOAD_URL" -o /dev/null \
  || { echo "Upload of the dashboard bundle failed"; exit 1; }
aws amplify start-deployment --app-id "$APP" --branch-name "$BRANCH" --job-id "$JOB" >/dev/null

for _ in $(seq 60); do
  STATUS=$(aws amplify get-job --app-id "$APP" --branch-name "$BRANCH" --job-id "$JOB" --query job.summary.status --output text)
  case $STATUS in SUCCEED|FAILED|CANCELLED) break ;; *) sleep 5 ;; esac
done
echo "Dashboard deployment $JOB: $STATUS"
[ "$STATUS" = SUCCEED ] || exit 1

DOMAIN=$(aws amplify get-app --app-id "$APP" --query app.defaultDomain --output text)
CODE=$(curl -s -o /dev/null -w '%{http_code}' "https://$BRANCH.$DOMAIN/")
if [ "$CODE" != 401 ]; then
  echo "WARNING: https://$BRANCH.$DOMAIN/ answered $CODE without a password (expected 401). Check basic auth now."
  exit 1
fi
echo "Published https://$BRANCH.$DOMAIN/ (password required: answered 401 without it)."
