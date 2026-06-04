import unittest

from binance_testnet_agent.local_backtest import BacktestConfig, _trend_buy_guard


class BacktestTrendFilterTest(unittest.TestCase):
    def test_trend_filter_is_disabled_by_default(self) -> None:
        result = _trend_buy_guard(
            [100, 99, 98, 97],
            quote_size=20,
            max_position_quote=100,
            current_position_quote=0,
            config=BacktestConfig(),
        )

        self.assertFalse(result["blocked"])
        self.assertEqual(result["quote_size"], 20)

    def test_downtrend_blocks_new_buy_without_rebound(self) -> None:
        prices = [100 - index * 0.05 for index in range(400)]
        result = _trend_buy_guard(
            prices,
            quote_size=20,
            max_position_quote=100,
            current_position_quote=0,
            config=BacktestConfig(trend_filter=True, price_interval_minutes=60),
        )

        self.assertTrue(result["blocked"])

    def test_normal_pool_is_reserved_before_long_window_is_available(self) -> None:
        result = _trend_buy_guard(
            [100, 99, 98, 97],
            quote_size=20,
            max_position_quote=100,
            current_position_quote=40,
            config=BacktestConfig(trend_filter=True),
        )

        self.assertTrue(result["blocked"])

    def test_adaptive_regime_blocks_downtrend_without_rebound(self) -> None:
        prices = [100 - index * 0.05 for index in range(400)]
        result = _trend_buy_guard(
            prices,
            quote_size=20,
            max_position_quote=100,
            current_position_quote=0,
            config=BacktestConfig(trend_filter=True, adaptive_regime=True, price_interval_minutes=60),
        )

        self.assertTrue(result["blocked"])

    def test_adaptive_regime_allows_recovered_market(self) -> None:
        prices = [100 - index * 0.02 for index in range(260)] + [95 + index * 0.04 for index in range(140)]
        result = _trend_buy_guard(
            prices,
            quote_size=20,
            max_position_quote=100,
            current_position_quote=0,
            config=BacktestConfig(trend_filter=True, adaptive_regime=True, price_interval_minutes=60),
        )

        self.assertFalse(result["blocked"])
        self.assertEqual(result["quote_size"], 20)


if __name__ == "__main__":
    unittest.main()
