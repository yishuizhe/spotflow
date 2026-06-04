from __future__ import annotations

from dataclasses import asdict, dataclass

from .strategy import Signal, StrategyDecision


@dataclass(frozen=True)
class TrendState:
    enabled: bool
    downtrend: bool
    recovered: bool
    rebound: bool
    price: float
    ma24: float
    ma7d: float
    prev_ma24: float
    mode: str
    reason: str
    normal_pool_quote: float
    dip_pool_quote: float
    grid_position_quote: float
    dip_position_quote: float

    def to_dict(self) -> dict[str, float | str | bool]:
        return asdict(self)


@dataclass(frozen=True)
class TrendGuard:
    enabled: bool
    normal_pool_pct: float
    dip_pool_pct: float
    dip_order_quote: float
    rebound_pct: float
    interval_minutes: int = 60

    def evaluate(
        self,
        price: float,
        closes: list[float],
        max_position_quote: float,
        grid_position_quote: float,
        dip_position_quote: float,
    ) -> TrendState:
        normal_pool = max(0.0, max_position_quote * self.normal_pool_pct)
        dip_pool = max(0.0, max_position_quote * self.dip_pool_pct)
        if not self.enabled:
            return TrendState(False, False, False, False, price, 0.0, 0.0, 0.0, "off", "trend guard disabled", normal_pool, dip_pool, grid_position_quote, dip_position_quote)

        interval = max(1, self.interval_minutes)
        ma24_window = max(2, int(24 * 60 / interval))
        ma7_window = max(ma24_window + 1, int(7 * 24 * 60 / interval))
        if len(closes) < ma7_window + ma24_window:
            return TrendState(True, False, False, False, price, 0.0, 0.0, 0.0, "warmup", "trend guard warming up", normal_pool, dip_pool, grid_position_quote, dip_position_quote)

        ma24 = sum(closes[-ma24_window:]) / ma24_window
        ma7d = sum(closes[-ma7_window:]) / ma7_window
        prev_ma24 = sum(closes[-ma24_window * 2 : -ma24_window]) / ma24_window
        downtrend = price < ma24 and price < ma7d and ma24 < prev_ma24
        recovered = price >= ma24 and ma24 >= prev_ma24 * 0.999
        rebound = _has_rebound(closes, self.rebound_pct, max(3, int(6 * 60 / interval)))

        if recovered:
            mode = "normal"
            reason = "price recovered above 24h average"
        elif downtrend and rebound:
            mode = "dip_probe"
            reason = "downtrend with rebound confirmation"
        elif downtrend:
            mode = "downtrend"
            reason = "below 24h and 7d averages with falling 24h average"
        else:
            mode = "recovery"
            reason = "between trend states; reduced grid pool"
        return TrendState(True, downtrend, recovered, rebound, price, ma24, ma7d, prev_ma24, mode, reason, normal_pool, dip_pool, grid_position_quote, dip_position_quote)

    def apply_to_grid(self, decision: StrategyDecision, state: TrendState) -> StrategyDecision:
        if decision.signal != Signal.BUY or not self.enabled:
            return decision
        if state.mode == "normal" or state.mode == "warmup":
            if state.grid_position_quote + decision.order_quote_size > state.normal_pool_quote:
                return StrategyDecision(Signal.HOLD, "trend guard: normal grid pool limit reached", decision.reference_price, decision.price)
            return decision
        if state.mode == "recovery":
            recovery_pool = state.normal_pool_quote * 0.70
            if state.grid_position_quote + decision.order_quote_size > recovery_pool:
                return StrategyDecision(Signal.HOLD, "trend guard: recovery grid pool limit reached", decision.reference_price, decision.price)
            return decision
        return StrategyDecision(Signal.HOLD, f"trend guard: {state.reason}; ordinary grid paused", decision.reference_price, decision.price)

    def apply_to_dip(self, decision: StrategyDecision, state: TrendState) -> StrategyDecision:
        if decision.signal != Signal.BUY or not self.enabled:
            return decision
        if state.mode not in {"downtrend", "dip_probe", "recovery"}:
            return decision
        if state.mode == "downtrend" and not state.rebound:
            return StrategyDecision(Signal.HOLD, "trend guard: dip buy waiting for rebound confirmation", decision.reference_price, decision.price)
        quote_size = min(decision.order_quote_size, self.dip_order_quote)
        if state.dip_position_quote + quote_size > state.dip_pool_quote:
            return StrategyDecision(Signal.HOLD, "trend guard: dip pool limit reached", decision.reference_price, decision.price)
        return StrategyDecision(
            decision.signal,
            f"{decision.reason}; trend guard dip probe",
            decision.reference_price,
            decision.price,
            quote_size,
            decision.level,
            decision.lot_id,
            decision.quantity,
            decision.target_price,
        )


def _has_rebound(closes: list[float], rebound_pct: float, window_size: int) -> bool:
    if len(closes) < max(3, window_size):
        return False
    window = closes[-window_size:]
    low = min(window)
    current = window[-1]
    return low > 0 and current >= low * (1 + rebound_pct) and window[-1] > window[-2] > window[-3]
