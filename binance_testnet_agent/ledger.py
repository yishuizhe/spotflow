from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

from .storage import SQLiteJsonListStore


@dataclass(frozen=True)
class Lot:
    id: str
    symbol: str
    quantity: float
    remaining_quantity: float
    buy_price: float
    buy_quote: float
    target_price: float
    status: str
    opened_at: str
    buy_fee_quote: float = 0.0
    closed_at: str | None = None
    sell_price: float | None = None
    sell_quote: float | None = None
    sell_fee_quote: float | None = None
    total_fee_quote: float | None = None
    realized_pnl: float | None = None
    net_realized_pnl: float | None = None
    auto_sell: bool = True


class PositionLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.store = SQLiteJsonListStore(path, "lots")

    def lots(self) -> list[dict[str, Any]]:
        return self.store.load()

    def save(self, lots: list[dict[str, Any]]) -> None:
        self.store.save(lots)

    def update_lots(self, mutator: Callable[[list[dict[str, Any]]], Any]) -> Any:
        return self.store.update(mutator)

    def open_lots(self) -> list[dict[str, Any]]:
        return [lot for lot in self.lots() if lot.get("status") == "open" and float(lot.get("remaining_quantity", 0)) > 0]

    def realized_pnl(self, trading_fee_rate: float = 0.0) -> float:
        return sum(_lot_net_realized_pnl(lot, trading_fee_rate) for lot in self.lots() if lot.get("status") == "closed")

    def fee_summary(self, trading_fee_rate: float) -> dict[str, float]:
        open_fee = 0.0
        closed_fee = 0.0
        for lot in self.lots():
            total_fee = _lot_fee_quote(lot)
            if lot.get("status") == "closed":
                closed_fee += total_fee
            elif lot.get("status") == "open":
                open_fee += total_fee
        return {
            "open_fee_quote": open_fee,
            "closed_fee_quote": closed_fee,
            "total_fee_quote": open_fee + closed_fee,
        }

    def lot_fee_quote(self, lot: dict[str, Any], trading_fee_rate: float) -> float:
        return _lot_fee_quote(lot)

    def lot_net_realized_pnl(self, lot: dict[str, Any], trading_fee_rate: float) -> float:
        return _lot_net_realized_pnl(lot, trading_fee_rate)

    def unrealized_pnl(self, current_price: float) -> float:
        pnl = 0.0
        for lot in self.open_lots():
            qty = float(lot.get("remaining_quantity", 0))
            pnl += (current_price - float(lot.get("buy_price", 0))) * qty
        return pnl

    def add_buy(
        self,
        symbol: str,
        order: dict[str, Any],
        target_profit_pct: float,
        trading_fee_rate: float,
        level: str,
        target_price: float | None = None,
        auto_sell: bool = True,
        base_asset: str = "",
    ) -> dict[str, Any] | None:
        executed_qty = float(order.get("executedQty", 0) or 0)
        quote_qty = float(order.get("cummulativeQuoteQty", 0) or 0)
        if executed_qty <= 0 or quote_qty <= 0:
            return None

        buy_price = quote_qty / executed_qty
        base_commission = _commission_for_asset(order, base_asset)
        net_quantity = max(0.0, executed_qty - base_commission)
        if net_quantity <= 0:
            return None
        lot = Lot(
            id=f"{int(time.time() * 1000)}-{order.get('orderId', 'manual')}",
            symbol=symbol,
            quantity=net_quantity,
            remaining_quantity=net_quantity,
            buy_price=buy_price,
            buy_quote=quote_qty,
            target_price=target_price or buy_price * (1 + target_profit_pct + trading_fee_rate * 2),
            status="open",
            opened_at=_now(),
            buy_fee_quote=quote_qty * trading_fee_rate,
            auto_sell=auto_sell,
        )
        def mutate(lots: list[dict[str, Any]]) -> dict[str, Any]:
            record = asdict(lot)
            record["level"] = level
            record["executed_quantity"] = executed_qty
            record["base_commission_quantity"] = base_commission
            lots.append(record)
            return record

        return self.update_lots(mutate)

    def close_lot(self, lot_id: str, order: dict[str, Any], trading_fee_rate: float) -> dict[str, Any] | None:
        executed_qty = float(order.get("executedQty", 0) or 0)
        quote_qty = float(order.get("cummulativeQuoteQty", 0) or 0)
        if executed_qty <= 0 or quote_qty <= 0:
            return None

        def mutate(lots: list[dict[str, Any]]) -> dict[str, Any] | None:
            for lot in lots:
                if lot.get("id") != lot_id or lot.get("status") != "open":
                    continue
                sold_qty = min(executed_qty, float(lot["remaining_quantity"]))
                sell_price = quote_qty / executed_qty
                cost = float(lot["buy_price"]) * sold_qty
                proceeds = sell_price * sold_qty
                buy_fee = float(lot.get("buy_fee_quote") or 0)
                sell_fee = proceeds * trading_fee_rate
                total_fee = buy_fee + sell_fee
                remaining = max(float(lot["remaining_quantity"]) - sold_qty, 0.0)
                lot["remaining_quantity"] = remaining
                lot["sell_price"] = sell_price
                lot["sell_quote"] = proceeds
                lot["realized_pnl"] = proceeds - cost
                lot["buy_fee_quote"] = buy_fee
                lot["sell_fee_quote"] = sell_fee
                lot["total_fee_quote"] = total_fee
                lot["net_realized_pnl"] = proceeds - cost - total_fee
                if remaining <= 0.00000001:
                    lot["status"] = "closed"
                    lot["closed_at"] = _now()
                return lot
            return None

        return self.update_lots(mutate)

    def external_close_lot(
        self,
        lot_id: str,
        sell_price: float,
        quantity: float | None,
        trading_fee_rate: float,
        note: str = "external manual sell",
    ) -> dict[str, Any] | None:
        if sell_price <= 0:
            return None

        def mutate(lots: list[dict[str, Any]]) -> dict[str, Any] | None:
            for lot in lots:
                if lot.get("id") != lot_id or lot.get("status") != "open":
                    continue
                open_qty = float(lot.get("remaining_quantity", 0) or 0)
                sold_qty = min(open_qty, quantity if quantity and quantity > 0 else open_qty)
                if sold_qty <= 0:
                    return None
                cost = float(lot["buy_price"]) * sold_qty
                proceeds = sell_price * sold_qty
                buy_fee = float(lot.get("buy_fee_quote") or 0)
                sell_fee = proceeds * trading_fee_rate
                total_fee = buy_fee + sell_fee
                remaining = max(open_qty - sold_qty, 0.0)
                lot["remaining_quantity"] = remaining
                lot["sell_price"] = sell_price
                lot["sell_quote"] = proceeds
                lot["realized_pnl"] = proceeds - cost
                lot["buy_fee_quote"] = buy_fee
                lot["sell_fee_quote"] = sell_fee
                lot["total_fee_quote"] = total_fee
                lot["net_realized_pnl"] = proceeds - cost - total_fee
                lot["external_close"] = True
                lot["external_close_note"] = note
                lot["manual_sell_price"] = sell_price
                if remaining <= 0.00000001:
                    lot["status"] = "closed"
                    lot["closed_at"] = _now()
                return lot
            return None

        return self.update_lots(mutate)

    def set_auto_sell(self, lot_id: str, enabled: bool) -> dict[str, Any] | None:
        def mutate(lots: list[dict[str, Any]]) -> dict[str, Any] | None:
            for lot in lots:
                if lot.get("id") != lot_id or lot.get("status") != "open":
                    continue
                lot["auto_sell"] = enabled
                return lot
            return None

        return self.update_lots(mutate)

    def retarget_open_lots(self, target_profit_pct: float, trading_fee_rate: float) -> dict[str, int]:
        def mutate(lots: list[dict[str, Any]]) -> dict[str, int]:
            updated = 0
            skipped = 0
            for lot in lots:
                if lot.get("status") != "open" or float(lot.get("remaining_quantity", 0) or 0) <= 0:
                    continue
                level = str(lot.get("level", ""))
                if level.startswith("swing-") or lot.get("pending_limit_sell_order_id"):
                    skipped += 1
                    continue
                buy_price = float(lot.get("buy_price") or 0)
                if buy_price <= 0:
                    skipped += 1
                    continue
                lot["target_price"] = buy_price * (1 + target_profit_pct + trading_fee_rate * 2)
                lot.pop("effective_target_price", None)
                lot.pop("target_price_adjusted", None)
                lot["target_profit_pct"] = target_profit_pct
                updated += 1
            return {"updated": updated, "skipped": skipped}

        return self.update_lots(mutate)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lot_fee_quote(lot: dict[str, Any]) -> float:
    if lot.get("total_fee_quote") is not None:
        return float(lot.get("total_fee_quote") or 0)
    buy_fee = lot.get("buy_fee_quote") or 0
    sell_fee = lot.get("sell_fee_quote") or 0
    return float(buy_fee or 0) + float(sell_fee or 0)


def _commission_for_asset(order: dict[str, Any], asset: str) -> float:
    clean_asset = asset.strip().upper()
    if not clean_asset:
        return 0.0
    total = 0.0
    for fill in order.get("fills", []) or []:
        if str(fill.get("commissionAsset", "")).upper() == clean_asset:
            total += float(fill.get("commission", 0) or 0)
    return total


def _lot_net_realized_pnl(lot: dict[str, Any], trading_fee_rate: float) -> float:
    if lot.get("net_realized_pnl") is not None:
        return float(lot.get("net_realized_pnl") or 0)
    return float(lot.get("realized_pnl") or 0)
