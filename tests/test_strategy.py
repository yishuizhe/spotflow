import unittest

from binance_testnet_agent.strategy import GridStrategy, MarketSnapshot, Signal


class GridStrategyTest(unittest.TestCase):
    def test_buy_when_price_below_grid_and_risk_allows(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=98,
            recent_closes=[100] * 20,
            base_balance=0,
            quote_balance=100,
        )

        decision = strategy.decide(snapshot)

        self.assertEqual(decision.signal, Signal.BUY)

    def test_hold_when_position_limit_reached(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=98,
            recent_closes=[100] * 20,
            base_balance=1.0,
            quote_balance=100,
        )

        decision = strategy.decide(snapshot)

        self.assertEqual(decision.signal, Signal.HOLD)
        self.assertIn("max position", decision.reason)

    def test_sell_when_lot_target_reached(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=102,
            recent_closes=[100] * 20,
            base_balance=0.5,
            quote_balance=100,
        )

        decision = strategy.decide(
            snapshot,
            open_lots=[
                {
                    "id": "lot-1",
                    "remaining_quantity": 0.1,
                    "target_price": 101,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.SELL)
        self.assertEqual(decision.lot_id, "lot-1")

    def test_hold_when_no_lot_target_even_if_price_is_high(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=102,
            recent_closes=[100] * 20,
            base_balance=0.5,
            quote_balance=100,
        )

        decision = strategy.decide(snapshot)

        self.assertEqual(decision.signal, Signal.HOLD)

    def test_starter_buy_when_no_open_lots_near_reference(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=100.1,
            recent_closes=[100] * 20,
            base_balance=0,
            quote_balance=100,
        )

        decision = strategy.decide(snapshot)

        self.assertEqual(decision.signal, Signal.BUY)
        self.assertEqual(decision.level, "starter")

    def test_downtrend_add_on_when_price_moves_below_existing_lot(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=500)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=99.0,
            recent_closes=[100] * 20,
            base_balance=0.2,
            quote_balance=500,
        )

        decision = strategy.decide(
            snapshot,
            state={"last_buy_level": 2},
            open_lots=[
                {
                    "id": "lot-1",
                    "level": "buy-2",
                    "buy_price": 99.5,
                    "remaining_quantity": 0.1,
                    "target_price": 100.5,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.BUY)
        self.assertTrue(decision.level.startswith("buy-"))

    def test_downtrend_add_on_after_starter_lot_uses_cost_not_reference(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=5.5, max_position_quote=60)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=99.4,
            recent_closes=[99.4] * 20,
            base_balance=0.5,
            quote_balance=60,
        )

        decision = strategy.decide(
            snapshot,
            open_lots=[
                {
                    "id": "lot-1",
                    "level": "starter",
                    "buy_price": 100,
                    "remaining_quantity": 0.5,
                    "target_price": 101,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.BUY)
        self.assertEqual(decision.level, "buy-2-add-2")
        self.assertAlmostEqual(decision.order_quote_size, 6.6)

    def test_defensive_add_on_step_waits_for_deeper_drop(self) -> None:
        strategy = GridStrategy(
            grid_step_pct=0.01,
            take_profit_pct=0.01,
            order_quote_size=5.5,
            max_position_quote=60,
            add_on_step_pct=0.005,
        )
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=99.6,
            recent_closes=[100.8] * 20,
            base_balance=0.5,
            quote_balance=60,
        )

        decision = strategy.decide(
            snapshot,
            open_lots=[
                {
                    "id": "lot-1",
                    "level": "buy-3",
                    "buy_price": 100,
                    "remaining_quantity": 0.5,
                    "target_price": 101,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.HOLD)
        self.assertIn("waiting for deeper add-on", decision.reason)

    def test_sell_uses_effective_target_price_when_defensive_target_is_lower(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=100.4,
            recent_closes=[100] * 20,
            base_balance=0.5,
            quote_balance=100,
        )

        decision = strategy.decide(
            snapshot,
            open_lots=[
                {
                    "id": "lot-1",
                    "remaining_quantity": 0.1,
                    "target_price": 100.8,
                    "effective_target_price": 100.35,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.SELL)
        self.assertEqual(decision.lot_id, "lot-1")

    def test_manual_lot_does_not_auto_sell_at_target(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=102,
            recent_closes=[100] * 20,
            base_balance=0.1,
            quote_balance=100,
        )

        decision = strategy.decide(
            snapshot,
            open_lots=[
                {
                    "id": "manual-1",
                    "level": "manual-entry",
                    "remaining_quantity": 0.1,
                    "target_price": 101,
                    "auto_sell": False,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.HOLD)

    def test_account_base_balance_counts_toward_position_limit(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=25, max_position_quote=100)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=98,
            recent_closes=[100] * 20,
            base_balance=2.0,
            quote_balance=100,
        )

        decision = strategy.decide(
            snapshot,
            open_lots=[
                {
                    "id": "lot-1",
                    "level": "buy-1",
                    "buy_price": 99,
                    "remaining_quantity": 0.1,
                    "target_price": 100,
                }
            ],
        )

        self.assertEqual(decision.signal, Signal.HOLD)
        self.assertIn("max position", decision.reason)

    def test_small_cap_grid_uses_tighter_weighted_levels(self) -> None:
        strategy = GridStrategy(grid_step_pct=0.01, take_profit_pct=0.01, order_quote_size=5.5, max_position_quote=50)
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=98.7,
            recent_closes=[100] * 20,
            base_balance=0,
            quote_balance=50,
        )

        decision = strategy.decide(snapshot)

        self.assertEqual(decision.signal, Signal.BUY)
        self.assertEqual(decision.level, "buy-4")
        self.assertAlmostEqual(decision.order_quote_size, 12.1)


if __name__ == "__main__":
    unittest.main()
