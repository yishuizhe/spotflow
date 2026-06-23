import unittest
from datetime import datetime, timezone

from binance_testnet_agent.defensive import enrich_lot_with_defensive_target, evaluate_defensive_mode


class DefensiveModeTest(unittest.TestCase):
    def test_activates_and_widens_add_on_step_when_position_usage_is_high(self) -> None:
        decision = evaluate_defensive_mode(
            enabled=True,
            price=100,
            recent_closes=[100] * 60,
            open_lots=[{"remaining_quantity": 0.9}],
            max_position_quote=100,
            unrealized_pnl=0,
            normal_add_on_step_pct=0.0025,
            defensive_add_on_step_pct=0.005,
            position_usage_trigger=0.80,
            floating_loss_trigger_quote=2.5,
            recent_drawdown_trigger_pct=0.025,
        )

        self.assertTrue(decision.active)
        self.assertAlmostEqual(decision.add_on_step_pct, 0.005)
        self.assertIn("position usage", decision.reasons[0])

    def test_stays_normal_when_no_trigger_matches(self) -> None:
        decision = evaluate_defensive_mode(
            enabled=True,
            price=100,
            recent_closes=[100] * 60,
            open_lots=[{"remaining_quantity": 0.2}],
            max_position_quote=100,
            unrealized_pnl=-1,
            normal_add_on_step_pct=0.0025,
            defensive_add_on_step_pct=0.005,
            position_usage_trigger=0.80,
            floating_loss_trigger_quote=2.5,
            recent_drawdown_trigger_pct=0.025,
        )

        self.assertFalse(decision.active)
        self.assertAlmostEqual(decision.add_on_step_pct, 0.0025)

    def test_aged_lot_target_is_lowered_but_keeps_fee_breakeven(self) -> None:
        lot = {
            "buy_price": 100,
            "target_price": 100.8,
            "opened_at": "2026-05-10T00:00:00Z",
        }

        enriched = enrich_lot_with_defensive_target(
            lot,
            enabled=True,
            target_profit_pct=0.006,
            trading_fee_rate=0.001,
            aged_days_1=7,
            aged_profit_pct_1=0.0035,
            aged_days_2=14,
            aged_profit_pct_2=0.0015,
            now=datetime(2026, 5, 30, tzinfo=timezone.utc),
        )

        self.assertAlmostEqual(enriched["effective_target_price"], 100.35)
        self.assertTrue(enriched["target_price_adjusted"])
        self.assertEqual(enriched["target_note"], "aged-14d")

    def test_manual_lot_custom_profit_target_is_never_clamped_by_global_or_aging(self) -> None:
        """人工买入时设置的自定义利润百分比（高于全局默认）必须原样生效，
        不能被全局 take_profit_pct 或老仓降目标逻辑悄悄改成更低的值。"""
        lot = {
            "level": "manual-entry",
            "buy_price": 100,
            # 用户手动设置的目标利润 5%，远高于全局默认的 0.6%。
            "target_price": 105.2,
            "opened_at": "2026-05-01T00:00:00Z",
        }

        enriched = enrich_lot_with_defensive_target(
            lot,
            enabled=True,
            target_profit_pct=0.006,
            trading_fee_rate=0.001,
            aged_days_1=7,
            aged_profit_pct_1=0.0035,
            aged_days_2=14,
            aged_profit_pct_2=0.0015,
            now=datetime(2026, 5, 30, tzinfo=timezone.utc),
        )

        self.assertAlmostEqual(enriched["effective_target_price"], 105.2)
        self.assertFalse(enriched["target_price_adjusted"])
        self.assertEqual(enriched["target_note"], "manual")


if __name__ == "__main__":
    unittest.main()
