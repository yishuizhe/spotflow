from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .binance_client import BinanceSpotClient
from .config import AgentConfig
from .ledger import PositionLedger
from .storage import SQLiteJsonListStore


@dataclass(frozen=True)
class ReconciliationReport:
    ok: bool
    ledger_quantity: float
    account_quantity: float
    quantity_difference: float
    active_exchange_orders: int
    active_local_orders: int
    repaired_stale_orders: int
    issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AccountReconciler:
    def __init__(self, config: AgentConfig, client: BinanceSpotClient | None = None) -> None:
        self.config = config
        self.client = client or BinanceSpotClient(config.base_url, config.api_key, config.api_secret)
        self.ledger = PositionLedger(Path(f"data/lots_{config.symbol}.json"))
        self.pending = SQLiteJsonListStore(Path(f"data/pending_orders_{config.symbol}.json"), "pending_orders")
        self.report_path = Path(f"data/reconciliation_{config.symbol}.json")

    def run(self, repair: bool = True) -> ReconciliationReport:
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        base = balances.get(self.config.base_asset, {})
        account_qty = float(base.get("free", 0) or 0) + float(base.get("locked", 0) or 0)
        ledger_qty = sum(float(lot.get("remaining_quantity", 0) or 0) for lot in self.ledger.open_lots())
        difference = account_qty - ledger_qty
        exchange_orders = self.client.open_orders(self.config.symbol)
        exchange_ids = {int(item.get("orderId", 0) or 0) for item in exchange_orders}
        local = self.pending.load()
        active_local = [
            item
            for item in local
            if not item.get("processed") and str(item.get("status", "NEW")) not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
        ]
        repaired = 0
        stale_ids = {
            int(item.get("order_id", 0) or 0)
            for item in active_local
            if int(item.get("order_id", 0) or 0) not in exchange_ids
        }
        tolerance = max(0.0000001, account_qty * 0.002)
        issues: list[str] = []
        if abs(difference) > tolerance:
            issues.append(f"账户与账本 BTC 相差 {difference:.8f}，可能存在外部持仓或外部成交")
        if stale_ids:
            issues.append(f"发现 {len(stale_ids)} 个本地挂单已不在币安活动挂单中，成交同步进程将查询最终状态")
        report = ReconciliationReport(
            not issues,
            ledger_qty,
            account_qty,
            difference,
            len(exchange_orders),
            len(active_local),
            repaired,
            issues,
        )
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return report
