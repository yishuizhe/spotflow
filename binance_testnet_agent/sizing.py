from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSizing:
    total_value_quote: float
    order_quote_size: float
    max_position_quote: float
    tier: str
    enabled: bool


def position_sizing(
    total_value_quote: float,
    fallback_order_quote_size: float,
    fallback_max_position_quote: float,
    enabled: bool = True,
) -> PositionSizing:
    if not enabled or total_value_quote <= 0:
        return PositionSizing(
            total_value_quote=total_value_quote,
            order_quote_size=fallback_order_quote_size,
            max_position_quote=fallback_max_position_quote,
            tier="fixed",
            enabled=False,
        )

    if total_value_quote < 80:
        return PositionSizing(
            total_value_quote=total_value_quote,
            order_quote_size=5.5,
            max_position_quote=round(total_value_quote * 0.92, 2),
            tier="small:<80",
            enabled=True,
        )
    if total_value_quote < 200:
        return PositionSizing(
            total_value_quote=total_value_quote,
            order_quote_size=round(_clamp(total_value_quote * 0.07, 8, 12), 2),
            max_position_quote=round(total_value_quote * 0.72, 2),
            tier="growth:80-200",
            enabled=True,
        )
    if total_value_quote < 500:
        return PositionSizing(
            total_value_quote=total_value_quote,
            order_quote_size=round(_clamp(total_value_quote * 0.055, 15, 25), 2),
            max_position_quote=round(total_value_quote * 0.65, 2),
            tier="medium:200-500",
            enabled=True,
        )
    return PositionSizing(
        total_value_quote=total_value_quote,
        order_quote_size=round(_clamp(total_value_quote * 0.04, 25, 80), 2),
        max_position_quote=round(total_value_quote * 0.60, 2),
        tier="large:500+",
        enabled=True,
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
