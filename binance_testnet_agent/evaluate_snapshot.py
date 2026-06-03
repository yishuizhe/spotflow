from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import AgentConfig
from .strategy import GridStrategy, MarketSnapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one market/account snapshot from downloaded JSON files")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--klines", required=True)
    parser.add_argument("--account", required=True)
    args = parser.parse_args()

    config = AgentConfig.from_env()
    ticker = json.loads(Path(args.ticker).read_text())
    klines = json.loads(Path(args.klines).read_text())
    account = json.loads(Path(args.account).read_text())
    balances = {item["asset"]: item for item in account.get("balances", [])}

    price = float(ticker["price"])
    snapshot = MarketSnapshot(
        symbol=config.symbol,
        price=price,
        recent_closes=[float(item[4]) for item in klines],
        base_balance=float(balances.get(config.base_asset, {}).get("free", 0)),
        quote_balance=float(balances.get(config.quote_asset, {}).get("free", 0)),
    )
    strategy = GridStrategy(
        grid_step_pct=config.grid_step_pct,
        take_profit_pct=config.take_profit_pct,
        order_quote_size=config.order_quote_size,
        max_position_quote=config.max_position_quote,
    )
    decision = strategy.decide(snapshot)
    print(
        json.dumps(
            {
                "snapshot": {
                    "symbol": snapshot.symbol,
                    "price": snapshot.price,
                    "base_balance": snapshot.base_balance,
                    "quote_balance": snapshot.quote_balance,
                },
                "decision": {
                    "signal": decision.signal.value,
                    "reason": decision.reason,
                    "reference_price": decision.reference_price,
                    "price": decision.price,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
