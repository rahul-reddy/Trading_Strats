"""Configuration. Defaults live here; config.toml overrides any subset of them."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class StrategyConfig:
    signal_symbol: str = "QQQ"  # signals are computed on the unlevered index proxy
    long_symbol: str = "TQQQ"
    short_symbol: str = "SQQQ"

    # Sleeve blend weights (net exposure = sum of weight * sleeve signal, in [-1, 1])
    # Equal weights (v2). v1 used 0.40/0.20/0.40; equal weights had a smaller drawdown and a higher
    # Sharpe in both 2000-12 and 2013-26, and they are the no-opinion prior, so they are less tuned.
    weight_trend: float = 1 / 3  # Mallik momentum / trend-following
    weight_mr: float = 1 / 3  # Mallik mean reversion
    weight_vibha: float = 1 / 3  # Vibha Jha market-timing overlay

    # Final allocation caps (fraction of strategy capital)
    max_long_alloc: float = 1.00  # TQQQ
    max_short_alloc: float = 0.50  # SQQQ (shorts lose to decay faster; keep smaller)
    rebalance_band: float = 0.10  # skip trades smaller than this change in weight
    vol_cap_threshold: float = 0.60  # QQQ 20d realized vol above this...
    vol_cap_long: float = 0.50  # ...caps the net long at this
    # Volatility target (v2): scale the net long by min(1, vol_target / QQQ realized vol). Leveraged-ETF
    # decay grows with variance, so cutting size when QQQ is volatile cut the max drawdown from -54% to
    # -35% since 2000. 0 disables it. Any target from 15% to 30% helped; 20% is a round number in that range.
    vol_target: float = 0.20
    vol_target_len: int = 20

    # --- Mallik trend sleeve ---
    trend_fast: int = 20
    trend_slow: int = 250
    trend_hysteresis: float = 0.01  # enter long > SMA250*(1+h), exit < SMA250*(1-h)
    ext_tier1: float = 0.15  # extension above SMA250 where size steps down
    ext_tier2: float = 0.25
    ext_size1: float = 0.75
    ext_size2: float = 0.50
    below_fast_size: float = 0.50  # above SMA250 but below SMA20 -> weakening trend
    bb_len: int = 20
    bb_k: float = 2.0  # close above upper band = momentum phase -> full size
    short_enabled: bool = False  # SQQQ momentum shorts: lost money in backtests (V-shaped recoveries)
    short_slope_lookback: int = 5  # SMA20 must be falling over this many days

    # --- Mallik mean-reversion sleeve ---
    mr_rsi_len: int = 2
    mr_long_entry: float = 10.0  # RSI2 below this in an uptrend -> buy the dip
    mr_long_exit: float = 70.0
    mr_deep_entry: float = 5.0  # RSI2 below this AND deep below SMA250 -> small bounce trade
    mr_deep_ext: float = -0.20
    mr_deep_size: float = 0.5
    mr_short_entry: float = 90.0  # RSI2 above this in a downtrend -> fade the rally (SQQQ)
    mr_short_exit: float = 30.0
    mr_max_hold: int = 7
    mr_short_enabled: bool = True  # SQQQ rally-fade in downtrends (short, capped holding period)

    # --- Vibha Jha sleeve ---
    ftd_min_gain: float = 0.0125  # follow-through day: >= +1.25% ...
    ftd_min_day: int = 4  # ... on day 4+ of a rally attempt, on higher volume
    hhhl_days: int = 3  # 3 days of higher highs and higher lows
    hhhl_base_size: float = 0.25  # pilot size; +0.25 per strength check, max 0.75
    hhhl_max_size: float = 0.75
    reclaim_ma: int = 50  # retaking the 50-day while light -> 50% position
    reclaim_size: float = 0.50
    dist_min_drop: float = 0.002  # distribution day: down >= 0.2% on higher volume
    dist_window: int = 25
    dist_expiry_rally: float = 0.05  # a distribution day expires once price is 5% above it
    dist_warn: int = 4  # counts as a sell flag
    dist_heavy: int = 5  # caps exposure at dist_heavy_cap
    dist_heavy_cap: float = 0.50
    flags_to_trim: int = 2  # trim when >= this many flags are active and a new one fires
    trim_fraction: float = 0.25  # each trim sells this fraction of the sleeve
    short_ma: int = 10  # losing the 10-day on rising volume = flag
    exit_ma: int = 21  # close below the 21-day EMA = full exit
    vol_avg_len: int = 50
    bear_regime_cap: float = 0.50  # blend rule: below the 250-day, Vibha entries are pilots only


@dataclass
class RiskConfig:
    capital: float = 10_000.0  # dollars dedicated to this strategy (not the whole account)
    max_order_value: float = 50_000.0
    max_drawdown_halt: float = 0.50  # halt (manual reset) if strategy equity falls this far from peak
    max_signal_move: float = 0.08  # halt if QQQ moved more than this today (bad data / crash day review)
    min_minutes_to_close: int = 5
    max_minutes_to_close: int = 30
    volume_projection: float = 1.15  # partial-day volume * this ~ full-day volume at T-15min


@dataclass
class BrokerConfig:
    name: str = "paper"  # paper | ibkr | schwab
    live_trading_enabled: bool = False
    quote_source: str = "broker"  # broker | finviz (Elite export API, real time); see quotes.py
    finviz_token_env: str = "FINVIZ_TOKEN"  # environment variable holding the Finviz Elite token
    # IBKR (TWS or IB Gateway must be running). 7497 = TWS paper, 7496 = TWS live,
    # 4002 = Gateway paper, 4001 = Gateway live.
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 17
    ibkr_account: str = ""
    ibkr_account_type: str = "paper"  # paper | live; checked against the account ID on connect (paper IDs start with D)
    ibkr_use_moc: bool = True
    # Schwab: secrets come from env vars SCHWAB_API_KEY / SCHWAB_APP_SECRET
    schwab_callback_url: str = "https://127.0.0.1:8182"
    schwab_token_path: str = "state/schwab_token.json"
    schwab_account_hash: str = ""  # leave blank to use the first account


@dataclass
class Config:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    state_dir: str = "state"


def _apply(obj, overrides: dict):
    known = {f.name for f in fields(obj)}
    for k, v in overrides.items():
        if k not in known:
            raise KeyError(f"Unknown config key '{k}' in [{type(obj).__name__}]")
        setattr(obj, k, v)


def _read(path: Path) -> dict:
    """Read a TOML file. `extends = "other.toml"` (relative to this file) loads that file first, and this
    file's values override it key by key. Broker profiles use it to share one strategy config."""
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
    if path is None:
        return cfg
    data = _read(Path(path))
    for section in ("strategy", "risk", "broker"):
        _apply(getattr(cfg, section), data.get(section, {}))
    if "state_dir" in data:
        cfg.state_dir = data["state_dir"]
    return cfg
