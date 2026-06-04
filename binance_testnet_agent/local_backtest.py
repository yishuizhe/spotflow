from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

from .defensive import enrich_lots_with_defensive_targets, evaluate_defensive_mode
from .risk import evaluate_buy_risk
from .sizing import position_sizing
from .strategy import GridStrategy, MarketSnapshot, Signal


@dataclass
class BacktestConfig:
    initial_quote: float = 50.0
    initial_price: float = 73_000.0
    minutes: int = 7 * 24 * 60
    order_quote_size: float = 5.5
    auto_position_sizing: bool = True
    max_position_quote: float = 47.0
    grid_step_pct: float = 0.006
    take_profit_pct: float = 0.0045
    trading_fee_rate: float = 0.001
    max_floating_loss_quote: float = 5.0
    rapid_drop_pause_pct: float = 0.008
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
    trend_filter: bool = False
    price_interval_minutes: int = 1
    trend_rebound_pct: float = 0.004
    trend_probe_order_quote: float = 5.0
    normal_pool_pct: float = 0.45
    deep_pool_pct: float = 0.20
    adaptive_regime: bool = False
    adaptive_normal_pool_pct: float = 0.65
    adaptive_recovery_pool_pct: float = 0.45
    adaptive_strict_pool_pct: float = 0.25
    adaptive_probe_order_quote: float = 5.0
    adaptive_strict_order_quote: float = 3.0
    adaptive_rebound_pct: float = 0.005
    seed: int = 20260529


@dataclass
class BacktestResult:
    scenario: str
    start_price: float
    end_price: float
    quote_balance: float
    base_balance: float
    final_value: float
    total_return_pct: float
    realized_net_pnl: float
    unrealized_pnl: float
    fees_paid: float
    buys: int
    sells: int
    blocked_buys: int
    open_lots: int
    closed_lots: int
    max_drawdown_quote: float


def run_scenarios(config: BacktestConfig) -> list[BacktestResult]:
    scenarios = {
        "窄幅震荡": _range_prices(config, amplitude=0.0035, noise=0.00045),
        "宽幅震荡": _range_prices(config, amplitude=0.0100, noise=0.00075),
        "缓慢上涨": _trend_prices(config, drift=0.0300, noise=0.00045),
        "单边下跌": _trend_prices(config, drift=-0.0600, noise=0.00055),
        "快速急跌": _crash_prices(config, crash=-0.0450, crash_minutes=15, noise=0.00035),
        "先跌后反弹": _dip_recovery_prices(config, dip=-0.0350, recovery=0.0250, noise=0.00065),
    }
    return [run_backtest(name, prices, config) for name, prices in scenarios.items()]


def run_backtest(scenario: str, prices: list[float], config: BacktestConfig) -> BacktestResult:
    quote_balance = config.initial_quote
    lots: list[dict[str, float | str]] = []
    closed_lots: list[dict[str, float | str]] = []
    state: dict[str, int] = {"last_buy_level": 0, "last_sell_level": 0}
    buys = 0
    sells = 0
    blocked_buys = 0
    fees_paid = 0.0
    peak_value = config.initial_quote
    max_drawdown = 0.0
    start_time = datetime(2026, 5, 1, tzinfo=timezone.utc)

    for index, price in enumerate(prices):
        now = start_time + timedelta(minutes=index)
        recent = prices[max(0, index - 59) : index + 1]
        base_balance = sum(float(lot["remaining_quantity"]) for lot in lots)
        snapshot = MarketSnapshot("BTCUSDT", price, recent, base_balance, quote_balance)
        sizing = position_sizing(
            quote_balance + base_balance * price,
            config.order_quote_size,
            config.max_position_quote,
            config.auto_position_sizing,
        )
        unrealized_now = sum((price - float(lot["buy_price"])) * float(lot["remaining_quantity"]) for lot in lots)
        defensive = evaluate_defensive_mode(
            enabled=config.defensive_mode,
            price=price,
            recent_closes=recent,
            open_lots=lots,
            max_position_quote=sizing.max_position_quote,
            unrealized_pnl=unrealized_now,
            normal_add_on_step_pct=config.defensive_normal_add_on_step_pct,
            defensive_add_on_step_pct=config.defensive_add_on_step_pct,
            position_usage_trigger=config.defensive_position_usage_trigger,
            floating_loss_trigger_quote=config.defensive_floating_loss_quote,
            recent_drawdown_trigger_pct=config.defensive_recent_drawdown_pct,
        )
        strategy = GridStrategy(
            grid_step_pct=config.grid_step_pct,
            take_profit_pct=config.take_profit_pct,
            order_quote_size=sizing.order_quote_size,
            max_position_quote=sizing.max_position_quote,
            add_on_step_pct=defensive.add_on_step_pct,
        )
        strategy_lots = enrich_lots_with_defensive_targets(
            lots,
            enabled=config.defensive_mode,
            target_profit_pct=config.take_profit_pct,
            trading_fee_rate=config.trading_fee_rate,
            aged_days_1=config.defensive_aged_lot_days_1,
            aged_profit_pct_1=config.defensive_aged_lot_profit_pct_1,
            aged_days_2=config.defensive_aged_lot_days_2,
            aged_profit_pct_2=config.defensive_aged_lot_profit_pct_2,
            now=now,
        )
        decision = strategy.decide(snapshot, state, strategy_lots)
        risk = evaluate_buy_risk(
            price=price,
            recent_closes=recent,
            unrealized_pnl=unrealized_now,
            max_floating_loss_quote=config.max_floating_loss_quote,
            rapid_drop_pause_pct=config.rapid_drop_pause_pct,
            large_drop_pause_pct=config.large_drop_pause_pct,
            rebound_buy_pct=config.rebound_buy_pct,
            price_anomaly_pct=config.price_anomaly_pct,
        )

        if decision.signal == Signal.BUY:
            if not risk.allow_buy:
                blocked_buys += 1
                current_value = quote_balance + sum(float(lot["remaining_quantity"]) * price for lot in lots)
                peak_value = max(peak_value, current_value)
                max_drawdown = max(max_drawdown, peak_value - current_value)
                continue
            quote_size = decision.order_quote_size
            trend = _trend_buy_guard(
                prices[: index + 1],
                quote_size,
                sizing.max_position_quote,
                current_position_quote=max(
                    sum(float(lot.get("remaining_quantity", 0)) * price for lot in lots),
                    base_balance * price,
                ),
                config=config,
            )
            if bool(trend["blocked"]):
                blocked_buys += 1
                current_value = quote_balance + sum(float(lot["remaining_quantity"]) * price for lot in lots)
                peak_value = max(peak_value, current_value)
                max_drawdown = max(max_drawdown, peak_value - current_value)
                continue
            quote_size = float(trend["quote_size"])
            buy_fee = quote_size * config.trading_fee_rate
            if quote_balance >= quote_size + buy_fee:
                qty = quote_size / price
                quote_balance -= quote_size + buy_fee
                fees_paid += buy_fee
                lots.append(
                    {
                        "id": f"lot-{index}-{buys}",
                        "level": decision.level,
                        "buy_price": price,
                        "buy_quote": quote_size,
                        "buy_fee_quote": buy_fee,
                        "quantity": qty,
                        "remaining_quantity": qty,
                        "target_price": price * (1 + config.take_profit_pct + config.trading_fee_rate * 2),
                        "status": "open",
                        "opened_at": now.isoformat(),
                    }
                )
                state["last_buy_level"] = max(int(state.get("last_buy_level", 0)), _level_number(decision.level))
                buys += 1

        elif decision.signal == Signal.SELL and decision.lot_id:
            lot = next((item for item in lots if item["id"] == decision.lot_id), None)
            if lot:
                qty = float(lot["remaining_quantity"])
                proceeds = qty * price
                sell_fee = proceeds * config.trading_fee_rate
                quote_balance += proceeds - sell_fee
                fees_paid += sell_fee
                cost = float(lot["buy_quote"])
                buy_fee = float(lot.get("buy_fee_quote") or 0)
                lot["sell_price"] = price
                lot["sell_quote"] = proceeds
                lot["sell_fee_quote"] = sell_fee
                lot["total_fee_quote"] = buy_fee + sell_fee
                lot["realized_pnl"] = proceeds - cost
                lot["net_realized_pnl"] = proceeds - cost - buy_fee - sell_fee
                lot["status"] = "closed"
                lot["remaining_quantity"] = 0.0
                closed_lots.append(lot)
                lots.remove(lot)
                sells += 1

        current_value = quote_balance + sum(float(lot["remaining_quantity"]) * price for lot in lots)
        peak_value = max(peak_value, current_value)
        max_drawdown = max(max_drawdown, peak_value - current_value)

    end_price = prices[-1]
    final_value = quote_balance + sum(float(lot["remaining_quantity"]) * end_price for lot in lots)
    realized_net = sum(float(lot.get("net_realized_pnl") or 0) for lot in closed_lots)
    unrealized = sum((end_price - float(lot["buy_price"])) * float(lot["remaining_quantity"]) for lot in lots)
    return BacktestResult(
        scenario=scenario,
        start_price=prices[0],
        end_price=end_price,
        quote_balance=quote_balance,
        base_balance=sum(float(lot["remaining_quantity"]) for lot in lots),
        final_value=final_value,
        total_return_pct=(final_value / config.initial_quote - 1) * 100,
        realized_net_pnl=realized_net,
        unrealized_pnl=unrealized,
        fees_paid=fees_paid,
        buys=buys,
        sells=sells,
        blocked_buys=blocked_buys,
        open_lots=len(lots),
        closed_lots=len(closed_lots),
        max_drawdown_quote=max_drawdown,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local synthetic one-week backtests for the grid strategy.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    args = parser.parse_args()

    results = run_scenarios(BacktestConfig())
    if args.json:
        print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2))
        return
    _print_table(results)


def _print_table(results: list[BacktestResult]) -> None:
    headers = ["场景", "终价", "总资产", "收益率", "已实现", "未实现", "手续费", "买/卖", "拦截买入", "未平", "最大回撤"]
    print(" | ".join(headers))
    print(" | ".join(["---"] * len(headers)))
    for item in results:
        print(
            " | ".join(
                [
                    item.scenario,
                    f"{item.end_price:,.2f}",
                    f"{item.final_value:.4f}",
                    f"{item.total_return_pct:+.2f}%",
                    f"{item.realized_net_pnl:+.4f}",
                    f"{item.unrealized_pnl:+.4f}",
                    f"{item.fees_paid:.4f}",
                    f"{item.buys}/{item.sells}",
                    str(item.blocked_buys),
                    str(item.open_lots),
                    f"{item.max_drawdown_quote:.4f}",
                ]
            )
        )


def _range_prices(config: BacktestConfig, amplitude: float, noise: float) -> list[float]:
    rng = random.Random(config.seed + int(amplitude * 1_000_000))
    prices = []
    price = config.initial_price
    for minute in range(config.minutes):
        cycle = math.sin(2 * math.pi * minute / 360) + 0.45 * math.sin(2 * math.pi * minute / 97)
        target = config.initial_price * (1 + amplitude * cycle)
        price += (target - price) * 0.09 + price * rng.gauss(0, noise)
        prices.append(max(1.0, price))
    return prices


def _trend_prices(config: BacktestConfig, drift: float, noise: float) -> list[float]:
    rng = random.Random(config.seed + int(drift * 1_000_000))
    prices = []
    price = config.initial_price
    per_minute_drift = drift / config.minutes
    for _minute in range(config.minutes):
        price *= 1 + per_minute_drift + rng.gauss(0, noise)
        prices.append(max(1.0, price))
    return prices


def _dip_recovery_prices(config: BacktestConfig, dip: float, recovery: float, noise: float) -> list[float]:
    rng = random.Random(config.seed + 7357)
    prices = []
    price = config.initial_price
    for minute in range(config.minutes):
        if minute < config.minutes * 0.4:
            drift = dip / (config.minutes * 0.4)
        else:
            drift = recovery / (config.minutes * 0.6)
        price *= 1 + drift + rng.gauss(0, noise)
        prices.append(max(1.0, price))
    return prices


def _crash_prices(config: BacktestConfig, crash: float, crash_minutes: int, noise: float) -> list[float]:
    rng = random.Random(config.seed + 911)
    prices = []
    price = config.initial_price
    crash_start = config.minutes // 4
    crash_end = crash_start + crash_minutes
    for minute in range(config.minutes):
        if crash_start <= minute < crash_end:
            drift = crash / crash_minutes
        else:
            drift = 0.002 / config.minutes
        price *= 1 + drift + rng.gauss(0, noise)
        prices.append(max(1.0, price))
    return prices


def _level_number(level: str) -> int:
    try:
        return int(level.split("-", 2)[1])
    except (IndexError, ValueError):
        return 0


def _trend_buy_guard(
    prices: list[float],
    quote_size: float,
    max_position_quote: float,
    current_position_quote: float,
    config: BacktestConfig,
) -> dict[str, float | bool]:
    if not config.trend_filter:
        return {"blocked": False, "quote_size": quote_size}
    interval = max(1, int(config.price_interval_minutes))
    ma24_window = max(2, int(24 * 60 / interval))
    ma7_window = max(ma24_window + 1, int(7 * 24 * 60 / interval))
    if config.adaptive_regime:
        return _adaptive_regime_buy_guard(
            prices,
            quote_size,
            max_position_quote,
            current_position_quote,
            ma24_window,
            ma7_window,
            config,
        )
    if len(prices) < ma7_window + ma24_window:
        normal_pool_quote = max_position_quote * config.normal_pool_pct
        if current_position_quote + quote_size > normal_pool_quote:
            return {"blocked": True, "quote_size": 0.0}
        return {"blocked": False, "quote_size": quote_size}

    ma24 = sum(prices[-ma24_window:]) / ma24_window
    ma7 = sum(prices[-ma7_window:]) / ma7_window
    prev_ma24 = sum(prices[-ma24_window * 2 : -ma24_window]) / ma24_window
    price = prices[-1]
    downtrend = price < ma24 and price < ma7 and ma24 < prev_ma24
    normal_pool_quote = max_position_quote * config.normal_pool_pct
    deep_pool_quote = max_position_quote * config.deep_pool_pct

    if not downtrend:
        if current_position_quote + quote_size > normal_pool_quote:
            return {"blocked": True, "quote_size": 0.0}
        return {"blocked": False, "quote_size": quote_size}

    if current_position_quote >= normal_pool_quote + deep_pool_quote:
        return {"blocked": True, "quote_size": 0.0}

    recent = prices[-max(3, int(6 * 60 / interval)) :]
    low = min(recent)
    rebounded = (
        low > 0
        and price >= low * (1 + config.trend_rebound_pct)
        and len(recent) >= 3
        and recent[-1] > recent[-2] > recent[-3]
    )
    if not rebounded:
        return {"blocked": True, "quote_size": 0.0}
    return {"blocked": False, "quote_size": min(quote_size, config.trend_probe_order_quote)}


def _adaptive_regime_buy_guard(
    prices: list[float],
    quote_size: float,
    max_position_quote: float,
    current_position_quote: float,
    ma24_window: int,
    ma7_window: int,
    config: BacktestConfig,
) -> dict[str, float | bool]:
    if len(prices) < ma7_window + ma24_window:
        pool_quote = max_position_quote * config.adaptive_normal_pool_pct
        if current_position_quote + quote_size > pool_quote:
            return {"blocked": True, "quote_size": 0.0}
        return {"blocked": False, "quote_size": quote_size}

    price = prices[-1]
    ma24 = sum(prices[-ma24_window:]) / ma24_window
    ma7 = sum(prices[-ma7_window:]) / ma7_window
    prev_ma24 = sum(prices[-ma24_window * 2 : -ma24_window]) / ma24_window
    downtrend = price < ma24 and price < ma7 and ma24 < prev_ma24
    recovered = price >= ma24 and ma24 >= prev_ma24 * 0.999

    if recovered:
        pool_quote = max_position_quote * config.adaptive_normal_pool_pct
        adjusted_quote = quote_size
    elif downtrend:
        recent = prices[-max(3, int(6 * 60 / max(1, config.price_interval_minutes))) :]
        low = min(recent)
        rebound = (
            low > 0
            and price >= low * (1 + config.adaptive_rebound_pct)
            and len(recent) >= 3
            and recent[-1] > recent[-2] > recent[-3]
        )
        if not rebound:
            return {"blocked": True, "quote_size": 0.0}
        pool_quote = max_position_quote * config.adaptive_strict_pool_pct
        adjusted_quote = min(quote_size, config.adaptive_strict_order_quote)
    else:
        pool_quote = max_position_quote * config.adaptive_recovery_pool_pct
        adjusted_quote = min(quote_size, config.adaptive_probe_order_quote)

    if current_position_quote + adjusted_quote > pool_quote:
        return {"blocked": True, "quote_size": 0.0}
    return {"blocked": False, "quote_size": adjusted_quote}


if __name__ == "__main__":
    main()
