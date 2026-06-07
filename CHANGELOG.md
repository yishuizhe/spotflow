# Changelog

## v1.0.17 - 2026-06-07

- Added a final sell gate in the auto-trading agent that blocks any automated sell whose execution price is below the lot's true cost basis plus bilateral fees, preventing loss-making exits from lots with stale target prices.
- Translated Binance `-2010` balance errors in the automated agent sell path into actionable Chinese messages with the current free balance.
- Fixed the closed-lots table ordering to sort by close time descending instead of by the original buy order, so recently closed lots appear first.

## v1.0.16 - 2026-06-07

- Accounted for base-asset commissions when recording the sellable quantity of newly filled buy orders.
- Limited each new limit sell to the exchange account's current free base-asset balance, allowing a partial lot order when other orders have locked funds.
- Replaced Binance `-2010` balance errors with actionable free/locked balance guidance.
- Made bulk limit selling continue across lots and report successful, skipped, and failed orders instead of stopping at the first failure.

## v1.0.15 - 2026-06-07

- Added a fee-aware minimum profit target for legacy and new swing lots so automatic swing exits cannot use a target below cost.
- Display corrected swing targets as protected targets on the dashboard.
- Added linked lot, cost price, and estimated net profit columns to pending limit orders.
- Added regression tests for legacy swing targets and pending-order cost/profit enrichment.

## v1.0.14 - 2026-06-07

- Rebased strategy targets on the actual market-buy fill price instead of the pre-order ticker price.
- Added a fee-aware profit floor for new and legacy defensive-scalp lots to prevent automatic loss-making exits.
- Added an administrator-controlled visitor-comment switch while retaining administrator announcements, replies, and deletion.
- Removed the Web App Manifest link and endpoint while retaining standard favicons and the Apple Touch Icon.
- Added regression tests for fill-price target rebasing, legacy-lot sell protection, and disabled visitor comments.

## v1.0.13 - 2026-06-07

- Added a session-only comment administrator mode protected by the trading management password.
- Administrators can publish top-level comments with an administrator badge, reply to visitors, and delete individual replies or complete comment threads.
- Enforced comment deletion permissions on the server and added confirmation before irreversible deletion.

## v1.0.12 - 2026-06-06

- Added a trusted-device login mode that remains signed in until credentials fail or the user explicitly revokes trust.
- Clarified the existing 24-hour option as temporary-device login and made it mutually exclusive with trusted-device mode.
- Added a revoke-trust-and-sign-out action to the theme drawer.
- Renamed the GitHub repository to `yishuizhe/spotflow`.

## v1.0.11 - 2026-06-06

- Renamed the browser title to `SpotFlow · 现货量化助手` and set the iOS home-screen name to `SpotFlow`.
- Updated the visible dashboard brand to `SpotFlow` with the subtitle `现货量化交易看板`.
- Added dedicated 180px Apple Touch Icon plus 192px and 512px PNG web app icons.
- Added a web app manifest and iOS standalone metadata so Safari no longer falls back to a gray `B` icon.

## v1.0.10 - 2026-06-06

- Reduced the floating utility rail and icon sizes and moved the rail from mid-page to the bottom-right corner.
- Repositioned the theme drawer to expand leftward from the bottom-right and reduced its mobile footprint.

## v1.0.9 - 2026-06-06

- Send exactly one report at 18:00 each day, prioritizing month-end reports over weekly reports and weekly reports over daily reports.
- Disabled the separate weekly and monthly timers and moved report-type selection into the daily report service.
- Standardized report colors to the Chinese market convention: red for profit and green for loss.
- Standardized prices, balances, PnL, and holdings in both HTML and plain-text templates to two decimal places.

## v1.0.8 - 2026-06-06

- Replaced the misleading `繁` theme control with a clear palette icon.
- Removed the duplicate system settings action from the theme drawer; the utility rail gear is now the single settings entry.

## v1.0.7 - 2026-06-06

- Reduced the default dashboard width and added a compact/wide layout toggle that preserves space for the mascot and utility rail.
- Rebuilt the right-side controls as a vertical utility rail for themes, dark mode, layout width, settings, and scroll-to-top.
- Replaced the misleading Chinese-character theme button with a palette icon and removed the duplicate settings action from the theme drawer.
- Replaced every native browser prompt, confirm, and alert with an in-page action dialog.
- Fixed the settings save button so its loading state always resets, including reload failures.
- Made remembered logins enter immediately and validate through the first real API request instead of a separate blocking login request.
- Expanded mascot help coverage across dashboard actions, table headers, account summaries, strategy labels, and settings.
- Added SQLite-backed comments, author-verified replies, optional danmaku display, speed controls, and hover-to-pause behavior.
- Added mascot image cache versioning, automatic retry, and a readable fallback for failed image loads.
- Closed SQLite connections deterministically after each storage operation to prevent resource accumulation during long-running dashboard use.
- Return a structured JSON status error when the upstream market connection drops unexpectedly instead of aborting the HTTP response.

## v1.0.6 - 2026-06-05

- Merged PR #3, adding a compact top strategy summary for take profit, grid spacing, position sizing, defensive mode, and floating-loss protection.
- Reviewed and closed PR #2 because it made every modal action bar sticky; applied the intended behavior only to the settings modal.
- Added mouse-wheel chart range switching across intraday, 5m, 15m, 1h, 4h, daily, and weekly views with synchronized range tabs.
- Reduced unused space in the top dashboard and reorganized the main account, PnL, and trading controls into a denser layout.
- Standardized displayed prices, quote amounts, PnL, fees, and backtest results to two decimals while preserving BTC quantity precision.
- Added open-lot pagination, kept all four desktop lot actions on one row, and moved the mascot further left.
- Added `CHANGELOG.md` to the deployment archive so server-side release notes stay synchronized with the deployed code.

## v1.0.5 - 2026-06-05

- Merged and hardened PR #1 by R0A1NG, adding an optional 24-hour remember-me login cache.
- Clear expired, malformed, or server-rejected cached passwords automatically.
- Write the persistent cache only after successful server validation and warn users to enable it only on private devices.

## v1.0.4 - 2026-06-05

- Added a prominent top-of-page success/error toast for system settings updates.
- Added a persistent styled result block inside the settings modal and prevented settings reload from immediately overwriting the save result.
- Added a saving state to the settings button and an explicit dismiss control for the toast.

## v1.0.3 - 2026-06-05

- Removed the mascot eye animation overlay and kept the original static character artwork for a cleaner look.

## v1.0.2 - 2026-06-05

- Reworked the mascot eye animation again: removed the oversized full-eye overlay and kept the original character eyes visible.
- Added only a tiny same-color iris/highlight movement layer with a much smaller tracking range to avoid the exaggerated look.

## v1.0.1 - 2026-06-05

- Replaced the awkward black mascot eye dots with a complete animated eye layer whose irises follow the mouse.
- Anchored the mascot to the bottom-left edge and made the speech bubble auto-hide after a short idle period.
- Added market sell, auto-sell toggle, limit sell, and external-close controls to every open lot.
- Added bulk open-lot controls for enabling/disabling auto-sell, market selling, and placing target-price limit sells.
- Removed the manual/swing-only restriction for dashboard market sell and auto-sell toggles.
- Switched profit/up colors to red and loss/down colors to green for Chinese market convention, while keeping trade enable/disable button colors semantic.

## v1.0.0 - 2026-06-05

- Promoted the project to `v1.0.0` as the first full learning/stable release for the live dashboard and strategy tool.
- Removed the top settings button and kept a compact bottom-right settings dock to avoid duplicated entry points.
- Reworked the theme selector into a small floating button with a drawer, and added forest, ocean, sunset, and minimal themes.
- Replaced the CSS mascot with an original generated assistant image and improved desktop/mobile sizing.
- Expanded hover explanations so buttons, headings, and key dashboard terms can trigger assistant help text.
- Moved the chart Y axis back to the left and changed recent trade markers into exchange-style `B` / `S` circles.
- Reduced recent live order rows per page so the order panel better matches the account and strategy panel height.

## v0.3.1 - 2026-06-05

- Changed open/closed lot display to show both the strategy level and a short ledger lot id, making repeated strategy levels easier to understand.
- Increased the recent live order page size to 13 rows and localized order side/level labels in the dashboard.
- Upgraded the chart from a close-price line to an OHLC candlestick chart with more time-range tabs and recent buy/sell markers.
- Reworked the account and strategy panel into concise Chinese summaries instead of mixed internal English reason fields.
- Made the settings modal header sticky and added clearer spacing between notification and strategy sections.
- Added browser-saved visual themes: day, exchange dark, arena, anime campus, and music stage.
- Added an original dashboard mascot with optional visibility, mouse-following eyes, hover explanations, and a brief BTC market status message.

## v0.3.0 - 2026-06-04

- Grouped the dashboard settings modal into security, profit/manual trading, notifications, trading basics, pools/sizing, risk, defensive mode, trend guard, swing, and defensive scalp sections.
- Exposed the common strategy and pool parameters in the dashboard settings page, including defensive scalp allocation, per-order percentage, min/max order quote, range, and take-profit controls.
- Clarified that defensive scalp sizing is account-based by default: pool and single-order sizes scale with each user's live account value, while manual overrides remain available.
- Applied dashboard defensive scalp settings to built-in backtests so simulations match the live configuration.
- Changed `.env` writes to atomic replacement to reduce the chance of a partially written config file.

## v0.2.9 - 2026-06-04

- Fixed Binance `quoteOrderQty has too much precision` errors for account-sized defensive scalp buys by rounding market buy quote quantities down to 2 decimals.
- Prevented failed or skipped order attempts from being written to the recent live order feed.

## v0.2.8 - 2026-06-04

- Added defensive scalp mode: when the main grid is in defensive mode, a separate small pool can trade range-bound dips and rebounds.
- Added account-sized defensive scalp order sizing with configurable allocation, order percentage, min/max quote, buy drop, and take-profit thresholds.
- Added dashboard defensive scalp status with center price, buy/sell edges, pool usage, and reason.
- Tuned the default defensive scalp thresholds to 0.4% buy drop and 0.5% take profit after a recent BTC 1m kline quick simulation.

## v0.2.7 - 2026-06-04

- Added an external-position limit sell action for users who hold BTC in the account but do not have a matching open lot in the script ledger.
- Improved the empty open-lot table message and the batch limit-sell error so users understand when a sell must be tied to a ledger lot.
- External limit sell fills are now recorded without trying to close a nonexistent ledger lot.
- Increased the recent live order page size from 10 to 12 and clarified the empty open-lot message when external limit sell orders are active.
- Moved open lots and pending limit orders from JSON files to SQLite stores, with automatic migration from legacy JSON files.

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
