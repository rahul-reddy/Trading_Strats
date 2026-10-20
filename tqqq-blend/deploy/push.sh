#!/usr/bin/env bash
# Deploys a merged revision of tqqq-blend to the trading server:
# git archive -> S3 -> SSM Run Command -> server/install.sh. Manual on purpose; nothing deploys on merge.
# Run from Git Bash on your PC (or any bash with git and the AWS CLI), from anywhere:
#
#   deploy/push.sh                       # deploys origin/main; uses AWS_PROFILE (default: trading-deploy)
#   deploy/push.sh <commit>              # an earlier merged commit, e.g. to roll back
#   KEEP_RUNNING=1 deploy/push.sh        # leave the server on afterwards (e.g. to run ibkr-check)
#
# The bundle is the committed tqqq-blend folder at that revision, never the working tree, and the
# revision must already be on origin/main (merged through scripts/merge-pr.ps1). You confirm by
# typing "deploy <short sha>". The deployed revision is written to /opt/trading/app/REVISION.
# If the server is stopped (outside 15:00-16:45 ET), it is started for the install and stopped
# again afterwards. Starting it logs the gateways in, so expect a 2FA push for the live account.
set -euo pipefail
export MSYS_NO_PATHCONV=1            # stop Git Bash rewriting /opt/... into C:/Program Files/Git/opt/...
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 # the AWS CLI otherwise fails printing non-ASCII output on Windows
export AWS_PROFILE=${AWS_PROFILE:-trading-deploy} AWS_REGION=${AWS_REGION:-us-east-1}
STACK=${STACK:-tqqq-trading}
cd "$(dirname "$0")/.."

git fetch --quiet origin main
SHA=$(git rev-parse --verify "${1:-origin/main}^{commit}")
git merge-base --is-ancestor "$SHA" origin/main || { echo "$SHA is not on origin/main; merge it first."; exit 1; }
SHORT=${SHA:0:7}
echo "Deploying tqqq-blend at $SHORT: $(git log -1 --format=%s "$SHA")"
read -r -p "Type 'deploy $SHORT' to continue: " answer
[ "$answer" = "deploy $SHORT" ] || { echo "Not confirmed; nothing deployed."; exit 1; }

out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
INSTANCE=$(out InstanceId)
BUCKET=$(out BucketName)
KEY=releases/tqqq-blend-$(date -u +%Y%m%dT%H%M%SZ)-$SHORT.tgz

STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE" --query "Reservations[0].Instances[0].State.Name" --output text)
STARTED=
if [ "$STATE" != running ]; then
  echo "Server is $STATE; starting it ..."
  aws ec2 wait instance-stopped --instance-ids "$INSTANCE" 2>/dev/null || true
  aws ec2 start-instances --instance-ids "$INSTANCE" >/dev/null
  STARTED=1
fi
until [ "$(aws ssm describe-instance-information --filters Key=InstanceIds,Values="$INSTANCE" \
          --query "InstanceInformationList[0].PingStatus" --output text)" = Online ]; do sleep 5; done

BUNDLE=.push-bundle.tgz            # relative: Git Bash /tmp paths mean nothing to the Windows AWS CLI
trap 'rm -f "$BUNDLE"' EXIT
tar czf "$BUNDLE" --exclude=state --exclude='*.log' --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=deploy/.env pyproject.toml uv.lock config*.toml tqqq_blend tests deploy
aws s3 cp --only-show-errors "$BUNDLE" "s3://$BUCKET/$KEY"
echo "Uploaded s3://$BUCKET/$KEY; installing on $INSTANCE ..."

CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
  --comment "tqqq-blend $KEY" --timeout-seconds 900 --query Command.CommandId --output text \
  --parameters commands="[
    \"set -euo pipefail\",
    \"aws s3 cp --only-show-errors s3://$BUCKET/$KEY /tmp/release.tgz\",
    \"rm -rf /opt/trading/app.new && mkdir -p /opt/trading/app.new && tar xzf /tmp/release.tgz -C /opt/trading/app.new\",
    \"rm -rf /opt/trading/app.old && { [ ! -d /opt/trading/app ] || mv /opt/trading/app /opt/trading/app.old; } && mv /opt/trading/app.new /opt/trading/app && echo $SHA > /opt/trading/app/REVISION\",
    \"bash /opt/trading/app/deploy/server/install.sh $BUCKET\"
  ]")

while :; do
  STATUS=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE" --query Status --output text 2>/dev/null || echo Pending)
  case $STATUS in Pending|InProgress|Delayed) sleep 5 ;; *) break ;; esac
done
aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE" \
  --query "[StandardOutputContent,StandardErrorContent]" --output text
echo "Status: $STATUS"
if [ -n "$STARTED" ] && [ -z "${KEEP_RUNNING:-}" ]; then
  aws ec2 stop-instances --instance-ids "$INSTANCE" >/dev/null
  echo "Stopped the server again (it was off before this push)."
fi
[ "$STATUS" = Success ]
