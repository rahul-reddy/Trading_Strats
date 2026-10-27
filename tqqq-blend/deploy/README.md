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
15:32  the mix portfolio's 20% bucket runs (every 5 minutes to 15:52, two minutes after the $100k bucket)
15:34  the sleeves buckets (rotation, sectors, quality) run one after another (also 15:39, 15:44, 15:49);
       they value themselves daily and trade only at a month-end
15:50  last run of the window (runs after the first successful one do nothing)
16:10  reconcile: each paper bucket's fills are credited to its own ledger (docs/BUCKETS.md)
16:30  state (ledgers, trade logs) is copied to S3
16:35  the dashboard is rebuilt from that state and published (password-protected)
16:45  server stops
```

- **Missed push:** `TWOFA_TIMEOUT_ACTION: restart` is meant to restart the login and send a new push. In the
  first test (2026-10-20), the gateway stayed on the 2FA dialog for 7+ minutes without resending. Until a resend
  is confirmed, run `sudo docker restart ibgw-live` from a Session Manager shell to get a new push. If none is
  approved by 15:50, the live run fails and places nothing. Paper is unaffected.
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
  aws/stack.yml                 instance, IAM role, security group, S3 bucket (releases + state backups),
                                Amplify app + generated login for the dashboard
  aws/deploy-user-policy.json   permissions for the trading-deploy IAM user that runs everything below
                                (a managed policy: it is over IAM's 2,048-character limit for inline user policies)
  Dockerfile, docker-compose.yml
  push.sh                       merged commit -> git archive -> S3 -> SSM Run Command -> server/install.sh
  server/install.sh             installs Docker, pulls secrets from SSM, starts gateways, enables timers
  server/*.service, *.timer     paper runs (orders on), live runs (dry run), post-close reconcile, state backup,
                                dashboard
  server/reconcile.sh           credits each paper bucket's fills to its ledger after the close
  server/sleeves-paper.sh       runs the three sleeves buckets one after another (sleeves-paper.timer)
  server/publish-dashboard.sh   builds the dashboard page and uploads it to Amplify, only behind the password
```

| Account | Gateway | Strategy config | Orders |
|---|---|---|---|
| Paper | `ibgw-paper` | `config.aws-paper.toml` -> `config.ibkr-paper.toml` | on |
| Paper, mix 20% bucket | `ibgw-paper` | `config.aws-paper-mix.toml` -> `config.aws-paper.toml` | on |
| Paper, sleeves buckets | `ibgw-paper` | `sleeves/config.aws-paper-{rotation,sectors,quality}.toml` (image `sleeves`) | on |

`push.sh` ships the other strategies that run on this server inside the same bundle, under
`strategies/<name>/`; `install.sh` builds their images. The dashboard shows every bucket and the mix
portfolio (`--buckets`, `--portfolio` in `docker-compose.yml`).
| Live | `ibgw-live` | `config.aws-live.toml` -> `config.ibkr-live.toml` | off (dry run, logs the plan) |

## First-time setup

All commands run in Git Bash on your PC. Set `export MSYS_NO_PATHCONV=1` first, or Git Bash rewrites
paths like `/trading/...` into Windows paths.

**1. Deploy user** (once, with your admin credentials):

```bash
aws iam create-user --user-name trading-deploy
ACCT=$(aws sts get-caller-identity --query Account --output text)
aws iam create-policy --policy-name trading-deploy --policy-document file://deploy/aws/deploy-user-policy.json
aws iam attach-user-policy --user-name trading-deploy --policy-arn "arn:aws:iam::$ACCT:policy/trading-deploy"
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

Finviz Elite token, for real-time quotes (`quote_source = "finviz"` in `config.toml`). Without the token,
runs fall back to IBKR quotes, which are 15 minutes delayed on a paper account.

```bash
read -s -p "finviz token: " t; echo; aws ssm put-parameter --type SecureString --overwrite --name /trading/finviz/token --value "$t"; unset t
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

- **Updating the code:** merge the change (scripts/merge-pr.ps1), then run `deploy/push.sh` and type `deploy <sha>` when asked.
  It ships `origin/main` (or the commit you name, e.g. to roll back) via `git archive`, never the working tree, and
  refuses a commit that isn't on `origin/main`. The server records what it runs in `/opt/trading/app/REVISION`.
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

## Dashboard

A single page showing each account's latest decision, performance against QQQ and TQQQ buy-and-hold,
drawdown against the halt line, net exposure, run health (missed runs, failures, kill switch, halt) and the
trade log. At 16:35 ET on weekdays the server builds it from `/opt/trading/state` (the `dashboard` Compose
service, which has no network) and uploads it to an Amplify app. It is static: Amplify only serves the
last page uploaded, and nothing on it can place or change anything.

**Getting the address and login:**

```bash
aws cloudformation describe-stacks --stack-name tqqq-trading   --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text
aws secretsmanager get-secret-value --secret-id tqqq-trading-dashboard --query SecretString --output text
```

The username and the 40-character password are generated when the stack creates the dashboard. Save them
in your password manager or browser; nobody else gets them.

**Setting it up** (once, after this change is merged):

1. Update the deploy user's permissions (Amplify and the dashboard secret), with your admin credentials:
   the policy is a managed one, so a change is a new default version (IAM keeps at most five; delete the oldest
   first if it refuses):
   `aws iam create-policy-version --policy-arn arn:aws:iam::<account>:policy/trading-deploy --policy-document file://deploy/aws/deploy-user-policy.json --set-as-default`.
   A deploy user still on the old inline policy gets `LimitExceeded` (IAM's 2,048-character limit for inline user
   policies). Move it with `create-policy` and `attach-user-policy` as in First-time setup, then
   `aws iam delete-user-policy --user-name trading-deploy --policy-name trading-deploy`.
2. Update the stack: `aws cloudformation deploy --stack-name tqqq-trading --template-file deploy/aws/stack.yml --capabilities CAPABILITY_IAM`
3. Run `deploy/push.sh`. It hands the app ID to the server, which turns on `trading-dashboard.timer`.
   To publish right away instead of at 16:35, push with `KEEP_RUNNING=1` and run
   `sudo systemctl start trading-dashboard` in a Session Manager shell (`journalctl -u trading-dashboard` shows the result).

**What keeps it private:**
- Basic auth is set on the app and on its branch, from the generated secret; the template never contains it.
- `publish-dashboard.sh` checks that both are on before every upload, and refuses to publish if either is off.
  After the upload it requests the page without the password and fails loudly unless it gets `401`.
- The page has no account numbers (anything shaped like an IBKR account ID is replaced), no credentials, and
  loads nothing from other sites. Headers forbid indexing, framing and caching.
- The server's role can deploy to this one Amplify app and nothing else; there are still no inbound ports.

**Cost:** Secrets Manager $0.40 a month for the secret; Amplify hosting is a few cents a month at this size
(a ~70 KB page, uploaded once a day). **Blast radius:** if the password leaked, someone could read the
dashboard (balances, positions, trades); they could not trade, reach the server or change anything. To change
the password, write a new one to the secret (`aws secretsmanager put-secret-value`) and set the same login on
the app and the branch (`aws amplify update-app` and `aws amplify update-branch` with `--enable-basic-auth
--basic-auth-credentials <base64 of user:password>`). A later stack update re-applies whatever the secret holds.

**Preview locally** from any state folder: `uv run python -m tqqq_blend dashboard --out state/dashboard`, then open
`state/dashboard/index.html`.

## Turning on live orders

Only after 4+ weeks of clean paper runs, and after the live dry runs plan the trades you expect:

1. In `config.ibkr-live.toml`, set `live_trading_enabled = true` and `capital` to the amount for this stage.
2. In `deploy/server/tqqq-live.service`, change `run` to `run --live`.
3. Run `deploy/push.sh`.
