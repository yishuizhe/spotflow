import tempfile
import unittest
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from binance_testnet_agent.agent import TradingAgent, _round_quote_order_qty
from binance_testnet_agent.binance_client import SymbolFilters
from binance_testnet_agent.config import AgentConfig
from binance_testnet_agent.strategy import MarketSnapshot, Signal, StrategyDecision


class _FakeMergeSellClient:
    """专给碎渣合并卖出场景用的假客户端：价格固定，最小下单额 5 USDT。"""

    def __init__(self, price: float = 64000.0, base_free: str = "1") -> None:
        self.price = price
        self.base_free = base_free
        self.market_sells: list[float] = []

    def ticker_price(self, symbol):
        return self.price

    def symbol_filters(self, symbol):
        return SymbolFilters(
            step_size=Decimal("0.00001"),
            min_qty=Decimal("0.00001"),
            min_notional=Decimal("5"),
            tick_size=Decimal("0.01"),
        )

    def round_quantity(self, quantity, filters):
        steps = (quantity / filters.step_size).to_integral_value(rounding=ROUND_DOWN)
        return steps * filters.step_size

    def round_price(self, price, filters):
        ticks = (price / filters.tick_size).to_integral_value(rounding=ROUND_DOWN)
        return ticks * filters.tick_size

    def account(self):
        return {"balances": [{"asset": "BTC", "free": self.base_free, "locked": "0"}]}

    def market_sell_qty(self, symbol, quantity):
        q = float(quantity)
        self.market_sells.append(q)
        return {
            "orderId": 1,
            "symbol": symbol,
            "side": "SELL",
            "status": "FILLED",
            "executedQty": str(q),
            "cummulativeQuoteQty": str(q * self.price),
        }


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

    def test_merge_sell_dust_runs_when_chosen_sell_was_skipped_for_min_notional(self) -> None:
        """复现死锁场景：网格策略每轮都选中目标价最低的碎渣批次，但碎渣批次单独卖不出去
        （低于最小下单额被跳过），导致其它已达标的正常批次永远排不上号。

        修复后：只要本轮选中的卖出最终没有真正成交（order_result 带 skipped），
        碎渣合并卖出逻辑就应该照常尝试运行，把碎渣和正常批次一起合并卖掉。
        """
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = TradingAgent(AgentConfig(api_key="key", api_secret="secret", execute_trades=True))
                agent.client = _FakeMergeSellClient(price=64000.0)
                agent.ledger.save(
                    [
                        {
                            "id": "dust-BTCUSDT",
                            "level": "dust",
                            "status": "open",
                            "buy_price": 60000,
                            "buy_quote": 3.6,
                            "quantity": 0.00006,
                            "remaining_quantity": 0.00006,
                            "buy_fee_quote": 0.0036,
                            "target_price": 60500,
                            "auto_sell": True,
                        },
                        {
                            "id": "lot-normal",
                            "level": "manual-entry",
                            "status": "open",
                            "buy_price": 60000,
                            "buy_quote": 60,
                            "quantity": 0.001,
                            "remaining_quantity": 0.001,
                            "buy_fee_quote": 0.06,
                            "target_price": 60500,
                            "auto_sell": True,
                        },
                    ]
                )
                # 网格策略每轮都会选中目标价最低的碎渣批次，但它单独卖不出去（低于最小下单额）。
                decision = StrategyDecision(
                    Signal.SELL,
                    "price reached lot target",
                    60500,
                    64000,
                    0.00006 * 64000,
                    "lot-target",
                    "dust-BTCUSDT",
                    0.00006,
                )
                skipped_order_result = {"skipped": True, "reason": "quantity below minNotional", "quantity": "0.00006"}

                merge_result = agent._maybe_merge_sell_dust(decision, skipped_order_result)

                self.assertTrue(merge_result is not None and merge_result.get("merged"), merge_result)
                # 碎渣 + 正常批次被合并成一笔单子一起卖掉，不再永久卡死。
                self.assertEqual(len(agent.client.market_sells), 1)
                self.assertEqual(agent.ledger.open_lots(), [])
            finally:
                os.chdir(old)

    def test_merge_sell_dust_still_skipped_when_chosen_sell_actually_filled(self) -> None:
        """如果本轮选中的卖出已经真正成交，不应该在同一个 tick 再触发一次合并卖出。"""
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                agent = TradingAgent(AgentConfig(api_key="key", api_secret="secret", execute_trades=True))
                agent.client = _FakeMergeSellClient(price=64000.0)
                agent.ledger.save(
                    [
                        {
                            "id": "lot-normal",
                            "level": "manual-entry",
                            "status": "open",
                            "buy_price": 60000,
                            "buy_quote": 60,
                            "quantity": 0.001,
                            "remaining_quantity": 0.001,
                            "buy_fee_quote": 0.06,
                            "target_price": 60500,
                            "auto_sell": True,
                        },
                    ]
                )
                decision = StrategyDecision(
                    Signal.SELL,
                    "price reached lot target",
                    60500,
                    64000,
                    0.001 * 64000,
                    "lot-target",
                    "lot-normal",
                    0.001,
                )
                filled_order_result = {
                    "orderId": 1,
                    "status": "FILLED",
                    "executedQty": "0.001",
                    "cummulativeQuoteQty": "64",
                }

                merge_result = agent._maybe_merge_sell_dust(decision, filled_order_result)

                self.assertIsNone(merge_result)
                self.assertEqual(agent.client.market_sells, [])
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
