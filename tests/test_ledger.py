import tempfile
import unittest
import json
from pathlib import Path

from binance_testnet_agent.ledger import PositionLedger


class PositionLedgerFeeTest(unittest.TestCase):
    def test_fee_summary_does_not_estimate_legacy_lots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PositionLedger(Path(tmp) / "lots.json")
            ledger.save(
                [
                    {
                        "status": "open",
                        "buy_quote": 10,
                        "remaining_quantity": 0.1,
                    },
                    {
                        "status": "closed",
                        "buy_quote": 10,
                        "sell_quote": 11,
                        "realized_pnl": 1,
                    },
                ]
            )

            summary = ledger.fee_summary(0.001)

            self.assertAlmostEqual(summary["open_fee_quote"], 0.0)
            self.assertAlmostEqual(summary["closed_fee_quote"], 0.0)
            self.assertAlmostEqual(ledger.lot_net_realized_pnl(ledger.lots()[1], 0.001), 1.0)

    def test_fee_summary_uses_recorded_fees_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PositionLedger(Path(tmp) / "lots.json")
            ledger.save(
                [
                    {
                        "status": "open",
                        "buy_quote": 10,
                        "buy_fee_quote": 0.01,
                        "remaining_quantity": 0.1,
                    },
                    {
                        "status": "closed",
                        "buy_quote": 10,
                        "sell_quote": 11,
                        "realized_pnl": 1,
                        "total_fee_quote": 0.021,
                        "net_realized_pnl": 0.979,
                    },
                ]
            )

            summary = ledger.fee_summary(0.001)

            self.assertAlmostEqual(summary["open_fee_quote"], 0.01)
            self.assertAlmostEqual(summary["closed_fee_quote"], 0.021)
            self.assertAlmostEqual(ledger.lot_net_realized_pnl(ledger.lots()[1], 0.001), 0.979)

    def test_external_close_lot_marks_external_sell_and_net_pnl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PositionLedger(Path(tmp) / "lots.json")
            ledger.save(
                [
                    {
                        "id": "lot-1",
                        "symbol": "BTCUSDT",
                        "status": "open",
                        "remaining_quantity": 0.01,
                        "quantity": 0.01,
                        "buy_price": 70000,
                        "buy_quote": 700,
                        "target_price": 70700,
                        "opened_at": "2026-01-01T00:00:00Z",
                        "buy_fee_quote": 0.7,
                    }
                ]
            )

            lot = ledger.external_close_lot("lot-1", 71000, None, 0.001)

            self.assertIsNotNone(lot)
            assert lot is not None
            self.assertEqual(lot["status"], "closed")
            self.assertTrue(lot["external_close"])
            self.assertAlmostEqual(lot["sell_quote"], 710)
            self.assertAlmostEqual(lot["sell_fee_quote"], 0.71)
            self.assertAlmostEqual(lot["net_realized_pnl"], 8.59)

    def test_set_auto_sell_updates_open_lot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PositionLedger(Path(tmp) / "lots.json")
            ledger.save(
                [
                    {
                        "id": "lot-1",
                        "symbol": "BTCUSDT",
                        "status": "open",
                        "remaining_quantity": 0.01,
                        "quantity": 0.01,
                        "buy_price": 70000,
                        "buy_quote": 700,
                        "target_price": 70700,
                        "opened_at": "2026-01-01T00:00:00Z",
                        "auto_sell": False,
                    }
                ]
            )

            enabled = ledger.set_auto_sell("lot-1", True)
            disabled = ledger.set_auto_sell("lot-1", False)

            self.assertIsNotNone(enabled)
            self.assertIsNotNone(disabled)
            self.assertFalse(ledger.lots()[0]["auto_sell"])

    def test_retarget_open_lots_skips_swing_and_pending_limit_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PositionLedger(Path(tmp) / "lots.json")
            ledger.save(
                [
                    {"id": "grid", "status": "open", "remaining_quantity": 0.01, "buy_price": 100, "target_price": 101, "level": "buy-1"},
                    {"id": "swing", "status": "open", "remaining_quantity": 0.01, "buy_price": 100, "target_price": 110, "level": "swing-entry"},
                    {
                        "id": "pending",
                        "status": "open",
                        "remaining_quantity": 0.01,
                        "buy_price": 100,
                        "target_price": 101,
                        "level": "manual-entry",
                        "pending_limit_sell_order_id": 123,
                    },
                ]
            )

            result = ledger.retarget_open_lots(0.01, 0.001)
            lots = {lot["id"]: lot for lot in ledger.lots()}

            self.assertEqual(result, {"updated": 1, "skipped": 2})
            self.assertAlmostEqual(lots["grid"]["target_price"], 101.2)
            self.assertAlmostEqual(lots["swing"]["target_price"], 110)
            self.assertAlmostEqual(lots["pending"]["target_price"], 101)

    def test_legacy_json_is_migrated_to_sqlite_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lots.json"
            path.write_text(json.dumps({"lots": [{"id": "legacy", "status": "open", "remaining_quantity": 0.01}]}))
            ledger = PositionLedger(path)

            self.assertEqual(ledger.lots()[0]["id"], "legacy")
            self.assertTrue(path.with_suffix(".sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
