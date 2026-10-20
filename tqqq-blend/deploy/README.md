# AWS deployment

One EC2 instance (t3.medium, us-east-1) runs two IB Gateways in Docker, one per IBKR account.
IBC types in the usernames and passwords for them. systemd timers start a one-shot strategy container
near each close. There is no SSH and no inbound port: you reach the server through SSM Session
Manager, and only containers on the Compose network can reach the gateways' API ports.

**The server is on for 1¾ hours each weekday.** EventBridge Scheduler starts it at 15:00 ET and stops it
at 16:45 ET. It costs about $4–5 a month: ~$1.60 for the instance, $2.40 for the disk (billed while
stopped too) and ~$0.20 for the public IP. Running it all the time would cost about $36.

## Daily routine: one tap

```
15:00  server starts; both gateways log in
~15:02 IBKR Mobile push for the live account -> approve it
15:30  first run: paper places orders, live logs the orders it would place (dry run)
15:50  last run of the window (runs after the first successful one do nothing)
16:30  state (ledgers, trade logs) is copied to S3
16:45  server stops
```

- **Missed push:** if you miss it, the gateway restarts and sends another about every 3–4 minutes until the
  first run at 15:30. If none is approved by 15:50, the live run fails and places nothing. Paper is unaffected.
- **Paper login:** paper logins normally don't need 2FA. If yours does, you get a second push.
- **Paper username:** the paper account needs its own username, because IBKR allows one session per
  username and the live gateway uses yours. Client Portal shows the paper username, and lets you reset its
  password, under *Settings → Paper Trading Account*. Store it under `/trading/ibkr/paper/` and re-run `push.sh`.
- **2FA device:** the gateways select `TWOFA_DEVICE: IB Key` in `docker-compose.yml`. A login with more than one
  2FA method otherwise stops at a "choose a device" dialog that nobody can click.
- **Push only:** the server can't use SMS or authenticator-app codes. In Client Portal, go to
  *Settings → Secure Login System* and set IBKR Mobile (push) as the method for both usernames.
- **Holidays:** the server starts on market holidays too. The runner skips them, but the push still arrives,
  so ignore it or approve it.
- **Half-days:** the 15:00 start misses 13:00 closes, so no trade happens. That's acceptable for this strategy.
- **Moving the window:** change the times with the `StartCron` / `StopCron` stack parameters.

```
deploy/
  aws/stack.yml                 instance, IAM role, security group, S3 bucket (releases + state backups)
  aws/deploy-user-policy.json   permissions for the trading-deploy IAM user that runs everything below
  Dockerfile, docker-compose.yml
  push.sh                       bundle -> S3 -> SSM Run Command -> server/install.sh
  server/install.sh             installs Docker, pulls secrets from SSM, starts gateways, enables timers
  server/*.service, *.timer     paper runs (orders on), live runs (dry run), daily state backup
```

| Account | Gateway | Strategy config | Orders |
|---|---|---|---|
| Paper | `ibgw-paper` | `config.aws-paper.toml` -> `config.ibkr-paper.toml` | on |
| Live | `ibgw-live` | `config.aws-live.toml` -> `config.ibkr-live.toml` | off (dry run, logs the plan) |

## First-time setup

All commands run in Git Bash on your PC. Set `export MSYS_NO_PATHCONV=1` first, or Git Bash rewrites
paths like `/trading/...` into Windows paths.

**1. Deploy user** (once, with your admin credentials):

```bash
aws iam create-user --user-name trading-deploy
aws iam put-user-policy --user-name trading-deploy --policy-name trading-deploy \
  --policy-document file://deploy/aws/deploy-user-policy.json
aws iam create-access-key --user-name trading-deploy       # paste the keys into the next command
aws configure --profile trading-deploy                      # region us-east-1
export AWS_PROFILE=trading-deploy
```

**2. IBKR logins in Parameter Store.** `read -s` keeps passwords off the screen and out of shell history.
Use a dedicated IBKR username for the server if you also log in from your PC or phone: IBKR allows
one session per username, and the gateway takes the session over (`EXISTING_SESSION_DETECTED_ACTION: primary`).

```bash
for acct in paper live; do
  read -p "$acct username: " u; read -s -p "$acct password: " p; echo
  aws ssm put-parameter --type SecureString --overwrite --name /trading/ibkr/$acct/username --value "$u"
  aws ssm put-parameter --type SecureString --overwrite --name /trading/ibkr/$acct/password --value "$p"
done
unset u p
```

Skip `live` to run paper only. The live gateway and timer stay off until those parameters exist.

**3. Stack, then code:**

```bash
aws cloudformation deploy --stack-name tqqq-trading --template-file deploy/aws/stack.yml --capabilities CAPABILITY_IAM
deploy/push.sh
```

`push.sh` starts the server if it's stopped and stops it again when it's done. When the live gateway
logs in, approve the push on your phone.

**4. Check both connections.** Push with the server left on, approve the live push, then:

```bash
KEEP_RUNNING=1 deploy/push.sh
aws ssm start-session --target $(aws cloudformation describe-stacks --stack-name tqqq-trading \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
# on the server:
cd /opt/trading/app/deploy
sudo docker compose run --rm tqqq-paper ibkr-check
sudo docker compose run --rm tqqq-live ibkr-check
```

The 16:45 stop schedule turns the server off. To stop it sooner, run `aws ec2 stop-instances --instance-ids <id>`.

## Day to day

- **Updating the code:** run `deploy/push.sh` after changing anything. It ships the working tree, not the last commit.
- **Logs:**
  - per account: `/opt/trading/state/ibkr-{paper,live}/` holds `tqqq_blend.log`, `runs.jsonl` and `trades.csv`
  - timers: `journalctl -u tqqq-paper`
  - gateways: `docker logs ibgw-paper`
- **Backups:** the state folder goes to `s3://<bucket>/state/` at 16:30 ET every weekday.
- **Kill switch:** `sudo touch /opt/trading/state/ibkr-paper/STOP`. Or stop the timer with `sudo systemctl stop tqqq-paper.timer`.
- **Login problems:** `docker logs ibgw-live` shows where the login is stuck: waiting for 2FA, wrong password,
  or session taken over.
- **Pausing:** to skip a day, or a vacation, disable `tqqq-trading-start` in the EventBridge Scheduler console.
  Ignoring the pushes also works, but only live stops; paper keeps trading.

## Turning on live orders

Only after 4+ weeks of clean paper runs, and after the live dry runs plan the trades you expect:

1. In `config.ibkr-live.toml`, set `live_trading_enabled = true` and `capital` to the amount for this stage.
2. In `deploy/server/tqqq-live.service`, change `run` to `run --live`.
3. Run `deploy/push.sh`.
