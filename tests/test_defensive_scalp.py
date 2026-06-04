import unittest

from binance_testnet_agent.defensive_scalp import DefensiveScalpStrategy
from binance_testnet_agent.strategy import MarketSnapshot, Signal


class DefensiveScalpTest(unittest.TestCase):
    def strategy(self) -> DefensiveScalpStrategy:
        return DefensiveScalpStrategy(
            enabled=True,
            allocation_pct=0.08,
            order_pct=0.018,
            min_order_quote=6,
            max_order_quote=10,
            buy_drop_pct=0.0025,
            take_profit_pct=0.0035,
            add_step_pct=0.003,
            min_range_pct=0.004,
            max_range_pct=0.018,
            trading_fee_rate=0.001,
        )

    def test_buys_lower_edge_in_defensive_range(self) -> None:
        closes = [64000, 64200, 63800, 64150, 63750, 64050, 63860, 64020]
        snapshot = MarketSnapshot("BTCUSDT", 63810, closes, 0, 100)

        decision, state = self.strategy().decide(snapshot, closes, [], 400, True)

        self.assertTrue(state.range_bound)
        self.assertEqual(decision.signal, Signal.BUY)
        self.assertTrue(decision.level.startswith("scalp-entry"))
        self.assertAlmostEqual(decision.order_quote_size, 7.2)

    def test_sells_when_target_reached(self) -> None:
        closes = [64000, 64200, 63800, 64150, 63750, 64050, 63860, 64020]
        snapshot = MarketSnapshot("BTCUSDT", 64250, closes, 0.001, 100)
        lots = [{"id": "scalp-1", "level": "scalp-entry-1", "status": "open", "remaining_quantity": 0.001, "target_price": 64200}]

        decision, _state = self.strategy().decide(snapshot, closes, lots, 400, True)

        self.assertEqual(decision.signal, Signal.SELL)
        self.assertEqual(decision.lot_id, "scalp-1")


if __name__ == "__main__":
    unittest.main()
