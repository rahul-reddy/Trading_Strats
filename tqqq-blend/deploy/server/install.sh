#!/usr/bin/env bash
# Sets up (or updates) the trading server. Runs as root on the EC2 instance; deploy/push.sh calls it
# through SSM Run Command after unpacking the code into /opt/trading/app. Safe to re-run.
#
#   install.sh <bucket> [dashboard-app-id]
#
# Reads the IBKR logins from SSM Parameter Store (/trading/ibkr/{paper,live}/{username,password}).
# Each account is optional; its gateway and timer run only when its login exists. IBKR allows one
# session per username, so paper and live need different usernames to run side by side.
set -euo pipefail

BUCKET=${1:?usage: install.sh <bucket> [dashboard-app-id]}
DASHBOARD_APP_ID=${2:-}                # the stack's Amplify app; empty until the stack has one
SECRETS_PATH=/trading/ibkr
COMPOSE_VERSION=v5.6.0
ROOT=/opt/trading
APP=$ROOT/app
cd "$APP/deploy"

# Docker and the Compose plugin
if ! command -v docker >/dev/null; then
  dnf install -y docker
fi
systemctl enable --now docker
PLUGIN=/usr/local/lib/docker/cli-plugins/docker-compose
if ! docker compose version 2>/dev/null | grep -q "${COMPOSE_VERSION#v}"; then
  mkdir -p "$(dirname "$PLUGIN")"
  curl -fsSL -o "$PLUGIN" "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-x86_64"
  chmod +x "$PLUGIN"
fi
timedatectl set-timezone America/New_York

# Directories. The gateway image runs as uid 1000 and writes its settings to its volume.
mkdir -p "$ROOT/state" "$ROOT/gateway/paper" "$ROOT/gateway/live" "$ROOT/secrets"
chown 1000:1000 "$ROOT/gateway/paper" "$ROOT/gateway/live"
chmod 700 "$ROOT/secrets"

# Secrets from SSM
param() { aws ssm get-parameter --with-decryption --name "$SECRETS_PATH/$1" --query Parameter.Value --output text 2>/dev/null; }
write_secret() {  # write_secret <file> <value>; readable by the gateway user only
  install -m 400 -o 1000 -g 1000 /dev/null "$1"
  printf '%s' "$2" > "$1"
}
PAPER_USER=$(param paper/username || true)
LIVE_USER=$(param live/username || true)
if [ -z "$PAPER_USER$LIVE_USER" ]; then
  echo "no IBKR login in SSM under $SECRETS_PATH (paper/username or live/username)"; exit 1
fi
if [ -n "$PAPER_USER" ] && [ "$PAPER_USER" = "$LIVE_USER" ]; then
  echo "paper and live use the same username; IBKR allows one session per username, so they can't run together"; exit 1
fi
write_secret "$ROOT/secrets/ibkr_paper_password" "$([ -z "$PAPER_USER" ] || param paper/password)"
write_secret "$ROOT/secrets/ibkr_live_password" "$([ -z "$LIVE_USER" ] || param live/password)"
FINVIZ_TOKEN=$(aws ssm get-parameter --with-decryption --name /trading/finviz/token --query Parameter.Value --output text 2>/dev/null || true)
[ -n "$FINVIZ_TOKEN" ] || echo "No /trading/finviz/token in SSM: runs will use broker quotes."
install -m 600 /dev/null "$ROOT/secrets/ibkr.env"
printf 'IBKR_PAPER_USERNAME=%s\nIBKR_LIVE_USERNAME=%s\nFINVIZ_TOKEN=%s\n' "$PAPER_USER" "$LIVE_USER" "$FINVIZ_TOKEN" > "$ROOT/secrets/ibkr.env"
ln -sfn "$ROOT/secrets/ibkr.env" "$APP/deploy/.env"
printf 'BUCKET=%s\nDASHBOARD_APP_ID=%s\n' "$BUCKET" "$DASHBOARD_APP_ID" > /etc/trading.env

# Strategy image, then for each account with a login: its gateway and its timer
docker build -q -f "$APP/deploy/Dockerfile" -t tqqq-blend:latest "$APP"   # compose build needs buildx, which AL2023 lacks
SLEEVES=$APP/strategies/sleeves           # push.sh ships the other strategies on this server under strategies/
if [ -f "$SLEEVES/deploy/Dockerfile" ]; then
  docker build -q -f "$SLEEVES/deploy/Dockerfile" -t sleeves:latest "$SLEEVES"
fi
cp "$APP"/deploy/server/*.service "$APP"/deploy/server/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trading-backup.timer
if [ -n "$DASHBOARD_APP_ID" ]; then
  systemctl enable --now trading-dashboard.timer
else
  systemctl disable --now trading-dashboard.timer 2>/dev/null || true
  echo "No dashboard app in the stack: dashboard timer is off."
fi
for acct in paper live; do
  user=$([ $acct = paper ] && echo "$PAPER_USER" || echo "$LIVE_USER")
  if [ -n "$user" ]; then
    docker compose --profile $acct up -d ibgw-$acct
    systemctl enable --now tqqq-$acct.timer
    # paper also runs the mix bucket and the post-close reconcile of the paper buckets' fills
    [ $acct = live ] || systemctl enable --now tqqq-paper-mix.timer trading-reconcile.timer
    if [ $acct = paper ] && [ -f "$SLEEVES/deploy/Dockerfile" ]; then systemctl enable --now sleeves-paper.timer; fi
  else
    docker compose --profile $acct rm -sf ibgw-$acct >/dev/null 2>&1 || true
    systemctl disable --now tqqq-$acct.timer 2>/dev/null || true
    [ $acct = live ] || systemctl disable --now tqqq-paper-mix.timer trading-reconcile.timer sleeves-paper.timer 2>/dev/null || true
    echo "No $acct login in SSM: $acct gateway and timer are off."
  fi
done

docker image prune -f >/dev/null
echo "Installed. Gateways:"; docker ps --format '  {{.Names}}  {{.Status}}'
echo "Timers:"; systemctl list-timers 'tqqq-*' 'trading-*' 'sleeves-*' --no-pager
