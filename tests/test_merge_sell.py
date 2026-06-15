import os
import unittest
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from tempfile import TemporaryDirectory

from binance_testnet_agent.binance_client import SymbolFilters
from binance_testnet_agent.config import AgentConfig
from binance_testnet_agent.dashboard import Dashboard


class FakeSellClient:
    def __init__(self, price: float = 64000.0, base_free: str = "0.01") -> None:
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
            "orderId": 12345,
            "symbol": symbol,
            "side": "SELL",
            "status": "FILLED",
            "executedQty": str(q),
            "cummulativeQuoteQty": str(q * self.price),
        }


def _dashboard(tmp: str, **config_kwargs) -> Dashboard:
    os.chdir(tmp)
    config = AgentConfig(api_key="key", api_secret="secret", trading_fee_rate=0.001, **config_kwargs)
    return Dashboard(config, Path("baseline.json"), Path("trades.jsonl"), Path("state.json"))


class LossProtectionTest(unittest.TestCase):
    def test_manual_market_sell_below_breakeven_is_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                dashboard = _dashboard(tmp)
                dashboard.client = FakeSellClient(price=64000.0)
                dashboard.ledger.save(
                    [
                        {
                            "id": "lot-1",
                            "level": "buy-1",
                            "status": "open",
                            "buy_price": 65000,
                            "buy_quote": 65,
                            "quantity": 0.001,
                            "remaining_quantity": 0.001,
                            "buy_fee_quote": 0.065,
                            "target_price": 66000,
                            "auto_sell": True,
                        }
                    ]
                )

                result = dashboard.manual_sell("lot-1")

                self.assertIn("error", result)
                self.assertIn("亏本保护", result["error"])
                self.assertEqual(dashboard.client.market_sells, [])
            finally:
                os.chdir(old_cwd)

    def test_manual_market_sell_above_breakeven_executes(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                dashboard = _dashboard(tmp)
                dashboard.client = FakeSellClient(price=66000.0)
                dashboard.ledger.save(
                    [
                        {
                            "id": "lot-1",
                            "level": "buy-1",
                            "status": "open",
                            "buy_price": 65000,
                            "buy_quote": 65,
                            "quantity": 0.001,
                            "remaining_quantity": 0.001,
                            "buy_fee_quote": 0.065,
                            "target_price": 65500,
                            "auto_sell": True,
                        }
                    ]
                )

                result = dashboard.manual_sell("lot-1")

                self.assertNotIn("error", result)
                self.assertEqual(len(dashboard.client.market_sells), 1)
            finally:
                os.chdir(old_cwd)

    def test_manual_limit_sell_below_breakeven_is_blocked_and_clears_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                dashboard = _dashboard(tmp)
                dashboard.client = FakeSellClient(price=64000.0)
                dashboard.ledger.save(
                    [
                        {
                            "id": "lot-1",
                            "level": "buy-1",
                            "status": "open",
                            "buy_price": 65000,
                            "buy_quote": 65,
                            "quantity": 0.001,
                            "remaining_quantity": 0.001,
                            "buy_fee_quote": 0.065,
                            "target_price": 66000,
                            "auto_sell": True,
                        }
                    ]
                )

                result = dashboard.manual_limit_sell("lot-1", 64000.0)

                self.assertIn("error", result)
                self.assertIn("亏本保护", result["error"])
                lot = next(item for item in dashboard.ledger.lots() if item["id"] == "lot-1")
                self.assertIsNone(lot.get("pending_limit_sell_order_id"))
            finally:
                os.chdir(old_cwd)


class MergeSellDustTest(unittest.TestCase):
    def _dust_lots(self) -> list[dict]:
        # 单笔 0.00006 BTC，现价 64000 时市值约 3.84 USDT，低于 5 USDT 最小下单额。
        return [
            {
                "id": f"lot-{i}",
                "level": f"buy-{i}",
                "status": "open",
                "buy_price": 60000,
                "buy_quote": 3.6,
                "quantity": 0.00006,
                "remaining_quantity": 0.00006,
                "buy_fee_quote": 0.0036,
                "target_price": 60500,
                "auto_sell": True,
            }
            for i in (1, 2)
        ]

    def test_merge_sell_combines_dust_lots_into_one_order(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                dashboard = _dashboard(tmp)
                dashboard.client = FakeSellClient(price=64000.0)
                dashboard.ledger.save(self._dust_lots())

                result = dashboard.merge_sell_dust()

                self.assertTrue(result.get("merged"), result)
                self.assertEqual(result["lots_closed"], 2)
                # 只下了一笔合并卖单
                self.assertEqual(len(dashboard.client.market_sells), 1)
                self.assertAlmostEqual(dashboard.client.market_sells[0], 0.00012, places=8)
                # 两个批次都已平仓
                open_lots = dashboard.ledger.open_lots()
                self.assertEqual(open_lots, [])
            finally:
                os.chdir(old_cwd)

    def test_merge_sell_skips_when_no_dust(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                dashboard = _dashboard(tmp)
                dashboard.client = FakeSellClient(price=64000.0)
                # 单笔 0.001 BTC，市值约 64 USDT，远高于最小下单额，不属于碎屑。
                dashboard.ledger.save(
                    [
                        {
                            "id": "lot-big",
                            "level": "buy-1",
                            "status": "open",
                            "buy_price": 60000,
                            "buy_quote": 60,
                            "quantity": 0.001,
                            "remaining_quantity": 0.001,
                            "buy_fee_quote": 0.06,
                            "target_price": 60500,
                            "auto_sell": True,
                        }
                    ]
                )

                result = dashboard.merge_sell_dust()

                self.assertFalse(result.get("merged"))
                self.assertEqual(dashboard.client.market_sells, [])
            finally:
                os.chdir(old_cwd)

    def test_merge_sell_does_not_touch_losing_dust(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                dashboard = _dashboard(tmp)
                # 现价 59000 低于成本 60000，碎屑批次亏损，不应被合并卖出。
                dashboard.client = FakeSellClient(price=59000.0)
                dashboard.ledger.save(self._dust_lots())

                result = dashboard.merge_sell_dust()

                self.assertFalse(result.get("merged"))
                self.assertEqual(dashboard.client.market_sells, [])
                self.assertEqual(len(dashboard.ledger.open_lots()), 2)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
