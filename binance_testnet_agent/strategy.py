from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    recent_closes: list[float]
    base_balance: float
    quote_balance: float


@dataclass(frozen=True)
class StrategyDecision:
    signal: Signal
    reason: str
    reference_price: float
    price: float
    order_quote_size: float = 0.0
    level: str = "none"
    lot_id: str | None = None
    quantity: float = 0.0
    target_price: float = 0.0


@dataclass(frozen=True)
class GridStrategy:
    grid_step_pct: float
    take_profit_pct: float
    order_quote_size: float
    max_position_quote: float
    add_on_step_pct: float = 0.0025

    def decide(
        self,
        snapshot: MarketSnapshot,
        state: dict[str, int] | None = None,
        open_lots: list[dict[str, float | str]] | None = None,
    ) -> StrategyDecision:
        state = state or {}
        open_lots = open_lots or []
        if not snapshot.recent_closes:
            return StrategyDecision(Signal.HOLD, "not enough kline data", snapshot.price, snapshot.price)

        reference_price = sum(snapshot.recent_closes[-20:]) / min(len(snapshot.recent_closes), 20)
        starter_level_pct = 0.0010
        add_on_step_pct = self.add_on_step_pct
        buy_levels = [
            (1, 0.0025, 1.0),
            (2, 0.0050, 1.2),
            (3, 0.0085, 1.6),
            (4, 0.0130, 2.2),
        ]

        if snapshot.price >= reference_price:
            state["last_buy_level"] = 0
        if snapshot.price <= reference_price:
            state["last_sell_level"] = 0

        open_buy_levels = {
            str(lot.get("level"))
            for lot in open_lots
            if str(lot.get("level", "")).startswith("buy-")
        }
        open_buy_prices = [
            float(lot.get("buy_price", 0))
            for lot in open_lots
            if float(lot.get("remaining_quantity", 0)) > 0 and float(lot.get("buy_price", 0)) > 0
        ]
        lowest_open_buy_price = min(open_buy_prices) if open_buy_prices else 0.0
        tracked_position_quote = _tracked_position_quote(open_lots, snapshot.price)
        account_position_quote = snapshot.base_balance * snapshot.price
        current_position_quote = max(tracked_position_quote, account_position_quote)
        profitable_lots = [
            lot for lot in open_lots
            if _auto_sell_enabled(lot) and snapshot.price >= _lot_target_price(lot) and float(lot.get("remaining_quantity", 0)) > 0
        ]
        if profitable_lots:
            lot = min(profitable_lots, key=_lot_target_price)
            return StrategyDecision(
                Signal.SELL,
                "price reached lot target",
                reference_price,
                snapshot.price,
                float(lot.get("remaining_quantity", 0)) * snapshot.price,
                "lot-target",
                str(lot.get("id")),
                float(lot.get("remaining_quantity", 0)),
            )

        if open_lots and lowest_open_buy_price and snapshot.price <= lowest_open_buy_price * (1 - add_on_step_pct):
            drop_from_lowest = 1 - snapshot.price / lowest_open_buy_price
            level, _drop_pct, multiplier = _buy_level_for_drop(drop_from_lowest, buy_levels)
            quote_size = self.order_quote_size * multiplier
            level_name = f"buy-{level}-add-{len(open_lots) + 1}"
            if current_position_quote + quote_size > self.max_position_quote:
                return StrategyDecision(Signal.HOLD, "max position limit reached", reference_price, snapshot.price)
            if snapshot.quote_balance < quote_size:
                return StrategyDecision(Signal.HOLD, "quote balance below downtrend add-on order size", reference_price, snapshot.price)
            return StrategyDecision(
                Signal.BUY,
                f"price fell {drop_from_lowest:.2%} below lowest open lot",
                reference_price,
                snapshot.price,
                quote_size,
                level_name,
            )

        for level, drop_pct, multiplier in reversed(buy_levels):
            buy_level = reference_price * (1 - drop_pct)
            quote_size = self.order_quote_size * multiplier
            level_name = f"buy-{level}"
            if snapshot.price > buy_level:
                continue
            if level_name in open_buy_levels:
                add_on_level = f"{level_name}-add-{len(open_lots) + 1}"
                if lowest_open_buy_price and snapshot.price <= lowest_open_buy_price * (1 - add_on_step_pct):
                    if current_position_quote + quote_size > self.max_position_quote:
                        return StrategyDecision(Signal.HOLD, "max position limit reached", reference_price, snapshot.price)
                    if snapshot.quote_balance < quote_size:
                        return StrategyDecision(Signal.HOLD, "quote balance below add-on order size", reference_price, snapshot.price)
                    return StrategyDecision(
                        Signal.BUY,
                        f"downtrend add-on at buy level {level}",
                        reference_price,
                        snapshot.price,
                        quote_size,
                        add_on_level,
                    )
                return StrategyDecision(
                    Signal.HOLD,
                    f"{level_name} already has an open lot; waiting for deeper add-on",
                    reference_price,
                    snapshot.price,
                    0.0,
                    level_name,
                )
            if level <= int(state.get("last_buy_level", 0)) and not (
                lowest_open_buy_price and snapshot.price <= lowest_open_buy_price * (1 - add_on_step_pct)
            ):
                return StrategyDecision(
                    Signal.HOLD,
                    f"buy level {level} already used",
                    reference_price,
                    snapshot.price,
                    0.0,
                    level_name,
                )
            if current_position_quote + quote_size > self.max_position_quote:
                return StrategyDecision(Signal.HOLD, "max position limit reached", reference_price, snapshot.price)
            if snapshot.quote_balance < quote_size:
                return StrategyDecision(Signal.HOLD, "quote balance below order size", reference_price, snapshot.price)
            return StrategyDecision(
                Signal.BUY,
                f"price reached buy level {level} ({drop_pct:.2%} below reference)",
                reference_price,
                snapshot.price,
                quote_size,
                level_name,
            )

        if not open_lots and snapshot.price <= reference_price * (1 + starter_level_pct):
            if current_position_quote + self.order_quote_size > self.max_position_quote:
                return StrategyDecision(Signal.HOLD, "max position limit reached", reference_price, snapshot.price)
            if snapshot.quote_balance < self.order_quote_size:
                return StrategyDecision(Signal.HOLD, "quote balance below starter order size", reference_price, snapshot.price)
            return StrategyDecision(
                Signal.BUY,
                "starter lot for rolling grid",
                reference_price,
                snapshot.price,
                self.order_quote_size,
                "starter",
            )

        return StrategyDecision(Signal.HOLD, "inside grid band", reference_price, snapshot.price)


def _tracked_position_quote(open_lots: list[dict[str, float | str]], price: float) -> float:
    return sum(float(lot.get("remaining_quantity", 0)) * price for lot in open_lots)


def _lot_target_price(lot: dict[str, float | str]) -> float:
    return float(lot.get("effective_target_price") or lot.get("target_price") or 0)


def _auto_sell_enabled(lot: dict[str, float | str]) -> bool:
    return lot.get("auto_sell", True) is not False and not lot.get("pending_limit_sell_order_id")


def _buy_level_for_drop(drop_pct: float, buy_levels: list[tuple[int, float, float]]) -> tuple[int, float, float]:
    chosen = buy_levels[0]
    for level in buy_levels:
        if drop_pct >= level[1]:
            chosen = level
    return chosen
