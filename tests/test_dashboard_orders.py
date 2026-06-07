import unittest
import os
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from tempfile import TemporaryDirectory

from binance_testnet_agent.binance_client import SymbolFilters
from binance_testnet_agent.config import AgentConfig
from binance_testnet_agent.dashboard import Dashboard, _historical_backtest_interval, _order_quote_qty


class FakeExternalSellClient:
    def account(self):
        return {"balances": [{"asset": "BTC", "free": "0.002", "locked": "0"}]}

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

    def limit_sell_qty(self, symbol, quantity, price):
        return {
            "orderId": 991,
            "symbol": symbol,
            "side": "SELL",
            "type": "LIMIT",
            "status": "NEW",
            "origQty": str(quantity),
            "price": str(price),
        }


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

    def test_historical_backtest_interval_scales_with_date_range(self) -> None:
        self.assertEqual(_historical_backtest_interval("2026-04-01", "2026-04-07"), "1m")
        self.assertEqual(_historical_backtest_interval("2026-04-01", "2026-05-01"), "5m")
        self.assertEqual(_historical_backtest_interval("2026-04-01", "2026-06-01"), "15m")

    def test_external_limit_sell_creates_pending_order_without_lot_id(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                dashboard = Dashboard(
                    AgentConfig(api_key="key", api_secret="secret"),
                    Path("baseline.json"),
                    Path("trades.jsonl"),
                    Path("state.json"),
                )
                dashboard.client = FakeExternalSellClient()

                result = dashboard.manual_external_limit_sell(0.001234, 65000.129)

                self.assertNotIn("error", result)
                self.assertEqual(result["pending"]["side"], "SELL")
                self.assertEqual(result["pending"]["level"], "manual-external-limit-sell")
                self.assertNotIn("lot_id", result["pending"])
                self.assertAlmostEqual(result["pending"]["quantity"], 0.00123)
                self.assertAlmostEqual(result["pending"]["limit_price"], 65000.12)
                self.assertTrue(Path("data/pending_orders_BTCUSDT.sqlite3").exists())
            finally:
                os.chdir(old_cwd)

    def test_settings_exposes_and_updates_defensive_scalp_pool_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            old_env = {
                "DEFENSIVE_SCALP_ALLOCATION_PCT": os.environ.get("DEFENSIVE_SCALP_ALLOCATION_PCT"),
                "DEFENSIVE_SCALP_ORDER_PCT": os.environ.get("DEFENSIVE_SCALP_ORDER_PCT"),
                "DEFENSIVE_SCALP_MAX_ORDER_QUOTE": os.environ.get("DEFENSIVE_SCALP_MAX_ORDER_QUOTE"),
            }
            try:
                os.chdir(tmp)
                for key in old_env:
                    os.environ.pop(key, None)
                dashboard = Dashboard(
                    AgentConfig(api_key="key", api_secret="secret"),
                    Path("baseline.json"),
                    Path("trades.jsonl"),
                    Path("state.json"),
                )

                fields = {item["key"]: item for item in dashboard.settings()["config_fields"]}
                self.assertEqual(fields["defensive_scalp_allocation_pct"]["category"], "防守震荡")

                result = dashboard.update_settings(
                    {
                        "config_updates": {
                            "defensive_scalp_allocation_pct": "0.2",
                            "defensive_scalp_order_pct": "0.04",
                            "defensive_scalp_max_order_quote": "20",
                        }
                    }
                )

                self.assertIn("DEFENSIVE_SCALP_ALLOCATION_PCT", result["updated"])
                self.assertEqual(os.environ["DEFENSIVE_SCALP_ALLOCATION_PCT"], "0.2")
                self.assertEqual(os.environ["DEFENSIVE_SCALP_ORDER_PCT"], "0.04")
                self.assertEqual(os.environ["DEFENSIVE_SCALP_MAX_ORDER_QUOTE"], "20")
                self.assertEqual(dashboard.config.defensive_scalp_allocation_pct, 0.2)
                self.assertIn("DEFENSIVE_SCALP_ORDER_PCT=0.04", Path(".env").read_text())
            finally:
                os.chdir(old_cwd)
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_comments_are_stored_in_sqlite_and_author_replies_are_marked(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                dashboard = Dashboard(
                    AgentConfig(api_key="key", api_secret="secret"),
                    Path("baseline.json"),
                    Path("trades.jsonl"),
                    Path("state.json"),
                )

                comment = dashboard.add_comment("访客", "页面很好用")["comment"]
                reply = dashboard.add_comment("ignored", "谢谢反馈", comment["id"], is_author=True)["comment"]

                self.assertEqual(len(dashboard.comments()), 2)
                self.assertEqual(reply["parent_id"], comment["id"])
                self.assertEqual(reply["name"], "管理员")
                self.assertTrue(reply["is_author"])
                self.assertTrue(Path("data/comments.sqlite3").exists())
            finally:
                os.chdir(old_cwd)

    def test_comment_validation_rejects_empty_and_missing_parent(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                dashboard = Dashboard(
                    AgentConfig(api_key="key", api_secret="secret"),
                    Path("baseline.json"),
                    Path("trades.jsonl"),
                    Path("state.json"),
                )

                self.assertIn("error", dashboard.add_comment("", ""))
                self.assertIn("error", dashboard.add_comment("作者", "回复", "missing", is_author=True))
            finally:
                os.chdir(old_cwd)

    def test_admin_can_delete_comment_and_nested_replies(self) -> None:
        with TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                dashboard = Dashboard(
                    AgentConfig(api_key="key", api_secret="secret"),
                    Path("baseline.json"),
                    Path("trades.jsonl"),
                    Path("state.json"),
                )

                comment = dashboard.add_comment("访客", "需要删除")["comment"]
                dashboard.add_comment("ignored", "管理员回复", comment["id"], is_author=True)
                retained = dashboard.add_comment("另一位访客", "保留")["comment"]

                result = dashboard.delete_comment(comment["id"])

                self.assertEqual(result["deleted"], 2)
                self.assertEqual([item["id"] for item in dashboard.comments()], [retained["id"]])
                self.assertIn("error", dashboard.delete_comment(comment["id"]))
                self.assertIn("error", dashboard.delete_comment(""))
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
