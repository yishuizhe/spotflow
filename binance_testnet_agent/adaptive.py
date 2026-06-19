from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .strategy import Signal, StrategyDecision


@dataclass(frozen=True)
class MarketRegime:
    name: str
    label: str
    confidence: float
    volatility_pct: float
    drawdown_pct: float
    ma_fast: float
    ma_slow: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapitalPlan:
    regime: str
    total_cap: float
    grid_cap: float
    swing_cap: float
    scalp_cap: float
    dip_cap: float
    order_multiplier: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayeredRisk:
    allow_buy: bool
    emergency_pause: bool
    order_multiplier: float
    layer: str
    reason: str
    limited_strategies: tuple[str, ...] = ()
    limited_order_multiplier: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def identify_market_regime(price: float, closes: list[float]) -> MarketRegime:
    clean = [float(item) for item in closes if float(item) > 0]
    if len(clean) < 24:
        return MarketRegime("warmup", "数据预热", 0.0, 0.0, 0.0, price, price, "大周期数据不足")
    fast_window = min(24, len(clean))
    slow_window = min(168, len(clean))
    ma_fast = sum(clean[-fast_window:]) / fast_window
    ma_slow = sum(clean[-slow_window:]) / slow_window
    previous_fast = (
        sum(clean[-fast_window * 2 : -fast_window]) / fast_window
        if len(clean) >= fast_window * 2
        else ma_fast
    )
    returns = [
        math.log(clean[index] / clean[index - 1])
        for index in range(max(1, len(clean) - fast_window + 1), len(clean))
        if clean[index - 1] > 0
    ]
    volatility = _stddev(returns) * math.sqrt(24) if returns else 0.0
    high = max(clean[-slow_window:])
    drawdown = max(0.0, 1 - price / high) if high > 0 else 0.0
    fast_slope = ma_fast / previous_fast - 1 if previous_fast > 0 else 0.0

    if price < ma_fast < ma_slow and fast_slope < -0.002:
        name, label, reason = "downtrend", "下跌趋势", "价格低于长短均线且短均线继续向下"
        confidence = min(1.0, abs(fast_slope) * 35 + drawdown * 8)
    elif price > ma_fast > ma_slow and fast_slope > 0.002:
        name, label, reason = "uptrend", "上涨趋势", "价格高于长短均线且短均线向上"
        confidence = min(1.0, fast_slope * 35 + max(0.0, price / ma_slow - 1) * 5)
    elif volatility >= 0.025:
        name, label, reason = "volatile", "高波动", "短周期波动显著放大"
        confidence = min(1.0, volatility / 0.05)
    else:
        name, label, reason = "range", "震荡行情", "均线方向不强，价格以区间波动为主"
        confidence = min(1.0, 0.55 + max(0.0, 0.02 - volatility) * 10)
    return MarketRegime(name, label, confidence, volatility, drawdown, ma_fast, ma_slow, reason)


def capital_plan(
    total_value: float,
    max_position_quote: float,
    regime: MarketRegime,
    account_drawdown_pct: float,
    position_usage_pct: float,
) -> CapitalPlan:
    base_cap = min(max_position_quote, total_value * 0.72)
    if regime.name == "downtrend":
        weights = (0.0, 0.0, 0.04, 0.10)
        regime_multiplier = 0.35
    elif regime.name == "volatile":
        weights = (0.18, 0.10, 0.06, 0.08)
        regime_multiplier = 0.55
    elif regime.name == "uptrend":
        weights = (0.30, 0.24, 0.05, 0.04)
        regime_multiplier = 0.80
    else:
        weights = (0.34, 0.22, 0.10, 0.06)
        regime_multiplier = 1.0
    drawdown_multiplier = _clamp(1 - account_drawdown_pct * 5, 0.25, 1.0)
    usage_multiplier = _clamp(1.15 - position_usage_pct, 0.25, 1.0)
    volatility_multiplier = _clamp(0.018 / max(regime.volatility_pct, 0.006), 0.35, 1.0)
    order_multiplier = min(regime_multiplier, drawdown_multiplier, usage_multiplier, volatility_multiplier)
    return CapitalPlan(
        regime=regime.name,
        total_cap=base_cap,
        grid_cap=base_cap * weights[0],
        swing_cap=base_cap * weights[1],
        scalp_cap=base_cap * weights[2],
        dip_cap=base_cap * weights[3],
        order_multiplier=order_multiplier,
        reason=(
            f"{regime.label}；回撤 {account_drawdown_pct:.2%}，"
            f"仓位占用 {position_usage_pct:.2%}，新单系数 {order_multiplier:.2f}"
        ),
    )


def layered_risk(
    *,
    account_drawdown_pct: float,
    daily_loss_quote: float,
    max_daily_loss_quote: float,
    position_usage_pct: float,
    volatility_pct: float,
    price_break_pct: float,
) -> LayeredRisk:
    if daily_loss_quote <= -abs(max_daily_loss_quote):
        return LayeredRisk(False, True, 0.0, "组合止损", "当日亏损达到上限，暂停全部自动买入")
    if account_drawdown_pct >= 0.12:
        return LayeredRisk(
            False,
            True,
            0.0,
            "组合止损",
            "账户回撤达到 12%，暂停网格/波段新增仓位；防守剥头皮与抄底保留小额度用于摊低成本",
            limited_strategies=("scalp", "dip"),
            limited_order_multiplier=0.15,
        )
    if price_break_pct >= 0.08:
        return LayeredRisk(False, False, 0.0, "趋势破位", "价格跌破大周期参考超过 8%，等待重新企稳")
    multiplier = 1.0
    reasons: list[str] = []
    if account_drawdown_pct >= 0.06:
        multiplier = min(multiplier, 0.35)
        reasons.append("账户回撤超过 6%")
    elif account_drawdown_pct >= 0.03:
        multiplier = min(multiplier, 0.60)
        reasons.append("账户回撤超过 3%")
    if position_usage_pct >= 0.85:
        multiplier = min(multiplier, 0.25)
        reasons.append("持仓占用超过 85%")
    elif position_usage_pct >= 0.70:
        multiplier = min(multiplier, 0.55)
        reasons.append("持仓占用超过 70%")
    if volatility_pct >= 0.03:
        multiplier = min(multiplier, 0.40)
        reasons.append("波动率过高")
    return LayeredRisk(True, False, multiplier, "动态降仓" if reasons else "正常", "；".join(reasons) or "组合风险正常")


def allocate_decision(
    decision: StrategyDecision,
    plan: CapitalPlan,
    risk: LayeredRisk,
    strategy_positions: dict[str, float],
    min_order_quote: float,
) -> StrategyDecision:
    if decision.signal != Signal.BUY:
        return decision
    strategy = strategy_name(decision)
    cap = {
        "grid": plan.grid_cap,
        "swing": plan.swing_cap,
        "scalp": plan.scalp_cap,
        "dip": plan.dip_cap,
    }[strategy]
    used = strategy_positions.get(strategy, 0.0)
    if not risk.allow_buy:
        if strategy in risk.limited_strategies and risk.limited_order_multiplier > 0:
            remaining = max(0.0, cap - used)
            quote_size = min(decision.order_quote_size * risk.limited_order_multiplier, remaining)
            if quote_size < min_order_quote:
                return StrategyDecision(
                    Signal.HOLD,
                    f"{risk.layer}限额：{strategy} 剩余额度或动态单笔低于最小下单额",
                    decision.reference_price,
                    decision.price,
                )
            return StrategyDecision(
                decision.signal,
                f"{decision.reason}；{risk.layer}限额放行 {strategy} ×{risk.limited_order_multiplier:.2f}",
                decision.reference_price,
                decision.price,
                quote_size,
                decision.level,
                decision.lot_id,
                decision.quantity,
                decision.target_price,
            )
        return StrategyDecision(Signal.HOLD, risk.reason, decision.reference_price, decision.price)
    remaining = max(0.0, cap - used)
    quote_size = min(decision.order_quote_size * plan.order_multiplier * risk.order_multiplier, remaining)
    if quote_size < min_order_quote:
        return StrategyDecision(
            Signal.HOLD,
            f"资金调度：{strategy} 剩余额度或动态单笔低于最小下单额",
            decision.reference_price,
            decision.price,
        )
    return StrategyDecision(
        decision.signal,
        f"{decision.reason}；统一资金调度 {strategy} ×{plan.order_multiplier * risk.order_multiplier:.2f}",
        decision.reference_price,
        decision.price,
        quote_size,
        decision.level,
        decision.lot_id,
        decision.quantity,
        decision.target_price,
    )


def dynamic_profit_pct(regime: MarketRegime, base_profit_pct: float) -> float:
    if regime.name == "uptrend":
        return max(base_profit_pct, 0.010)
    if regime.name == "range":
        return max(base_profit_pct, 0.007)
    if regime.name == "volatile":
        return max(0.004, min(base_profit_pct, 0.007))
    if regime.name == "downtrend":
        return max(0.0025, min(base_profit_pct, 0.0045))
    return base_profit_pct


def strategy_name(decision: StrategyDecision) -> str:
    level = str(decision.level)
    if level.startswith("scalp-"):
        return "scalp"
    if level.startswith("swing-"):
        return "dip" if "dip probe" in decision.reason else "swing"
    return "grid"


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((item - mean) ** 2 for item in values) / len(values))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
