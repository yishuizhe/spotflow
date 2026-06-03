from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BinanceAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SymbolFilters:
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    tick_size: Decimal


class BinanceSpotClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.timeout = timeout

    def ping(self) -> dict[str, Any]:
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> dict[str, Any]:
        return self._request("GET", "/api/v3/time")

    def ticker_price(self, symbol: str) -> float:
        payload = self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        return float(payload["price"])

    def klines(self, symbol: str, interval: str = "1m", limit: int = 120) -> list[list[Any]]:
        return self._request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    def exchange_info(self, symbol: str) -> dict[str, Any]:
        return self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})

    def account(self) -> dict[str, Any]:
        return self._signed_request("GET", "/api/v3/account")

    def market_buy_quote(self, symbol: str, quote_order_qty: float) -> dict[str, Any]:
        return self._signed_request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": self._format_decimal(Decimal(str(quote_order_qty))),
            },
        )

    def market_sell_qty(self, symbol: str, quantity: Decimal) -> dict[str, Any]:
        return self._signed_request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": self._format_decimal(quantity),
            },
        )

    def limit_buy_qty(self, symbol: str, quantity: Decimal, price: Decimal) -> dict[str, Any]:
        return self._signed_request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": self._format_decimal(quantity),
                "price": self._format_decimal(price),
            },
        )

    def limit_sell_qty(self, symbol: str, quantity: Decimal, price: Decimal) -> dict[str, Any]:
        return self._signed_request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": self._format_decimal(quantity),
                "price": self._format_decimal(price),
            },
        )

    def order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return self._signed_request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id})

    def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return self._signed_request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id})

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        info = self.exchange_info(symbol)
        symbol_info = info["symbols"][0]
        lot_size = next(item for item in symbol_info["filters"] if item["filterType"] == "LOT_SIZE")
        price_filter = next(item for item in symbol_info["filters"] if item["filterType"] == "PRICE_FILTER")
        min_notional_filter = next(
            (item for item in symbol_info["filters"] if item["filterType"] in {"MIN_NOTIONAL", "NOTIONAL"}),
            {"minNotional": "0"},
        )
        return SymbolFilters(
            step_size=Decimal(lot_size["stepSize"]),
            min_qty=Decimal(lot_size["minQty"]),
            min_notional=Decimal(min_notional_filter.get("minNotional", min_notional_filter.get("notional", "0"))),
            tick_size=Decimal(price_filter["tickSize"]),
        )

    def round_quantity(self, quantity: Decimal, filters: SymbolFilters) -> Decimal:
        if filters.step_size <= 0:
            return quantity
        steps = (quantity / filters.step_size).to_integral_value(rounding=ROUND_DOWN)
        return steps * filters.step_size

    def round_price(self, price: Decimal, filters: SymbolFilters) -> Decimal:
        if filters.tick_size <= 0:
            return price
        ticks = (price / filters.tick_size).to_integral_value(rounding=ROUND_DOWN)
        return ticks * filters.tick_size

    def _signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise BinanceAPIError("Missing BINANCE_API_KEY or BINANCE_API_SECRET")

        signed_params = dict(params or {})
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = 5000
        query = urlencode(signed_params, doseq=True)
        signature = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
        signed_params["signature"] = signature
        return self._request(method, path, signed_params, signed=True)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any] | list[Any]:
        params = params or {}
        query = urlencode(params, doseq=True)
        url = f"{self.base_url}{path}"
        body = None
        headers = {"User-Agent": "binance-testnet-agent/0.1"}
        if signed:
            headers["X-MBX-APIKEY"] = self.api_key

        if method == "GET":
            if query:
                url = f"{url}?{query}"
        else:
            body = query.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode()
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise BinanceAPIError(f"Binance HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            return self._curl_request(method, url, body, headers, exc)

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BinanceAPIError(f"Invalid JSON response: {raw[:200]}") from exc

    def _curl_request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        original_error: URLError,
    ) -> dict[str, Any] | list[Any]:
        command = ["curl", "-sS", "--max-time", str(self.timeout), "-X", method]
        for key, value in headers.items():
            command.extend(["-H", f"{key}: {value}"])
        if body is not None:
            command.extend(["--data", body.decode()])
        command.extend(["-w", "\n%{http_code}", url])

        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise BinanceAPIError(f"Network error: {original_error.reason}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or str(original_error.reason)
            raise BinanceAPIError(f"Network error: {detail}") from original_error

        raw = completed.stdout
        if "\n" not in raw:
            raise BinanceAPIError(f"Invalid curl response: {raw[:200]}")
        response_body, status_text = raw.rsplit("\n", 1)
        status = int(status_text)
        if status >= 400:
            raise BinanceAPIError(f"Binance HTTP {status}: {response_body}")
        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise BinanceAPIError(f"Invalid JSON response: {response_body[:200]}") from exc

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return format(value.normalize(), "f")
