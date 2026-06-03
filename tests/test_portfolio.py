import json
import tempfile
import unittest
from pathlib import Path

from binance_testnet_agent.portfolio import reset_baseline
from binance_testnet_agent.dashboard import _update_dotenv


class PortfolioBaselineTest(unittest.TestCase):
    def test_reset_baseline_records_current_value_and_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(json.dumps({"baseline_value_quote": 100}) + "\n")

            payload = reset_baseline(
                path,
                symbol="BTCUSDT",
                base_asset="BTC",
                quote_asset="USDT",
                price=200,
                base_balance=0.1,
                quote_balance=90,
            )

            self.assertAlmostEqual(payload["baseline_value_quote"], 110)
            self.assertAlmostEqual(payload["previous_baseline_value_quote"], 100)
            self.assertAlmostEqual(payload["baseline_delta_quote"], 10)
            saved = json.loads(path.read_text())
            self.assertAlmostEqual(saved["baseline_value_quote"], 110)

    def test_update_dotenv_preserves_existing_values_and_quotes_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("A=1\n# comment\nB=old\n")

            _update_dotenv(path, {"B": "new value", "C": "3"})

            self.assertEqual(path.read_text(), 'A=1\n# comment\nB="new value"\nC=3\n')


if __name__ == "__main__":
    unittest.main()
