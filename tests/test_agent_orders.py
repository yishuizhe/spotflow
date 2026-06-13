import tempfile
import unittest
from pathlib import Path

from binance_testnet_agent.agent import TradingAgent, _round_quote_order_qty
from binance_testnet_agent.config import AgentConfig
from binance_testnet_agent.strategy import MarketSnapshot, Signal, StrategyDecision


class AgentOrderTest(unittest.TestCase):
    @staticmethod
    def _agent_with_lot(**overrides):
        agent = TradingAgent(AgentConfig(api_key="key", api_secret="secret"))
        lot = {
            "id": "lot-1",
            "symbol": "BTCUSDT",
            "status": "open",
            "remaining_quantity": 0.01,
            "quantity": 0.01,
            "buy_price": 100,
            "buy_quote": 1,
            "target_price": 101,
            "opened_at": "2026-01-01T00:00:00Z",
            "level": "buy-1",
            "auto_sell": True,
        }
        lot.update(overrides)
        agent.ledger.save([lot])
        return agent

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

    def test_final_sell_gate_reloads_disabled_auto_sell_from_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = self._agent_with_lot(auto_sell=False)
                decision = StrategyDecision(Signal.SELL, "stale strategy decision", 100, 102, 1.02, "lot-target", "lot-1", 0.01)

                guarded = agent._sell_gate(decision)

                self.assertEqual(guarded.signal, Signal.HOLD)
                self.assertIn("auto sell is disabled", guarded.reason)
            finally:
                os.chdir(old)

    def test_execution_path_does_not_submit_stale_disabled_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = self._agent_with_lot(auto_sell=False)
                snapshot = MarketSnapshot("BTCUSDT", 102, [102], 0.01, 100)
                decision = StrategyDecision(Signal.SELL, "stale strategy decision", 100, 102, 1.02, "lot-target", "lot-1", 0.01)

                guarded, order, lot_update = agent._execute_with_final_guard(snapshot, decision)

                self.assertEqual(guarded.signal, Signal.HOLD)
                self.assertIsNone(order)
                self.assertIsNone(lot_update)
            finally:
                os.chdir(old)

    def test_final_sell_gate_blocks_pending_limit_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = self._agent_with_lot(pending_limit_sell_order_id=123)
                decision = StrategyDecision(Signal.SELL, "stale strategy decision", 100, 102, 1.02, "lot-target", "lot-1", 0.01)

                guarded = agent._sell_gate(decision)

                self.assertEqual(guarded.signal, Signal.HOLD)
                self.assertIn("pending limit sell", guarded.reason)
            finally:
                os.chdir(old)

    def test_final_sell_gate_enforces_strategy_profit_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = self._agent_with_lot(target_price=100.1)
                decision = StrategyDecision(Signal.SELL, "bad upstream target", 100, 100.3, 1.003, "lot-target", "lot-1", 0.01)

                guarded = agent._sell_gate(decision)

                self.assertEqual(guarded.signal, Signal.HOLD)
                self.assertIn("below required", guarded.reason)
            finally:
                os.chdir(old)

    def test_manual_lot_sell_gate_uses_saved_target_instead_of_global_profit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = self._agent_with_lot(
                    level="manual-entry",
                    buy_price=100,
                    buy_quote=1,
                    target_price=100.45,
                )
                decision = StrategyDecision(
                    Signal.SELL,
                    "manual lot reached saved target",
                    100,
                    100.5,
                    1.005,
                    "lot-target",
                    "lot-1",
                    0.01,
                )

                guarded = agent._sell_gate(decision)

                self.assertEqual(guarded.signal, Signal.SELL)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
