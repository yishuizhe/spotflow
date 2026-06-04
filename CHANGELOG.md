# Changelog

## v0.2.6 - 2026-06-04

- Added live trend guard using 24h and 7d moving averages; ordinary grid buys pause when price is below both averages and the 24h average is falling.
- Kept dip-buying available during downtrends, but only after rebound confirmation and within a separate small dip pool.
- Added dashboard trend guard status with mode, moving averages, normal grid pool usage, and dip pool usage.

## v0.2.5 - 2026-06-04

- Fixed month-scale historical dashboard backtests timing out through Nginx by automatically selecting `1m`, `5m`, or `15m` klines based on date range.
- Added the selected historical kline interval to the dashboard backtest advice.
- Increased the deployed Nginx reverse proxy timeout for the dashboard from 60s to 180s as a fallback.

## v0.2.4 - 2026-06-04

- Fixed dashboard backtest handling when an upstream or proxy returns HTML instead of JSON; the UI now reports a readable non-JSON response error instead of `Unexpected token '<'`.
- Removed the trading-toggle password prompt from dashboard backtests because backtests do not place orders.
- Changed swing strategy display from a vague budget label to pool, used amount, and per-order range.
- Exposed swing min/max order quote in the dashboard status payload for clearer live strategy display.

## v0.2.3 - 2026-06-03

- Improved mobile dashboard tables by rendering recent trades, open lots, pending orders, closed lots, and backtest results as compact cards on narrow screens.
- Added mobile field labels for prices, fees, PnL, quantities, statuses, and actions so table rows remain understandable after collapsing into cards.
- Made mobile action buttons wrap inside each card instead of pushing the table beyond the viewport.

## v0.2.2 - 2026-06-03

- Fixed recent order amount display for newly placed limit orders by falling back to planned quote amount when cumulative quote is zero.
- Fixed filled limit buy sync when Binance order lookup returns an empty or zero cumulative quote amount by reconstructing quote from executed quantity and limit price.
- Preserved manual order levels such as `manual-limit-buy` and `manual-limit-sell` in recent order records instead of showing every manual action as `manual-entry`.

## v0.2.1 - 2026-06-03

- Added built-in SVG favicon for the dashboard.
- Improved mobile dashboard layout with tighter cards, smaller chart height, horizontal table scrolling, and touch-friendly controls.
- Cached dashboard login validation per browser session so refreshes no longer call `/api/login` every cycle.
- Included locked Binance balances in portfolio valuation, fixing undercounting while limit sell orders are open.
- Kept live sell protection based on available base balance only, so locked assets cannot be sold twice.
- Added clear educational-use and self-responsibility risk disclaimers to the README.
- Added contribution thanks to [R0A1NG](https://github.com/R0A1NG).

## v0.2.0 - 2026-06-03

- Added Binance Spot live trading support with dry-run and dashboard trading toggle.
- Added Web dashboard with page password login, realtime account summary, price chart, lots, orders, pending limit orders, and baseline PnL.
- Added manual market buy/sell, manual limit buy/sell, pending order cancellation, and external sell ledger sync.
- Added per-lot auto-sell control for manual and swing lots.
- Added multi-layer grid strategy with fee-aware targets, defensive mode, aged-lot target reduction, and small-capital sizing.
- Added independent swing dip-buy strategy with configurable allocation, max order size, add-on spacing, and manual control.
- Added dashboard backtesting for current capital synthetic scenarios and Binance historical K-line ranges.
- Added configurable take-profit percentage from the dashboard, with optional retargeting for open non-swing lots.
- Added daily, weekly, and monthly email trading reports.
- Added project backup scripts and systemd deployment files.
- Added sanitized share package workflow and expanded test coverage to 33 tests.

## v0.1.0 - 2026-05-29

- Initial conservative Binance trading agent scaffold.
- Added basic grid strategy, ledger, risk checks, CLI, and dashboard prototype.
