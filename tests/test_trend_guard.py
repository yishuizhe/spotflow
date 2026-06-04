import unittest

from binance_testnet_agent.strategy import Signal, StrategyDecision
from binance_testnet_agent.trend_guard import TrendGuard


class TrendGuardTest(unittest.TestCase):
    def test_downtrend_pauses_ordinary_grid_buy(self) -> None:
        guard = TrendGuard(True, 0.30, 0.10, 3, 0.005, interval_minutes=60)
        closes = [100 - index * 0.05 for index in range(240)]
        state = guard.evaluate(88, closes, 100, grid_position_quote=0, dip_position_quote=0)
        decision = StrategyDecision(Signal.BUY, "grid buy", 90, 88, 20, "buy-1")

        guarded = guard.apply_to_grid(decision, state)

        self.assertEqual(guarded.signal, Signal.HOLD)
        self.assertIn("ordinary grid paused", guarded.reason)

    def test_dip_buy_requires_rebound_confirmation(self) -> None:
        guard = TrendGuard(True, 0.30, 0.10, 3, 0.005, interval_minutes=60)
        closes = [100 - index * 0.05 for index in range(240)]
        state = guard.evaluate(88, closes, 100, grid_position_quote=0, dip_position_quote=0)
        decision = StrategyDecision(Signal.BUY, "swing buy", 90, 88, 15, "swing-entry-1")

        guarded = guard.apply_to_dip(decision, state)

        self.assertEqual(guarded.signal, Signal.HOLD)
        self.assertIn("waiting for rebound", guarded.reason)

    def test_rebound_confirmed_dip_buy_is_reduced_to_small_order(self) -> None:
        guard = TrendGuard(True, 0.30, 0.10, 3, 0.005, interval_minutes=60)
        closes = [100 - index * 0.05 for index in range(230)] + [88, 88.2, 88.7, 89.1]
        state = guard.evaluate(89.1, closes, 100, grid_position_quote=0, dip_position_quote=0)
        decision = StrategyDecision(Signal.BUY, "swing buy", 90, 89.1, 15, "swing-entry-1")

        guarded = guard.apply_to_dip(decision, state)

        self.assertEqual(guarded.signal, Signal.BUY)
        self.assertEqual(guarded.order_quote_size, 3)


if __name__ == "__main__":
    unittest.main()
