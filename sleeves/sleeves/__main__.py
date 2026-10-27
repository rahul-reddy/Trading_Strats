"""CLI:  python -m sleeves --config <profile> <command> [options]

  backtest      rotation or sector momentum with the live rules on daily ETF closes since 1998
  signal        what the bucket's rules would hold today, from end-of-day data (no broker needed)
  run           the near-close process: value the bucket, decide at month-ends, execute (dry run by default)
  reconcile     after the close: credit today's fills to this bucket's ledger (read-only; places nothing)
  ibkr-check    read-only IBKR connection test: account, balances, positions, quotes (never places orders)

Profiles: config.aws-paper-rotation.toml, config.aws-paper-sectors.toml, config.aws-paper-quality.toml.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from .config import load_config


def main():
    ap = argparse.ArgumentParser(prog="sleeves")
    ap.add_argument("--config", default="config.toml" if Path("config.toml").exists() else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--kind", choices=["rotation", "sectors"], help="default: the profile's sleeve")
    b.add_argument("--start", default="2000-01-01")
    b.add_argument("--cost-bps", type=float, default=10.0)
    b.add_argument("--refresh", action="store_true", help="re-download data")
    sub.add_parser("signal")
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true", help="actually send orders (also needs live_trading_enabled)")
    r.add_argument("--force", action="store_true", help="ignore the near-close window and once-per-day guard")
    r.add_argument("--check-now", action="store_true", help="make today a month-end check (for dry runs)")
    sub.add_parser("reconcile")
    sub.add_parser("ibkr-check")
    args = ap.parse_args()

    cfg = load_config(args.config)
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(Path(cfg.state_dir) / "sleeves.log")],
    )
    pd.set_option("display.width", 200)

    if args.cmd == "backtest":
        from .backtest import run

        kind = args.kind or cfg.sleeve.kind
        if kind == "quality":
            raise SystemExit("quality-value needs point-in-time fundamentals; see STRATEGY.md for its CRSP proxy")
        pct = "{:.1%}"
        fmt = {"CAGR": pct, "Vol": pct, "MaxDD": pct, "Worst 12m": pct, "Sharpe": "{:.2f}", "Years": "{:.1f}"}
        print(f"\n{kind}: month-end rebalance, {args.cost_bps:g} bps per side, decided at the close, held next day")
        for title, table in run(kind, args.start, args.cost_bps, Path(cfg.state_dir) / "cache", args.refresh).items():
            print(f"\n{title}\n" + table.apply(lambda c: c.map(lambda v, f=fmt[c.name]: f.format(v))).to_string())

    elif args.cmd == "signal":
        from . import rules
        from .data import finviz_token, history, sp500_screen
        from .runner import et_today

        sc = cfg.sleeve
        need = sorted(set(sc.symbols()) | {sc.filter_symbol, sc.benchmark})
        today = et_today()
        closes = history(need, today)  # through the previous trading day (yfinance fills today in late)
        if sc.kind == "quality":
            d = rules.quality(sp500_screen(finviz_token(cfg.broker.finviz_token_env)), closes, sc)
        else:
            d = (rules.rotation if sc.kind == "rotation" else rules.sectors)(closes, sc)
        print(f"{d.summary}  (closes through {closes.index[-1].date()})")
        for r in d.reasons:
            print(f"  {r['symbol']:6} {r['weight']:5.0%}  {r['text']}")

    elif args.cmd == "run":
        from .runner import run_once

        rec = run_once(cfg, live=args.live, force=args.force, check_now=args.check_now)
        print(json.dumps(rec, indent=2, default=str))

    elif args.cmd == "reconcile":
        from .runner import reconcile

        print(json.dumps(reconcile(cfg), indent=2, default=str))

    elif args.cmd == "ibkr-check":
        from .brokers.ibkr import IBKRBroker

        b = cfg.broker
        print(f"Connecting read-only to {b.ibkr_host}:{b.ibkr_port}, expecting a {b.ibkr_account_type} account")
        broker = IBKRBroker(cfg)
        broker.connect(readonly=True, client_id=b.ibkr_client_id + 100)
        try:
            print(json.dumps(broker.describe(), indent=2, default=str))
        finally:
            broker.disconnect()


if __name__ == "__main__":
    main()
