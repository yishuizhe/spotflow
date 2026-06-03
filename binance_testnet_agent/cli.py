from __future__ import annotations

import argparse
import json
import sys

from .agent import TradingAgent
from .binance_client import BinanceAPIError
from .config import AgentConfig


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance Spot live trading agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Check public Binance connectivity")
    subparsers.add_parser("account", help="Show spot account balances")
    subparsers.add_parser("once", help="Run one strategy cycle")
    subparsers.add_parser("run", help="Run strategy loop")
    args = parser.parse_args()

    agent = TradingAgent(AgentConfig.from_env())

    try:
        if args.command == "health":
            _print_json(agent.health())
        elif args.command == "account":
            _print_json(agent.account_summary())
        elif args.command == "once":
            _print_json(agent.once())
        elif args.command == "run":
            agent.run_forever()
    except BinanceAPIError as exc:
        _print_json({"ok": False, "error": str(exc)})
        sys.exit(1)


if __name__ == "__main__":
    main()
