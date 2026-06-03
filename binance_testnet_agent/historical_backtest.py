from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from .config import AgentConfig
from .local_backtest import BacktestConfig, run_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run historical Binance kline backtests.")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--start", required=True, help="UTC date, for example 2026-04-01")
    parser.add_argument("--end", required=True, help="UTC date, exclusive, for example 2026-05-01")
    parser.add_argument("--initial-quote", type=float, required=True)
    parser.add_argument("--take-profits", default="0.0045,0.006,0.008,0.010,0.012")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = AgentConfig.from_env()
    symbol = (args.symbol or config.symbol).upper()
    prices = fetch_close_prices(
        config.base_url,
        symbol,
        args.interval,
        _date_ms(args.start),
        _date_ms(args.end),
    )
    if len(prices) < 120:
        raise SystemExit(f"not enough kline data for {symbol} {args.start}..{args.end}: {len(prices)}")

    results = []
    for take_profit in _parse_take_profits(args.take_profits):
        backtest_config = BacktestConfig(
            initial_quote=args.initial_quote,
            initial_price=prices[0],
            minutes=len(prices),
            order_quote_size=config.order_quote_size,
            auto_position_sizing=config.auto_position_sizing,
            max_position_quote=config.max_position_quote,
            grid_step_pct=config.grid_step_pct,
            take_profit_pct=take_profit,
            trading_fee_rate=config.trading_fee_rate,
            max_floating_loss_quote=config.max_floating_loss_quote,
            rapid_drop_pause_pct=config.rapid_drop_pause_pct,
            large_drop_pause_pct=config.large_drop_pause_pct,
            rebound_buy_pct=config.rebound_buy_pct,
            price_anomaly_pct=config.price_anomaly_pct,
            defensive_mode=config.defensive_mode,
            defensive_position_usage_trigger=config.defensive_position_usage_trigger,
            defensive_floating_loss_quote=config.defensive_floating_loss_quote,
            defensive_recent_drawdown_pct=config.defensive_recent_drawdown_pct,
            defensive_normal_add_on_step_pct=config.defensive_normal_add_on_step_pct,
            defensive_add_on_step_pct=config.defensive_add_on_step_pct,
            defensive_aged_lot_days_1=config.defensive_aged_lot_days_1,
            defensive_aged_lot_profit_pct_1=config.defensive_aged_lot_profit_pct_1,
            defensive_aged_lot_days_2=config.defensive_aged_lot_days_2,
            defensive_aged_lot_profit_pct_2=config.defensive_aged_lot_profit_pct_2,
        )
        result = run_backtest(f"{args.start}..{args.end} tp={take_profit:.4f}", prices, backtest_config)
        payload = asdict(result)
        payload["take_profit_pct"] = take_profit
        results.append(payload)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    _print_table(results)


def fetch_close_prices(base_url: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[float]:
    prices: list[float] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        with urlopen(f"{base_url.rstrip('/')}/api/v3/klines?{urlencode(params)}", timeout=20) as response:
            rows: list[list[Any]] = json.loads(response.read().decode())
        if not rows:
            break
        prices.extend(float(row[4]) for row in rows)
        next_cursor = int(rows[-1][0]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.04)
    return prices


def _date_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _parse_take_profits(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def _print_table(results: list[dict[str, Any]]) -> None:
    headers = ["止盈", "终价", "总资产", "收益率", "已实现", "未实现", "手续费", "买/卖", "未平", "最大回撤"]
    print(" | ".join(headers))
    print(" | ".join(["---"] * len(headers)))
    for item in results:
        print(
            " | ".join(
                [
                    f"{item['take_profit_pct'] * 100:.2f}%",
                    f"{item['end_price']:,.2f}",
                    f"{item['final_value']:.4f}",
                    f"{item['total_return_pct']:+.2f}%",
                    f"{item['realized_net_pnl']:+.4f}",
                    f"{item['unrealized_pnl']:+.4f}",
                    f"{item['fees_paid']:.4f}",
                    f"{item['buys']}/{item['sells']}",
                    str(item["open_lots"]),
                    f"{item['max_drawdown_quote']:.4f}",
                ]
            )
        )


if __name__ == "__main__":
    main()
