import unittest

from binance_testnet_agent.agent import TradingAgent
from binance_testnet_agent.strategy import GridStrategy, MarketSnapshot, Signal


def _lot(level, qty, buy_price, target):
    return {
        "id": level, "symbol": "BTCUSDT", "status": "open", "level": level,
        "remaining_quantity": qty, "quantity": qty, "buy_price": buy_price,
        "buy_quote": buy_price * qty, "target_price": target, "auto_sell": True,
        "opened_at": "2026-01-01T00:00:00Z",
    }


class DustJamTest(unittest.TestCase):
    def test_grid_strategy_lots_excludes_dust(self):
        lots = [_lot("dust", 0.0000094, 60000, 64000), _lot("buy-1", 0.0001, 65000, 66000)]
        kept = TradingAgent._grid_strategy_lots(lots)
        self.assertEqual([l["level"] for l in kept], ["buy-1"])

    def test_dust_crumb_would_jam_but_filter_prevents_it(self):
        # 一个低于最小下单数量、但价格已过目标价的碎渣批次
        dust = _lot("dust", 0.0000094, 60000, 64000)
        strat = GridStrategy(grid_step_pct=0.006, take_profit_pct=0.006,
                             order_quote_size=5.5, max_position_quote=100)
        snap = MarketSnapshot("BTCUSDT", 65800, [65800] * 20, 0.0000094, 50)

        # 不过滤:策略会把碎渣当成"该卖"返回 SELL —— 这正是卡死根因
        d_with = strat.decide(snap, {}, [dust])
        self.assertEqual(d_with.signal, Signal.SELL)

        # 过滤掉碎渣后:不会再产生卖碎渣的决定
        d_without = strat.decide(snap, {}, TradingAgent._grid_strategy_lots([dust]))
        self.assertFalse(d_without.signal == Signal.SELL and d_without.level == "dust")


if __name__ == "__main__":
    unittest.main()
