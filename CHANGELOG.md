# Changelog

## v2.1.7 - 2026-06-23

- Show the running app version on the dashboard page itself, next to the "SpotFlow" title, sourced from `binance_testnet_agent.__version__` (previously defined but unused, and stale at `2.0.2`). The HTML title bar now always reflects the actually-deployed version, so it's obvious from the page alone whether a given deploy is current.
- `dashboard.HTML` keeps a `__SPOTFLOW_VERSION__` placeholder substituted once at import time into a new `RENDERED_HTML` constant served for `/` and `/index.html`.
- Bumped `__version__` to `2.1.7` to match this release.

## v2.1.6 - 2026-06-23

- Fixed manual-buy custom take-profit percentages being silently overridden. Every open lot (including `manual-entry`/`manual-limit-buy` lots) was run through `enrich_lot_with_defensive_target`, which recomputes a target from the *global* `take_profit_pct` and clamps the lot's saved `target_price` down to `min(saved_target, defensive_target)`. Whenever a user manually set a custom profit percentage higher than the global default, the resulting `effective_target_price` — the value actually read by the grid strategy's lot selection and shown in the dashboard — silently fell back to the lower global-default target instead of the percentage the user chose.
- `enrich_lot_with_defensive_target` now special-cases `manual-*` lots: it returns the lot's own saved `target_price` as `effective_target_price` unchanged, with no global-config or lot-aging adjustment, matching how `TradingAgent._strategy_sell_floor` already protects manual lots at the sell-gate level.
- Added a regression test confirming a manual lot's custom profit target (set above the global default) survives enrichment unchanged.

## v2.1.5 - 2026-06-22

- Fixed the auto-agent freezing on an unsellable dust lot and halting all trading. A `dust` consolidation lot whose remaining quantity is below the exchange minimum (rounds to 0) but whose target price sits below the market price is picked by the grid strategy as a "ready to sell" lot every tick; the order is rejected as below minQty and skipped, and because the strategy returns that sell decision before evaluating anything else, the agent stops buying or selling entirely. This also froze the dashboard "recent orders" feed, since skipped orders are never written to the trade log.
- The dust lot (`level=dust`) is now excluded from the per-lot grid sell candidates via `TradingAgent._grid_strategy_lots`. Dust is only ever sold by the merge-sell path once it accumulates above the minimum notional and is profitable; it no longer participates in per-lot grid decisions.
- Added 2 regression tests (a dust crumb triggers a sell decision that would jam; the filter prevents it). Full suite passes.

## v2.1.4 - 2026-06-20

- Fixed automatic merge-sell trades never showing up in the trade log / dashboard "recent orders": `merge_sell_ready_lots` places the order and updates the ledger but never wrote to `trades_<symbol>.jsonl` itself, and the auto-agent's `once()` loop only ever called `_record_trade()` with the original (non-merge) decision. The dashboard's manual "merge sell dust" button already recorded correctly via `_record_manual_trade`; the automatic tick-triggered path never had an equivalent call. This bug was latent until v2.1.3 actually unblocked automatic merge-sell from firing — at which point the merges started executing for real but stayed invisible in the trade history.
- Added `TradingAgent._record_merge_sell_trade`, called from `once()` right after `_maybe_merge_sell_dust`, so a successful automatic merge sell now writes a `MERGE_SELL` record with the order, proceeds, and number of lots closed.
- Added 2 unit tests covering the recorded and not-recorded cases.

## v2.1.3 - 2026-06-19

- Fixed a second self-locking deadlock, this time on the sell side: the grid strategy always picks the single open lot with the *lowest* target price to sell each cycle (`min(profitable_lots, key=_lot_target_price)`). If that lot happens to be a dust lot (remaining quantity worth less than Binance's minNotional), the order gets skipped every cycle — and `_maybe_merge_sell_dust` was gated off whenever the cycle's decision was a SELL, regardless of whether that SELL actually filled. So a single unsellable dust lot with the lowest target price would permanently block both itself and every other lot that had already reached its own target, since merge-sell (the only mechanism that can combine dust with normal lots into one order above minNotional) never got a chance to run. Diagnosed and reported with exact log evidence by a user of the project — thank you.
- `_maybe_merge_sell_dust` now takes the actual `order_result` of the cycle's chosen decision. It only skips merge-sell when a SELL genuinely filled; if the chosen SELL was skipped (minNotional, minQty, balance mismatch, etc.), merge-sell still runs in the same cycle and can combine the stuck dust lot with any other ready lots into one order.
- Added two regression tests in `tests/test_agent_orders.py`: one reproducing the exact deadlock (skipped dust SELL unblocks merge-sell) and one confirming merge-sell is still correctly skipped when the chosen SELL actually fills.

## v2.1.2 - 2026-06-19

- Fixed a self-locking deadlock in the portfolio drawdown circuit breaker: once account drawdown reached 12%, `layered_risk` set `order_multiplier` to 0 for every strategy, including the defensive scalp and dip-buy strategies that exist specifically to average down during exactly this kind of drawdown. With every open lot underwater, no sell could realize profit to reduce the drawdown either, so the agent stayed completely frozen (observed: 30+ hours, 3500+ decision cycles, zero buys or sells).
- `LayeredRisk` now carries `limited_strategies`/`limited_order_multiplier`. When the 12% drawdown breaker fires, grid and swing additions stay fully blocked, but `scalp` and `dip` keep a 0.15x order-size allowance so the agent can still average down. The daily-loss-limit and price-break (>8%) circuit breakers remain a full, unconditional pause — those are real risk events, not deadlock conditions.
- Added unit tests in `tests/test_adaptive.py` covering both the carve-out (drawdown breaker) and the no-exception case (daily-loss breaker).

## v2.1.1 - 2026-06-15

- Added a dust-consolidation account. Partial sells rounded to the Binance step size often leave a remainder below the minimum order quantity (`MIN_QTY`, ~0.00001 BTC) that can never be sold on its own and keeps the lot open forever. Any open lot whose remaining quantity is below `MIN_QTY` is now folded into a single `dust` lot that accumulates quantity-weighted blended cost and buy fees and recomputes its target price; the original lot is marked closed (noted as folded into dust) so it shows up in closed batches.
- The dust lot is a normal open lot: once it grows past the minimum quantity and notional (~5 USDT) and the price covers its blended cost plus fees, it is sold by the normal strategy or by merge-sell.
- The agent sweeps dust every tick; manual sells and merge-sells also sweep immediately after a fill. Lots with an active limit-sell order are skipped to avoid conflicts.
- Added a `consolidate_dust` ledger method and 4 unit tests. Full suite passes.

## v2.1.0 - 2026-06-15

- Added a hard loss-protection guard to every manual sell path. Single-lot market sell, single-lot limit sell, one-click market sell, and one-click limit sell now reject any order priced below the lot break-even (cost plus both-side fees). Market sells add a slippage buffer; limit sells use the exact break-even since the fill price is guaranteed.
- Added merge-sell for dust lots. Multiple ready lots whose individual notional is below the Binance minimum (`MIN_NOTIONAL`, ~5 USDT) are combined into a single market order, then the fill is allocated back to each lot's ledger entry by remaining quantity in a waterfall so no included lot closes at a loss.
- Added a `合并卖碎屑` button to the open-lots toolbar in the dashboard, gated by the trading-toggle password with a confirmation prompt.
- The trading agent now auto-clears ready dust lots on idle ticks (only when trades are enabled and the main decision is not itself a sell); any error is swallowed so the core loop is never affected.
- Added a `merge_sell.py` module with a shared break-even helper aligned with the agent sell gate, plus 6 unit tests covering loss rejection, dust merging, no-dust skip, and not touching losing lots. Full suite passes.

## v2.0.2 - 2026-06-14

- Rebalanced the account/strategy and recent-order desktop columns with denser status rows and full-page order table height distribution.
- Aligned panel headings, table starts, and the order pager while preserving natural row heights on short final pages.

## v2.0.1 - 2026-06-14

- Added one-click cancellation for all active local limit orders with per-order result summaries.
- Made bulk market selling continue after individual lot failures and skip lots already reserved by limit orders.
- Prevented market and limit sell actions from silently selling only a small free-balance fragment of a ledger lot.
- Fixed break-even and safety-line calculations for partially closed lots by allocating original cost and buy fees proportionally to the remaining quantity.
- Added explicit remaining-quantity context to exit reasons for legacy partially closed lots.

## v2.0.0 - 2026-06-13

- Added explicit lot lifecycle states and per-lot exit explanations.
- Added an independent pending-order fill worker that links fills to lot IDs and closes the correct ledger lot.
- Added account/order reconciliation and SQLite decision auditing.
- Added market-regime detection, unified capital allocation, volatility/drawdown-aware sizing, and layered portfolio risk controls.
- Added adaptive profit targets and optional uptrend trailing exits behind an opt-in adaptive strategy flag.
- Added shadow decisions so the adaptive model can be compared without placing its orders.
- Added realistic backtest slippage, exchange minimums, order failures, and execution latency.
- Split order synchronization and reconciliation into independent services.

## v1.0.20 - 2026-06-13

- Fixed manual lots failing to auto-sell after their saved target was reached because the final gate reapplied the global profit target.
- Added a fee-aware market-slippage buffer to the final automatic sell guard.
- Removed zero-value auto-sell toggle events from the recent live order list.
- Added fill-based quote amount recovery and requested full Binance order responses.
- Recorded manual market trades using actual execution quote amounts when available.

## v1.0.19 - 2026-06-08

- Added a cross-process trading lock shared by the agent and dashboard so auto-sell changes, manual orders, pending-order synchronization, exchange submission, and ledger updates cannot race each other.
- Replaced the browser-side per-lot bulk auto-sell loop with one atomic server-side update.
- Added a final execution-time ledger reload that blocks stale sells when auto-sell was disabled, a limit sell is pending, the lot quantity changed, cost data is missing, or the current strategy profit floor is not met.
- Added unique Binance client order IDs and recovery-by-ID after ambiguous network failures; non-idempotent order requests are no longer blindly retried.
- Process partial fills before releasing canceled orders and mark terminal zero-fill orders complete instead of querying them forever.
- Fixed partial-close accounting to accumulate proceeds and sell fees while proportionally allocating the original buy fee.
- Clarified that disabling script auto-sell does not cancel limit orders already resting at Binance.

## v1.0.18 - 2026-06-08

- Fixed a JavaScript syntax error caused by `\n` inside a Python triple-quoted string being rendered as a literal newline inside a JS single-quoted string, which silently broke all JavaScript execution on the dashboard page.
- Added an `onclick` fallback for the login button and a JS-loaded indicator in the login status text.
- Fixed the defensive scalp strategy ignoring the `auto_sell` flag: batches manually set to "取消自动卖" were still being sold when the scalp target price was reached.
- Added pagination to the pending limit orders table (6 per page) to prevent unbounded stacking.

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
