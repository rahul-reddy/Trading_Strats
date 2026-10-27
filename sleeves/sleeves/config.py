"""Configuration. Defaults live here; a profile TOML overrides any subset of them (`extends` chains profiles).

One profile is one bucket: one sleeve (rotation, sectors or quality) with its own capital and ledger."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ROTATION_UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ", "TLT", "IEF", "GLD", "DBC", "LQD", "HYG"]
SECTOR_UNIVERSE = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE"]
KINDS = ("rotation", "sectors", "quality")


@dataclass
class SleeveConfig:
    kind: str = "rotation"  # rotation | sectors | quality
    universe: list[str] = field(default_factory=list)  # blank = the kind's default (quality: the S&P 500)
    cash_symbol: str = "SGOV"  # 0-3 month T-bill ETF: where unfilled slots and filtered-out money sit
    benchmark: str = "SPY"
    invest_fraction: float = 0.99  # leave 1% in cash: MOC fills differ a little from the sizing price
    rebalance_band: float = 0.02  # an existing holding is resized only if its weight is off by more than this

    # --- rotation: multi-asset momentum with an absolute trend filter (STRATEGY.md) ---
    top_n: int = 4  # rotation: hold the best 4 eligible (backtest grid: 4-5 beat 3 on Sharpe and drawdown)
    score_lookbacks: list[int] = field(default_factory=lambda: [21, 63, 126, 252])  # 1/3/6/12-month returns
    trend_days: int = 210  # about 10 months: an asset must be above its 210-day average to be held

    # --- sectors: SPDR sector momentum with a market filter ---
    sector_top_n: int = 3
    mom_lookback: int = 252  # 12-month momentum ...
    mom_skip: int = 21  # ... skipping the most recent month

    # --- sectors and quality: hold stocks only while the market is above its 10-month average ---
    filter_symbol: str = "SPY"
    filter_months: int = 10

    # --- quality: cheap and profitable S&P 500 stocks, rebalanced once a year ---
    q_cheap_fraction: float = 0.40  # cheapest 40% by price-to-book ...
    q_profit_fraction: float = 0.40  # ... that are also in the top 40% by return on equity
    q_max_names: int = 25
    q_max_per_sector: int = 5
    q_rebalance_months: int = 12

    def symbols(self) -> list[str]:
        if self.universe:
            return list(self.universe)
        return {"rotation": ROTATION_UNIVERSE, "sectors": SECTOR_UNIVERSE, "quality": []}[self.kind]


@dataclass
class RiskConfig:
    capital: float = 10_000.0  # dollars dedicated to this bucket
    max_order_value: float = 25_000.0
    max_drawdown_halt: float = 0.40  # halt (manual reset) if bucket equity falls this far from its peak
    max_signal_move: float = 0.08  # stop if the benchmark moved more than this today (bad data or a crash day)
    min_minutes_to_close: int = 5
    max_minutes_to_close: int = 30
    account_check: str = "exact"  # ledger.check_account: "exact" (dedicated account) or "at_least"


@dataclass
class BrokerConfig:
    name: str = "paper"  # paper | ibkr
    live_trading_enabled: bool = False
    quote_source: str = "broker"  # broker | finviz (Elite export API, real time)
    finviz_token_env: str = "FINVIZ_TOKEN"  # environment variable holding the Finviz Elite token
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 21
    ibkr_account: str = ""
    ibkr_account_type: str = "paper"  # paper | live; checked against the account ID on connect (paper IDs start with D)
    ibkr_use_moc: bool = True


@dataclass
class Config:
    sleeve: SleeveConfig = field(default_factory=SleeveConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    state_dir: str = "state"
    bucket: str = "sleeves/local"  # order tag (IBKR orderRef); unique per account
    # Buckets with the same account_group share one account and are checked together. The local paper broker
    # keeps a simulated account per state_dir, so local paper profiles each need their own account_group.
    account_group: str = "local-paper-sleeves"
    label: str = ""  # name on the dashboard


def _apply(obj, overrides: dict):
    known = {f.name for f in fields(obj)}
    for k, v in overrides.items():
        if k not in known:
            raise KeyError(f"Unknown config key '{k}' in [{type(obj).__name__}]")
        setattr(obj, k, v)


def _read(path: Path) -> dict:
    """Read a TOML file. `extends = "other.toml"` (relative to this file) loads that file first, and this
    file's values override it key by key."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = data.pop("extends", None)
    if base is None:
        return data
    merged = _read(path.parent / base)
    for k, v in data.items():
        merged[k] = {**merged.get(k, {}), **v} if isinstance(v, dict) else v
    return merged


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path is not None:
        data = _read(Path(path))
        for section in ("sleeve", "risk", "broker"):
            _apply(getattr(cfg, section), data.get(section, {}))
        for key in ("state_dir", "bucket", "account_group", "label"):
            if key in data:
                setattr(cfg, key, data[key])
    if cfg.sleeve.kind not in KINDS:
        raise ValueError(f"sleeve.kind must be one of {KINDS}, not '{cfg.sleeve.kind}'")
    return cfg
