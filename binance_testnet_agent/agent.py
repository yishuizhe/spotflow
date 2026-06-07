from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from .binance_client import BinanceAPIError, BinanceSpotClient
from .config import AgentConfig
from .defensive import DefensiveMode, enrich_lots_with_defensive_targets, evaluate_defensive_mode
from .defensive_scalp import DefensiveScalpStrategy, is_scalp_lot
from .ledger import PositionLedger
from .portfolio import metrics_asdict, portfolio_metrics
from .risk import evaluate_buy_risk
from .sizing import PositionSizing, position_sizing
from .strategy import GridStrategy, MarketSnapshot, Signal, StrategyDecision
from .swing import SwingStrategy, split_lots
from .trend_guard import TrendGuard, TrendState


class TradingAgent:
    def __init__(self, config: AgentConfig, client: BinanceSpotClient | None = None) -> None:
        self.config = config
        self.client = client or BinanceSpotClient(config.base_url, config.api_key, config.api_secret)
        self.state_path = Path(f"data/grid_state_{config.symbol}.json")
        self.trades_path = Path(f"data/trades_{config.symbol}.jsonl")
        self.control_path = Path("data/control.json")
        self.ledger = PositionLedger(Path(f"data/lots_{config.symbol}.json"))

    def health(self) -> dict[str, Any]:
        return {
            "base_url": self.config.base_url,
            "ping": self.client.ping(),
            "server_time": self.client.server_time(),
            "symbol": self.config.symbol,
            "price": self.client.ticker_price(self.config.symbol),
        }

    def account_summary(self) -> dict[str, Any]:
        account = self.client.account()
        balances = {
            item["asset"]: {
                "free": item["free"],
                "locked": item["locked"],
            }
            for item in account.get("balances", [])
            if float(item["free"]) or float(item["locked"])
        }
        return {"accountType": account.get("accountType"), "balances": balances}

    def once(self) -> dict[str, Any]:
        self._reload_config()
        state = self._load_state()
        snapshot = self._snapshot()
        sizing = self._position_sizing(snapshot)
        open_lots = self.ledger.open_lots()
        grid_lots, swing_lots = split_lots(open_lots)
        scalp_lots = [lot for lot in open_lots if is_scalp_lot(lot)]
        defensive = self._defensive(snapshot, sizing, grid_lots)
        strategy = self._strategy_for_sizing(sizing, defensive)
        grid_decision = strategy.decide(snapshot, state, self._lots_for_strategy(grid_lots))
        trend_state = self._trend_state(snapshot, sizing, grid_lots, swing_lots)
        trend_guard = self._trend_guard()
        grid_decision = trend_guard.apply_to_grid(grid_decision, trend_state)
        swing_decision, swing_band = self._swing_decision(snapshot, swing_lots)
        swing_decision = trend_guard.apply_to_dip(swing_decision, trend_state)
        scalp_decision, scalp_state = self._scalp_decision(snapshot, scalp_lots, defensive.active)
        decision = self._choose_decision(grid_decision, swing_decision, scalp_decision)
        risk = self._risk(snapshot)
        if decision.signal == Signal.BUY and not risk["allow_buy"] and not self._buy_can_bypass_risk(decision, risk, trend_state, scalp_state):
            decision = StrategyDecision(Signal.HOLD, risk["reason"], decision.reference_price, decision.price)
        order_result = self._maybe_execute(snapshot, decision)
        lot_update = self._update_ledger(decision, order_result)
        self._update_state(state, decision, order_result)
        self._record_trade(snapshot, decision, order_result)
        return {
            "snapshot": asdict(snapshot),
            "decision": asdict(decision),
            "execute_trades": self._execute_trades_enabled(),
            "order_result": order_result,
            "lot_update": lot_update,
            "risk": risk,
            "position_sizing": asdict(sizing),
            "defensive_mode": defensive.to_dict(),
            "swing_band": swing_band.to_dict(),
            "trend_guard": trend_state.to_dict(),
            "defensive_scalp": scalp_state.to_dict(),
        }

    def run_forever(self) -> None:
        while True:
            try:
                result = self.once()
                print(json.dumps(self._compact_result(result), sort_keys=True), flush=True)
            except BinanceAPIError as exc:
                print(json.dumps({"error": str(exc)}, indent=2), flush=True)
            time.sleep(self.config.loop_seconds)

    def _compact_result(self, result: dict[str, Any]) -> dict[str, Any]:
        snapshot = result["snapshot"]
        decision = result["decision"]
        metrics = portfolio_metrics(
            Path(f"data/baseline_{snapshot['symbol']}.json"),
            symbol=snapshot["symbol"],
            base_asset=self.config.base_asset,
            quote_asset=self.config.quote_asset,
            price=snapshot["price"],
            base_balance=snapshot["base_balance"],
            quote_balance=snapshot["quote_balance"],
        )
        return {
            **metrics_asdict(metrics),
            "symbol": snapshot["symbol"],
            "price": snapshot["price"],
            "signal": decision["signal"],
            "reason": decision["reason"],
            "reference_price": decision["reference_price"],
            "execute_trades": result["execute_trades"],
            "order_result": result["order_result"],
            "lot_update": result.get("lot_update"),
            "open_lots": len(self.ledger.open_lots()),
            "realized_pnl": self.ledger.realized_pnl(self.config.trading_fee_rate),
            "unrealized_lot_pnl": self.ledger.unrealized_pnl(snapshot["price"]),
            "risk": result.get("risk"),
            "position_sizing": result.get("position_sizing"),
            "defensive_mode": result.get("defensive_mode"),
            "swing_band": result.get("swing_band"),
            "trend_guard": result.get("trend_guard"),
            "defensive_scalp": result.get("defensive_scalp"),
        }

    def _reload_config(self) -> None:
        latest = AgentConfig.from_env()
        if latest.symbol != self.config.symbol:
            return
        if latest.base_url != self.config.base_url or latest.api_key != self.config.api_key or latest.api_secret != self.config.api_secret:
            self.client = BinanceSpotClient(latest.base_url, latest.api_key, latest.api_secret)
        self.config = latest

    def _snapshot(self) -> MarketSnapshot:
        price = self.client.ticker_price(self.config.symbol)
        klines = self.client.klines(self.config.symbol, interval="1m", limit=60)
        closes = [float(item[4]) for item in klines]
        base_balance, quote_balance = self._balances_or_zero()
        return MarketSnapshot(
            symbol=self.config.symbol,
            price=price,
            recent_closes=closes,
            base_balance=base_balance,
            quote_balance=quote_balance,
        )

    def _balances_or_zero(self) -> tuple[float, float]:
        if not self.config.api_key or not self.config.api_secret:
            return 0.0, 0.0
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        base = _balance_total(balances, self.config.base_asset)
        quote = _balance_total(balances, self.config.quote_asset)
        return base, quote

    def _position_sizing(self, snapshot: MarketSnapshot) -> PositionSizing:
        total_value = snapshot.quote_balance + snapshot.base_balance * snapshot.price
        return position_sizing(
            total_value,
            self.config.order_quote_size,
            self.config.max_position_quote,
            self.config.auto_position_sizing,
        )

    def _strategy_for_sizing(self, sizing: PositionSizing, defensive: DefensiveMode | None = None) -> GridStrategy:
        return GridStrategy(
            grid_step_pct=self.config.grid_step_pct,
            take_profit_pct=self.config.take_profit_pct,
            order_quote_size=sizing.order_quote_size,
            max_position_quote=sizing.max_position_quote,
            add_on_step_pct=(defensive.add_on_step_pct if defensive else self.config.defensive_normal_add_on_step_pct),
        )

    def _defensive(
        self,
        snapshot: MarketSnapshot,
        sizing: PositionSizing,
        open_lots: list[dict[str, Any]],
    ) -> DefensiveMode:
        return evaluate_defensive_mode(
            enabled=self.config.defensive_mode,
            price=snapshot.price,
            recent_closes=snapshot.recent_closes,
            open_lots=open_lots,
            max_position_quote=sizing.max_position_quote,
            unrealized_pnl=self.ledger.unrealized_pnl(snapshot.price),
            normal_add_on_step_pct=self.config.defensive_normal_add_on_step_pct,
            defensive_add_on_step_pct=self.config.defensive_add_on_step_pct,
            position_usage_trigger=self.config.defensive_position_usage_trigger,
            floating_loss_trigger_quote=self.config.defensive_floating_loss_quote,
            recent_drawdown_trigger_pct=self.config.defensive_recent_drawdown_pct,
        )

    def _lots_for_strategy(self, open_lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return enrich_lots_with_defensive_targets(
            open_lots,
            enabled=self.config.defensive_mode,
            target_profit_pct=self.config.take_profit_pct,
            trading_fee_rate=self.config.trading_fee_rate,
            aged_days_1=self.config.defensive_aged_lot_days_1,
            aged_profit_pct_1=self.config.defensive_aged_lot_profit_pct_1,
            aged_days_2=self.config.defensive_aged_lot_days_2,
            aged_profit_pct_2=self.config.defensive_aged_lot_profit_pct_2,
        )

    def _swing_decision(
        self,
        snapshot: MarketSnapshot,
        swing_lots: list[dict[str, Any]],
    ) -> tuple[StrategyDecision, Any]:
        klines = self.client.klines(
            self.config.symbol,
            interval=self.config.swing_kline_interval,
            limit=self.config.swing_kline_limit,
        )
        closes = [float(item[4]) for item in klines]
        total_value = snapshot.quote_balance + snapshot.base_balance * snapshot.price
        return SwingStrategy(
            enabled=self.config.swing_strategy,
            allocation_pct=self.config.swing_allocation_pct,
            min_order_quote=self.config.swing_min_order_quote,
            max_order_quote=self.config.swing_max_order_quote,
            add_step_pct=self.config.swing_add_step_pct,
            min_band_pct=self.config.swing_min_band_pct,
            max_band_pct=self.config.swing_max_band_pct,
            trading_fee_rate=self.config.trading_fee_rate,
            manual_center_price=self.config.swing_manual_center_price,
        ).decide(snapshot, closes, swing_lots, total_value)

    def _scalp_decision(
        self,
        snapshot: MarketSnapshot,
        scalp_lots: list[dict[str, Any]],
        defensive_active: bool,
    ) -> tuple[StrategyDecision, Any]:
        total_value = snapshot.quote_balance + snapshot.base_balance * snapshot.price
        return DefensiveScalpStrategy(
            enabled=self.config.defensive_scalp,
            allocation_pct=self.config.defensive_scalp_allocation_pct,
            order_pct=self.config.defensive_scalp_order_pct,
            min_order_quote=self.config.defensive_scalp_min_order_quote,
            max_order_quote=self.config.defensive_scalp_max_order_quote,
            buy_drop_pct=self.config.defensive_scalp_buy_drop_pct,
            take_profit_pct=self.config.defensive_scalp_take_profit_pct,
            add_step_pct=self.config.defensive_scalp_add_step_pct,
            min_range_pct=self.config.defensive_scalp_min_range_pct,
            max_range_pct=self.config.defensive_scalp_max_range_pct,
            trading_fee_rate=self.config.trading_fee_rate,
        ).decide(snapshot, snapshot.recent_closes, scalp_lots, total_value, defensive_active)

    def _trend_guard(self) -> TrendGuard:
        return TrendGuard(
            enabled=self.config.trend_guard,
            normal_pool_pct=self.config.trend_normal_pool_pct,
            dip_pool_pct=self.config.trend_dip_pool_pct,
            dip_order_quote=self.config.trend_dip_order_quote,
            rebound_pct=self.config.trend_rebound_pct,
            interval_minutes=_interval_minutes(self.config.trend_kline_interval),
        )

    def _trend_state(
        self,
        snapshot: MarketSnapshot,
        sizing: PositionSizing,
        grid_lots: list[dict[str, Any]],
        swing_lots: list[dict[str, Any]],
    ) -> TrendState:
        klines = self.client.klines(
            self.config.symbol,
            interval=self.config.trend_kline_interval,
            limit=self.config.trend_kline_limit,
        )
        closes = [float(item[4]) for item in klines]
        return self._trend_guard().evaluate(
            snapshot.price,
            closes,
            sizing.max_position_quote,
            _position_quote(grid_lots, snapshot.price),
            _position_quote(swing_lots, snapshot.price),
        )

    @staticmethod
    def _choose_decision(
        grid_decision: StrategyDecision,
        swing_decision: StrategyDecision,
        scalp_decision: StrategyDecision,
    ) -> StrategyDecision:
        if scalp_decision.signal == Signal.SELL:
            return scalp_decision
        if swing_decision.signal == Signal.SELL:
            return swing_decision
        if grid_decision.signal == Signal.SELL:
            return grid_decision
        if swing_decision.signal == Signal.BUY:
            return swing_decision
        if scalp_decision.signal == Signal.BUY:
            return scalp_decision
        return grid_decision

    @staticmethod
    def _buy_can_bypass_risk(
        decision: StrategyDecision,
        risk: dict[str, Any],
        trend_state: TrendState | None = None,
        scalp_state: Any | None = None,
    ) -> bool:
        if decision.signal != Signal.BUY:
            return False
        if str(decision.level).startswith("scalp-entry"):
            return bool(scalp_state and scalp_state.active and scalp_state.range_bound)
        if not str(decision.level).startswith("swing-entry"):
            return False
        if trend_state and trend_state.mode == "dip_probe" and trend_state.rebound:
            return True
        reason = str(risk.get("reason", ""))
        return "floating loss" in reason

    def _maybe_execute(self, snapshot: MarketSnapshot, decision: StrategyDecision) -> dict[str, Any] | None:
        if decision.signal == Signal.HOLD:
            return None

        if not self._execute_trades_enabled():
            return {"dry_run": True, "would": decision.signal.value, "reason": decision.reason}

        try:
            if decision.signal == Signal.BUY:
                filters = self.client.symbol_filters(self.config.symbol)
                quote_order_qty = _round_quote_order_qty(decision.order_quote_size)
                if quote_order_qty < filters.min_notional:
                    return {
                        "skipped": True,
                        "reason": "quote order below minNotional",
                        "quoteOrderQty": str(quote_order_qty),
                        "minNotional": str(filters.min_notional),
                    }
                return self.client.market_buy_quote(self.config.symbol, float(quote_order_qty))

            if decision.signal == Signal.SELL and decision.quantity > 0:
                filters = self.client.symbol_filters(self.config.symbol)
                rounded_qty = self.client.round_quantity(Decimal(str(decision.quantity)), filters)
                if rounded_qty < filters.min_qty:
                    return {"skipped": True, "reason": "rounded quantity below minQty", "quantity": str(rounded_qty)}
                available_base = self._available_base_balance()
                if rounded_qty > Decimal(str(available_base)):
                    return {
                        "skipped": True,
                        "reason": "ledger quantity exceeds available account base balance; sync ledger first",
                        "quantity": str(rounded_qty),
                        "base_balance": snapshot.base_balance,
                        "available_base_balance": available_base,
                    }
                if rounded_qty * Decimal(str(snapshot.price)) < filters.min_notional:
                    return {"skipped": True, "reason": "quantity below minNotional", "quantity": str(rounded_qty)}
                return self.client.market_sell_qty(self.config.symbol, rounded_qty)

            filters = self.client.symbol_filters(self.config.symbol)
            qty = Decimal(str(decision.order_quote_size / snapshot.price))
            rounded_qty = self.client.round_quantity(qty, filters)
            if rounded_qty < filters.min_qty:
                return {"skipped": True, "reason": "rounded quantity below minQty", "quantity": str(rounded_qty)}
            if rounded_qty * Decimal(str(snapshot.price)) < filters.min_notional:
                return {"skipped": True, "reason": "quantity below minNotional", "quantity": str(rounded_qty)}
            return self.client.market_sell_qty(self.config.symbol, rounded_qty)
        except BinanceAPIError as exc:
            return {"error": str(exc), "side": decision.signal.value, "level": decision.level}

    def _available_base_balance(self) -> float:
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        return float(balances.get(self.config.base_asset, {}).get("free", 0) or 0)

    def _update_ledger(
        self,
        decision: StrategyDecision,
        order_result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not order_result or order_result.get("dry_run") or order_result.get("skipped") or order_result.get("error"):
            return None
        if decision.signal == Signal.BUY:
            target_price = decision.target_price or None
            if target_price and decision.price > 0:
                executed_qty = float(order_result.get("executedQty", 0) or 0)
                quote_qty = float(order_result.get("cummulativeQuoteQty", 0) or 0)
                if executed_qty > 0 and quote_qty > 0:
                    fill_price = quote_qty / executed_qty
                    target_markup = max(0.0, target_price / decision.price - 1)
                    target_price = fill_price * (1 + target_markup)
            return self.ledger.add_buy(
                self.config.symbol,
                order_result,
                self.config.take_profit_pct,
                self.config.trading_fee_rate,
                decision.level,
                target_price,
                True,
            )
        if decision.signal == Signal.SELL and decision.lot_id:
            return self.ledger.close_lot(decision.lot_id, order_result, self.config.trading_fee_rate)
        return None

    def _load_state(self) -> dict[str, int]:
        if not self.state_path.exists():
            return {"last_buy_level": 0, "last_sell_level": 0}
        try:
            return json.loads(self.state_path.read_text())
        except json.JSONDecodeError:
            return {"last_buy_level": 0, "last_sell_level": 0}

    def _save_state(self, state: dict[str, int]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    def _update_state(
        self,
        state: dict[str, int],
        decision: StrategyDecision,
        order_result: dict[str, Any] | None,
    ) -> None:
        if decision.signal == Signal.HOLD:
            self._save_state(state)
            return

        executed = bool(order_result) and not order_result.get("dry_run") and not order_result.get("skipped") and not order_result.get("error")
        if not executed:
            self._save_state(state)
            return

        if decision.signal == Signal.BUY:
            state["last_buy_level"] = max(int(state.get("last_buy_level", 0)), self._level_number(decision.level))
        if decision.signal == Signal.SELL:
            state["last_sell_level"] = max(int(state.get("last_sell_level", 0)), self._level_number(decision.level))
        self._save_state(state)

    def _record_trade(
        self,
        snapshot: MarketSnapshot,
        decision: StrategyDecision,
        order_result: dict[str, Any] | None,
    ) -> None:
        if not order_result or order_result.get("dry_run") or order_result.get("skipped") or order_result.get("error"):
            return
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": snapshot.symbol,
            "side": decision.signal.value,
            "level": decision.level,
            "reason": decision.reason,
            "price": snapshot.price,
            "reference_price": decision.reference_price,
            "target_quote_size": decision.order_quote_size,
            "order": order_result,
        }
        with self.trades_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _risk(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        decision = evaluate_buy_risk(
            price=snapshot.price,
            recent_closes=snapshot.recent_closes,
            unrealized_pnl=self.ledger.unrealized_pnl(snapshot.price),
            max_floating_loss_quote=self.config.max_floating_loss_quote,
            rapid_drop_pause_pct=self.config.rapid_drop_pause_pct,
            large_drop_pause_pct=self.config.large_drop_pause_pct,
            rebound_buy_pct=self.config.rebound_buy_pct,
            price_anomaly_pct=self.config.price_anomaly_pct,
        )
        return {
            "allow_buy": decision.allow_buy,
            "reason": decision.reason,
            "rebound": decision.rebound,
        }

    def _execute_trades_enabled(self) -> bool:
        if not self.control_path.exists():
            return self.config.execute_trades
        try:
            payload = json.loads(self.control_path.read_text())
        except json.JSONDecodeError:
            return self.config.execute_trades
        return bool(payload.get("execute_trades", self.config.execute_trades))

    @staticmethod
    def _level_number(level: str) -> int:
        try:
            return int(level.split("-", 1)[1])
        except (IndexError, ValueError):
            return 0


def _balance_total(balances: dict[str, dict[str, Any]], asset: str) -> float:
    item = balances.get(asset, {})
    return float(item.get("free", 0) or 0) + float(item.get("locked", 0) or 0)


def _round_quote_order_qty(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _position_quote(lots: list[dict[str, Any]], price: float) -> float:
    return sum(float(lot.get("remaining_quantity", 0) or 0) * price for lot in lots)


def _interval_minutes(interval: str) -> int:
    raw = interval.strip().lower()
    if raw.endswith("m"):
        return max(1, int(raw[:-1] or "1"))
    if raw.endswith("h"):
        return max(1, int(raw[:-1] or "1") * 60)
    if raw.endswith("d"):
        return max(1, int(raw[:-1] or "1") * 24 * 60)
    return 60
