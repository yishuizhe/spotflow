import tempfile
import unittest
from pathlib import Path

from binance_testnet_agent.agent import TradingAgent, _round_quote_order_qty
from binance_testnet_agent.config import AgentConfig
from binance_testnet_agent.strategy import MarketSnapshot, Signal, StrategyDecision


class AgentOrderTest(unittest.TestCase):
    def test_quote_order_qty_is_rounded_down_to_two_decimals(self) -> None:
        self.assertEqual(str(_round_quote_order_qty(7.289636868518398)), "7.28")

    def test_error_order_is_not_recorded_as_trade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = TradingAgent(AgentConfig(api_key="key", api_secret="secret"))
                order_result = {"error": "Binance HTTP 400", "side": "BUY", "level": "scalp-entry-1"}
                decision = StrategyDecision(Signal.BUY, "defensive scalp lower edge buy", 100, 99, 7.28, "scalp-entry-1")
                snapshot = MarketSnapshot("BTCUSDT", 99, [100], 0, 100)

                agent._record_trade(snapshot, decision, order_result)

                self.assertFalse(agent.trades_path.exists())
            finally:
                os.chdir(old)

    def test_buy_target_is_rebased_to_actual_fill_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = TradingAgent(AgentConfig(api_key="key", api_secret="secret"))
                decision = StrategyDecision(
                    Signal.BUY,
                    "defensive scalp lower edge buy",
                    100,
                    100,
                    10,
                    "scalp-entry-1",
                    target_price=101,
                )
                order = {"executedQty": "0.1", "cummulativeQuoteQty": "10.05", "orderId": 1}

                lot = agent._update_ledger(decision, order)

                self.assertIsNotNone(lot)
                assert lot is not None
                self.assertAlmostEqual(lot["buy_price"], 100.5)
                self.assertAlmostEqual(lot["target_price"], 101.505)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
