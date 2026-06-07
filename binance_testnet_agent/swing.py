from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .strategy import MarketSnapshot, Signal, StrategyDecision
from .defensive_scalp import is_scalp_lot


@dataclass(frozen=True)
class SwingBand:
    enabled: bool
    center_price: float
    buy_price: float
    sell_price: float
    band_pct: float
    allocation_quote: float
    position_quote: float
    min_order_quote: float
    max_order_quote: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SwingStrategy:
    enabled: bool
    allocation_pct: float
    min_order_quote: float
    max_order_quote: float
    add_step_pct: float
    min_band_pct: float
    max_band_pct: float
    trading_fee_rate: float = 0.0
    manual_center_price: float = 0.0

    def decide(
        self,
        snapshot: MarketSnapshot,
        swing_closes: list[float],
        swing_lots: list[dict[str, Any]],
        total_value_quote: float,
    ) -> tuple[StrategyDecision, SwingBand]:
        band = self.band(snapshot.price, swing_closes, swing_lots, total_value_quote)
        if not self.enabled:
            return StrategyDecision(Signal.HOLD, "swing disabled", band.center_price, snapshot.price), band
        if not swing_closes:
            return StrategyDecision(Signal.HOLD, "swing missing kline data", snapshot.price, snapshot.price), band

        open_lots = [
            lot for lot in swing_lots
            if float(lot.get("remaining_quantity", 0)) > 0 and str(lot.get("status", "open")) == "open"
        ]
        sellable_lots = [
            lot for lot in open_lots
            if lot.get("auto_sell", True) is not False
            and not lot.get("pending_limit_sell_order_id")
            and snapshot.price >= self.safe_target_price(lot, band.sell_price)
        ]
        if sellable_lots:
            lot = min(sellable_lots, key=lambda item: self.safe_target_price(item, band.sell_price))
            return (
                StrategyDecision(
                    Signal.SELL,
                    "swing price reached sell band",
                    band.center_price,
                    snapshot.price,
                    float(lot.get("remaining_quantity", 0)) * snapshot.price,
                    "swing-target",
                    str(lot.get("id")),
                    float(lot.get("remaining_quantity", 0)),
                    self.safe_target_price(lot, band.sell_price),
                ),
                band,
            )

        if open_lots:
            lowest_buy = min(float(lot.get("buy_price") or snapshot.price) for lot in open_lots)
            next_buy_price = lowest_buy * (1 - self.add_step_pct)
            if snapshot.price > next_buy_price:
                return StrategyDecision(Signal.HOLD, "swing waiting for deeper add-on", band.center_price, snapshot.price), band
        elif snapshot.price > band.buy_price:
            return StrategyDecision(Signal.HOLD, "swing waiting for buy band", band.center_price, snapshot.price), band

        quote_size = max(0.0, band.allocation_quote - band.position_quote)
        quote_size = min(quote_size, snapshot.quote_balance)
        if self.max_order_quote > 0:
            quote_size = min(quote_size, self.max_order_quote)
        if quote_size < self.min_order_quote:
            return StrategyDecision(Signal.HOLD, "swing quote below min order", band.center_price, snapshot.price), band

        return (
            StrategyDecision(
                Signal.BUY,
                "swing price reached buy band" if not open_lots else "swing price reached deeper add-on",
                band.center_price,
                snapshot.price,
                quote_size,
                f"swing-entry-{len(open_lots) + 1}",
                target_price=band.sell_price,
            ),
            band,
        )

    def safe_target_price(self, lot: dict[str, Any], fallback: float = 0.0) -> float:
        buy_price = float(lot.get("buy_price") or 0)
        recorded_target = float(lot.get("target_price") or fallback)
        profitable_target = buy_price * (1 + self.min_band_pct + self.trading_fee_rate * 2)
        return max(recorded_target, profitable_target)

    def band(
        self,
        price: float,
        swing_closes: list[float],
        swing_lots: list[dict[str, Any]],
        total_value_quote: float,
    ) -> SwingBand:
        clean = [item for item in swing_closes if item > 0]
        if self.manual_center_price > 0:
            center = self.manual_center_price
            reason = "manual center"
        elif clean:
            center = sum(clean) / len(clean)
            reason = f"{len(clean)} candle average"
        else:
            center = price
            reason = "fallback current price"

        if clean:
            high = max(clean)
            low = min(clean)
            half_range_pct = ((high - low) / ((high + low) / 2)) / 2 if high > 0 and low > 0 else self.min_band_pct
            band_pct = _clamp(half_range_pct * 0.45, self.min_band_pct, self.max_band_pct)
        else:
            band_pct = self.min_band_pct

        position_quote = sum(float(lot.get("remaining_quantity", 0)) * price for lot in swing_lots)
        allocation_quote = max(0.0, total_value_quote * max(0.0, self.allocation_pct))
        return SwingBand(
            enabled=self.enabled,
            center_price=center,
            buy_price=center * (1 - band_pct),
            sell_price=center * (1 + band_pct),
            band_pct=band_pct,
            allocation_quote=allocation_quote,
            position_quote=position_quote,
            min_order_quote=self.min_order_quote,
            max_order_quote=self.max_order_quote,
            reason=reason,
        )


def is_swing_lot(lot: dict[str, Any]) -> bool:
    return str(lot.get("level", "")).startswith("swing-")


def split_lots(lots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    swing_lots = [lot for lot in lots if is_swing_lot(lot)]
    grid_lots = [lot for lot in lots if not is_swing_lot(lot) and not is_scalp_lot(lot)]
    return grid_lots, swing_lots


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
