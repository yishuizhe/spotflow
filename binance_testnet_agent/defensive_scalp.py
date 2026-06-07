from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .strategy import MarketSnapshot, Signal, StrategyDecision


@dataclass(frozen=True)
class DefensiveScalpState:
    enabled: bool
    active: bool
    range_bound: bool
    center_price: float
    buy_price: float
    sell_price: float
    range_pct: float
    allocation_quote: float
    position_quote: float
    order_quote_size: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DefensiveScalpStrategy:
    enabled: bool
    allocation_pct: float
    order_pct: float
    min_order_quote: float
    max_order_quote: float
    buy_drop_pct: float
    take_profit_pct: float
    add_step_pct: float
    min_range_pct: float
    max_range_pct: float
    trading_fee_rate: float

    def decide(
        self,
        snapshot: MarketSnapshot,
        recent_closes: list[float],
        scalp_lots: list[dict[str, Any]],
        total_value_quote: float,
        defensive_active: bool,
    ) -> tuple[StrategyDecision, DefensiveScalpState]:
        state = self.state(snapshot, recent_closes, scalp_lots, total_value_quote, defensive_active)
        if not state.enabled:
            return StrategyDecision(Signal.HOLD, "defensive scalp disabled", state.center_price, snapshot.price), state
        if not state.active:
            return StrategyDecision(Signal.HOLD, state.reason, state.center_price, snapshot.price), state

        open_lots = [
            lot for lot in scalp_lots
            if str(lot.get("status", "open")) == "open" and float(lot.get("remaining_quantity", 0) or 0) > 0
        ]
        sellable = [
            lot for lot in open_lots
            if not lot.get("pending_limit_sell_order_id")
            and snapshot.price >= self.safe_target_price(lot)
        ]
        if sellable:
            lot = min(sellable, key=self.safe_target_price)
            return (
                StrategyDecision(
                    Signal.SELL,
                    "defensive scalp target reached",
                    state.center_price,
                    snapshot.price,
                    float(lot.get("remaining_quantity", 0) or 0) * snapshot.price,
                    "scalp-target",
                    str(lot.get("id")),
                    float(lot.get("remaining_quantity", 0) or 0),
                    self.safe_target_price(lot),
                ),
                state,
            )

        if not state.range_bound:
            return StrategyDecision(Signal.HOLD, state.reason, state.center_price, snapshot.price), state
        if snapshot.price > state.buy_price:
            return StrategyDecision(Signal.HOLD, "defensive scalp waiting for lower edge", state.center_price, snapshot.price), state
        if open_lots:
            lowest_buy = min(float(lot.get("buy_price") or snapshot.price) for lot in open_lots)
            if snapshot.price > lowest_buy * (1 - self.add_step_pct):
                return StrategyDecision(Signal.HOLD, "defensive scalp waiting for spacing", state.center_price, snapshot.price), state

        quote_size = min(state.order_quote_size, state.allocation_quote - state.position_quote, snapshot.quote_balance)
        if quote_size < self.min_order_quote:
            return StrategyDecision(Signal.HOLD, "defensive scalp quote below min order", state.center_price, snapshot.price), state
        return (
            StrategyDecision(
                Signal.BUY,
                "defensive scalp lower edge buy",
                state.center_price,
                snapshot.price,
                quote_size,
                f"scalp-entry-{len(open_lots) + 1}",
                target_price=snapshot.price * (1 + self.take_profit_pct + self.trading_fee_rate * 2),
            ),
            state,
        )

    def state(
        self,
        snapshot: MarketSnapshot,
        recent_closes: list[float],
        scalp_lots: list[dict[str, Any]],
        total_value_quote: float,
        defensive_active: bool,
    ) -> DefensiveScalpState:
        clean = [item for item in recent_closes if item > 0]
        center = sum(clean) / len(clean) if clean else snapshot.price
        if len(clean) >= 6:
            high = max(clean)
            low = min(clean)
            range_pct = (high - low) / center if center > 0 else 0.0
        else:
            range_pct = 0.0
        range_bound = self.min_range_pct <= range_pct <= self.max_range_pct
        active = self.enabled and defensive_active
        allocation_quote = max(0.0, total_value_quote * max(0.0, self.allocation_pct))
        position_quote = sum(float(lot.get("remaining_quantity", 0) or 0) * snapshot.price for lot in scalp_lots)
        order_quote = _clamp(total_value_quote * max(0.0, self.order_pct), self.min_order_quote, self.max_order_quote)
        if not self.enabled:
            reason = "disabled"
        elif not defensive_active:
            reason = "not in defensive mode"
        elif not range_bound:
            reason = f"range {range_pct:.2%} outside scalp band"
        else:
            reason = "defensive range bound"
        return DefensiveScalpState(
            self.enabled,
            active,
            range_bound,
            center,
            center * (1 - self.buy_drop_pct),
            center * (1 + self.take_profit_pct + self.trading_fee_rate * 2),
            range_pct,
            allocation_quote,
            position_quote,
            order_quote,
            reason,
        )

    def safe_target_price(self, lot: dict[str, Any]) -> float:
        buy_price = float(lot.get("buy_price") or 0)
        recorded_target = float(lot.get("target_price") or 0)
        profitable_target = buy_price * (1 + self.take_profit_pct + self.trading_fee_rate * 2)
        return max(recorded_target, profitable_target)


def is_scalp_lot(lot: dict[str, Any]) -> bool:
    return str(lot.get("level", "")).startswith("scalp-")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
