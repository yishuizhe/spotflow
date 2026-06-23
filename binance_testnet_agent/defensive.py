from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DefensiveMode:
    enabled: bool
    active: bool
    reasons: list[str]
    add_on_step_pct: float
    normal_add_on_step_pct: float
    position_quote: float
    position_usage_pct: float
    unrealized_pnl: float
    recent_drawdown_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_defensive_mode(
    *,
    enabled: bool,
    price: float,
    recent_closes: list[float],
    open_lots: list[dict[str, Any]],
    max_position_quote: float,
    unrealized_pnl: float,
    normal_add_on_step_pct: float,
    defensive_add_on_step_pct: float,
    position_usage_trigger: float,
    floating_loss_trigger_quote: float,
    recent_drawdown_trigger_pct: float,
) -> DefensiveMode:
    position_quote = sum(float(lot.get("remaining_quantity", 0)) * price for lot in open_lots)
    position_usage_pct = position_quote / max(max_position_quote, 0.00000001)
    recent_drawdown_pct = _recent_drawdown_pct(price, recent_closes)
    reasons: list[str] = []

    if enabled and position_usage_pct >= position_usage_trigger:
        reasons.append(f"position usage {position_usage_pct:.2%}")
    if enabled and unrealized_pnl <= -abs(floating_loss_trigger_quote):
        reasons.append(f"floating loss {unrealized_pnl:.4f}")
    if enabled and recent_drawdown_pct >= recent_drawdown_trigger_pct:
        reasons.append(f"recent drawdown {recent_drawdown_pct:.2%}")

    active = enabled and bool(reasons)
    add_on_step_pct = max(normal_add_on_step_pct, defensive_add_on_step_pct) if active else normal_add_on_step_pct
    return DefensiveMode(
        enabled=enabled,
        active=active,
        reasons=reasons,
        add_on_step_pct=add_on_step_pct,
        normal_add_on_step_pct=normal_add_on_step_pct,
        position_quote=position_quote,
        position_usage_pct=position_usage_pct,
        unrealized_pnl=unrealized_pnl,
        recent_drawdown_pct=recent_drawdown_pct,
    )


def enrich_lots_with_defensive_targets(
    lots: list[dict[str, Any]],
    *,
    enabled: bool,
    target_profit_pct: float,
    trading_fee_rate: float,
    aged_days_1: int,
    aged_profit_pct_1: float,
    aged_days_2: int,
    aged_profit_pct_2: float,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return [
        enrich_lot_with_defensive_target(
            lot,
            enabled=enabled,
            target_profit_pct=target_profit_pct,
            trading_fee_rate=trading_fee_rate,
            aged_days_1=aged_days_1,
            aged_profit_pct_1=aged_profit_pct_1,
            aged_days_2=aged_days_2,
            aged_profit_pct_2=aged_profit_pct_2,
            now=now,
        )
        for lot in lots
    ]


def enrich_lot_with_defensive_target(
    lot: dict[str, Any],
    *,
    enabled: bool,
    target_profit_pct: float,
    trading_fee_rate: float,
    aged_days_1: int,
    aged_profit_pct_1: float,
    aged_days_2: int,
    aged_profit_pct_2: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    enriched = dict(lot)
    base_target = float(enriched.get("target_price") or 0)
    buy_price = float(enriched.get("buy_price") or 0)

    if str(enriched.get("level", "")).startswith("manual-"):
        # 人工买入时设置的自定义利润百分比已经折算进 target_price 里，这里绝不能
        # 再用全局 take_profit_pct 或老仓降目标逻辑去覆盖/钳制它，否则用户自定义的
        # 利润比例就会被静默改成全局默认值（"自定义利润百分比失效"）。
        enriched["age_days"] = _lot_age_days(enriched, now)
        enriched["effective_target_price"] = base_target if base_target > 0 else buy_price
        enriched["target_profit_pct_effective"] = (
            (base_target / buy_price) - 1 if base_target > 0 and buy_price > 0 else 0.0
        )
        enriched["target_note"] = "manual"
        enriched["target_price_adjusted"] = False
        return enriched

    age_days = _lot_age_days(enriched, now)
    effective_profit_pct = target_profit_pct
    target_note = "normal"

    if enabled and age_days is not None:
        if age_days >= aged_days_2:
            effective_profit_pct = min(target_profit_pct, max(0.0, aged_profit_pct_2))
            target_note = f"aged-{aged_days_2}d"
        elif age_days >= aged_days_1:
            effective_profit_pct = min(target_profit_pct, max(0.0, aged_profit_pct_1))
            target_note = f"aged-{aged_days_1}d"

    fee_breakeven_target = buy_price * (1 + trading_fee_rate * 2)
    defensive_target = buy_price * (1 + effective_profit_pct + trading_fee_rate * 2)
    effective_target = max(fee_breakeven_target, defensive_target)
    if base_target > 0:
        effective_target = min(base_target, effective_target)

    enriched["age_days"] = age_days
    enriched["effective_target_price"] = effective_target if buy_price > 0 else base_target
    enriched["target_profit_pct_effective"] = effective_profit_pct
    enriched["target_note"] = target_note
    enriched["target_price_adjusted"] = bool(base_target and effective_target < base_target)
    return enriched


def _recent_drawdown_pct(price: float, recent_closes: list[float]) -> float:
    window = recent_closes[-60:] if recent_closes else []
    if not window:
        return 0.0
    high = max(window)
    if high <= 0:
        return 0.0
    return max(0.0, (high - price) / high)


def _lot_age_days(lot: dict[str, Any], now: datetime | None = None) -> float | None:
    opened_at = lot.get("opened_at")
    if not opened_at:
        return None
    parsed = _parse_utc(str(opened_at))
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - parsed).total_seconds() / 86400)


def _parse_utc(value: str) -> datetime | None:
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None
