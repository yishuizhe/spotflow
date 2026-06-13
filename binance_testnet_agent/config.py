from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    api_secret: str
    base_url: str = "https://api.binance.com"
    symbol: str = "BTCUSDT"
    base_asset: str = "BTC"
    quote_asset: str = "USDT"
    execute_trades: bool = False
    order_quote_size: float = 25.0
    auto_position_sizing: bool = True
    grid_step_pct: float = 0.006
    take_profit_pct: float = 0.008
    max_position_quote: float = 200.0
    max_daily_loss_quote: float = 50.0
    loop_seconds: int = 30
    trading_fee_rate: float = 0.001
    max_floating_loss_quote: float = 300.0
    rapid_drop_pause_pct: float = 0.006
    large_drop_pause_pct: float = 0.02
    rebound_buy_pct: float = 0.0015
    price_anomaly_pct: float = 0.02
    defensive_mode: bool = True
    defensive_position_usage_trigger: float = 0.80
    defensive_floating_loss_quote: float = 2.5
    defensive_recent_drawdown_pct: float = 0.025
    defensive_normal_add_on_step_pct: float = 0.0025
    defensive_add_on_step_pct: float = 0.005
    defensive_aged_lot_days_1: int = 7
    defensive_aged_lot_profit_pct_1: float = 0.0035
    defensive_aged_lot_days_2: int = 14
    defensive_aged_lot_profit_pct_2: float = 0.0015
    swing_strategy: bool = True
    swing_allocation_pct: float = 0.30
    swing_min_order_quote: float = 10.0
    swing_max_order_quote: float = 15.0
    swing_add_step_pct: float = 0.015
    swing_min_band_pct: float = 0.012
    swing_max_band_pct: float = 0.025
    swing_manual_center_price: float = 0.0
    swing_kline_interval: str = "1h"
    swing_kline_limit: int = 24
    trend_guard: bool = True
    trend_normal_pool_pct: float = 0.30
    trend_dip_pool_pct: float = 0.10
    trend_dip_order_quote: float = 3.0
    trend_rebound_pct: float = 0.005
    trend_kline_interval: str = "1h"
    trend_kline_limit: int = 240
    defensive_scalp: bool = True
    defensive_scalp_allocation_pct: float = 0.08
    defensive_scalp_order_pct: float = 0.018
    defensive_scalp_min_order_quote: float = 6.0
    defensive_scalp_max_order_quote: float = 10.0
    defensive_scalp_buy_drop_pct: float = 0.004
    defensive_scalp_take_profit_pct: float = 0.005
    defensive_scalp_add_step_pct: float = 0.003
    defensive_scalp_min_range_pct: float = 0.004
    defensive_scalp_max_range_pct: float = 0.018
    manual_buy_auto_sell: bool = False
    adaptive_strategy_enabled: bool = False
    shadow_mode: bool = True
    reconciliation_enabled: bool = True
    dynamic_take_profit: bool = True
    trailing_profit_pct: float = 0.003
    backtest_slippage_pct: float = 0.0005
    backtest_failure_rate: float = 0.002
    backtest_latency_bars: int = 1

    @classmethod
    def from_env(cls) -> "AgentConfig":
        load_dotenv()
        return cls(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            base_url=os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/"),
            symbol=os.getenv("BINANCE_SYMBOL", "BTCUSDT").upper(),
            base_asset=os.getenv("BINANCE_BASE_ASSET", "BTC").upper(),
            quote_asset=os.getenv("BINANCE_QUOTE_ASSET", "USDT").upper(),
            execute_trades=_bool_env("EXECUTE_TRADES", False),
            order_quote_size=float(os.getenv("ORDER_QUOTE_SIZE", "25")),
            auto_position_sizing=_bool_env("AUTO_POSITION_SIZING", True),
            grid_step_pct=float(os.getenv("GRID_STEP_PCT", "0.006")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.008")),
            max_position_quote=float(os.getenv("MAX_POSITION_QUOTE", "200")),
            max_daily_loss_quote=float(os.getenv("MAX_DAILY_LOSS_QUOTE", "50")),
            loop_seconds=int(os.getenv("LOOP_SECONDS", "30")),
            trading_fee_rate=float(os.getenv("TRADING_FEE_RATE", "0.001")),
            max_floating_loss_quote=float(os.getenv("MAX_FLOATING_LOSS_QUOTE", "300")),
            rapid_drop_pause_pct=float(os.getenv("RAPID_DROP_PAUSE_PCT", "0.006")),
            large_drop_pause_pct=float(os.getenv("LARGE_DROP_PAUSE_PCT", "0.02")),
            rebound_buy_pct=float(os.getenv("REBOUND_BUY_PCT", "0.0015")),
            price_anomaly_pct=float(os.getenv("PRICE_ANOMALY_PCT", "0.02")),
            defensive_mode=_bool_env("DEFENSIVE_MODE", True),
            defensive_position_usage_trigger=float(os.getenv("DEFENSIVE_POSITION_USAGE_TRIGGER", "0.80")),
            defensive_floating_loss_quote=float(os.getenv("DEFENSIVE_FLOATING_LOSS_QUOTE", "2.5")),
            defensive_recent_drawdown_pct=float(os.getenv("DEFENSIVE_RECENT_DRAWDOWN_PCT", "0.025")),
            defensive_normal_add_on_step_pct=float(os.getenv("DEFENSIVE_NORMAL_ADD_ON_STEP_PCT", "0.0025")),
            defensive_add_on_step_pct=float(os.getenv("DEFENSIVE_ADD_ON_STEP_PCT", "0.005")),
            defensive_aged_lot_days_1=int(os.getenv("DEFENSIVE_AGED_LOT_DAYS_1", "7")),
            defensive_aged_lot_profit_pct_1=float(os.getenv("DEFENSIVE_AGED_LOT_PROFIT_PCT_1", "0.0035")),
            defensive_aged_lot_days_2=int(os.getenv("DEFENSIVE_AGED_LOT_DAYS_2", "14")),
            defensive_aged_lot_profit_pct_2=float(os.getenv("DEFENSIVE_AGED_LOT_PROFIT_PCT_2", "0.0015")),
            swing_strategy=_bool_env("SWING_STRATEGY", True),
            swing_allocation_pct=float(os.getenv("SWING_ALLOCATION_PCT", "0.30")),
            swing_min_order_quote=float(os.getenv("SWING_MIN_ORDER_QUOTE", "10")),
            swing_max_order_quote=float(os.getenv("SWING_MAX_ORDER_QUOTE", "15")),
            swing_add_step_pct=float(os.getenv("SWING_ADD_STEP_PCT", "0.015")),
            swing_min_band_pct=float(os.getenv("SWING_MIN_BAND_PCT", "0.012")),
            swing_max_band_pct=float(os.getenv("SWING_MAX_BAND_PCT", "0.025")),
            swing_manual_center_price=float(os.getenv("SWING_MANUAL_CENTER_PRICE", "0")),
            swing_kline_interval=os.getenv("SWING_KLINE_INTERVAL", "1h"),
            swing_kline_limit=int(os.getenv("SWING_KLINE_LIMIT", "24")),
            trend_guard=_bool_env("TREND_GUARD", True),
            trend_normal_pool_pct=float(os.getenv("TREND_NORMAL_POOL_PCT", "0.30")),
            trend_dip_pool_pct=float(os.getenv("TREND_DIP_POOL_PCT", "0.10")),
            trend_dip_order_quote=float(os.getenv("TREND_DIP_ORDER_QUOTE", "3")),
            trend_rebound_pct=float(os.getenv("TREND_REBOUND_PCT", "0.005")),
            trend_kline_interval=os.getenv("TREND_KLINE_INTERVAL", "1h"),
            trend_kline_limit=int(os.getenv("TREND_KLINE_LIMIT", "240")),
            defensive_scalp=_bool_env("DEFENSIVE_SCALP", True),
            defensive_scalp_allocation_pct=float(os.getenv("DEFENSIVE_SCALP_ALLOCATION_PCT", "0.08")),
            defensive_scalp_order_pct=float(os.getenv("DEFENSIVE_SCALP_ORDER_PCT", "0.018")),
            defensive_scalp_min_order_quote=float(os.getenv("DEFENSIVE_SCALP_MIN_ORDER_QUOTE", "6")),
            defensive_scalp_max_order_quote=float(os.getenv("DEFENSIVE_SCALP_MAX_ORDER_QUOTE", "10")),
            defensive_scalp_buy_drop_pct=float(os.getenv("DEFENSIVE_SCALP_BUY_DROP_PCT", "0.004")),
            defensive_scalp_take_profit_pct=float(os.getenv("DEFENSIVE_SCALP_TAKE_PROFIT_PCT", "0.005")),
            defensive_scalp_add_step_pct=float(os.getenv("DEFENSIVE_SCALP_ADD_STEP_PCT", "0.003")),
            defensive_scalp_min_range_pct=float(os.getenv("DEFENSIVE_SCALP_MIN_RANGE_PCT", "0.004")),
            defensive_scalp_max_range_pct=float(os.getenv("DEFENSIVE_SCALP_MAX_RANGE_PCT", "0.018")),
            manual_buy_auto_sell=_bool_env("MANUAL_BUY_AUTO_SELL", False),
            adaptive_strategy_enabled=_bool_env("ADAPTIVE_STRATEGY_ENABLED", False),
            shadow_mode=_bool_env("SHADOW_MODE", True),
            reconciliation_enabled=_bool_env("RECONCILIATION_ENABLED", True),
            dynamic_take_profit=_bool_env("DYNAMIC_TAKE_PROFIT", True),
            trailing_profit_pct=float(os.getenv("TRAILING_PROFIT_PCT", "0.003")),
            backtest_slippage_pct=float(os.getenv("BACKTEST_SLIPPAGE_PCT", "0.0005")),
            backtest_failure_rate=float(os.getenv("BACKTEST_FAILURE_RATE", "0.002")),
            backtest_latency_bars=int(os.getenv("BACKTEST_LATENCY_BARS", "1")),
        )
