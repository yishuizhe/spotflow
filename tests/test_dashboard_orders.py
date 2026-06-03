import unittest

from binance_testnet_agent.dashboard import _order_quote_qty


class DashboardOrderTest(unittest.TestCase):
    def test_order_quote_qty_falls_back_to_price_times_executed_qty(self) -> None:
        order = {
            "status": "FILLED",
            "executedQty": "0.001",
            "cummulativeQuoteQty": "0.00000000",
            "origQuoteOrderQty": "0.00000000",
        }

        self.assertAlmostEqual(_order_quote_qty(order, fallback_price=65000), 65.0)

    def test_order_quote_qty_prefers_positive_cumulative_quote(self) -> None:
        order = {
            "status": "FILLED",
            "executedQty": "0.001",
            "cummulativeQuoteQty": "64.5",
        }

        self.assertAlmostEqual(_order_quote_qty(order, fallback_price=65000), 64.5)


if __name__ == "__main__":
    unittest.main()
