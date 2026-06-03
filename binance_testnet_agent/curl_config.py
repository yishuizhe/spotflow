from __future__ import annotations

import argparse
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import urlencode

from .config import AgentConfig


def signed_url(config: AgentConfig, path: str, params: dict[str, str]) -> str:
    signed_params = dict(params)
    signed_params["timestamp"] = str(int(time.time() * 1000))
    signed_params["recvWindow"] = "60000"
    query = urlencode(signed_params)
    signature = hmac.new(config.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{config.base_url}{path}?{query}&signature={signature}"


def write_curl_config(path: Path, method: str, url: str, api_key: str, data: str | None = None) -> None:
    lines = [
        "silent",
        "show-error",
        "max-time = 15",
        f"request = {method}",
        f"header = X-MBX-APIKEY: {api_key}",
        "header = Content-Type: application/x-www-form-urlencoded",
        f"url = {url}",
    ]
    if data is not None:
        lines.append(f"data = {data}")
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate curl config for signed Binance Spot requests")
    parser.add_argument("kind", choices=["account", "order-test", "order"])
    parser.add_argument("--side", choices=["BUY", "SELL"])
    parser.add_argument("--quantity")
    parser.add_argument("--quote-order-qty")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = AgentConfig.from_env()
    output = Path(args.output)

    if args.kind == "account":
        url = signed_url(config, "/api/v3/account", {})
        write_curl_config(output, "GET", url, config.api_key)
        return

    params = {
        "symbol": config.symbol,
        "side": args.side or "BUY",
        "type": "MARKET",
    }
    if args.quote_order_qty:
        params["quoteOrderQty"] = args.quote_order_qty
    if args.quantity:
        params["quantity"] = args.quantity
    path = "/api/v3/order/test" if args.kind == "order-test" else "/api/v3/order"
    url = signed_url(config, path, params)
    write_curl_config(output, "POST", url, config.api_key)


if __name__ == "__main__":
    main()
