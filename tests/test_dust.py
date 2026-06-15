import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from binance_testnet_agent.ledger import PositionLedger


MIN_QTY = 0.00001


def _lot(lot_id: str, qty: float, *, level: str = "buy-1", buy_price: float = 60000.0,
         buy_fee: float = 0.0, pending: str | None = None) -> dict:
    lot = {
        "id": lot_id,
        "symbol": "BTCUSDT",
        "level": level,
        "status": "open",
        "buy_price": buy_price,
        "quantity": qty,
        "remaining_quantity": qty,
        "buy_quote": buy_price * qty,
        "buy_fee_quote": buy_fee,
        "target_price": buy_price * 1.006,
        "auto_sell": True,
    }
    if pending:
        lot["pending_limit_sell_order_id"] = pending
    return lot


class ConsolidateDustTest(unittest.TestCase):
    def _ledger(self, tmp: str) -> PositionLedger:
        return PositionLedger(Path(tmp) / "lots.sqlite3")

    def test_sub_min_qty_lots_fold_into_one_dust_lot(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.save([
                _lot("a", 0.000006, buy_fee=0.36),
                _lot("b", 0.000007, buy_fee=0.42),
            ])

            result = ledger.consolidate_dust(MIN_QTY, 0.006, 0.001)

            self.assertEqual(result["moved"], 2)
            lots = ledger.lots()
            dust = [l for l in lots if l.get("level") == "dust"]
            self.assertEqual(len(dust), 1)
            d = dust[0]
            self.assertAlmostEqual(d["remaining_quantity"], 0.000013, places=9)
            # 综合成本与手续费累计
            self.assertAlmostEqual(d["buy_quote"], 60000 * 0.000006 + 60000 * 0.000007, places=8)
            self.assertAlmostEqual(d["buy_fee_quote"], 0.36 + 0.42, places=8)
            self.assertAlmostEqual(d["buy_price"], 60000.0, places=2)
            self.assertGreater(d["target_price"], d["buy_price"])
            # 原批次已平仓
            self.assertEqual([l for l in lots if l["id"] == "a"][0]["status"], "closed")
            self.assertEqual([l for l in lots if l["id"] == "b"][0]["status"], "closed")
            # 只剩碎渣账户一个未平批次
            self.assertEqual(len(ledger.open_lots()), 1)
            self.assertEqual(ledger.open_lots()[0]["level"], "dust")

    def test_normal_lot_is_not_folded(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.save([_lot("big", 0.001, buy_fee=0.06)])

            result = ledger.consolidate_dust(MIN_QTY, 0.006, 0.001)

            self.assertEqual(result["moved"], 0)
            self.assertEqual(len(ledger.open_lots()), 1)
            self.assertFalse([l for l in ledger.lots() if l.get("level") == "dust"])

    def test_pending_limit_sell_dust_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.save([_lot("p", 0.000006, buy_fee=0.36, pending="999")])

            result = ledger.consolidate_dust(MIN_QTY, 0.006, 0.001)

            self.assertEqual(result["moved"], 0)
            self.assertEqual([l for l in ledger.lots() if l["id"] == "p"][0]["status"], "open")

    def test_second_pass_accumulates_into_existing_dust(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.save([_lot("a", 0.000006, buy_fee=0.36)])
            ledger.consolidate_dust(MIN_QTY, 0.006, 0.001)
            # 新增一个碎渣后再次合并，应并入同一个 dust 批次
            lots = ledger.lots()
            lots.append(_lot("c", 0.000008, buy_fee=0.48))
            ledger.save(lots)

            result = ledger.consolidate_dust(MIN_QTY, 0.006, 0.001)

            self.assertEqual(result["moved"], 1)
            dust = [l for l in ledger.lots() if l.get("level") == "dust"]
            self.assertEqual(len(dust), 1)
            self.assertAlmostEqual(dust[0]["remaining_quantity"], 0.000014, places=9)


if __name__ == "__main__":
    unittest.main()
