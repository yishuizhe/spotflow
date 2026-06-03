from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class PortfolioMetrics:
    symbol: str
    base_asset: str
    quote_asset: str
    price: float
    base_balance: float
    quote_balance: float
    value_quote: float
    baseline_value_quote: float
    pnl_quote: float
    pnl_pct: float


def load_or_create_baseline(
    path: Path,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    price: float,
    base_balance: float,
    quote_balance: float,
) -> float:
    value = quote_balance + base_balance * price
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        payload = json.loads(path.read_text())
        return float(payload["baseline_value_quote"])

    payload = {
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "baseline_price": price,
        "baseline_base_balance": base_balance,
        "baseline_quote_balance": quote_balance,
        "baseline_value_quote": value,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return value


def reset_baseline(
    path: Path,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    price: float,
    base_balance: float,
    quote_balance: float,
    note: str = "manual calibration",
) -> dict[str, float | str]:
    value = quote_balance + base_balance * price
    previous_value = None
    if path.exists():
        try:
            previous_value = float(json.loads(path.read_text()).get("baseline_value_quote", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            previous_value = None

    payload: dict[str, float | str] = {
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "baseline_price": price,
        "baseline_base_balance": base_balance,
        "baseline_quote_balance": quote_balance,
        "baseline_value_quote": value,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }
    if previous_value is not None:
        payload["previous_baseline_value_quote"] = previous_value
        payload["baseline_delta_quote"] = value - previous_value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def portfolio_metrics(
    baseline_path: Path,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    price: float,
    base_balance: float,
    quote_balance: float,
) -> PortfolioMetrics:
    value = quote_balance + base_balance * price
    baseline = load_or_create_baseline(
        baseline_path,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        price=price,
        base_balance=base_balance,
        quote_balance=quote_balance,
    )
    pnl = value - baseline
    pnl_pct = (pnl / baseline * 100) if baseline else 0.0
    return PortfolioMetrics(
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        price=price,
        base_balance=base_balance,
        quote_balance=quote_balance,
        value_quote=value,
        baseline_value_quote=baseline,
        pnl_quote=pnl,
        pnl_pct=pnl_pct,
    )


def metrics_asdict(metrics: PortfolioMetrics) -> dict[str, float | str]:
    return asdict(metrics)
