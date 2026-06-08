import unittest
from decimal import Decimal
from urllib.error import URLError
from unittest.mock import patch

from binance_testnet_agent.binance_client import BinanceAPIError, BinanceSpotClient


class BinanceClientRetryTest(unittest.TestCase):
    def test_post_network_error_is_not_retried(self) -> None:
        client = BinanceSpotClient("https://example.invalid", "key", "secret")

        with patch("binance_testnet_agent.binance_client.urlopen", side_effect=URLError("timeout")):
            with patch.object(client, "_curl_request") as fallback:
                with self.assertRaisesRegex(BinanceAPIError, "request was not retried"):
                    client._request("POST", "/api/v3/order", {"symbol": "BTCUSDT"}, signed=True)

        fallback.assert_not_called()

    def test_ambiguous_order_submission_recovers_by_client_order_id(self) -> None:
        client = BinanceSpotClient("https://example.invalid", "key", "secret")
        recovered = {"orderId": 123, "status": "FILLED", "executedQty": "0.01"}

        with patch.object(
            client,
            "_signed_request",
            side_effect=[BinanceAPIError("Ambiguous network error during POST"), recovered],
        ) as request:
            result = client.market_sell_qty("BTCUSDT", Decimal("0.01"))

        self.assertEqual(result, recovered)
        submitted = request.call_args_list[0].args[2]
        queried = request.call_args_list[1].args[2]
        self.assertEqual(queried["origClientOrderId"], submitted["newClientOrderId"])


if __name__ == "__main__":
    unittest.main()
