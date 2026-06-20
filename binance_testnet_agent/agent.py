from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from .adaptive import (
    allocate_decision,
    capital_plan,
    dynamic_profit_pct,
    identify_market_regime,
    layered_risk,
    strategy_name,
)
from .audit import DecisionAudit
from .binance_client import BinanceAPIError, BinanceSpotClient
from .config import AgentConfig
from .defensive import DefensiveMode, enrich_lots_with_defensive_targets, evaluate_defensive_mode
from .defensive_scalp import DefensiveScalpStrategy, is_scalp_lot
from .ledger import PositionLedger
from .merge_sell import merge_sell_ready_lots
from .portfolio import metrics_asdict, portfolio_metrics
from .risk import evaluate_buy_risk
from .sizing import PositionSizing, position_sizing
from .strategy import GridStrategy, MarketSnapshot, Signal, StrategyDecision
from .swing import SwingStrategy, split_lots
from .trade_lock import trading_lock
from .trend_guard import TrendGuard, TrendState


class TradingAgent:
    def __init__(self, config: AgentConfig, client: BinanceSpotClient | None = None) -> None:
        self.config = config
        self.client = client or BinanceSpotClient(config.base_url, config.api_key, config.api_secret)
        self.state_path = Path(f"data/grid_state_{config.symbol}.json")
        self.trades_path = Path(f"data/trades_{config.symbol}.jsonl")
        self.control_path = Path("data/control.json")
        self.risk_state_path = Path(f"data/risk_state_{config.symbol}.json")
        self.ledger = PositionLedger(Path(f"data/lots_{config.symbol}.json"))
        self.audit = DecisionAudit(Path(f"data/decision_audit_{config.symbol}.json"))

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
        baseline_decision = decision
        regime = self._market_regime(snapshot)
        total_value = snapshot.quote_balance + snapshot.base_balance * snapshot.price
        risk_state = self._portfolio_risk_state(total_value)
        position_quote = snapshot.base_balance * snapshot.price
        usage = position_quote / max(sizing.max_position_quote, 0.00000001)
        plan = capital_plan(total_value, sizing.max_position_quote, regime, risk_state["drawdown_pct"], usage)
        price_break = max(0.0, 1 - snapshot.price / regime.ma_slow) if regime.ma_slow > 0 else 0.0
        portfolio_risk = layered_risk(
            account_drawdown_pct=risk_state["drawdown_pct"],
            daily_loss_quote=risk_state["daily_pnl_quote"],
            max_daily_loss_quote=self.config.max_daily_loss_quote,
            position_usage_pct=usage,
            volatility_pct=regime.volatility_pct,
            price_break_pct=price_break,
        )
        adaptive_decision = allocate_decision(
            decision,
            plan,
            portfolio_risk,
            self._strategy_positions(open_lots, snapshot.price),
            float(self.client.symbol_filters(self.config.symbol).min_notional),
        )
        adaptive_decision = self._dynamic_buy_target(adaptive_decision, regime)
        if self.config.adaptive_strategy_enabled:
            decision = adaptive_decision
        trailing_decision = self._trailing_exit(snapshot, regime, open_lots, decision)
        if trailing_decision is not None:
            decision = trailing_decision
        decision, order_result, lot_update = self._execute_with_final_guard(snapshot, decision)
        self._consolidate_dust()
        merge_result = self._maybe_merge_sell_dust(decision, order_result)
        self._record_merge_sell_trade(snapshot, merge_result)
        self._update_state(state, decision, order_result)
        self._record_trade(snapshot, decision, order_result)
        self.audit.record(
            {
                "price": snapshot.price,
                "market_regime": regime.to_dict(),
                "capital_plan": plan.to_dict(),
                "portfolio_risk": portfolio_risk.to_dict(),
                "baseline_decision": asdict(baseline_decision),
                "adaptive_decision": asdict(adaptive_decision),
                "executed_decision": asdict(decision),
                "shadow_mode": self.config.shadow_mode,
                "adaptive_enabled": self.config.adaptive_strategy_enabled,
                "order_result": order_result,
            }
        )
        return {
            "snapshot": asdict(snapshot),
            "decision": asdict(decision),
            "execute_trades": self._execute_trades_enabled(),
            "order_result": order_result,
            "lot_update": lot_update,
            "merge_result": merge_result,
            "risk": risk,
            "position_sizing": asdict(sizing),
            "defensive_mode": defensive.to_dict(),
            "swing_band": swing_band.to_dict(),
            "trend_guard": trend_state.to_dict(),
            "defensive_scalp": scalp_state.to_dict(),
            "market_regime": regime.to_dict(),
            "capital_plan": plan.to_dict(),
            "portfolio_risk": portfolio_risk.to_dict(),
            "shadow_decision": asdict(adaptive_decision) if self.config.shadow_mode else None,
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
            "merge_result": result.get("merge_result"),
            "open_lots": len(self.ledger.open_lots()),
            "realized_pnl": self.ledger.realized_pnl(self.config.trading_fee_rate),
            "unrealized_lot_pnl": self.ledger.unrealized_pnl(snapshot["price"]),
            "risk": result.get("risk"),
            "position_sizing": result.get("position_sizing"),
            "defensive_mode": result.get("defensive_mode"),
            "swing_band": result.get("swing_band"),
            "trend_guard": result.get("trend_guard"),
            "defensive_scalp": result.get("defensive_scalp"),
            "market_regime": result.get("market_regime"),
            "capital_plan": result.get("capital_plan"),
            "portfolio_risk": result.get("portfolio_risk"),
            "shadow_decision": result.get("shadow_decision"),
        }

    def _market_regime(self, snapshot: MarketSnapshot) -> Any:
        klines = self.client.klines(
            self.config.symbol,
            interval=self.config.trend_kline_interval,
            limit=self.config.trend_kline_limit,
        )
        return identify_market_regime(snapshot.price, [float(item[4]) for item in klines])

    def _portfolio_risk_state(self, total_value: float) -> dict[str, float]:
        today = datetime.now(timezone.utc).date().isoformat()
        state = {"peak_value": total_value, "day": today, "day_start_value": total_value}
        if self.risk_state_path.exists():
            try:
                loaded = json.loads(self.risk_state_path.read_text())
                if isinstance(loaded, dict):
                    state.update(loaded)
            except json.JSONDecodeError:
                pass
        state["peak_value"] = max(float(state.get("peak_value", total_value) or total_value), total_value)
        if state.get("day") != today:
            state["day"] = today
            state["day_start_value"] = total_value
        peak = float(state["peak_value"])
        day_start = float(state.get("day_start_value", total_value) or total_value)
        self.risk_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.risk_state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        return {
            "drawdown_pct": max(0.0, 1 - total_value / peak) if peak > 0 else 0.0,
            "daily_pnl_quote": total_value - day_start,
        }

    @staticmethod
    def _strategy_positions(open_lots: list[dict[str, Any]], price: float) -> dict[str, float]:
        positions = {"grid": 0.0, "swing": 0.0, "scalp": 0.0, "dip": 0.0}
        for lot in open_lots:
            level = str(lot.get("level", ""))
            key = "scalp" if level.startswith("scalp-") else "swing" if level.startswith("swing-") else "grid"
            positions[key] += float(lot.get("remaining_quantity", 0) or 0) * price
        return positions

    def _dynamic_buy_target(self, decision: StrategyDecision, regime: Any) -> StrategyDecision:
        if decision.signal != Signal.BUY or not self.config.dynamic_take_profit:
            return decision
        profit = dynamic_profit_pct(regime, self.config.take_profit_pct)
        target = decision.price * (1 + profit + self.config.trading_fee_rate * 2)
        if strategy_name(decision) == "swing" and decision.target_price > 0:
            target = max(target, decision.target_price)
        return StrategyDecision(
            decision.signal,
            f"{decision.reason}；动态止盈 {profit:.2%}",
            decision.reference_price,
            decision.price,
            decision.order_quote_size,
            decision.level,
            decision.lot_id,
            decision.quantity,
            target,
        )

    def _trailing_exit(
        self,
        snapshot: MarketSnapshot,
        regime: Any,
        open_lots: list[dict[str, Any]],
        current_decision: StrategyDecision,
    ) -> StrategyDecision | None:
        if (
            not self.config.adaptive_strategy_enabled
            or not self.config.dynamic_take_profit
            or regime.name != "uptrend"
        ):
            return None
        triggered: dict[str, Any] | None = None

        def update(lots: list[dict[str, Any]]) -> None:
            nonlocal triggered
            for lot in lots:
                if lot.get("status") != "open" or lot.get("auto_sell", True) is False or lot.get("pending_limit_sell_order_id"):
                    continue
                target = float(lot.get("effective_target_price") or lot.get("target_price") or 0)
                if target <= 0:
                    continue
                peak = max(float(lot.get("trailing_peak_price", 0) or 0), snapshot.price if snapshot.price >= target else 0)
                if peak >= target:
                    lot["trailing_armed"] = True
                    lot["trailing_peak_price"] = peak
                    lot["lifecycle_note"] = f"上涨趋势移动止盈已启动，峰值 {peak:.2f}"
                if lot.get("trailing_armed") and snapshot.price <= peak * (1 - self.config.trailing_profit_pct):
                    triggered = dict(lot)
                    return

        self.ledger.update_lots(update)
        if triggered:
            qty = float(triggered.get("remaining_quantity", 0) or 0)
            return StrategyDecision(
                Signal.SELL,
                "上涨趋势移动止盈回撤触发",
                float(triggered.get("trailing_peak_price", snapshot.price)),
                snapshot.price,
                qty * snapshot.price,
                "trailing-target",
                str(triggered.get("id")),
                qty,
                float(triggered.get("target_price", 0) or 0),
            )
        if current_decision.signal == Signal.SELL:
            return StrategyDecision(
                Signal.HOLD,
                "上涨趋势已达到目标，移动止盈等待回撤",
                current_decision.reference_price,
                current_decision.price,
            )
        return None

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

    def _sell_gate(self, decision: StrategyDecision) -> StrategyDecision:
        if decision.signal != Signal.SELL or not decision.lot_id:
            return decision
        lot = next((item for item in self.ledger.open_lots() if str(item.get("id")) == decision.lot_id), None)
        if not lot:
            return self._blocked_sell(decision, "sell gate blocked: lot is no longer open")
        if lot.get("auto_sell", True) is False:
            return self._blocked_sell(decision, "sell gate blocked: auto sell is disabled for this lot")
        if lot.get("pending_limit_sell_order_id"):
            return self._blocked_sell(decision, "sell gate blocked: lot already has a pending limit sell")
        remaining = float(lot.get("remaining_quantity", 0) or 0)
        if remaining <= 0 or abs(remaining - decision.quantity) > 0.000000001:
            return self._blocked_sell(decision, "sell gate blocked: lot quantity changed; retry with fresh ledger state")

        buy_price = float(lot.get("buy_price", 0) or 0)
        buy_quote = float(lot.get("buy_quote", 0) or 0)
        original_quantity = float(lot.get("quantity", 0) or 0)
        true_unit_cost = max(buy_price, buy_quote / original_quantity if buy_quote > 0 and original_quantity > 0 else 0)
        if true_unit_cost <= 0:
            return self._blocked_sell(decision, "sell gate blocked: lot cost basis is missing")
        fee_rate = self.config.trading_fee_rate
        breakeven_price = true_unit_cost * (1 + fee_rate) / max(1 - fee_rate, 0.00000001)
        slippage_buffer = max(0.0005, fee_rate * 0.5)
        protected_breakeven = breakeven_price * (1 + slippage_buffer)
        required_price = max(protected_breakeven, self._strategy_sell_floor(lot))
        if decision.price < required_price:
            return self._blocked_sell(
                decision,
                f"sell gate blocked: price {decision.price:.2f} below required {required_price:.2f}",
            )
        return decision

    def _strategy_sell_floor(self, lot: dict[str, Any]) -> float:
        level = str(lot.get("level", ""))
        buy_price = float(lot.get("buy_price", 0) or 0)
        fee_markup = self.config.trading_fee_rate * 2
        if level.startswith("manual-"):
            return float(lot.get("target_price", 0) or 0)
        if level.startswith("scalp-"):
            return max(
                float(lot.get("target_price", 0) or 0),
                buy_price * (1 + self.config.defensive_scalp_take_profit_pct + fee_markup),
            )
        if level.startswith("swing-"):
            return max(
                float(lot.get("target_price", 0) or 0),
                buy_price * (1 + self.config.swing_min_band_pct + fee_markup),
            )
        enriched = self._lots_for_strategy([lot])[0]
        effective_profit = float(enriched.get("target_profit_pct_effective", self.config.take_profit_pct) or 0)
        return buy_price * (1 + effective_profit + fee_markup)

    @staticmethod
    def _blocked_sell(decision: StrategyDecision, reason: str) -> StrategyDecision:
        return StrategyDecision(Signal.HOLD, reason, decision.reference_price, decision.price)

    def _consolidate_dust(self) -> dict[str, Any] | None:
        """每个 tick 把卖不掉的碎渣批次并入碎渣账户。

        无论本轮是否下单都执行，把任何低于最小下单数量的零头（含手动卖出留下的）
        收进碎渣账户。任何异常都被吞掉，不影响主交易循环。
        """
        try:
            min_qty = float(self.client.symbol_filters(self.config.symbol).min_qty)
            with trading_lock():
                return self.ledger.consolidate_dust(
                    min_qty,
                    self.config.take_profit_pct,
                    self.config.trading_fee_rate,
                )
        except BinanceAPIError:
            return None

    def _maybe_merge_sell_dust(
        self, decision: StrategyDecision, order_result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """空闲 tick 时自动把达标的碎屑批次合并卖出，避免它们因低于最小下单额永远卖不掉。

        只在交易开关开启、且本轮没有真正成交一笔卖单时触发，避免重复卖出或抢占余额。
        如果本轮选中的卖出批次因低于最小下单额/数量被跳过（order_result 带 skipped），
        说明那笔单子根本没有真正下出去，这里仍然要继续尝试合并卖渣，否则那个永远卖不掉的
        碎渣批次会一直占着"目标价最低"的位置，把其它已达标的正常批次永久卡在后面排不上队。
        任何异常都被吞掉，绝不影响主交易循环。
        """
        if decision.signal == Signal.SELL and order_result and not order_result.get("skipped"):
            return None
        if not self._execute_trades_enabled():
            return None
        try:
            with trading_lock():
                return merge_sell_ready_lots(self.client, self.ledger, self.config, require_dust=True)
        except BinanceAPIError as exc:
            return {"merged": False, "error": str(exc)}

    def _execute_with_final_guard(
        self,
        snapshot: MarketSnapshot,
        decision: StrategyDecision,
    ) -> tuple[StrategyDecision, dict[str, Any] | None, dict[str, Any] | None]:
        if decision.signal == Signal.HOLD:
            return decision, None, None
        with trading_lock():
            guarded = self._sell_gate(decision) if decision.signal == Signal.SELL else decision
            order_result = self._maybe_execute(snapshot, guarded)
            lot_update = self._update_ledger(guarded, order_result)
            return guarded, order_result, lot_update

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
            message = str(exc)
            if "-2010" in message or "insufficient balance" in message.lower():
                if decision.signal == Signal.SELL:
                    available = self._available_base_balance()
                    return {"error": f"币安拒绝卖出：可用 {self.config.base_asset} 余额不足（当前可用 {available:.8f}）。请检查是否有其他卖单锁定了余额，或同步账本后再试。", "side": decision.signal.value, "level": decision.level}
                return {"error": f"币安拒绝下单：余额不足。请检查账户余额。", "side": decision.signal.value, "level": decision.level}
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
                self.config.base_asset,
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

    def _record_merge_sell_trade(
        self,
        snapshot: MarketSnapshot,
        merge_result: dict[str, Any] | None,
    ) -> None:
        """合并卖碎屑（自动 tick 触发）成交后，补一条交易记录。

        merge_sell_ready_lots 只负责下单和更新账本，本身不写交易日志；手动点击仪表盘的
        「合并卖碎屑」按钮那条路径会调用 _record_manual_trade 记一笔，但自动 agent 这边的
        once() 一直没有对应调用，导致自动触发的合并卖出永远不会出现在「最近实盘订单」里。
        """
        if not merge_result or not merge_result.get("merged"):
            return
        order_result = merge_result.get("order")
        if not order_result:
            return
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": snapshot.symbol,
            "side": "MERGE_SELL",
            "level": "merge-sell-dust",
            "reason": "auto agent merged dust lots above minNotional",
            "price": snapshot.price,
            "target_quote_size": merge_result.get("proceeds", 0),
            "order": order_result,
            "lots_closed": merge_result.get("lots_closed", 0),
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
