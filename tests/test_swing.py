import unittest

from binance_testnet_agent.strategy import MarketSnapshot, Signal
from binance_testnet_agent.swing import SwingStrategy, split_lots


class SwingStrategyTest(unittest.TestCase):
    def test_dynamic_band_around_recent_center(self) -> None:
        strategy = SwingStrategy(
            enabled=True,
            allocation_pct=0.30,
            min_order_quote=10,
            max_order_quote=15,
            add_step_pct=0.015,
            min_band_pct=0.012,
            max_band_pct=0.025,
        )

        band = strategy.band(73000, [72000, 73000, 74000], [], 100)

        self.assertAlmostEqual(band.center_price, 73000)
        self.assertAlmostEqual(band.buy_price, 73000 * (1 - 0.012))
        self.assertAlmostEqual(band.sell_price, 73000 * (1 + 0.012))
        self.assertAlmostEqual(band.allocation_quote, 30)

    def test_buy_uses_full_swing_budget_when_price_reaches_buy_band(self) -> None:
        strategy = SwingStrategy(
            enabled=True,
            allocation_pct=0.30,
            min_order_quote=10,
            max_order_quote=15,
            add_step_pct=0.015,
            min_band_pct=0.012,
            max_band_pct=0.025,
            manual_center_price=73000,
        )
        snapshot = MarketSnapshot("BTCUSDT", 72000, [72000], 0, 100)

        decision, _band = strategy.decide(snapshot, [73000] * 168, [], 100)

        self.assertEqual(decision.signal, Signal.BUY)
        self.assertEqual(decision.level, "swing-entry-1")
        self.assertAlmostEqual(decision.order_quote_size, 15)
        self.assertGreater(decision.target_price, snapshot.price)

    def test_sell_when_swing_target_reached(self) -> None:
        strategy = SwingStrategy(
            enabled=True,
            allocation_pct=0.30,
            min_order_quote=10,
            max_order_quote=15,
            add_step_pct=0.015,
            min_band_pct=0.012,
            max_band_pct=0.025,
            manual_center_price=73000,
        )
        snapshot = MarketSnapshot("BTCUSDT", 74000, [74000], 0.001, 50)

        decision, _band = strategy.decide(
            snapshot,
            [73000] * 168,
            [{"id": "swing-1", "level": "swing-entry", "status": "open", "remaining_quantity": 0.001, "target_price": 73876}],
            124,
        )

        self.assertEqual(decision.signal, Signal.SELL)
        self.assertEqual(decision.lot_id, "swing-1")

    def test_auto_sell_disabled_swing_lot_does_not_sell(self) -> None:
        strategy = SwingStrategy(
            enabled=True,
            allocation_pct=0.30,
            min_order_quote=10,
            max_order_quote=15,
            add_step_pct=0.015,
            min_band_pct=0.012,
            max_band_pct=0.025,
            manual_center_price=73000,
        )
        snapshot = MarketSnapshot("BTCUSDT", 74000, [74000], 0.001, 50)

        decision, _band = strategy.decide(
            snapshot,
            [73000] * 168,
            [
                {
                    "id": "swing-1",
                    "level": "swing-entry",
                    "status": "open",
                    "remaining_quantity": 0.001,
                    "target_price": 73876,
                    "auto_sell": False,
                }
            ],
            124,
        )

        self.assertEqual(decision.signal, Signal.HOLD)

    def test_split_lots_keeps_grid_and_swing_separate(self) -> None:
        grid, swing = split_lots([
            {"level": "starter"},
            {"level": "swing-entry"},
        ])

        self.assertEqual(len(grid), 1)
        self.assertEqual(len(swing), 1)

    def test_swing_adds_only_after_deeper_drop(self) -> None:
        strategy = SwingStrategy(
            enabled=True,
            allocation_pct=0.30,
            min_order_quote=10,
            max_order_quote=15,
            add_step_pct=0.015,
            min_band_pct=0.012,
            max_band_pct=0.025,
            manual_center_price=73000,
        )
        lot = {"id": "swing-1", "level": "swing-entry-1", "status": "open", "remaining_quantity": 0.0002, "buy_price": 72000, "target_price": 73876}

        hold, _band = strategy.decide(MarketSnapshot("BTCUSDT", 71000, [71000], 0.0002, 100), [73000] * 168, [lot], 100)
        buy, _band = strategy.decide(MarketSnapshot("BTCUSDT", 70800, [70800], 0.0002, 100), [73000] * 168, [lot], 100)

        self.assertEqual(hold.signal, Signal.HOLD)
        self.assertEqual(buy.signal, Signal.BUY)
        self.assertEqual(buy.level, "swing-entry-2")
        self.assertLessEqual(buy.order_quote_size, 15)


if __name__ == "__main__":
    unittest.main()
