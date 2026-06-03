import unittest

from binance_testnet_agent.risk import evaluate_buy_risk


class RiskDecisionTest(unittest.TestCase):
    def test_large_drop_blocks_while_not_stabilized(self) -> None:
        closes = [100] * 40 + [99, 98, 97, 96, 95, 94]

        decision = evaluate_buy_risk(
            price=94,
            recent_closes=closes,
            unrealized_pnl=-1,
            max_floating_loss_quote=5,
            rapid_drop_pause_pct=0.08,
            large_drop_pause_pct=0.02,
            rebound_buy_pct=0.0015,
            price_anomaly_pct=0.02,
        )

        self.assertFalse(decision.allow_buy)
        self.assertIn("large drop", decision.reason)

    def test_large_drop_allows_sideways_stabilization(self) -> None:
        closes = [100] * 36 + [97, 95] + [94.95, 95.02, 94.98, 95.01, 94.99, 95.03, 94.97, 95.0, 95.02, 94.98, 95.01, 95.0]

        decision = evaluate_buy_risk(
            price=95.0,
            recent_closes=closes,
            unrealized_pnl=-1,
            max_floating_loss_quote=5,
            rapid_drop_pause_pct=0.08,
            large_drop_pause_pct=0.02,
            rebound_buy_pct=0.0015,
            price_anomaly_pct=0.02,
        )

        self.assertTrue(decision.allow_buy)
        self.assertIn("sideways", decision.reason)


if __name__ == "__main__":
    unittest.main()
