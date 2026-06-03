import unittest

from binance_testnet_agent.sizing import position_sizing


class PositionSizingTest(unittest.TestCase):
    def test_small_account_keeps_current_unit_size(self) -> None:
        sizing = position_sizing(51, 5.5, 47, True)

        self.assertEqual(sizing.tier, "small:<80")
        self.assertEqual(sizing.order_quote_size, 5.5)
        self.assertAlmostEqual(sizing.max_position_quote, 46.92)

    def test_growth_account_increases_new_order_size(self) -> None:
        sizing = position_sizing(120, 5.5, 47, True)

        self.assertEqual(sizing.tier, "growth:80-200")
        self.assertAlmostEqual(sizing.order_quote_size, 8.4)
        self.assertAlmostEqual(sizing.max_position_quote, 86.4)

    def test_can_disable_auto_sizing(self) -> None:
        sizing = position_sizing(120, 5.5, 47, False)

        self.assertEqual(sizing.tier, "fixed")
        self.assertEqual(sizing.order_quote_size, 5.5)
        self.assertEqual(sizing.max_position_quote, 47)


if __name__ == "__main__":
    unittest.main()
