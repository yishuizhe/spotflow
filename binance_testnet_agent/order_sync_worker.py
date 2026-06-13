from __future__ import annotations

import json
import time
from pathlib import Path

from .config import AgentConfig
from .dashboard import Dashboard
from .trade_lock import trading_lock


def main() -> None:
    while True:
        config = AgentConfig.from_env()
        dashboard = Dashboard(
            config,
            Path(f"data/baseline_{config.symbol}.json"),
            Path(f"data/trades_{config.symbol}.jsonl"),
            Path(f"data/grid_state_{config.symbol}.json"),
        )
        try:
            with trading_lock():
                orders = dashboard.sync_pending_orders()
            active = sum(
                1
                for item in orders
                if not item.get("processed")
                and str(item.get("status", "NEW")) not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
            )
            print(json.dumps({"ok": True, "active_pending_orders": active}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
