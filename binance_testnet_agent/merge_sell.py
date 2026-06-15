from __future__ import annotations

from decimal import Decimal
from typing import Any

from .binance_client import BinanceSpotClient
from .config import AgentConfig
from .defensive import enrich_lots_with_defensive_targets
from .ledger import PositionLedger


def lot_breakeven_floor(lot: dict[str, Any], fee_rate: float, include_slippage: bool) -> float:
    """该批次按真实成本（含买入手续费）覆盖卖出手续费后的最低不亏卖价。

    与 agent._sell_gate 使用同一套精确公式：成本 * (1+f) / (1-f)。
    include_slippage=True 时再加一层市价滑点缓冲，用于市价卖出；
    限价卖出价格可保证，不需要滑点缓冲。
    """
    buy_price = float(lot.get("buy_price", 0) or 0)
    buy_quote = float(lot.get("buy_quote", 0) or 0)
    original_quantity = float(lot.get("quantity", 0) or 0)
    unit_from_quote = buy_quote / original_quantity if buy_quote > 0 and original_quantity > 0 else 0.0
    true_unit_cost = max(buy_price, unit_from_quote)
    if true_unit_cost <= 0:
        return 0.0
    breakeven = true_unit_cost * (1 + fee_rate) / max(1 - fee_rate, 0.00000001)
    if include_slippage:
        breakeven *= 1 + max(0.0005, fee_rate * 0.5)
    return breakeven


def eligible_ready_lots(
    open_lots: list[dict[str, Any]],
    price: float,
    config: AgentConfig,
) -> list[dict[str, Any]]:
    """筛选可以自动卖出、且现价已同时覆盖保本价和目标价的未平批次。"""
    enriched = enrich_lots_with_defensive_targets(
        open_lots,
        enabled=config.defensive_mode,
        target_profit_pct=config.take_profit_pct,
        trading_fee_rate=config.trading_fee_rate,
        aged_days_1=config.defensive_aged_lot_days_1,
        aged_profit_pct_1=config.defensive_aged_lot_profit_pct_1,
        aged_days_2=config.defensive_aged_lot_days_2,
        aged_profit_pct_2=config.defensive_aged_lot_profit_pct_2,
    )
    ready: list[dict[str, Any]] = []
    for lot in enriched:
        if lot.get("pending_limit_sell_order_id"):
            continue
        if lot.get("auto_sell", True) is False:
            continue
        qty = float(lot.get("remaining_quantity", 0) or 0)
        if qty <= 0:
            continue
        floor = lot_breakeven_floor(lot, config.trading_fee_rate, include_slippage=True)
        target = float(lot.get("effective_target_price") or lot.get("target_price") or 0)
        required = max(floor, target)
        if required <= 0 or price < required:
            continue
        ready.append(lot)
    return ready


def merge_sell_ready_lots(
    client: BinanceSpotClient,
    ledger: PositionLedger,
    config: AgentConfig,
    *,
    available_base: float | None = None,
    require_dust: bool = True,
) -> dict[str, Any]:
    """把多个达标但单笔过小的未平批次合并成一笔市价卖单，成交后按瀑布方式分摊回各批次账本。

    每个被纳入的批次都已独立满足保本价和目标价，因此合并卖出对每个批次都不会亏本。
    require_dust=True 时，只有在存在「单笔金额低于币安最小下单额」的碎屑批次时才会触发，
    避免对正常批次做不必要的批量卖出。
    """
    symbol = config.symbol
    price = client.ticker_price(symbol)
    filters = client.symbol_filters(symbol)
    min_notional = filters.min_notional

    open_lots = ledger.open_lots()
    ready = eligible_ready_lots(open_lots, price, config)
    if not ready:
        return {"merged": False, "reason": "没有达到保本/目标价且可自动卖出的批次", "lots": 0}

    has_dust = any(
        Decimal(str(float(lot.get("remaining_quantity", 0) or 0))) * Decimal(str(price)) < min_notional
        for lot in ready
    )
    if require_dust and not has_dust:
        return {"merged": False, "reason": "没有低于最小下单额的碎屑批次需要合并", "lots": len(ready)}

    total_qty = sum(float(lot.get("remaining_quantity", 0) or 0) for lot in ready)
    rounded_total = client.round_quantity(Decimal(str(total_qty)), filters)
    if rounded_total < filters.min_qty:
        return {
            "merged": False,
            "reason": f"合并后数量 {rounded_total} 仍低于币安最小下单数量 {filters.min_qty}",
            "lots": len(ready),
        }
    if rounded_total * Decimal(str(price)) < min_notional:
        return {
            "merged": False,
            "reason": f"合并后金额仍低于币安最小下单额 {min_notional}，请等待更多批次达标后再合并",
            "lots": len(ready),
        }

    if available_base is None:
        account = client.account()
        available_base = 0.0
        for item in account.get("balances", []):
            if str(item.get("asset", "")).upper() == config.base_asset:
                available_base = float(item.get("free", 0) or 0)
                break
    if rounded_total > Decimal(str(available_base)):
        return {
            "merged": False,
            "reason": (
                f"可用 {config.base_asset} 余额 {available_base:.8f} 低于合并卖出数量 {rounded_total}；"
                "请先取消占用余额的限价卖单或同步账本后再试。"
            ),
            "lots": len(ready),
        }

    order = client.market_sell_qty(symbol, rounded_total)
    executed = float(order.get("executedQty", 0) or 0)
    proceeds = float(order.get("cummulativeQuoteQty", 0) or 0)
    if executed <= 0 or proceeds <= 0:
        return {"merged": False, "reason": "合并卖单未成交", "order": order, "lots": len(ready)}
    avg_price = proceeds / executed

    closed: list[dict[str, Any]] = []
    remaining_to_allocate = executed
    for lot in ready:
        if remaining_to_allocate <= 0.00000001:
            break
        lot_qty = float(lot.get("remaining_quantity", 0) or 0)
        assign = min(lot_qty, remaining_to_allocate)
        remaining_to_allocate -= assign
        per_lot_order = {
            "orderId": order.get("orderId"),
            "executedQty": assign,
            "cummulativeQuoteQty": assign * avg_price,
            "fills": [],
        }
        updated = ledger.close_lot(str(lot.get("id")), per_lot_order, config.trading_fee_rate)
        if updated:
            closed.append(updated)

    return {
        "merged": True,
        "order": order,
        "sold_quantity": executed,
        "proceeds": proceeds,
        "avg_price": avg_price,
        "lots_closed": len(closed),
        "lots": closed,
    }
