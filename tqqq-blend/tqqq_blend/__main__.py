"""CLI:  python -m tqqq_blend <command> [options]

  backtest      full-history backtest of the blend and each sleeve (synthetic TQQQ/SQQQ from 1999)
  signal        today's signal and sleeve diagnostics from end-of-day data (no broker needed)
  run           the near-close process: compute targets, plan orders, execute (dry-run by default)
  ibkr-check    read-only IBKR connection test: account, balances, positions, quotes (never places orders)
  schwab-login  one-time (and weekly) Schwab OAuth login that writes the token file

Broker profiles: python -m tqqq_blend --config config.ibkr-paper.toml <command>
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from .config import load_config


def main():
    ap = argparse.ArgumentParser(prog="tqqq_blend")
    ap.add_argument("--config", default="config.toml" if Path("config.toml").exists() else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--start", default="2000-03-01")
    b.add_argument("--cost-bps", type=float, default=5.0)
    b.add_argument("--refresh", action="store_true", help="re-download data")
    b.add_argument("--out", default="state/backtest")
    sub.add_parser("signal").add_argument("--days", type=int, default=15)
    r = sub.add_parser("run")
    r.add_argument("--broker", choices=["paper", "ibkr", "schwab"])
    r.add_argument("--live", action="store_true", help="actually send orders (also needs live_trading_enabled)")
    r.add_argument("--force", action="store_true", help="ignore the near-close window and stale-data check")
    sub.add_parser("ibkr-check")
    sub.add_parser("schwab-login")
    args = ap.parse_args()

    cfg = load_config(args.config)
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(Path(cfg.state_dir) / "tqqq_blend.log")])
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.float_format", "{:.3f}".format)

    if args.cmd == "backtest":
        from .backtest import annual_returns, backtest, validate_synthetic
        table, curves, sig, res = backtest(cfg.strategy, args.start, args.cost_bps, args.refresh)
        print(f"\nBacktest from {args.start}, {args.cost_bps} bps per trade, signal at close t -> held t+1\n")
        print(table.to_string())
        yearly = pd.DataFrame({k: annual_returns(v) for k, v in curves.items()})
        yearly.index = yearly.index.year
        print("\nAnnual returns\n" + yearly.to_string())
        v = validate_synthetic()
        print(f"\nSynthetic vs real TQQQ since 2010: daily corr {v['daily_corr']:.4f}, "
              f"CAGR real {v['real_cagr']:.1%} vs synthetic {v['synthetic_cagr']:.1%}")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        table.to_csv(out / "summary.csv")
        curves.to_csv(out / "equity_curves.csv")
        sig.join(res, rsuffix="_held").to_csv(out / "signals.csv")
        print(f"\nWrote {out}/summary.csv, equity_curves.csv, signals.csv")

    elif args.cmd == "signal":
        from .data import daily_bars
        from .strategy import compute_signals
        sig = compute_signals(daily_bars(cfg.strategy.signal_symbol, refresh=True), cfg.strategy)
        cols = ["close", "sma_fast", "sma_slow", "ext", "rsi_mr", "dist_days", "sell_flags",
                "trend", "mr", "vibha", "vibha_event", "vol_scale", "net", "w_long", "w_short"]
        print(sig[cols].tail(args.days).to_string())

    elif args.cmd == "run":
        if args.broker:
            cfg.broker.name = args.broker
        from .runner import run_once
        print(json.dumps(run_once(cfg, live=args.live, force=args.force), indent=2, default=str))

    elif args.cmd == "ibkr-check":
        from .brokers.ibkr import IBKRBroker
        b = cfg.broker
        print(f"Connecting read-only to {b.ibkr_host}:{b.ibkr_port}, expecting a {b.ibkr_account_type} account "
              f"(config {args.config}, state_dir {cfg.state_dir})")
        broker = IBKRBroker(cfg)
        broker.connect(readonly=True, client_id=b.ibkr_client_id + 100)  # never collides with a scheduled run
        try:
            print(json.dumps(broker.describe(), indent=2, default=str))
            print(f"\nConnected. Orders for this profile are {'ENABLED' if b.live_trading_enabled else 'disabled'} "
                  f"(live_trading_enabled = {str(b.live_trading_enabled).lower()}).")
        finally:
            broker.disconnect()

    elif args.cmd == "schwab-login":
        from .brokers.schwab import SchwabBroker
        SchwabBroker(cfg).connect(interactive=True)
        print("Schwab token saved to", cfg.broker.schwab_token_path)


if __name__ == "__main__":
    main()
