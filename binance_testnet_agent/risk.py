from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskDecision:
    allow_buy: bool
    reason: str
    rebound: bool = False


def evaluate_buy_risk(
    price: float,
    recent_closes: list[float],
    unrealized_pnl: float,
    max_floating_loss_quote: float,
    rapid_drop_pause_pct: float,
    large_drop_pause_pct: float,
    rebound_buy_pct: float,
    price_anomaly_pct: float,
) -> RiskDecision:
    if not recent_closes:
        return RiskDecision(False, "risk: missing kline data")

    last_close = recent_closes[-1]
    if last_close <= 0:
        return RiskDecision(False, "risk: invalid last close")

    price_gap = abs(price - last_close) / last_close
    if price_gap >= price_anomaly_pct:
        return RiskDecision(False, f"risk: price anomaly gap {price_gap:.2%}")

    if len(recent_closes) >= 6:
        old = recent_closes[-6]
        if old > 0:
            drop = (old - price) / old
            if drop >= rapid_drop_pause_pct:
                return RiskDecision(False, f"risk: rapid drop {drop:.2%}")

    if len(recent_closes) >= 30:
        window = recent_closes[-60:]
        high = max(window)
        if high > 0:
            drawdown = (high - price) / high
            if drawdown >= large_drop_pause_pct:
                rebound = _has_rebound(recent_closes, rebound_buy_pct)
                if rebound:
                    return RiskDecision(True, "risk: large drop but rebound detected", True)
                if _has_sideways_stabilized(recent_closes, rebound_buy_pct):
                    return RiskDecision(True, "risk: large drop but sideways stabilized", False)
                return RiskDecision(False, f"risk: large drop cooldown {drawdown:.2%}")

    if unrealized_pnl <= -abs(max_floating_loss_quote):
        rebound = _has_rebound(recent_closes, rebound_buy_pct)
        if rebound:
            return RiskDecision(True, "risk: floating loss exceeded but rebound detected", True)
        return RiskDecision(False, f"risk: floating loss {unrealized_pnl:.6f} exceeds limit")

    return RiskDecision(True, "risk: ok")


def _has_rebound(recent_closes: list[float], rebound_buy_pct: float) -> bool:
    if len(recent_closes) < 8:
        return False
    window = recent_closes[-8:]
    low = min(window)
    current = window[-1]
    if low <= 0:
        return False
    last_three_up = window[-1] > window[-2] > window[-3]
    bounced_enough = (current - low) / low >= rebound_buy_pct
    return last_three_up and bounced_enough


def _has_sideways_stabilized(recent_closes: list[float], rebound_buy_pct: float) -> bool:
    if len(recent_closes) < 16:
        return False
    window = recent_closes[-12:]
    previous = recent_closes[-24:-12] if len(recent_closes) >= 24 else recent_closes[:-12]
    high = max(window)
    low = min(window)
    if low <= 0:
        return False
    narrow_enough = (high - low) / low <= max(rebound_buy_pct * 2, 0.003)
    no_new_low = not previous or low >= min(previous) * (1 - rebound_buy_pct)
    return narrow_enough and no_new_low
