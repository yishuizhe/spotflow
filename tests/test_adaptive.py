import unittest

from binance_testnet_agent.adaptive import (
    allocate_decision,
    capital_plan,
    identify_market_regime,
    layered_risk,
)
from binance_testnet_agent.strategy import Signal, StrategyDecision


class AdaptiveStrategyTest(unittest.TestCase):
    def test_downtrend_disables_normal_grid_and_keeps_small_dip_pool(self) -> None:
        closes = [100 - index * 0.08 for index in range(240)]
        regime = identify_market_regime(closes[-1], closes)
        plan = capital_plan(100, 70, regime, 0.02, 0.40)

        self.assertEqual(regime.name, "downtrend")
        self.assertEqual(plan.grid_cap, 0)
        self.assertGreater(plan.dip_cap, 0)
        self.assertLess(plan.order_multiplier, 0.5)

    def test_drawdown_and_high_usage_shrink_new_order(self) -> None:
        risk = layered_risk(
            account_drawdown_pct=0.07,
            daily_loss_quote=-2,
            max_daily_loss_quote=10,
            position_usage_pct=0.88,
            volatility_pct=0.02,
            price_break_pct=0.01,
        )

        self.assertTrue(risk.allow_buy)
        self.assertLessEqual(risk.order_multiplier, 0.25)

    def test_capital_allocator_blocks_strategy_without_remaining_pool(self) -> None:
        closes = [100 + (index % 5) * 0.05 for index in range(240)]
        regime = identify_market_regime(closes[-1], closes)
        plan = capital_plan(100, 70, regime, 0, 0.2)
        risk = layered_risk(
            account_drawdown_pct=0,
            daily_loss_quote=0,
            max_daily_loss_quote=10,
            position_usage_pct=0.2,
            volatility_pct=0.01,
            price_break_pct=0,
        )
        decision = StrategyDecision(Signal.BUY, "grid buy", 100, 99, 10, "buy-1")

        guarded = allocate_decision(
            decision,
            plan,
            risk,
            {"grid": plan.grid_cap, "swing": 0, "scalp": 0, "dip": 0},
            5,
        )

        self.assertEqual(guarded.signal, Signal.HOLD)

    def test_daily_loss_triggers_portfolio_pause(self) -> None:
        risk = layered_risk(
            account_drawdown_pct=0.02,
            daily_loss_quote=-10,
            max_daily_loss_quote=10,
            position_usage_pct=0.3,
            volatility_pct=0.01,
            price_break_pct=0,
        )

        self.assertFalse(risk.allow_buy)
        self.assertTrue(risk.emergency_pause)
        self.assertEqual(risk.limited_strategies, ())

    def test_daily_loss_pause_blocks_scalp_and_dip_with_no_exception(self) -> None:
        risk = layered_risk(
            account_drawdown_pct=0.02,
            daily_loss_quote=-10,
            max_daily_loss_quote=10,
            position_usage_pct=0.3,
            volatility_pct=0.01,
            price_break_pct=0,
        )
        plan = capital_plan(100, 70, identify_market_regime(100, [100] * 240), 0.02, 0.3)
        decision = StrategyDecision(Signal.BUY, "scalp entry", 100, 99, 10, "scalp-entry")

        guarded = allocate_decision(
            decision, plan, risk, {"scalp": 0}, 5,
        )

        self.assertEqual(guarded.signal, Signal.HOLD)

    def test_drawdown_pause_keeps_small_pool_for_scalp_and_dip(self) -> None:
        risk = layered_risk(
            account_drawdown_pct=0.12,
            daily_loss_quote=-2,
            max_daily_loss_quote=10,
            position_usage_pct=0.9,
            volatility_pct=0.02,
            price_break_pct=0.01,
        )

        self.assertFalse(risk.allow_buy)
        self.assertTrue(risk.emergency_pause)
        self.assertEqual(set(risk.limited_strategies), {"scalp", "dip"})

        plan = capital_plan(100, 70, identify_market_regime(100, [100] * 240), 0.12, 0.9)
        scalp_decision = StrategyDecision(Signal.BUY, "scalp entry", 100, 99, 10, "scalp-entry")
        scalp_guarded = allocate_decision(
            scalp_decision, plan, risk, {"scalp": 0}, 0.5,
        )
        self.assertEqual(scalp_guarded.signal, Signal.BUY)
        self.assertAlmostEqual(scalp_guarded.order_quote_size, 10 * 0.15)

        grid_decision = StrategyDecision(Signal.BUY, "grid buy", 100, 99, 10, "buy-1")
        grid_guarded = allocate_decision(
            grid_decision, plan, risk, {"grid": 0}, 0.5,
        )
        self.assertEqual(grid_guarded.signal, Signal.HOLD)
