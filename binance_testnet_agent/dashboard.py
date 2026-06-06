from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import secrets
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any

from .binance_client import BinanceAPIError, BinanceSpotClient
from .config import AgentConfig
from .defensive import enrich_lot_with_defensive_target, enrich_lots_with_defensive_targets, evaluate_defensive_mode
from .defensive_scalp import DefensiveScalpStrategy, is_scalp_lot
from .ledger import PositionLedger
from .local_backtest import BacktestConfig, run_scenarios, run_backtest
from .portfolio import metrics_asdict, portfolio_metrics, reset_baseline
from .sizing import position_sizing
from .storage import SQLiteJsonListStore
from .strategy import GridStrategy, MarketSnapshot
from .swing import SwingStrategy, is_swing_lot, split_lots
from .trend_guard import TrendGuard


FAVICON_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="bg" x1="10" y1="6" x2="56" y2="58" gradientUnits="userSpaceOnUse">
      <stop stop-color="#0f172a"/>
      <stop offset=".58" stop-color="#123242"/>
      <stop offset="1" stop-color="#065f46"/>
    </linearGradient>
    <linearGradient id="coin" x1="18" y1="12" x2="49" y2="48" gradientUnits="userSpaceOnUse">
      <stop stop-color="#facc15"/>
      <stop offset="1" stop-color="#f59e0b"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="url(#bg)"/>
  <circle cx="32" cy="32" r="21" fill="none" stroke="url(#coin)" stroke-width="4"/>
  <path d="M18 42 27 34l7 5 13-17" fill="none" stroke="#34d399" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M47 22v12h-12" fill="none" stroke="#34d399" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="20" y="23" width="5" height="18" rx="2.5" fill="#f8fafc"/>
  <rect x="31" y="17" width="5" height="28" rx="2.5" fill="#f8fafc"/>
  <rect x="42" y="28" width="5" height="17" rx="2.5" fill="#f8fafc"/>
</svg>
"""

CONFIG_SETTING_FIELDS: tuple[dict[str, str], ...] = (
    {"key": "execute_trades", "env": "EXECUTE_TRADES", "label": "默认自动交易", "category": "交易基础", "kind": "bool"},
    {"key": "auto_position_sizing", "env": "AUTO_POSITION_SIZING", "label": "按账户资金自动分档", "category": "资金池与仓位", "kind": "bool"},
    {"key": "order_quote_size", "env": "ORDER_QUOTE_SIZE", "label": "手动固定单笔金额", "category": "资金池与仓位", "kind": "float"},
    {"key": "max_position_quote", "env": "MAX_POSITION_QUOTE", "label": "手动固定最大持仓", "category": "资金池与仓位", "kind": "float"},
    {"key": "grid_step_pct", "env": "GRID_STEP_PCT", "label": "网格买入间距", "category": "交易基础", "kind": "float"},
    {"key": "trading_fee_rate", "env": "TRADING_FEE_RATE", "label": "手续费率", "category": "交易基础", "kind": "float"},
    {"key": "max_floating_loss_quote", "env": "MAX_FLOATING_LOSS_QUOTE", "label": "最大浮亏买入保护", "category": "风控", "kind": "float"},
    {"key": "rapid_drop_pause_pct", "env": "RAPID_DROP_PAUSE_PCT", "label": "急跌暂停阈值", "category": "风控", "kind": "float"},
    {"key": "large_drop_pause_pct", "env": "LARGE_DROP_PAUSE_PCT", "label": "大跌反弹保护阈值", "category": "风控", "kind": "float"},
    {"key": "rebound_buy_pct", "env": "REBOUND_BUY_PCT", "label": "急跌后反弹确认", "category": "风控", "kind": "float"},
    {"key": "price_anomaly_pct", "env": "PRICE_ANOMALY_PCT", "label": "价格源异常阈值", "category": "风控", "kind": "float"},
    {"key": "defensive_mode", "env": "DEFENSIVE_MODE", "label": "防守模式", "category": "防守模式", "kind": "bool"},
    {"key": "defensive_position_usage_trigger", "env": "DEFENSIVE_POSITION_USAGE_TRIGGER", "label": "持仓占用触发防守", "category": "防守模式", "kind": "float"},
    {"key": "defensive_floating_loss_quote", "env": "DEFENSIVE_FLOATING_LOSS_QUOTE", "label": "浮亏触发防守", "category": "防守模式", "kind": "float"},
    {"key": "defensive_recent_drawdown_pct", "env": "DEFENSIVE_RECENT_DRAWDOWN_PCT", "label": "近期回撤触发防守", "category": "防守模式", "kind": "float"},
    {"key": "defensive_normal_add_on_step_pct", "env": "DEFENSIVE_NORMAL_ADD_ON_STEP_PCT", "label": "正常补仓间距", "category": "防守模式", "kind": "float"},
    {"key": "defensive_add_on_step_pct", "env": "DEFENSIVE_ADD_ON_STEP_PCT", "label": "防守补仓间距", "category": "防守模式", "kind": "float"},
    {"key": "defensive_aged_lot_days_1", "env": "DEFENSIVE_AGED_LOT_DAYS_1", "label": "老仓降目标天数 1", "category": "防守模式", "kind": "int"},
    {"key": "defensive_aged_lot_profit_pct_1", "env": "DEFENSIVE_AGED_LOT_PROFIT_PCT_1", "label": "老仓目标利润 1", "category": "防守模式", "kind": "float"},
    {"key": "defensive_aged_lot_days_2", "env": "DEFENSIVE_AGED_LOT_DAYS_2", "label": "老仓降目标天数 2", "category": "防守模式", "kind": "int"},
    {"key": "defensive_aged_lot_profit_pct_2", "env": "DEFENSIVE_AGED_LOT_PROFIT_PCT_2", "label": "老仓目标利润 2", "category": "防守模式", "kind": "float"},
    {"key": "trend_guard", "env": "TREND_GUARD", "label": "趋势保护", "category": "趋势保护", "kind": "bool"},
    {"key": "trend_normal_pool_pct", "env": "TREND_NORMAL_POOL_PCT", "label": "普通网格资金池比例", "category": "趋势保护", "kind": "float"},
    {"key": "trend_dip_pool_pct", "env": "TREND_DIP_POOL_PCT", "label": "下跌抄底资金池比例", "category": "趋势保护", "kind": "float"},
    {"key": "trend_dip_order_quote", "env": "TREND_DIP_ORDER_QUOTE", "label": "下跌抄底单笔上限", "category": "趋势保护", "kind": "float"},
    {"key": "trend_rebound_pct", "env": "TREND_REBOUND_PCT", "label": "抄底反弹确认", "category": "趋势保护", "kind": "float"},
    {"key": "trend_kline_interval", "env": "TREND_KLINE_INTERVAL", "label": "趋势 K 线周期", "category": "趋势保护", "kind": "str"},
    {"key": "trend_kline_limit", "env": "TREND_KLINE_LIMIT", "label": "趋势 K 线数量", "category": "趋势保护", "kind": "int"},
    {"key": "swing_strategy", "env": "SWING_STRATEGY", "label": "波段策略", "category": "波段策略", "kind": "bool"},
    {"key": "swing_allocation_pct", "env": "SWING_ALLOCATION_PCT", "label": "波段资金池比例", "category": "波段策略", "kind": "float"},
    {"key": "swing_min_order_quote", "env": "SWING_MIN_ORDER_QUOTE", "label": "波段单笔最小金额", "category": "波段策略", "kind": "float"},
    {"key": "swing_max_order_quote", "env": "SWING_MAX_ORDER_QUOTE", "label": "波段单笔最大金额", "category": "波段策略", "kind": "float"},
    {"key": "swing_add_step_pct", "env": "SWING_ADD_STEP_PCT", "label": "波段补仓间距", "category": "波段策略", "kind": "float"},
    {"key": "swing_min_band_pct", "env": "SWING_MIN_BAND_PCT", "label": "波段最小带宽", "category": "波段策略", "kind": "float"},
    {"key": "swing_max_band_pct", "env": "SWING_MAX_BAND_PCT", "label": "波段最大带宽", "category": "波段策略", "kind": "float"},
    {"key": "swing_manual_center_price", "env": "SWING_MANUAL_CENTER_PRICE", "label": "波段手动中枢", "category": "波段策略", "kind": "float"},
    {"key": "swing_kline_interval", "env": "SWING_KLINE_INTERVAL", "label": "波段 K 线周期", "category": "波段策略", "kind": "str"},
    {"key": "swing_kline_limit", "env": "SWING_KLINE_LIMIT", "label": "波段 K 线数量", "category": "波段策略", "kind": "int"},
    {"key": "defensive_scalp", "env": "DEFENSIVE_SCALP", "label": "防守震荡小仓", "category": "防守震荡", "kind": "bool"},
    {"key": "defensive_scalp_allocation_pct", "env": "DEFENSIVE_SCALP_ALLOCATION_PCT", "label": "震荡资金池比例", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_order_pct", "env": "DEFENSIVE_SCALP_ORDER_PCT", "label": "震荡单笔比例", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_min_order_quote", "env": "DEFENSIVE_SCALP_MIN_ORDER_QUOTE", "label": "震荡单笔最小金额", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_max_order_quote", "env": "DEFENSIVE_SCALP_MAX_ORDER_QUOTE", "label": "震荡单笔最大金额", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_buy_drop_pct", "env": "DEFENSIVE_SCALP_BUY_DROP_PCT", "label": "震荡低吸偏移", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_take_profit_pct", "env": "DEFENSIVE_SCALP_TAKE_PROFIT_PCT", "label": "震荡止盈空间", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_add_step_pct", "env": "DEFENSIVE_SCALP_ADD_STEP_PCT", "label": "震荡加仓间距", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_min_range_pct", "env": "DEFENSIVE_SCALP_MIN_RANGE_PCT", "label": "震荡最小波动", "category": "防守震荡", "kind": "float"},
    {"key": "defensive_scalp_max_range_pct", "env": "DEFENSIVE_SCALP_MAX_RANGE_PCT", "label": "震荡最大波动", "category": "防守震荡", "kind": "float"},
)


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Binance Spot Live Agent</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="shortcut icon" href="/favicon.svg">
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bg: #eef4f7;
      --bg-soft: rgba(255,255,255,.96);
      --panel: rgba(255,255,255,.96);
      --panel-strong: #ffffff;
      --text: #17212b;
      --muted: #607080;
      --line: #d6e0ea;
      --line-soft: #e5ebf2;
      --accent: #0ea5e9;
      --accent-2: #10b981;
      --button: #0f172a;
      --good: #e11d48;
      --bad: #059669;
      --switch-on: #059669;
      --switch-off: #e11d48;
      --warn: #b7791f;
      --shadow: 0 18px 42px rgba(15, 23, 42, .08);
      --page-art: linear-gradient(150deg, rgba(14,165,233,.11), rgba(255,255,255,.58) 42%, rgba(16,185,129,.10));
    }
    body[data-theme="night"] {
      color-scheme: dark;
      --bg: #080d14; --panel: rgba(15, 23, 42, .94); --panel-strong: #111827; --text: #e5eef8; --muted: #8fa3b8; --line: #253449; --line-soft: #1e293b; --accent: #f0b90b; --accent-2: #22c55e; --button: #f0b90b; --good: #ef4444; --bad: #22c55e; --switch-on: #22c55e; --switch-off: #ef4444; --warn: #f59e0b; --shadow: 0 22px 52px rgba(0, 0, 0, .28); --page-art: radial-gradient(circle at 12% 16%, rgba(240,185,11,.16), transparent 28%), radial-gradient(circle at 86% 12%, rgba(34,197,94,.12), transparent 26%), linear-gradient(150deg, #08111f, #0f172a 55%, #061413);
    }
    body[data-theme="rift"] {
      --bg: #07131f; --panel: rgba(9, 22, 36, .92); --panel-strong: #0d2035; --text: #e7f8ff; --muted: #9bb8c9; --line: #1f4b64; --line-soft: #183247; --accent: #38bdf8; --accent-2: #c084fc; --button: #155e75; --good: #fb7185; --bad: #34d399; --switch-on: #34d399; --switch-off: #fb7185; --warn: #facc15; --shadow: 0 24px 58px rgba(2, 6, 23, .32); --page-art: radial-gradient(circle at 20% 10%, rgba(56,189,248,.22), transparent 24%), radial-gradient(circle at 82% 22%, rgba(192,132,252,.20), transparent 22%), linear-gradient(140deg, #061826, #101534 62%, #061c1b);
    }
    body[data-theme="anime"] {
      --bg: #fff4f8; --panel: rgba(255,255,255,.94); --panel-strong: #ffffff; --text: #2a2433; --muted: #7a6878; --line: #f2c7d7; --line-soft: #f7dfea; --accent: #ec4899; --accent-2: #06b6d4; --button: #be185d; --good: #e11d48; --bad: #0f9f80; --switch-on: #0f9f80; --switch-off: #e11d48; --warn: #d97706; --shadow: 0 20px 48px rgba(190, 24, 93, .12); --page-art: radial-gradient(circle at 10% 18%, rgba(236,72,153,.18), transparent 24%), radial-gradient(circle at 88% 18%, rgba(6,182,212,.16), transparent 24%), linear-gradient(145deg, #fff4f8, #f0fdff 68%, #fff7ed);
    }
    body[data-theme="stage"] {
      --bg: #fff8e8; --panel: rgba(255,252,244,.95); --panel-strong: #fffdf8; --text: #312516; --muted: #7a6850; --line: #ead7b5; --line-soft: #f2e4ca; --accent: #f59e0b; --accent-2: #10b981; --button: #7c2d12; --good: #dc2626; --bad: #15803d; --switch-on: #15803d; --switch-off: #dc2626; --warn: #b45309; --shadow: 0 20px 46px rgba(124, 45, 18, .12); --page-art: radial-gradient(circle at 15% 15%, rgba(245,158,11,.20), transparent 24%), radial-gradient(circle at 85% 12%, rgba(16,185,129,.14), transparent 22%), linear-gradient(145deg, #fff8e8, #fff1f2 60%, #ecfeff);
    }
    body[data-theme="forest"] {
      --bg: #f3fbf5; --panel: rgba(255,255,255,.94); --panel-strong: #ffffff; --text: #17251f; --muted: #62776d; --line: #cfe2d6; --line-soft: #e3efe7; --accent: #16a34a; --accent-2: #0ea5e9; --button: #14532d; --good: #dc2626; --bad: #15803d; --switch-on: #15803d; --switch-off: #dc2626; --warn: #a16207; --shadow: 0 20px 48px rgba(20,83,45,.10); --page-art: radial-gradient(circle at 10% 18%, rgba(22,163,74,.16), transparent 24%), radial-gradient(circle at 88% 12%, rgba(14,165,233,.12), transparent 22%), linear-gradient(145deg, #f3fbf5, #eefdf8 64%, #f8fafc);
    }
    body[data-theme="ocean"] {
      --bg: #edf7ff; --panel: rgba(255,255,255,.94); --panel-strong: #ffffff; --text: #162231; --muted: #607184; --line: #c6dbef; --line-soft: #dcebf7; --accent: #0284c7; --accent-2: #14b8a6; --button: #075985; --good: #e11d48; --bad: #0f9f80; --switch-on: #0f9f80; --switch-off: #e11d48; --warn: #b45309; --shadow: 0 20px 48px rgba(2,132,199,.12); --page-art: radial-gradient(circle at 16% 12%, rgba(2,132,199,.18), transparent 25%), radial-gradient(circle at 86% 22%, rgba(20,184,166,.16), transparent 24%), linear-gradient(145deg, #edf7ff, #f0fdfa 68%, #ffffff);
    }
    body[data-theme="sunset"] {
      --bg: #fff7ed; --panel: rgba(255,255,255,.95); --panel-strong: #ffffff; --text: #2f2218; --muted: #806b5b; --line: #efd4bd; --line-soft: #f6e5d5; --accent: #ea580c; --accent-2: #db2777; --button: #9a3412; --good: #dc2626; --bad: #15803d; --switch-on: #15803d; --switch-off: #dc2626; --warn: #b45309; --shadow: 0 20px 48px rgba(154,52,18,.12); --page-art: radial-gradient(circle at 12% 14%, rgba(234,88,12,.18), transparent 24%), radial-gradient(circle at 88% 15%, rgba(219,39,119,.14), transparent 22%), linear-gradient(145deg, #fff7ed, #fff1f2 66%, #f0f9ff);
    }
    body[data-theme="mono"] {
      --bg: #f6f7f9; --panel: rgba(255,255,255,.94); --panel-strong: #ffffff; --text: #171b22; --muted: #657080; --line: #d5dbe3; --line-soft: #e7ebf0; --accent: #475569; --accent-2: #0891b2; --button: #111827; --good: #be123c; --bad: #047857; --switch-on: #047857; --switch-off: #be123c; --warn: #a16207; --shadow: 0 18px 42px rgba(17,24,39,.09); --page-art: linear-gradient(145deg, #f6f7f9, #eef4f7 72%, #ffffff);
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); overflow-x: hidden; transition: background .28s ease, color .28s ease; }
    body::before { content: ""; position: fixed; inset: 0; pointer-events: none; background: var(--page-art); }
    main { position: relative; width: min(1200px, calc(100% - 260px)); margin: 0 auto; padding: 22px 0 40px; }
    body.layout-wide main { width: min(1440px, calc(100% - 120px)); }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 14px; }
    h1 { margin: 0; font-size: 30px; font-weight: 760; letter-spacing: 0; }
    .muted { color: var(--muted); font-size: 14px; }
    .top-board { display: grid; grid-template-columns: 1.15fr 1fr 1fr 1.15fr; gap: 12px; align-items: stretch; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: var(--shadow); }
    .metric-card { position: relative; overflow: hidden; min-height: 154px; }
    .metric-card::after { content: ""; position: absolute; inset: auto -42px -64px auto; width: 160px; height: 160px; border-radius: 999px; background: rgba(14, 165, 233, .10); }
    .label { color: var(--muted); font-size: 13px; font-weight: 760; margin-bottom: 8px; }
    .value { font-size: 25px; font-weight: 760; overflow-wrap: anywhere; letter-spacing: 0; }
    .hero-symbol { font-size: 18px; color: var(--muted); font-weight: 760; margin-bottom: 12px; }
    .hero-price { font-size: 42px; line-height: 1; font-weight: 800; letter-spacing: 0; margin-bottom: 14px; }
    .signal-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .signal-pill { display: inline-flex; align-items: center; justify-content: center; min-height: 34px; padding: 0 13px; border-radius: 999px; background: color-mix(in srgb, var(--accent-2) 14%, transparent); color: var(--good); font-weight: 800; }
    .capital-stack { display: contents; }
    .capital-stack .panel { min-height: 154px; }
    .capital-value { font-size: 28px; font-weight: 800; margin-bottom: 8px; }
    .pnl-card .value { font-size: 24px; margin-bottom: 12px; }
    .control-panel { display: grid; align-content: start; gap: 14px; }
    .control-row { display: grid; grid-template-columns: 1fr; gap: 9px; padding-bottom: 14px; border-bottom: 1px solid var(--line-soft); }
    .control-row:last-child { padding-bottom: 0; border-bottom: 0; }
    .button-stack { display: flex; flex-wrap: wrap; gap: 8px; align-items: stretch; }
    .button-stack button { min-height: 40px; }
    .strategy-bar { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px; margin-top: 12px; overflow: hidden; border: 1px solid var(--line); border-radius: 8px; background: var(--line-soft); box-shadow: var(--shadow); }
    .strat-item { display: grid; gap: 3px; padding: 10px 14px; background: var(--panel); min-width: 0; }
    .strat-label { color: var(--muted); font-size: 11px; font-weight: 800; }
    .strat-value { color: var(--text); font-size: 14px; font-weight: 850; overflow-wrap: anywhere; }
    .strat-on { color: var(--switch-on); }
    .strat-off { color: var(--muted); }
    .profit { color: var(--good); }
    .loss { color: var(--bad); }
    .warn { color: var(--warn); }
    .chart-wrap { height: 460px; padding: 0; overflow: hidden; position: relative; }
    .chart-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px 0; }
    .range-tabs { display: inline-flex; gap: 6px; padding: 4px; background: color-mix(in srgb, var(--accent) 10%, var(--panel-strong)); border-radius: 8px; overflow-x: auto; }
    .range-tabs button { border: 0; border-radius: 6px; background: transparent; color: var(--muted); height: 30px; padding: 0 11px; font-weight: 700; cursor: pointer; white-space: nowrap; }
    .range-tabs button.active { background: var(--panel-strong); color: var(--text); box-shadow: 0 2px 8px rgba(15,23,42,.10); }
    canvas { width: 100%; height: calc(100% - 70px); display: block; cursor: crosshair; }
    .action-button { border: 0; border-radius: 8px; min-height: 38px; padding: 0 14px; background: var(--button); color: #fff; font-weight: 800; cursor: pointer; }
    body[data-theme="night"] .action-button { color: #111827; }
    .action-button.off { background: var(--switch-off); color: #fff; }
    .action-button.on { background: var(--switch-on); color: #fff; }
    .tooltip { position: absolute; z-index: 5; pointer-events: none; display: none; min-width: 188px; padding: 10px 11px; border-radius: 8px; background: rgba(15, 23, 42, .94); color: #f8fafc; font-size: 12px; box-shadow: 0 12px 24px rgba(15,23,42,.22); line-height: 1.5; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { text-align: left; padding: 11px 10px; border-bottom: 1px solid var(--line-soft); font-size: 14px; vertical-align: top; }
    th { color: var(--muted); font-weight: 650; }
    tr:last-child td, tr:last-child th { border-bottom: 0; }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; align-items: stretch; }
    .split .panel { min-height: 430px; display: flex; flex-direction: column; }
    .panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
    .panel-title { font-size: 16px; font-weight: 800; color: var(--text); }
    .account-table { margin-top: 4px; }
    .account-summary { display: grid; gap: 12px; margin-top: 8px; }
    .summary-card { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--line-soft); border-radius: 8px; background: color-mix(in srgb, var(--panel-strong) 88%, transparent); }
    .summary-card strong { font-size: 18px; overflow-wrap: anywhere; }
    .summary-card span { color: var(--muted); font-size: 12px; font-weight: 750; }
    .status-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }
    .status-item { padding: 10px; border-radius: 8px; background: color-mix(in srgb, var(--accent) 8%, transparent); border: 1px solid var(--line-soft); }
    .status-item .k { color: var(--muted); font-size: 12px; font-weight: 800; margin-bottom: 5px; }
    .status-item .v { font-weight: 760; line-height: 1.45; overflow-wrap: anywhere; }
    .account-table th { width: 150px; color: var(--muted); font-size: 13px; }
    .account-table td { color: var(--text); font-weight: 650; }
    .orders-table th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .orders-table td { height: 54px; vertical-align: middle; }
    .orders-panel .table-scroll { flex: 1; }
    .table-scroll { overflow-x: auto; }
    .pager { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 10px; color: #687789; font-size: 13px; }
    .pager button { border: 1px solid var(--line); background: var(--panel-strong); color: var(--text); border-radius: 6px; height: 30px; min-width: 34px; padding: 0 10px; font-weight: 700; cursor: pointer; }
    .pager button:disabled { opacity: .45; cursor: not-allowed; }
    .badge { display: inline-flex; align-items: center; min-height: 26px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--accent) 10%, var(--panel-strong)); color: var(--muted); font-weight: 700; font-size: 13px; }
    .header-actions { display: flex; gap: 10px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
    .modal { position: fixed; inset: 0; z-index: 20; display: none; align-items: center; justify-content: center; padding: 20px; background: rgba(15, 23, 42, .38); }
    .modal.open { display: flex; }
    .modal-panel { width: min(820px, 100%); max-height: 88vh; overflow-y: auto; background: var(--panel-strong); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 24px 60px rgba(15,23,42,.24); padding: 0 18px 18px; }
    .modal-head { position: sticky; top: 0; z-index: 2; display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 0 -18px 12px; padding: 16px 18px 12px; background: var(--panel-strong); border-bottom: 1px solid var(--line-soft); }
    .modal-head h2 { margin: 0; font-size: 20px; letter-spacing: 0; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .field label { display: block; color: #687789; font-size: 13px; font-weight: 700; margin-bottom: 6px; }
    .field input, .field select { width: 100%; height: 38px; border: 1px solid var(--line); border-radius: 7px; padding: 0 10px; font: inherit; background: var(--panel-strong); color: var(--text); }
    #configSettings { margin-top: 14px; }
    .settings-section { margin-top: 16px; padding: 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: color-mix(in srgb, var(--panel-strong) 88%, transparent); }
    .settings-section:first-of-type { margin-top: 0; }
    .settings-title { margin: 0 0 10px; color: var(--text); font-size: 15px; font-weight: 850; }
    .settings-help { margin: -4px 0 12px; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .settings-status { margin-top: 12px; padding: 11px 12px; border: 1px solid var(--line-soft); border-radius: 8px; background: color-mix(in srgb, var(--panel-strong) 88%, transparent); color: var(--muted); font-size: 13px; line-height: 1.5; }
    .settings-status.success { border-color: color-mix(in srgb, var(--switch-on) 48%, var(--line)); background: color-mix(in srgb, var(--switch-on) 10%, var(--panel-strong)); color: var(--switch-on); font-weight: 800; }
    .settings-status.error { border-color: color-mix(in srgb, var(--switch-off) 48%, var(--line)); background: color-mix(in srgb, var(--switch-off) 10%, var(--panel-strong)); color: var(--switch-off); font-weight: 800; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
    .settings-modal-actions { position: sticky; bottom: -18px; z-index: 3; margin: 14px -18px -18px; padding: 12px 18px; background: var(--panel-strong); border-top: 1px solid var(--line-soft); box-shadow: 0 -12px 24px rgba(15,23,42,.06); }
    .secondary-button { border: 1px solid var(--line); border-radius: 8px; min-height: 38px; padding: 0 13px; background: var(--panel-strong); color: var(--text); font-weight: 800; cursor: pointer; }
    .inline-controls { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; align-items: end; }
    .inline-controls .field input, .inline-controls .field select { height: 36px; }
    .result-note { margin-top: 12px; padding: 12px; border-radius: 8px; background: color-mix(in srgb, var(--accent-2) 12%, transparent); color: var(--good); font-weight: 750; }
    .floating-dock { position: fixed; right: 18px; top: 50%; z-index: 18; display: flex; flex-direction: row; align-items: center; gap: 10px; pointer-events: none; transform: translateY(-50%); }
    .dock-rail { display: grid; gap: 8px; pointer-events: auto; }
    .dock-fab { width: 48px; height: 48px; border-radius: 10px; border: 0; background: color-mix(in srgb, var(--accent) 84%, #38bdf8); color: #fff; box-shadow: 0 10px 24px color-mix(in srgb, var(--accent) 24%, transparent); cursor: pointer; font-size: 21px; font-weight: 900; pointer-events: auto; transition: transform .16s ease, filter .16s ease; }
    .dock-fab:hover { transform: translateX(-3px); filter: brightness(1.06); }
    .theme-icon { position: relative; display: inline-block; width: 23px; height: 23px; border: 3px solid #fff; border-radius: 50%; }
    .theme-icon::before { content: ""; position: absolute; width: 6px; height: 6px; left: 3px; top: 3px; border-radius: 50%; background: #facc15; box-shadow: 8px 0 #fb7185, 0 8px #34d399; }
    .theme-icon::after { content: ""; position: absolute; right: -4px; bottom: -3px; width: 9px; height: 7px; border-radius: 0 0 9px 9px; background: inherit; transform: rotate(-35deg); }
    body[data-theme="night"] .dock-fab { color: #111827; }
    .theme-drawer { position: absolute; right: 58px; top: 50%; width: min(320px, calc(100vw - 92px)); padding: 12px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel); box-shadow: var(--shadow); backdrop-filter: blur(10px); opacity: 0; transform: translateY(-50%) translateX(10px) scale(.98); transform-origin: center right; pointer-events: none; transition: opacity .18s ease, transform .18s ease; }
    .floating-dock.open .theme-drawer { opacity: 1; transform: translateY(-50%) translateX(0) scale(1); pointer-events: auto; }
    .theme-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; font-weight: 850; }
    .theme-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .theme-chip { min-height: 34px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-strong); color: var(--text); font-weight: 760; cursor: pointer; }
    .theme-chip.active { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 18%, transparent); }
    .theme-toggle { width: 100%; margin-top: 8px; }
    .mascot { position: fixed; left: 8px; bottom: 0; z-index: 17; display: flex; align-items: flex-end; gap: 10px; pointer-events: none; }
    .mascot.hidden { display: none; }
    .mascot-figure { width: 112px; height: 168px; position: relative; filter: drop-shadow(0 18px 28px rgba(15,23,42,.18)); }
    .mascot-figure img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
    .mascot-fallback { display: none; place-items: center; width: 100%; height: 100%; border-radius: 8px 8px 0 0; background: var(--panel); border: 1px solid var(--line); color: var(--muted); font-size: 12px; text-align: center; padding: 10px; }
    .mascot-figure.failed img { display: none; }
    .mascot-figure.failed .mascot-fallback { display: grid; }
    .mascot-bubble { max-width: 270px; margin-bottom: 52px; padding: 10px 12px; border-radius: 8px; background: var(--panel-strong); border: 1px solid var(--line); color: var(--text); box-shadow: var(--shadow); font-size: 13px; line-height: 1.5; pointer-events: none; opacity: 0; transform: translateY(8px); transition: opacity .18s ease, transform .18s ease; }
    .mascot.speaking .mascot-bubble { opacity: 1; transform: translateY(0); }
    .notice-toast { position: fixed; left: 50%; top: 22px; z-index: 40; width: min(520px, calc(100vw - 28px)); padding: 14px 48px 14px 16px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel-strong); color: var(--text); box-shadow: 0 22px 55px rgba(15,23,42,.22); opacity: 0; transform: translate(-50%, -18px); pointer-events: none; transition: opacity .2s ease, transform .2s ease; font-weight: 800; line-height: 1.5; }
    .notice-toast.show { opacity: 1; transform: translate(-50%, 0); pointer-events: auto; }
    .notice-toast.success { border-color: var(--switch-on); box-shadow: 0 22px 55px color-mix(in srgb, var(--switch-on) 20%, transparent); }
    .notice-toast.error { border-color: var(--switch-off); box-shadow: 0 22px 55px color-mix(in srgb, var(--switch-off) 20%, transparent); }
    .notice-toast button { position: absolute; right: 10px; top: 9px; width: 30px; height: 30px; border: 0; border-radius: 6px; background: transparent; color: inherit; font-size: 20px; cursor: pointer; }
    .lot-id { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; font-weight: 700; }
    .lot-actions { display: grid; grid-template-columns: repeat(4, max-content); gap: 6px; align-items: center; }
    .lot-actions button { min-height: 34px; padding: 0 10px; white-space: nowrap; font-size: 12px; }
    .dialog-message { color: var(--muted); line-height: 1.6; margin-bottom: 12px; white-space: pre-wrap; }
    .dialog-fields { display: grid; gap: 12px; }
    .comments-panel { margin-top: 14px; }
    .comment-form { display: grid; grid-template-columns: 180px 1fr auto; gap: 10px; align-items: end; margin-top: 12px; }
    .comment-form textarea { width: 100%; min-height: 76px; resize: vertical; border: 1px solid var(--line); border-radius: 7px; padding: 10px; font: inherit; background: var(--panel-strong); color: var(--text); }
    .comment-list { display: grid; gap: 10px; margin-top: 16px; }
    .comment-item { padding: 13px 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: color-mix(in srgb, var(--panel-strong) 92%, transparent); }
    .comment-meta { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; }
    .comment-name { color: var(--text); font-weight: 850; }
    .author-badge { color: #fff; background: var(--accent); border-radius: 999px; padding: 2px 7px; font-weight: 800; }
    .comment-body { margin-top: 7px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
    .comment-replies { display: grid; gap: 8px; margin: 10px 0 0 24px; }
    .comment-reply { border-left: 3px solid var(--accent); padding: 8px 10px; background: color-mix(in srgb, var(--accent) 6%, transparent); border-radius: 0 6px 6px 0; }
    .comment-actions { margin-left: auto; }
    .comment-actions button { min-height: 28px; padding: 0 9px; font-size: 12px; }
    .danmaku-layer { position: fixed; inset: 74px 0 auto; height: 180px; z-index: 16; overflow: hidden; pointer-events: none; }
    .danmaku-layer.hidden { display: none; }
    .danmaku-item { position: absolute; right: -50vw; max-width: 520px; padding: 7px 12px; border-radius: 999px; color: #fff; background: rgba(15,23,42,.72); box-shadow: 0 6px 18px rgba(15,23,42,.16); font-size: 13px; white-space: nowrap; pointer-events: auto; animation: danmaku-move var(--danmaku-duration, 18s) linear forwards; }
    .danmaku-item:hover { animation-play-state: paused; background: rgba(15,23,42,.92); }
    @keyframes danmaku-move { from { transform: translateX(0); } to { transform: translateX(calc(-100vw - 120%)); } }
    @media (max-width: 1100px) {
      main { width: 100%; padding: 18px; overflow-x: hidden; }
      body.layout-wide main { width: 100%; }
      .top-board { grid-template-columns: 1fr; }
      .capital-stack { display: contents; }
      .split { grid-template-columns: 1fr; }
      .inline-controls { grid-template-columns: 1fr 1fr; }
      header { display: block; }
      .hero-price { font-size: 38px; }
      .mascot-figure { width: 70px; height: 105px; }
      .mascot-bubble { max-width: 190px; margin-bottom: 38px; font-size: 11px; }
      .table-scroll { overflow-x: visible; }
      table:not(.account-table) { display: block; margin-top: 10px; }
      table:not(.account-table) thead { display: none; }
      table:not(.account-table) tbody { display: grid; gap: 10px; }
      table:not(.account-table) tr { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; padding: 12px; border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; box-shadow: 0 8px 22px rgba(15, 23, 42, .06); }
      table:not(.account-table) td { display: flex; flex-direction: column; gap: 3px; min-width: 0; border: 0; padding: 0; white-space: normal; overflow-wrap: anywhere; font-size: 13px; line-height: 1.35; }
      table:not(.account-table) td::before { content: attr(data-label); color: #738397; font-size: 11px; font-weight: 800; }
      table:not(.account-table) td[colspan] { grid-column: 1 / -1; display: block; }
      table:not(.account-table) td[colspan]::before { display: none; }
      table:not(.account-table) td[data-label="操作"] { grid-column: 1 / -1; }
      table:not(.account-table) td[data-label="操作"] > div { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
      table:not(.account-table) td[data-label="操作"] button { width: 100%; padding: 0 8px; }
      table:not(.account-table) td[data-label="预计卖价"],
      table:not(.account-table) td[data-label="手动价格"],
      table:not(.account-table) td[data-label="净利润"],
      table:not(.account-table) td[data-label="浮盈亏"] { font-weight: 750; }
    }
    @media (max-width: 560px) {
      main { padding: 14px; }
      header { margin-bottom: 12px; }
      h1 { font-size: 25px; line-height: 1.08; }
      .header-actions { justify-content: flex-start; margin-top: 12px; }
      .panel { padding: 14px; }
      .metric-card { min-height: auto; }
      .hero-symbol { font-size: 15px; margin-bottom: 8px; }
      .hero-price { font-size: 34px; margin-bottom: 12px; }
      .value { font-size: 21px; }
      .capital-value { font-size: 25px; }
      .pnl-card .value { font-size: 22px; }
      .form-grid, .settings-grid, .inline-controls { grid-template-columns: 1fr; }
      .chart-wrap { height: 330px; }
      .chart-head { display: block; padding: 12px 12px 0; }
      .range-tabs { width: 100%; margin-top: 10px; overflow-x: auto; }
      .range-tabs button { flex: 1 0 auto; padding: 0 10px; }
      th, td { padding: 10px 8px; font-size: 13px; white-space: nowrap; }
      .account-table th, .account-table td { white-space: normal; }
      .account-table th { width: 118px; }
      .secondary-button, .action-button { min-height: 40px; }
      .modal { padding: 12px; align-items: flex-start; }
      .modal-panel { max-height: calc(100vh - 24px); padding: 14px; }
      .modal-actions { flex-direction: column-reverse; }
      .modal-actions button { width: 100%; }
      .settings-modal-actions { bottom: -14px; margin: 14px -14px -14px; padding: 12px 14px; }
      .floating-dock { right: 8px; top: 50%; bottom: auto; transform: translateY(-50%); }
      .dock-rail { grid-auto-flow: row; gap: 6px; }
      .dock-fab { width: 40px; height: 40px; font-size: 17px; border-radius: 9px; }
      .theme-drawer { right: 48px; width: min(300px, calc(100vw - 68px)); }
      .mascot { left: 2px; bottom: 0; gap: 6px; }
      .mascot-figure { width: 52px; height: 78px; }
      .mascot-bubble { max-width: 156px; margin-bottom: 28px; font-size: 10px; padding: 7px 8px; }
      .strategy-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .strat-item:last-child { grid-column: 1 / -1; }
      .comment-form { grid-template-columns: 1fr; }
      .danmaku-layer { top: 58px; height: 130px; }
    }
  </style>
</head>
<body data-theme="day">
  <main>
    <header>
      <div>
        <h1>Binance Spot Live Agent</h1>
        <div class="muted">实盘实时交易看板</div>
      </div>
      <div class="header-actions">
        <div class="muted" id="updated">加载中...</div>
      </div>
    </header>
    <section class="top-board">
      <div class="panel metric-card">
        <div class="hero-symbol" id="symbol">--</div>
        <div class="hero-price" id="price">--</div>
        <div class="signal-row">
          <span class="label" style="margin:0">当前信号</span>
          <span class="signal-pill" id="signal">--</span>
        </div>
      </div>
      <div class="capital-stack">
        <div class="panel">
          <div class="label">当前资产估值</div>
          <div class="capital-value" id="value">--</div>
          <div class="muted">包含账户 USDT 与 BTC 折算</div>
        </div>
        <div class="panel pnl-card">
          <div class="label">较启动基准盈亏</div>
          <div class="value" id="pnl">--</div>
          <button class="action-button" id="calibrate">校准基准</button>
        </div>
      </div>
      <div class="panel control-panel">
        <div class="control-row">
          <div>
            <div class="label">交易开关</div>
            <div class="muted">自动策略是否允许真实下单</div>
          </div>
          <button class="action-button" id="execute">--</button>
        </div>
        <div class="control-row">
          <div>
            <div class="label">人工交易</div>
            <div class="muted">人工买入会记账，默认不自动卖出</div>
          </div>
          <div class="button-stack">
            <button class="action-button" id="manualBuy">人工买入并记账</button>
            <button class="secondary-button" id="externalLimitSell">外部持仓限价卖出</button>
          </div>
        </div>
      </div>
    </section>
    <section class="strategy-bar" id="strategyBar">
      <div class="strat-item"><span class="strat-label">止盈比例</span><span class="strat-value" id="stratTP">--</span></div>
      <div class="strat-item"><span class="strat-label">买入间距</span><span class="strat-value" id="stratGrid">--</span></div>
      <div class="strat-item"><span class="strat-label">仓位分档</span><span class="strat-value" id="stratSizing">--</span></div>
      <div class="strat-item"><span class="strat-label">防守模式</span><span class="strat-value" id="stratDefensive">--</span></div>
      <div class="strat-item"><span class="strat-label">浮亏保护</span><span class="strat-value" id="stratMaxLoss">--</span></div>
    </section>
    <section class="panel chart-wrap" style="margin-top:14px">
      <div class="chart-head">
        <div>
          <div class="label">行情走势</div>
          <div class="muted" id="chartLabel">分时</div>
        </div>
        <div class="range-tabs">
          <button data-range="minute" class="active">分时</button>
          <button data-range="5m">5分</button>
          <button data-range="15m">15分</button>
          <button data-range="1h">1小时</button>
          <button data-range="4h">4小时</button>
          <button data-range="day">1日</button>
          <button data-range="week">1周</button>
        </div>
      </div>
      <canvas id="chart"></canvas>
      <div class="tooltip" id="chartTip"></div>
    </section>
    <section class="panel" style="margin-top:14px">
      <div class="panel-head">
        <div>
          <div class="panel-title">策略回测</div>
          <div class="muted">按当前资金和当前策略参数，对比不同盈利比例</div>
        </div>
      </div>
      <div class="inline-controls">
        <div class="field"><label>数据类型</label><select id="backtestMode"><option value="synthetic">模拟盘</option><option value="historical">真实 K 线</option></select></div>
        <div class="field"><label>开始日期</label><input id="backtestStart" value="2026-04-01"></div>
        <div class="field"><label>结束日期</label><input id="backtestEnd" value="2026-05-01"></div>
        <div class="field"><label>模拟天数</label><input id="backtestDays" value="30" inputmode="numeric"></div>
        <div class="field"><label>盈利比例</label><input id="backtestProfits" value="0.6,0.8,1.0,1.2"></div>
      </div>
      <div class="modal-actions" style="justify-content:flex-start">
        <button class="action-button" id="runBacktest">开始回测</button>
        <span class="muted" id="backtestStatus">未运行</span>
      </div>
      <div id="backtestAdvice" class="result-note" style="display:none"></div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>场景</th><th>盈利比例</th><th>总资产</th><th>收益率</th><th>已实现</th><th>未实现</th><th>手续费</th><th>买/卖</th><th>未平</th><th>最大回撤</th></tr></thead>
          <tbody id="backtestResults"><tr><td colspan="10" class="muted">暂无回测结果</td></tr></tbody>
        </table>
      </div>
    </section>
    <section class="split">
      <div class="panel account-panel">
        <div class="panel-head">
          <div>
            <div class="panel-title">账户与策略</div>
            <div class="muted">余额、仓位、风控与策略状态</div>
          </div>
        </div>
        <table class="account-table">
          <tbody>
            <tr><th>账户余额</th><td id="base">--</td></tr>
            <tr><th>可用现金</th><td id="quote">--</td></tr>
            <tr><th>当前持仓</th><td id="lots">--</td></tr>
            <tr><th>收益概况</th><td id="profitBrief">--</td></tr>
            <tr><th>交易费用</th><td id="fees">--</td></tr>
            <tr><th>自动买入</th><td id="buyStatus">--</td></tr>
            <tr><th>趋势判断</th><td id="trendGuard">--</td></tr>
            <tr><th>波段抄底</th><td id="swing">--</td></tr>
            <tr><th>防守震荡</th><td id="scalp">--</td></tr>
            <tr><th>账本同步</th><td id="ledgerSync">--</td></tr>
            <tr><th>提示</th><td id="error">--</td></tr>
          </tbody>
        </table>
      </div>
      <div class="panel orders-panel">
        <div class="panel-head">
          <div>
            <div class="panel-title">最近实盘订单</div>
            <div class="muted">买入、卖出与人工交易记录</div>
          </div>
        </div>
        <div class="table-scroll">
          <table class="orders-table">
            <thead><tr><th>时间</th><th>方向</th><th>批次</th><th>金额</th></tr></thead>
            <tbody id="trades"><tr><td colspan="4" class="muted">暂无订单</td></tr></tbody>
          </table>
        </div>
        <div class="pager"><button id="tradePrev">上一页</button><span id="tradePage">1 / 1</span><button id="tradeNext">下一页</button></div>
      </div>
    </section>
    <section class="panel" style="margin-top:14px">
      <div class="panel-head">
        <div class="label">未平批次</div>
        <div class="button-stack">
          <button class="secondary-button" id="bulkAutoOn">一键开启自动卖</button>
          <button class="secondary-button" id="bulkAutoOff">一键关闭自动卖</button>
          <button class="secondary-button" id="bulkMarketSell">一键市价卖</button>
          <button class="secondary-button" id="bulkLimitSell">一键限价卖</button>
        </div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>批次</th><th>状态</th><th>成本价</th><th>预计卖价</th><th>手动价格</th><th>数量</th><th>手续费</th><th>浮盈亏</th><th>操作</th></tr></thead>
          <tbody id="openLots"><tr><td colspan="9" class="muted">暂无未平批次</td></tr></tbody>
        </table>
      </div>
      <div class="pager"><button id="openPrev">上一页</button><span id="openPage">1 / 1</span><button id="openNext">下一页</button></div>
    </section>
    <section class="panel" style="margin-top:14px">
      <div class="label">限价挂单</div>
      <table>
        <thead><tr><th>时间</th><th>方向</th><th>限价</th><th>数量</th><th>状态</th><th>操作</th></tr></thead>
        <tbody id="pendingOrders"><tr><td colspan="6" class="muted">暂无限价挂单</td></tr></tbody>
      </table>
    </section>
    <section class="panel" style="margin-top:14px">
      <div class="label">已平批次</div>
      <table>
        <thead><tr><th>关闭时间</th><th>批次</th><th>状态</th><th>成本价</th><th>卖出价</th><th>手动价格</th><th>数量</th><th>手续费</th><th>净利润</th></tr></thead>
        <tbody id="closedLots"><tr><td colspan="9" class="muted">暂无已平批次</td></tr></tbody>
      </table>
      <div class="pager"><button id="closedPrev">上一页</button><span id="closedPage">1 / 1</span><button id="closedNext">下一页</button></div>
    </section>
    <section class="panel comments-panel" id="comments">
      <div class="panel-head">
        <div>
          <div class="panel-title">交流与评论</div>
          <div class="muted">分享使用体验、策略想法或问题；评论会同步显示为弹幕。</div>
        </div>
        <div class="button-stack">
          <button class="secondary-button" id="danmakuToggle">关闭弹幕</button>
          <select id="danmakuSpeed" class="secondary-button" aria-label="弹幕速度">
            <option value="slow">慢速</option><option value="normal" selected>正常</option><option value="fast">快速</option>
          </select>
        </div>
      </div>
      <div class="comment-form">
        <div class="field"><label>昵称</label><input id="commentName" maxlength="20" placeholder="怎么称呼你"></div>
        <div class="field"><label>评论内容</label><textarea id="commentMessage" maxlength="300" placeholder="说点具体的，会更容易得到有价值的回复。"></textarea></div>
        <button class="action-button" id="commentSubmit">发表评论</button>
      </div>
      <div class="comment-list" id="commentList"><div class="muted">正在读取评论...</div></div>
    </section>
  </main>
  <div class="danmaku-layer" id="danmakuLayer" aria-hidden="true"></div>
  <div class="modal" id="settingsModal">
    <div class="modal-panel">
      <div class="modal-head">
        <h2>面板设置</h2>
        <button class="secondary-button" id="settingsClose">关闭</button>
      </div>
      <div class="settings-section">
        <div class="settings-title">面板安全</div>
        <div class="settings-grid">
          <div class="field"><label>页面登录密码</label><input id="setDashboardPassword" type="password" placeholder="留空不改" autocomplete="new-password"></div>
          <div class="field"><label>交易开关密码</label><input id="setTradingPassword" type="password" placeholder="留空不改" autocomplete="new-password"></div>
        </div>
      </div>
      <div class="settings-section">
        <div class="settings-title">收益与人工交易</div>
        <div class="settings-help">默认盈利比例只影响后续新批次；选择重算时，会更新未平普通/人工批次的预计卖价。</div>
        <div class="settings-grid">
          <div class="field"><label>默认盈利比例 %</label><input id="setTakeProfitPct" inputmode="decimal" placeholder="例如 1.0"></div>
          <div class="field"><label>应用到未平批次</label><select id="setApplyTakeProfit"><option value="false">只影响后续</option><option value="true">重算未平批次</option></select></div>
          <div class="field"><label>人工买入默认自动卖出</label><select id="setManualBuyAutoSell"><option value="false">关闭</option><option value="true">开启</option></select></div>
        </div>
      </div>
      <div class="settings-section">
        <div class="settings-title">通知设置</div>
        <div class="settings-grid">
          <div class="field"><label>SMTP 服务器</label><input id="setSmtpHost" placeholder="smtp.example.com"></div>
          <div class="field"><label>SMTP 端口</label><input id="setSmtpPort" inputmode="numeric" placeholder="465"></div>
          <div class="field"><label>SMTP 账号</label><input id="setSmtpUsername" autocomplete="username"></div>
          <div class="field"><label>SMTP 密码</label><input id="setSmtpPassword" type="password" placeholder="留空不改" autocomplete="new-password"></div>
          <div class="field"><label>发件人姓名</label><input id="setSmtpFromName"></div>
          <div class="field"><label>报告收件人</label><input id="setReportRecipient"></div>
        </div>
      </div>
      <div id="configSettings"></div>
      <div class="settings-status" id="settingsStatus">密码字段不会回显；留空表示不修改。</div>
      <div class="modal-actions settings-modal-actions">
        <button class="secondary-button" id="settingsReload">重新读取</button>
        <button class="action-button" id="settingsSave">保存设置</button>
      </div>
    </div>
  </div>
  <div class="modal open" id="loginModal">
      <div class="modal-panel" style="width:min(420px,100%)">
      <div class="modal-head"><h2>登录看板</h2></div>
      <div class="field"><label>页面密码</label><input id="loginPassword" type="password" autocomplete="current-password"></div>
      <label style="display:flex;align-items:flex-start;gap:8px;margin-top:12px;cursor:pointer;color:var(--text);font-size:13px;line-height:1.45">
        <input type="checkbox" id="loginRemember" style="width:auto;margin-top:2px;accent-color:var(--accent)">
        <span>记住我，24 小时内免登录<br><span class="muted">密码会保存在当前浏览器中，仅建议在私人设备使用。</span></span>
      </label>
      <div class="muted" id="loginStatus" style="margin-top:12px">请输入页面密码后查看实盘看板。</div>
      <div class="modal-actions"><button class="action-button" id="loginButton">登录</button></div>
    </div>
  </div>
  <div class="modal" id="actionModal">
    <div class="modal-panel" style="width:min(500px,100%)">
      <div class="modal-head"><h2 id="actionTitle">操作确认</h2><button class="secondary-button" id="actionClose">关闭</button></div>
      <div class="dialog-message" id="actionMessage"></div>
      <div class="dialog-fields" id="actionFields"></div>
      <div class="modal-actions">
        <button class="secondary-button" id="actionCancel">取消</button>
        <button class="action-button" id="actionConfirm">确认</button>
      </div>
    </div>
  </div>
  <div class="notice-toast" id="noticeToast" role="status" aria-live="polite">
    <span id="noticeText"></span>
    <button id="noticeClose" aria-label="关闭提示">×</button>
  </div>
  <aside class="floating-dock" id="themeDock">
    <div class="theme-drawer" id="themeDrawer">
      <div class="theme-head">
        <span>主题外观</span>
      </div>
      <div class="theme-grid">
        <button class="theme-chip" data-theme-choice="day">白天</button>
        <button class="theme-chip" data-theme-choice="night">交易所深色</button>
        <button class="theme-chip" data-theme-choice="rift">星界竞技</button>
        <button class="theme-chip" data-theme-choice="anime">动画校园</button>
        <button class="theme-chip" data-theme-choice="stage">樱音舞台</button>
        <button class="theme-chip" data-theme-choice="forest">森林</button>
        <button class="theme-chip" data-theme-choice="ocean">海洋</button>
        <button class="theme-chip" data-theme-choice="sunset">落日</button>
        <button class="theme-chip" data-theme-choice="mono">极简</button>
      </div>
      <button class="secondary-button theme-toggle" id="mascotToggle">隐藏看板助手</button>
    </div>
    <div class="dock-rail">
      <button class="dock-fab" id="themeFab" title="主题" aria-label="主题" data-help="打开主题选择和看板助手显示设置。"><span class="theme-icon" aria-hidden="true"></span></button>
      <button class="dock-fab" id="quickTheme" title="明暗模式" data-help="在白天主题和交易所深色主题之间快速切换。">◐</button>
      <button class="dock-fab" id="layoutToggle" title="页面宽度" data-help="在紧凑宽度和宽屏布局之间切换，默认使用不遮挡助手的紧凑宽度。">↔</button>
      <button class="dock-fab" id="openSettingsDock" title="系统设置" data-help="打开系统设置，管理安全、通知、资金池、风控和策略。">⚙</button>
      <button class="dock-fab" id="scrollTop" title="回到顶部" data-help="平滑回到看板顶部。">↑</button>
    </div>
  </aside>
  <aside class="mascot" id="mascot">
    <div class="mascot-figure">
      <img id="mascotImage" src="/static/mascot-ai.png?v=1.0.7" alt="看板助手">
      <div class="mascot-fallback">看板助手<br>图片加载中</div>
    </div>
    <div class="mascot-bubble" id="mascotBubble">正在读取 BTC 走势，稍后告诉你当前更像震荡、下跌还是反弹。</div>
  </aside>
  <script>
    const fmt = (n, digits = 4) => Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
    const rangeLabels = { minute: '近 60 分钟', '5m': '近 5 分钟', '15m': '近 15 分钟', '1h': '近 1 小时', '4h': '近 4 小时', day: '近 24 小时', week: '近 7 天' };
    const rangeOrder = ['5m', '15m', '1h', '4h', 'day', 'week'];
    let activeRange = 'minute';
    let chartZoom = { start: 0, end: 1 };
    let chartPoints = [];
    let chartTrades = [];
    let chartReference = null;
    let chartLayout = null;
    let latestTrades = [];
    let latestClosedLots = [];
    let latestOpenLots = [];
    let latestOpenPrice = 0;
    let latestPendingOrders = [];
    let manualBuyAutoSellDefault = false;
    let tradePage = 0;
    let closedPage = 0;
    let openPage = 0;
    let rangeWheelLocked = false;
    let settingsLoaded = false;
    const loginCacheKey = 'dashboardPasswordCache';
    function readCachedLogin() {
      const cached = localStorage.getItem(loginCacheKey);
      if (cached) {
        try {
          const data = JSON.parse(cached);
          if (data.password && Number(data.expiry) > Date.now()) {
            return { password: data.password, remembered: true };
          }
        } catch (err) {}
        localStorage.removeItem(loginCacheKey);
      }
      return { password: sessionStorage.getItem('dashboardPassword') || '', remembered: false };
    }
    function clearRememberedLogin() {
      localStorage.removeItem(loginCacheKey);
      sessionStorage.removeItem('dashboardPassword');
      const remember = document.getElementById('loginRemember');
      if (remember) remember.checked = false;
    }
    const cachedLogin = readCachedLogin();
    let dashboardPassword = cachedLogin.password;
    let loginValidated = Boolean(cachedLogin.password);
    let mascotBubbleTimer = null;
    let mascotMarketSaidAt = 0;
    let noticeTimer = null;
    const tradePageSize = 9;
    const closedPageSize = 8;
    const openPageSize = 6;
    const canvas = document.getElementById('chart');
    const ctx = canvas.getContext('2d');
    const chartTip = document.getElementById('chartTip');
    document.getElementById('loginRemember').checked = cachedLogin.remembered;
    if (cachedLogin.password) document.getElementById('loginModal').classList.remove('open');
    function authHeaders(tradingPassword, payload) {
      const headers = { 'X-Dashboard-Password': dashboardPassword };
      if (tradingPassword) headers['X-Trading-Password'] = tradingPassword;
      if (payload) headers['X-Action-Payload'] = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
      return headers;
    }
    function setSettingsStatus(message, type = '') {
      const status = document.getElementById('settingsStatus');
      status.textContent = message;
      status.className = `settings-status${type ? ` ${type}` : ''}`;
    }
    function showNotice(message, type = 'success', duration = 8000) {
      const toast = document.getElementById('noticeToast');
      document.getElementById('noticeText').textContent = message;
      toast.className = `notice-toast ${type} show`;
      if (noticeTimer) clearTimeout(noticeTimer);
      noticeTimer = setTimeout(() => { toast.classList.remove('show'); }, duration);
    }
    let actionResolver = null;
    function closeActionModal(result = null) {
      document.getElementById('actionModal').classList.remove('open');
      if (actionResolver) {
        const resolve = actionResolver;
        actionResolver = null;
        resolve(result);
      }
    }
    function openActionModal({ title, message = '', fields = [], confirmText = '确认', cancelText = '取消', alertOnly = false }) {
      document.getElementById('actionTitle').textContent = title;
      document.getElementById('actionMessage').textContent = message;
      document.getElementById('actionConfirm').textContent = confirmText;
      document.getElementById('actionCancel').textContent = cancelText;
      document.getElementById('actionCancel').style.display = alertOnly ? 'none' : '';
      document.getElementById('actionClose').style.display = alertOnly ? 'none' : '';
      const container = document.getElementById('actionFields');
      container.innerHTML = '';
      fields.forEach((field, index) => {
        const wrap = document.createElement('div');
        wrap.className = 'field';
        const label = document.createElement('label');
        label.textContent = field.label || '';
        const input = document.createElement(field.kind === 'select' ? 'select' : 'input');
        input.id = `actionField${index}`;
        if (field.kind === 'select') {
          (field.options || []).forEach(option => {
            const node = document.createElement('option');
            node.value = option.value;
            node.textContent = option.label;
            if (String(option.value) === String(field.value ?? '')) node.selected = true;
            input.appendChild(node);
          });
        } else {
          input.type = field.type || 'text';
          input.value = field.value ?? '';
          input.placeholder = field.placeholder || '';
          input.autocomplete = field.autocomplete || 'off';
        }
        wrap.append(label, input);
        container.appendChild(wrap);
      });
      document.getElementById('actionModal').classList.add('open');
      setTimeout(() => container.querySelector('input,select')?.focus(), 30);
      return new Promise(resolve => { actionResolver = resolve; });
    }
    async function uiPrompt(message, value = '', type = 'text') {
      const result = await openActionModal({
        title: '请输入信息',
        message,
        fields: [{ label: '输入内容', value, type }],
      });
      return result ? result[0] : null;
    }
    function uiConfirm(message, title = '确认操作') {
      return openActionModal({ title, message }).then(result => Boolean(result));
    }
    function uiAlert(message, title = '操作提示') {
      return openActionModal({ title, message, confirmText: '知道了', alertOnly: true }).then(() => undefined);
    }
    document.getElementById('actionConfirm').addEventListener('click', () => {
      const values = Array.from(document.querySelectorAll('#actionFields input, #actionFields select')).map(input => input.value);
      closeActionModal(values.length ? values : ['confirmed']);
    });
    document.getElementById('actionCancel').addEventListener('click', () => closeActionModal(null));
    document.getElementById('actionClose').addEventListener('click', () => closeActionModal(null));
    document.getElementById('actionModal').addEventListener('keydown', event => {
      if (event.key === 'Enter') document.getElementById('actionConfirm').click();
      if (event.key === 'Escape') closeActionModal(null);
    });
    async function apiGet(path, payload, tradingPassword) {
      const res = await fetch(path, { method: 'GET', cache: 'no-store', headers: authHeaders(tradingPassword, payload) });
      const contentType = res.headers.get('content-type') || '';
      const raw = await res.text();
      let data = {};
      if (raw && contentType.includes('application/json')) {
        try {
          data = JSON.parse(raw);
        } catch (err) {
          throw new Error(`接口返回了异常 JSON：${raw.slice(0, 120)}`);
        }
      } else if (raw) {
        const plain = raw.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim();
        throw new Error(plain ? `接口返回了非 JSON 内容：${plain.slice(0, 160)}` : `接口返回了非 JSON 内容，HTTP ${res.status}`);
      }
      if (res.status === 403 && data.error === 'not logged in') {
        loginValidated = false;
        dashboardPassword = '';
        clearRememberedLogin();
        document.getElementById('loginModal').classList.add('open');
        document.getElementById('loginStatus').textContent = '登录信息已失效，请重新输入页面密码。';
      }
      if (data.error) throw new Error(data.error);
      return data;
    }
    async function requireLogin() {
      if (!dashboardPassword) {
        document.getElementById('loginModal').classList.add('open');
        return false;
      }
      if (loginValidated) {
        document.getElementById('loginModal').classList.remove('open');
        return true;
      }
      try {
        await apiGet('/api/login');
        loginValidated = true;
        document.getElementById('loginModal').classList.remove('open');
        return true;
      } catch (err) {
        dashboardPassword = '';
        loginValidated = false;
        clearRememberedLogin();
        document.getElementById('loginModal').classList.add('open');
        document.getElementById('loginPassword').value = '';
        document.getElementById('loginStatus').textContent = '登录信息已失效，请重新输入页面密码。';
        return false;
      }
    }
    function drawChart(points, reference, trades) {
      chartPoints = points || [];
      chartTrades = trades || chartTrades || [];
      chartReference = reference;
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      const styles = getComputedStyle(document.body);
      const lineColor = styles.getPropertyValue('--line-soft').trim() || '#e5ebf2';
      const textColor = styles.getPropertyValue('--muted').trim() || '#687789';
      const upColor = styles.getPropertyValue('--good').trim() || '#059669';
      const downColor = styles.getPropertyValue('--bad').trim() || '#e11d48';
      const accent = styles.getPropertyValue('--accent').trim() || '#0ea5e9';
      const pad = { left: 96, right: 28, top: 24, bottom: 64 };
      const total = chartPoints.length;
      const startIndex = Math.max(0, Math.floor(chartZoom.start * Math.max(total - 1, 1)));
      const endIndex = Math.min(total, Math.max(startIndex + 2, Math.ceil(chartZoom.end * total)));
      const visible = chartPoints.slice(startIndex, endIndex);
      const values = visible.flatMap(p => [Number(p.high ?? p.close), Number(p.low ?? p.close), Number(p.close)]);
      if (reference) values.push(reference);
      if (!values.length) return;
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(max - min, 1);
      const plotW = rect.width - pad.left - pad.right;
      const plotH = rect.height - pad.top - pad.bottom;
      const x = i => pad.left + plotW * (i / Math.max(visible.length - 1, 1));
      const y = v => pad.top + (rect.height - pad.top - pad.bottom) * (1 - (v - min) / span);
      ctx.strokeStyle = lineColor; ctx.lineWidth = 1;
      ctx.fillStyle = textColor; ctx.font = '12px sans-serif';
      for (let i = 0; i < 5; i++) {
        const yy = pad.top + i * plotH / 4;
        const val = max - span * i / 4;
        ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(rect.width - pad.right, yy); ctx.stroke();
        ctx.textAlign = 'right';
        ctx.fillText(fmt(val, 2), pad.left - 10, yy + 4);
      }
      for (let i = 0; i < 5; i++) {
        const xx = pad.left + i * plotW / 4;
        ctx.beginPath(); ctx.moveTo(xx, pad.top); ctx.lineTo(xx, rect.height - pad.bottom); ctx.stroke();
      }
      if (reference) {
        ctx.strokeStyle = '#d97706'; ctx.setLineDash([6, 5]); ctx.beginPath();
        ctx.moveTo(pad.left, y(reference)); ctx.lineTo(rect.width - pad.right, y(reference)); ctx.stroke(); ctx.setLineDash([]);
      }
      const candleW = Math.max(3, Math.min(14, plotW / Math.max(visible.length, 1) * .58));
      visible.forEach((p, i) => {
        const open = Number(p.open ?? p.close), close = Number(p.close), high = Number(p.high ?? close), low = Number(p.low ?? close);
        const xx = x(i);
        const color = close >= open ? upColor : downColor;
        ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(xx, y(high)); ctx.lineTo(xx, y(low)); ctx.stroke();
        const top = Math.min(y(open), y(close));
        const bodyH = Math.max(2, Math.abs(y(open) - y(close)));
        ctx.fillRect(xx - candleW / 2, top, candleW, bodyH);
      });
      const visibleStart = visible[0]?.open_time || 0;
      const visibleEnd = visible[visible.length - 1]?.open_time || 0;
      const markers = (chartTrades || []).filter(t => {
        const ts = Date.parse(t.ts);
        return Number.isFinite(ts) && ts >= visibleStart && ts <= visibleEnd;
      });
      markers.forEach(t => {
        const ts = Date.parse(t.ts);
        const i = Math.max(0, Math.min(visible.length - 1, visible.findIndex(p => p.open_time >= ts)));
        const p = visible[i < 0 ? visible.length - 1 : i];
        const side = String(t.side || '');
        const isSell = side.includes('SELL');
        const yy = isSell ? y(Number(p.high ?? p.close)) - 15 : y(Number(p.low ?? p.close)) + 15;
        const xx = x(i < 0 ? visible.length - 1 : i);
        ctx.fillStyle = isSell ? downColor : upColor;
        ctx.beginPath();
        ctx.arc(xx, yy, 10, 0, Math.PI * 2);
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--panel-strong').trim() || '#fff';
        ctx.stroke();
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 11px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(isSell ? 'S' : 'B', xx, yy + .5);
        ctx.textBaseline = 'alphabetic';
      });
      ctx.strokeStyle = lineColor; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad.left, pad.top); ctx.lineTo(pad.left, rect.height - pad.bottom); ctx.lineTo(rect.width - pad.right, rect.height - pad.bottom); ctx.stroke();
      if (visible.length) {
        const labels = [0, Math.floor((visible.length - 1) / 2), visible.length - 1];
        labels.forEach(i => {
          const label = new Date(visible[i].open_time).toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
          ctx.textAlign = i === 0 ? 'left' : (i === visible.length - 1 ? 'right' : 'center');
          ctx.fillText(label, Math.min(rect.width - pad.right, Math.max(pad.left, x(i))), rect.height - 24);
        });
      }
      ctx.textAlign = 'left';
      chartLayout = { rect, pad, visible, min, max, span, plotW, plotH, x, y };
    }
    function chartPointAt(clientX) {
      if (!chartLayout || !chartLayout.visible.length) return null;
      const box = canvas.getBoundingClientRect();
      const x = clientX - box.left;
      const ratio = (x - chartLayout.pad.left) / Math.max(chartLayout.plotW, 1);
      const index = Math.max(0, Math.min(chartLayout.visible.length - 1, Math.round(ratio * (chartLayout.visible.length - 1))));
      return { index, point: chartLayout.visible[index], x: chartLayout.x(index), y: chartLayout.y(chartLayout.visible[index].close) };
    }
    function renderTrades(trades) {
      latestTrades = trades || latestTrades;
      const tbody = document.getElementById('trades');
      tbody.innerHTML = '';
      if (!latestTrades.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="muted">暂无订单</td></tr>';
        updatePager('trade', 0, 0);
        return;
      }
      const rows = latestTrades.slice().reverse();
      const pages = Math.max(1, Math.ceil(rows.length / tradePageSize));
      tradePage = Math.min(tradePage, pages - 1);
      rows.slice(tradePage * tradePageSize, (tradePage + 1) * tradePageSize).forEach(t => {
        const sideClass = String(t.side || '').includes('BUY') ? 'profit' : 'warn';
        const quote = tradeQuoteAmount(t);
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${new Date(t.ts).toLocaleString()}</td><td><span class="badge ${sideClass}">${sideLabel(t.side)}</span></td><td>${levelLabel(t.level)}</td><td>${fmt(quote || 0, 2)}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['时间', '方向', '批次', '金额']);
      updatePager('trade', tradePage, pages);
    }
    function applyMobileLabels(tbody, labels) {
      tbody.querySelectorAll('tr').forEach(row => {
        Array.from(row.children).forEach((cell, index) => {
          if (!cell.hasAttribute('colspan')) cell.dataset.label = labels[index] || '';
        });
      });
    }
    function positiveNumber(value) {
      const n = Number(value || 0);
      return Number.isFinite(n) && n > 0 ? n : 0;
    }
    function tradeQuoteAmount(trade) {
      const order = trade.order || {};
      return positiveNumber(order.cummulativeQuoteQty)
        || positiveNumber(order.origQuoteOrderQty)
        || (positiveNumber(order.price) * positiveNumber(order.origQty))
        || positiveNumber(trade.target_quote_size);
    }
    function sideLabel(side) {
      const raw = String(side || '');
      if (raw.includes('BUY')) return raw.includes('LIMIT') ? '限价买入' : '买入';
      if (raw.includes('SELL')) return raw.includes('LIMIT') ? '限价卖出' : '卖出';
      if (raw.includes('CANCELED')) return '取消挂单';
      return raw || '--';
    }
    function levelLabel(level) {
      const raw = String(level || 'legacy');
      const map = {
        starter: '启动仓',
        'lot-target': '目标卖出',
        'manual-entry': '人工买入',
        'manual-limit-buy': '人工限价买入',
        'manual-limit-sell': '批次限价卖出',
        'manual-external-limit-sell': '外部持仓限价卖出',
        'scalp-target': '防守震荡卖出'
      };
      if (map[raw]) return map[raw];
      if (raw.startsWith('buy-')) return `网格 ${raw}`;
      if (raw.startsWith('swing-')) return '波段抄底';
      if (raw.startsWith('scalp-entry')) return '防守震荡买入';
      return raw;
    }
    function lotDisplay(lot) {
      const id = String(lot.id || '').slice(0, 8);
      return `${levelLabel(lot.level)}${id ? `<span class="lot-id">批次 ${id}</span>` : ''}`;
    }
    function zhReason(text) {
      const raw = String(text || '--');
      return raw
        .replace('max position limit reached', '已达到最大持仓限制')
        .replace('hold neutral', '等待更合适的位置')
        .replace('risk: floating loss', '浮亏超过保护阈值')
        .replace('exceeds limit', '，暂停普通买入')
        .replace('below 24h and 7d averages with falling 24h average', '价格低于 24 小时和 7 日均线，且 24 小时均线下行')
        .replace('defensive range bound', '防守期横盘，可做小仓震荡')
        .replace('not in defensive mode', '当前不在防守期')
        .replace('outside scalp band', '不在防守震荡区间');
    }
    function setTheme(theme) {
      const next = theme || localStorage.getItem('dashboardTheme') || 'day';
      document.body.dataset.theme = next;
      localStorage.setItem('dashboardTheme', next);
      document.querySelectorAll('[data-theme-choice]').forEach(button => button.classList.toggle('active', button.dataset.themeChoice === next));
      drawChart(chartPoints, chartReference, chartTrades);
    }
    function setMascotVisible(visible) {
      localStorage.setItem('dashboardMascot', visible ? 'on' : 'off');
      document.getElementById('mascot').classList.toggle('hidden', !visible);
      document.getElementById('mascotToggle').textContent = visible ? '隐藏看板助手' : '显示看板助手';
    }
    function mascotSay(text) {
      const mascot = document.getElementById('mascot');
      const bubble = document.getElementById('mascotBubble');
      if (bubble) bubble.textContent = text;
      if (mascot) mascot.classList.add('speaking');
      if (mascotBubbleTimer) clearTimeout(mascotBubbleTimer);
      mascotBubbleTimer = setTimeout(() => {
        const node = document.getElementById('mascot');
        if (node) node.classList.remove('speaking');
      }, 8000);
    }
    function updateMascotMarket(data) {
      const now = Date.now();
      if (now - mascotMarketSaidAt < 120000) return;
      mascotMarketSaidAt = now;
      const trend = data.trend_guard || {};
      const pnl = Number(data.pnl_quote || 0);
      const price = fmt(data.price || 0, 2);
      if (trend.downtrend) mascotSay(`BTC 现在约 ${price}，处在下跌趋势保护里。普通网格会谨慎，波段和防守震荡按各自规则找机会。`);
      else if (pnl >= 0) mascotSay(`BTC 现在约 ${price}，账户相对基准为正。别急，批次还是按目标价和风险规则走。`);
      else mascotSay(`BTC 现在约 ${price}，账户相对基准偏弱。先看现金、浮亏和趋势保护，不要被短线晃到。`);
    }
    function renderOpenLots(lots, currentPrice, pendingOrders) {
      latestOpenLots = lots || [];
      latestOpenPrice = Number(currentPrice || latestOpenPrice || 0);
      latestPendingOrders = pendingOrders || latestPendingOrders || [];
      const tbody = document.getElementById('openLots');
      tbody.innerHTML = '';
      if (!lots.length) {
        const externalSells = (pendingOrders || []).filter(order => order.side === 'SELL' && !order.lot_id && !order.processed && !['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'].includes(order.status || ''));
        tbody.innerHTML = externalSells.length
          ? `<tr><td colspan="9" class="muted">暂无未平批次。当前有 ${externalSells.length} 个外部持仓限价卖单，请在下方“限价挂单”查看；外部挂单不会生成账本批次。</td></tr>`
          : '<tr><td colspan="9" class="muted">暂无未平批次。按批次限价卖出需要先有脚本账本批次；如果是外部持仓，请用上方“外部持仓限价卖出”。</td></tr>';
        updatePager('open', 0, 0);
        return;
      }
      const pages = Math.max(1, Math.ceil(lots.length / openPageSize));
      openPage = Math.min(openPage, pages - 1);
      lots.slice(openPage * openPageSize, (openPage + 1) * openPageSize).forEach(lot => {
        const qty = Number(lot.remaining_quantity || 0);
        const buy = Number(lot.buy_price || 0);
        const target = Number(lot.effective_target_price || lot.target_price || 0);
        const manualNote = lot.auto_sell === false ? ' <span class="badge">手动</span>' : '';
        const swingNote = lot.target_note === 'swing' ? ' <span class="badge">波段</span>' : '';
        const note = lot.target_price_adjusted ? ` <span class="badge">防守 ${lot.target_note}</span>` : (swingNote || manualNote);
        const pnl = (latestOpenPrice - buy) * qty;
        const fee = Number(lot.fee_quote || lot.buy_fee_quote || 0);
        const status = lot.pending_limit_sell_order_id ? '限价卖出中' : (lot.auto_sell === false ? '手动持仓' : '自动卖出');
        const manualPrice = lot.pending_limit_sell_price ? fmt(lot.pending_limit_sell_price, 2) : '--';
        const manualSell = `<button class="secondary-button" data-manual-sell="${lot.id}">市价卖出</button>`;
        const autoToggle = `<button class="secondary-button" data-auto-sell="${lot.id}" data-auto-sell-enabled="${lot.auto_sell === false ? 'true' : 'false'}">${lot.auto_sell === false ? '开启自动卖' : '取消自动卖'}</button>`;
        const limitSell = `<button class="secondary-button" data-limit-sell="${lot.id}" data-target-price="${target}">限价卖出</button>`;
        const externalClose = `<button class="secondary-button" data-external-close="${lot.id}">外部已卖</button>`;
        const action = `<div class="lot-actions">${manualSell}${autoToggle}${limitSell}${externalClose}</div>`;
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${lotDisplay(lot)}</td><td><span class="badge">${status}</span></td><td>${fmt(buy, 2)}</td><td>${fmt(target, 2)}${note}</td><td>${manualPrice}</td><td>${fmt(qty, 8)}</td><td>${fmt(fee, 2)}</td><td class="${pnl >= 0 ? 'profit' : 'loss'}">${fmt(pnl, 2)}</td><td>${action}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['批次', '状态', '成本价', '预计卖价', '手动价格', '数量', '手续费', '浮盈亏', '操作']);
      tbody.querySelectorAll('[data-manual-sell]').forEach(button => {
        button.addEventListener('click', () => manualSell(button.dataset.manualSell));
      });
      tbody.querySelectorAll('[data-external-close]').forEach(button => {
        button.addEventListener('click', () => externalClose(button.dataset.externalClose));
      });
      tbody.querySelectorAll('[data-limit-sell]').forEach(button => {
        button.addEventListener('click', () => limitSell(button.dataset.limitSell, button.dataset.targetPrice));
      });
      tbody.querySelectorAll('[data-auto-sell]').forEach(button => {
        button.addEventListener('click', () => setLotAutoSell(button.dataset.autoSell, button.dataset.autoSellEnabled === 'true'));
      });
      updatePager('open', openPage, pages);
    }
    function renderPendingOrders(orders) {
      const tbody = document.getElementById('pendingOrders');
      tbody.innerHTML = '';
      const active = (orders || []).slice().reverse();
      if (!active.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">暂无限价挂单</td></tr>';
        return;
      }
      active.forEach(order => {
        const canCancel = !order.processed && !['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'].includes(order.status);
        const sideClass = order.side === 'BUY' ? 'profit' : 'warn';
        const action = canCancel ? `<button class="secondary-button" data-cancel-order="${order.order_id}">取消</button>` : '--';
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${order.created_at ? new Date(order.created_at).toLocaleString() : '--'}</td><td><span class="badge ${sideClass}">${sideLabel(order.side)}</span></td><td>${fmt(order.limit_price || 0, 2)}</td><td>${fmt(order.quantity || 0, 8)}</td><td>${order.status || '--'}</td><td>${action}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['时间', '方向', '限价', '数量', '状态', '操作']);
      tbody.querySelectorAll('[data-cancel-order]').forEach(button => {
        button.addEventListener('click', () => cancelPendingOrder(button.dataset.cancelOrder));
      });
    }
    function renderClosedLots(lots) {
      latestClosedLots = lots || latestClosedLots;
      const tbody = document.getElementById('closedLots');
      tbody.innerHTML = '';
      if (!latestClosedLots.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="muted">暂无已平批次</td></tr>';
        updatePager('closed', 0, 0);
        return;
      }
      const rows = latestClosedLots.slice().reverse();
      const pages = Math.max(1, Math.ceil(rows.length / closedPageSize));
      closedPage = Math.min(closedPage, pages - 1);
      rows.slice(closedPage * closedPageSize, (closedPage + 1) * closedPageSize).forEach(lot => {
        const pnl = Number(lot.net_realized_pnl ?? lot.realized_pnl ?? 0);
        const fee = Number(lot.fee_quote || lot.total_fee_quote || 0);
        const status = lot.external_close ? '外部已卖' : (lot.limit_sell_filled ? '限价卖出成交' : '自动/市价卖出');
        const manualPrice = lot.manual_sell_price ? fmt(lot.manual_sell_price, 2) : '--';
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${lot.closed_at ? new Date(lot.closed_at).toLocaleString() : '--'}</td><td>${lotDisplay(lot)}</td><td><span class="badge">${status}</span></td><td>${fmt(lot.buy_price || 0, 2)}</td><td>${fmt(lot.sell_price || 0, 2)}</td><td>${manualPrice}</td><td>${fmt(lot.quantity || 0, 8)}</td><td>${fmt(fee, 2)}</td><td class="${pnl >= 0 ? 'profit' : 'loss'}">${fmt(pnl, 2)}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['关闭时间', '批次', '状态', '成本价', '卖出价', '手动价格', '数量', '手续费', '净利润']);
      updatePager('closed', closedPage, pages);
    }
    function updatePager(kind, page, pages) {
      const prev = document.getElementById(kind + 'Prev');
      const next = document.getElementById(kind + 'Next');
      const label = document.getElementById(kind + 'Page');
      label.textContent = pages ? `${page + 1} / ${pages}` : '0 / 0';
      prev.disabled = page <= 0;
      next.disabled = !pages || page >= pages - 1;
    }
    async function loadSettings(resetStatus = true) {
      const data = await apiGet('/api/settings');
      document.getElementById('setDashboardPassword').value = '';
      document.getElementById('setDashboardPassword').placeholder = data.dashboard_password_set ? '已设置，留空不改' : '未设置';
      document.getElementById('setTradingPassword').value = '';
      document.getElementById('setTradingPassword').placeholder = data.trading_toggle_password_set ? '已设置，留空不改' : '未设置';
      document.getElementById('setTakeProfitPct').value = fmt(Number(data.take_profit_pct || 0) * 100, 4);
      document.getElementById('setApplyTakeProfit').value = 'false';
      document.getElementById('setManualBuyAutoSell').value = data.manual_buy_auto_sell ? 'true' : 'false';
      document.getElementById('setSmtpHost').value = data.smtp_host || '';
      document.getElementById('setSmtpPort').value = data.smtp_port || '465';
      document.getElementById('setSmtpUsername').value = data.smtp_username || '';
      document.getElementById('setSmtpPassword').value = '';
      document.getElementById('setSmtpPassword').placeholder = data.smtp_password_set ? '已设置，留空不改' : '未设置';
      document.getElementById('setSmtpFromName').value = data.smtp_from_name || '';
      document.getElementById('setReportRecipient').value = data.report_recipient || '';
      renderConfigSettings(data.config_fields || []);
      if (resetStatus) setSettingsStatus('密码字段不会回显；留空表示不修改。');
      settingsLoaded = true;
    }
    function renderConfigSettings(fields) {
      const root = document.getElementById('configSettings');
      root.innerHTML = '';
      const groups = {};
      fields.forEach(field => {
        const category = field.category || '策略设置';
        if (!groups[category]) groups[category] = [];
        groups[category].push(field);
      });
      Object.entries(groups).forEach(([category, items]) => {
        const section = document.createElement('div');
        section.className = 'settings-section';
        const title = document.createElement('div');
        title.className = 'settings-title';
        title.textContent = category;
        section.appendChild(title);
        if (category === '资金池与仓位') {
          const help = document.createElement('div');
          help.className = 'settings-help';
          help.textContent = '开启自动分档时，单笔金额和最大持仓会按账户总估值自动计算；手动固定值主要用于关闭自动分档后的覆盖。';
          section.appendChild(help);
        }
        if (category === '防守震荡') {
          const help = document.createElement('div');
          help.className = 'settings-help';
          help.textContent = '资金池比例和单笔比例都按账户总估值计算，再受单笔最小/最大金额限制；资金变多会自动放大。';
          section.appendChild(help);
        }
        const grid = document.createElement('div');
        grid.className = 'settings-grid';
        items.forEach(field => {
          const wrap = document.createElement('div');
          wrap.className = 'field';
          const label = document.createElement('label');
          label.textContent = `${field.label} (${field.env})`;
          wrap.appendChild(label);
          if (field.kind === 'bool') {
            const select = document.createElement('select');
            select.dataset.configKey = field.key;
            select.innerHTML = '<option value="true">开启</option><option value="false">关闭</option>';
            select.value = String(Boolean(field.value));
            wrap.appendChild(select);
          } else {
            const input = document.createElement('input');
            input.dataset.configKey = field.key;
            input.value = field.value ?? '';
            input.placeholder = field.env;
            if (field.kind === 'float' || field.kind === 'int') input.inputMode = 'decimal';
            wrap.appendChild(input);
          }
          grid.appendChild(wrap);
        });
        section.appendChild(grid);
        root.appendChild(section);
      });
    }
    function configSettingsPayload() {
      const updates = {};
      document.querySelectorAll('[data-config-key]').forEach(input => {
        updates[input.dataset.configKey] = input.value;
      });
      return updates;
    }
    function settingsPayload(password) {
      return {
        dashboard_password: document.getElementById('setDashboardPassword').value,
        trading_toggle_password: document.getElementById('setTradingPassword').value,
        take_profit_pct: Number(document.getElementById('setTakeProfitPct').value || 0),
        apply_existing_take_profit: document.getElementById('setApplyTakeProfit').value,
        manual_buy_auto_sell: document.getElementById('setManualBuyAutoSell').value,
        smtp_host: document.getElementById('setSmtpHost').value.trim(),
        smtp_port: document.getElementById('setSmtpPort').value.trim(),
        smtp_username: document.getElementById('setSmtpUsername').value.trim(),
        smtp_password: document.getElementById('setSmtpPassword').value,
        smtp_from_name: document.getElementById('setSmtpFromName').value.trim(),
        report_recipient: document.getElementById('setReportRecipient').value.trim(),
        config_updates: configSettingsPayload()
      };
    }
    function renderBacktest(data) {
      const tbody = document.getElementById('backtestResults');
      tbody.innerHTML = '';
      const rows = data.results || [];
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="muted">暂无回测结果</td></tr>';
        return;
      }
      rows.forEach(row => {
        const tr = document.createElement('tr');
        const ret = Number(row.total_return_pct || 0);
        tr.innerHTML = `<td>${row.scenario || '--'}</td><td>${fmt(Number(row.take_profit_pct || 0) * 100, 2)}%</td><td>${fmt(row.final_value || 0, 2)}</td><td class="${ret >= 0 ? 'profit' : 'loss'}">${fmt(ret, 2)}%</td><td>${fmt(row.realized_net_pnl || 0, 2)}</td><td>${fmt(row.unrealized_pnl || 0, 2)}</td><td>${fmt(row.fees_paid || 0, 2)}</td><td>${row.buys || 0}/${row.sells || 0}</td><td>${row.open_lots || 0}</td><td>${fmt(row.max_drawdown_quote || 0, 2)}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['场景', '盈利比例', '总资产', '收益率', '已实现', '未实现', '手续费', '买/卖', '未平', '最大回撤']);
      const advice = document.getElementById('backtestAdvice');
      const intervalNote = data.interval ? `本次真实 K 线回测使用 ${data.interval} 粒度。` : '';
      const adviceText = data.recommendation && data.recommendation.text ? data.recommendation.text : '';
      advice.textContent = [intervalNote, adviceText].filter(Boolean).join(' ');
      advice.style.display = advice.textContent ? 'block' : 'none';
    }
    async function refresh() {
      try {
        if (!(await requireLogin())) return;
        const data = await apiGet('/api/status?range=' + encodeURIComponent(activeRange));
        manualBuyAutoSellDefault = Boolean(data.manual_buy_auto_sell);
        document.getElementById('symbol').textContent = data.symbol;
        document.getElementById('price').textContent = fmt(data.price, 2);
        document.getElementById('signal').textContent = data.signal;
        const executeButton = document.getElementById('execute');
        executeButton.textContent = data.execute_trades ? '交易已开' : '交易暂停';
        executeButton.className = 'action-button ' + (data.execute_trades ? 'on' : 'off');
        document.getElementById('value').textContent = fmt(data.value_quote, 2) + ' ' + data.quote_asset;
        const pnl = document.getElementById('pnl');
        pnl.textContent = fmt(data.pnl_quote, 2) + ' ' + data.quote_asset + ' / ' + fmt(data.pnl_pct, 2) + '%';
        pnl.className = 'value ' + (data.pnl_quote >= 0 ? 'profit' : 'loss');
        const baseLocked = Number(data.base_locked_balance || 0);
        const quoteLocked = Number(data.quote_locked_balance || 0);
        document.getElementById('base').textContent = fmt(data.base_balance, 8) + ' ' + data.base_asset + (baseLocked > 0 ? `（锁定 ${fmt(baseLocked, 8)}）` : '');
        document.getElementById('quote').textContent = fmt(data.quote_balance, 2) + ' ' + data.quote_asset + (quoteLocked > 0 ? `（锁定 ${fmt(quoteLocked, 2)}）` : '');
            const ledgerSync = data.ledger_sync || {};
            const ledgerSyncText = ledgerSync.mismatch
              ? `需同步：账本 ${fmt(ledgerSync.tracked_base_quantity || 0, 8)} / 账户 ${fmt(ledgerSync.account_base_balance || 0, 8)} ${data.base_asset}`
              : `正常：账本 ${fmt(ledgerSync.tracked_base_quantity || 0, 8)} / 账户 ${fmt(ledgerSync.account_base_balance || 0, 8)} ${data.base_asset}`;
            document.getElementById('ledgerSync').textContent = ledgerSyncText;
            const sizing = data.position_sizing || {};
            document.getElementById('lots').textContent = `${(data.open_lots || []).length} 批未平，当前新单约 ${fmt(sizing.order_quote_size || 0, 2)} ${data.quote_asset}，最大持仓约 ${fmt(sizing.max_position_quote || 0, 2)} ${data.quote_asset}`;
            document.getElementById('profitBrief').textContent = `已实现 ${fmt(data.realized_pnl || 0, 2)}，未平浮动 ${fmt(data.unrealized_lot_pnl || 0, 2)} ${data.quote_asset}`;
            const feeSummary = data.fee_summary || {};
            document.getElementById('fees').textContent = `未平 ${fmt(feeSummary.open_fee_quote || 0, 2)} / 已平 ${fmt(feeSummary.closed_fee_quote || 0, 2)} / 合计 ${fmt(feeSummary.total_fee_quote || 0, 2)} ${data.quote_asset}`;
        document.getElementById('buyStatus').textContent = data.risk && data.risk.allow_buy ? `允许自动买入，参考价 ${fmt(data.reference_price, 2)}` : `暂停普通买入：${zhReason(data.risk ? data.risk.reason : data.reason)}`;
        const defensive = data.defensive_mode || {};
        const defensiveReasons = defensive.reasons && defensive.reasons.length ? defensive.reasons.map(zhReason).join(' / ') : '未触发';
        const trend = data.trend_guard || {};
        document.getElementById('trendGuard').textContent = `${trend.downtrend ? '下跌趋势保护中' : '趋势正常'}；24小时均线 ${fmt(trend.ma24 || 0, 2)}，7日均线 ${fmt(trend.ma7d || 0, 2)}；${zhReason(trend.reason || '')}`;
        const swing = data.swing_band || {};
        document.getElementById('swing').textContent = `${swing.enabled ? '开启' : '关闭'}；买入线 ${fmt(swing.buy_price || 0, 2)}，目标线 ${fmt(swing.sell_price || 0, 2)}；已用 ${fmt(swing.position_quote || 0, 2)} / ${fmt(swing.allocation_quote || 0, 2)} ${data.quote_asset}`;
        const scalp = data.defensive_scalp || {};
        document.getElementById('scalp').textContent = `${scalp.enabled ? '开启' : '关闭'}；${scalp.active ? '防守期' : '等待防守'}，${scalp.range_bound ? '可吃小震荡' : '暂不适合'}；单笔约 ${fmt(scalp.order_quote_size || 0, 2)}，已用 ${fmt(scalp.position_quote || 0, 2)} / ${fmt(scalp.allocation_quote || 0, 2)} ${data.quote_asset}`;
        const summary = data.strategy_summary || {};
        document.getElementById('stratTP').textContent = fmt((summary.take_profit_pct || 0) * 100, 2) + '%';
        document.getElementById('stratGrid').textContent = fmt((summary.grid_step_pct || 0) * 100, 2) + '%';
        const sizingEl = document.getElementById('stratSizing');
        sizingEl.textContent = summary.auto_position_sizing ? '自动分档' : '手动分档';
        sizingEl.className = 'strat-value ' + (summary.auto_position_sizing ? 'strat-on' : '');
        const defEl = document.getElementById('stratDefensive');
        defEl.textContent = summary.defensive_mode ? '已开启' : '已关闭';
        defEl.className = 'strat-value ' + (summary.defensive_mode ? 'strat-on' : 'strat-off');
        document.getElementById('stratMaxLoss').textContent = fmt(summary.max_floating_loss_quote || 0, 2) + ' ' + data.quote_asset;
        document.getElementById('error').textContent = '--';
        updateMascotMarket(data);
        drawChart(data.price_history || [], data.reference_price, data.trades || []);
        document.getElementById('chartLabel').textContent = rangeLabels[activeRange] || activeRange;
        renderTrades(data.trades || []);
        renderOpenLots(data.open_lots || [], data.price, data.pending_orders || []);
        renderPendingOrders(data.pending_orders || []);
        renderClosedLots(data.closed_lots || []);
        document.getElementById('updated').textContent = '更新时间 ' + new Date().toLocaleString();
      } catch (err) {
        document.getElementById('error').textContent = String(err.message || err);
        document.getElementById('updated').textContent = '刷新失败 ' + new Date().toLocaleString();
      }
    }
    document.getElementById('loginButton').addEventListener('click', async () => {
      dashboardPassword = document.getElementById('loginPassword').value;
      if (!dashboardPassword) return;
      const rememberLogin = document.getElementById('loginRemember').checked;
      loginValidated = false;
      if (await requireLogin()) {
        sessionStorage.setItem('dashboardPassword', dashboardPassword);
        if (rememberLogin) {
          localStorage.setItem(loginCacheKey, JSON.stringify({
            password: dashboardPassword,
            expiry: Date.now() + 24 * 60 * 60 * 1000
          }));
        } else {
          localStorage.removeItem(loginCacheKey);
        }
        refresh();
        loadComments();
      }
    });
    document.getElementById('loginPassword').addEventListener('keydown', event => {
      if (event.key === 'Enter') document.getElementById('loginButton').click();
    });
    refresh();
    loadComments();
    setInterval(refresh, 5000);
    setInterval(loadComments, 60000);
    window.addEventListener('resize', () => drawChart(chartPoints, chartReference, chartTrades));
    function setActiveRange(range) {
      activeRange = range;
      chartZoom = { start: 0, end: 1 };
      document.querySelectorAll('.range-tabs button').forEach(item => item.classList.toggle('active', item.dataset.range === range));
      document.getElementById('chartLabel').textContent = rangeLabels[range] || range;
      refresh();
    }
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      if (rangeWheelLocked) return;
      rangeWheelLocked = true;
      setTimeout(() => { rangeWheelLocked = false; }, 260);
      if (activeRange === 'minute') {
        setActiveRange('5m');
        return;
      }
      let index = rangeOrder.indexOf(activeRange);
      if (index < 0) index = 0;
      const direction = event.deltaY > 0 ? 1 : -1;
      const nextIndex = Math.max(0, Math.min(rangeOrder.length - 1, index + direction));
      if (nextIndex !== index) setActiveRange(rangeOrder[nextIndex]);
    }, { passive: false });
    canvas.addEventListener('mousemove', event => {
      const hit = chartPointAt(event.clientX);
      if (!hit || !chartLayout) return;
      drawChart(chartPoints, chartReference, chartTrades);
      ctx.strokeStyle = '#94a3b8'; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(hit.x, chartLayout.pad.top); ctx.lineTo(hit.x, chartLayout.rect.height - chartLayout.pad.bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(chartLayout.pad.left, hit.y); ctx.lineTo(chartLayout.rect.width - chartLayout.pad.right, hit.y); ctx.stroke(); ctx.setLineDash([]);
      const box = canvas.getBoundingClientRect();
      chartTip.style.display = 'block';
      chartTip.style.left = Math.min(box.width - 190, Math.max(10, event.clientX - box.left + 14)) + 'px';
      chartTip.style.top = Math.max(54, event.clientY - box.top - 12) + 'px';
      chartTip.innerHTML = `<strong>${fmt(hit.point.close, 2)}</strong><br>开 ${fmt(hit.point.open ?? hit.point.close, 2)} / 高 ${fmt(hit.point.high ?? hit.point.close, 2)}<br>低 ${fmt(hit.point.low ?? hit.point.close, 2)} / 收 ${fmt(hit.point.close, 2)}<br>${new Date(hit.point.open_time).toLocaleString()}`;
    });
    canvas.addEventListener('mouseleave', () => {
      chartTip.style.display = 'none';
      drawChart(chartPoints, chartReference, chartTrades);
    });
    document.getElementById('tradePrev').addEventListener('click', () => { tradePage = Math.max(0, tradePage - 1); renderTrades(); });
    document.getElementById('tradeNext').addEventListener('click', () => { tradePage += 1; renderTrades(); });
    document.getElementById('closedPrev').addEventListener('click', () => { closedPage = Math.max(0, closedPage - 1); renderClosedLots(); });
    document.getElementById('closedNext').addEventListener('click', () => { closedPage += 1; renderClosedLots(); });
    document.getElementById('openPrev').addEventListener('click', () => {
      openPage = Math.max(0, openPage - 1);
      renderOpenLots(latestOpenLots, latestOpenPrice, latestPendingOrders);
    });
    document.getElementById('openNext').addEventListener('click', () => {
      openPage += 1;
      renderOpenLots(latestOpenLots, latestOpenPrice, latestPendingOrders);
    });
    document.getElementById('bulkAutoOn').addEventListener('click', () => bulkAutoSell(true));
    document.getElementById('bulkAutoOff').addEventListener('click', () => bulkAutoSell(false));
    document.getElementById('bulkMarketSell').addEventListener('click', () => bulkMarketSell());
    document.getElementById('bulkLimitSell').addEventListener('click', () => bulkLimitSell());
    document.getElementById('runBacktest').addEventListener('click', async () => {
      document.getElementById('backtestStatus').textContent = '回测运行中...';
      try {
        const data = await apiGet('/api/backtest', {
          mode: document.getElementById('backtestMode').value,
          start: document.getElementById('backtestStart').value.trim(),
          end: document.getElementById('backtestEnd').value.trim(),
          days: document.getElementById('backtestDays').value.trim(),
          take_profits: document.getElementById('backtestProfits').value.trim()
        });
        renderBacktest(data);
        document.getElementById('backtestStatus').textContent = `完成，初始资金 ${fmt(data.initial_quote || 0, 2)} USDT`;
      } catch (err) {
        document.getElementById('backtestStatus').textContent = err.message || String(err);
      }
    });
    document.getElementById('execute').addEventListener('click', async () => {
      const enabled = !document.getElementById('execute').classList.contains('on');
      const password = await uiPrompt(enabled ? '输入开关密码以开启交易' : '输入开关密码以暂停交易', '', 'password');
      if (!password) return;
      try { await apiGet('/api/trading', { execute_trades: enabled }, password); }
      catch (err) { await uiAlert(err.message || String(err), '操作失败'); }
      refresh();
    });
    document.getElementById('calibrate').addEventListener('click', async () => {
      const password = await uiPrompt('输入开关密码以校准当前资产为新基准', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm('确认把当前总资产设为新的盈亏基准？这会让看板的较启动基准盈亏从当前值重新计算。');
      if (!confirmed) return;
      await apiGet('/api/baseline/calibrate', {}, password);
      refresh();
    });
    document.getElementById('manualBuy').addEventListener('click', async () => {
      const orderType = await uiPrompt('输入买入类型：market 市价 / limit 限价', 'market');
      if (!orderType) return;
      const quoteSize = await uiPrompt('输入手动买入金额（USDT）。买入后会记账，但默认不自动卖出。', '10', 'number');
      if (!quoteSize) return;
      const autoSellText = await uiPrompt('这次人工买入是否自动卖出？输入 yes/no。默认跟随设置。', manualBuyAutoSellDefault ? 'yes' : 'no');
      if (autoSellText === null) return;
      const autoSell = ['1', 'true', 'yes', 'y', 'on', '是', '开'].includes(autoSellText.trim().toLowerCase());
      const targetProfitPct = await uiPrompt(autoSell ? '输入自动卖出目标利润百分比，例如 0.6 表示 0.6%。' : '输入参考目标利润百分比，例如 0.6 表示 0.6%。只作为参考卖价。', '0.6', 'number');
      if (targetProfitPct === null) return;
      let limitPrice = null;
      if (orderType.trim().toLowerCase() === 'limit') {
        limitPrice = await uiPrompt('输入限价买入价格。订单成交后才会记到账本。', '', 'number');
        if (!limitPrice) return;
      }
      const password = await uiPrompt('输入交易开关密码以确认手动买入', '', 'password');
      if (!password) return;
      const isLimit = orderType.trim().toLowerCase() === 'limit';
      const confirmed = await uiConfirm(isLimit ? `确认挂限价买入单：约 ${quoteSize} USDT，价格 ${limitPrice}？成交后才记账。自动卖出：${autoSell ? '是' : '否'}。` : `确认市价买入约 ${quoteSize} USDT 的 BTC，并记录为手动仓？自动卖出：${autoSell ? '是' : '否'}。`);
      if (!confirmed) return;
      try {
        await apiGet(isLimit ? '/api/manual/limit-buy' : '/api/manual/buy', { quote_size: quoteSize, limit_price: limitPrice, target_profit_pct: Number(targetProfitPct) / 100, auto_sell: autoSell }, password);
      } catch (err) { await uiAlert(err.message || String(err), '买入失败'); return; }
      refresh();
    });
    document.getElementById('externalLimitSell').addEventListener('click', async () => {
      const quantity = await uiPrompt('输入要限价卖出的 BTC 数量。这个操作只给账户可用 BTC 挂单，不会关闭任何账本批次。', '', 'number');
      if (!quantity) return;
      const limitPrice = await uiPrompt('输入限价卖出价格。成交后会记录在最近订单和限价挂单里。', '', 'number');
      if (!limitPrice) return;
      const password = await uiPrompt('输入交易开关密码以确认外部持仓限价卖出', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm(`确认挂外部持仓限价卖出单：数量 ${quantity} BTC，价格 ${limitPrice}？这不会关闭未平批次。`);
      if (!confirmed) return;
      try { await apiGet('/api/manual/external-limit-sell', { quantity, limit_price: limitPrice }, password); }
      catch (err) { await uiAlert(err.message || String(err), '挂单失败'); return; }
      refresh();
    });
    async function manualSell(lotId) {
      const password = await uiPrompt('输入交易开关密码以确认手动卖出', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm('确认市价卖出这个未平批次，并关闭账本记录？这是实盘下单，无法撤回。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/sell', { lot_id: lotId }, password); }
      catch (err) { await uiAlert(err.message || String(err), '卖出失败'); return; }
      refresh();
    }
    async function setLotAutoSell(lotId, enabled) {
      const password = await uiPrompt(enabled ? '输入交易开关密码以开启这个批次的自动卖出' : '输入交易开关密码以取消这个批次的自动卖出', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm(enabled ? '确认让这个批次到目标价后由脚本自动卖出？' : '确认取消这个批次的自动卖出？取消后脚本不会自动卖出它。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/auto-sell', { lot_id: lotId, auto_sell: enabled }, password); }
      catch (err) { await uiAlert(err.message || String(err), '设置失败'); return; }
      refresh();
    }
    async function limitSell(lotId, targetPrice) {
      const limitPrice = await uiPrompt('输入限价卖出价格。订单成交后才会关闭账本批次。', targetPrice || '', 'number');
      if (!limitPrice) return;
      const password = await uiPrompt('输入交易开关密码以确认限价卖出', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm(`确认为这个批次挂限价卖出单，价格 ${limitPrice}？成交前不会关闭账本。`);
      if (!confirmed) return;
      try { await apiGet('/api/manual/limit-sell', { lot_id: lotId, limit_price: limitPrice }, password); }
      catch (err) { await uiAlert(err.message || String(err), '挂单失败'); return; }
      refresh();
    }
    async function bulkAutoSell(enabled) {
      if (!latestOpenLots.length) return uiAlert('当前没有未平批次。');
      const password = await uiPrompt(enabled ? '输入交易开关密码以一键开启所有未平批次自动卖' : '输入交易开关密码以一键关闭所有未平批次自动卖', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm(`${enabled ? '开启' : '关闭'} ${latestOpenLots.length} 个未平批次的自动卖？`);
      if (!confirmed) return;
      for (const lot of latestOpenLots) {
        const result = await apiGet('/api/manual/auto-sell', { lot_id: lot.id, auto_sell: enabled }, password);
        if (result.error) return uiAlert(result.error, '设置失败');
      }
      refresh();
    }
    async function bulkMarketSell() {
      if (!latestOpenLots.length) return uiAlert('当前没有未平批次。');
      const password = await uiPrompt('输入交易开关密码以一键市价卖出所有未平批次', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm(`确认按市价卖出 ${latestOpenLots.length} 个未平批次？这是实盘下单，无法撤回。`);
      if (!confirmed) return;
      for (const lot of latestOpenLots) {
        const result = await apiGet('/api/manual/sell', { lot_id: lot.id }, password);
        if (result.error) return uiAlert(result.error, '卖出失败');
      }
      refresh();
    }
    async function bulkLimitSell() {
      if (!latestOpenLots.length) return uiAlert('当前没有未平批次。');
      const password = await uiPrompt('输入交易开关密码以一键限价卖出所有未平批次', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm(`确认按每个批次的预计卖价分别挂 ${latestOpenLots.length} 个限价卖单？`);
      if (!confirmed) return;
      for (const lot of latestOpenLots) {
        if (lot.pending_limit_sell_order_id) continue;
        const price = Number(lot.effective_target_price || lot.target_price || 0);
        if (!price) continue;
        const result = await apiGet('/api/manual/limit-sell', { lot_id: lot.id, limit_price: price }, password);
        if (result.error) return uiAlert(result.error, '挂单失败');
      }
      refresh();
    }
    async function cancelPendingOrder(orderId) {
      const password = await uiPrompt('输入交易开关密码以取消限价挂单', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm('确认取消这个限价挂单？如果已经成交，取消会失败或只取消未成交部分。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/cancel-order', { order_id: orderId }, password); }
      catch (err) { await uiAlert(err.message || String(err), '取消失败'); return; }
      refresh();
    }
    async function externalClose(lotId) {
      const sellPrice = await uiPrompt('输入你在币安外部卖出的成交价。这个操作只同步账本，不会再次下单。', '', 'number');
      if (!sellPrice) return;
      const quantity = await uiPrompt('输入卖出数量，留空表示关闭整个批次。', '', 'number');
      if (quantity === null) return;
      const password = await uiPrompt('输入交易开关密码以确认同步账本', '', 'password');
      if (!password) return;
      const confirmed = await uiConfirm('确认只按外部成交同步这个批次？不会向币安提交新订单。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/external-close', { lot_id: lotId, sell_price: sellPrice, quantity }, password); }
      catch (err) { await uiAlert(err.message || String(err), '同步失败'); return; }
      refresh();
    }
    async function openSettingsModal() {
      document.getElementById('settingsModal').classList.add('open');
      if (!settingsLoaded) {
        try { await loadSettings(); } catch (err) { setSettingsStatus(String(err.message || err), 'error'); }
      }
    }
    let latestComments = [];
    let danmakuTimers = [];
    function danmakuDuration() {
      return { slow: 26, normal: 18, fast: 11 }[document.getElementById('danmakuSpeed').value] || 18;
    }
    function clearDanmaku() {
      danmakuTimers.forEach(timer => clearTimeout(timer));
      danmakuTimers = [];
      document.getElementById('danmakuLayer').innerHTML = '';
    }
    function launchDanmaku(comments) {
      clearDanmaku();
      if (localStorage.getItem('dashboardDanmaku') === 'off') return;
      const layer = document.getElementById('danmakuLayer');
      const rows = (comments || []).filter(item => !item.parent_id).slice(-20);
      rows.forEach((item, index) => {
        const timer = setTimeout(() => {
          const node = document.createElement('div');
          node.className = 'danmaku-item';
          node.style.top = `${8 + (index % 5) * 32}px`;
          node.style.setProperty('--danmaku-duration', `${danmakuDuration()}s`);
          node.textContent = `${item.name}：${item.message}`;
          node.addEventListener('animationend', () => node.remove());
          layer.appendChild(node);
        }, index * 850);
        danmakuTimers.push(timer);
      });
    }
    function renderComments(comments) {
      latestComments = comments || [];
      const list = document.getElementById('commentList');
      list.innerHTML = '';
      const roots = latestComments.filter(item => !item.parent_id).slice().reverse();
      if (!roots.length) {
        list.innerHTML = '<div class="muted">还没有评论，欢迎留下第一条具体建议。</div>';
        clearDanmaku();
        return;
      }
      roots.forEach(item => {
        const card = document.createElement('article');
        card.className = 'comment-item';
        const meta = document.createElement('div');
        meta.className = 'comment-meta';
        const name = document.createElement('span');
        name.className = 'comment-name';
        name.textContent = item.name;
        meta.appendChild(name);
        if (item.is_author) {
          const badge = document.createElement('span');
          badge.className = 'author-badge';
          badge.textContent = '作者';
          meta.appendChild(badge);
        }
        const time = document.createElement('span');
        time.textContent = item.created_at ? new Date(item.created_at).toLocaleString() : '';
        meta.appendChild(time);
        const actions = document.createElement('span');
        actions.className = 'comment-actions';
        const reply = document.createElement('button');
        reply.className = 'secondary-button';
        reply.textContent = '作者回复';
        reply.addEventListener('click', () => authorReply(item.id));
        actions.appendChild(reply);
        meta.appendChild(actions);
        const body = document.createElement('div');
        body.className = 'comment-body';
        body.textContent = item.message;
        card.append(meta, body);
        const replies = latestComments.filter(replyItem => replyItem.parent_id === item.id);
        if (replies.length) {
          const replyList = document.createElement('div');
          replyList.className = 'comment-replies';
          replies.forEach(replyItem => {
            const row = document.createElement('div');
            row.className = 'comment-reply';
            const replyMeta = document.createElement('div');
            replyMeta.className = 'comment-meta';
            const replyName = document.createElement('span');
            replyName.className = 'comment-name';
            replyName.textContent = replyItem.name;
            replyMeta.appendChild(replyName);
            if (replyItem.is_author) {
              const badge = document.createElement('span');
              badge.className = 'author-badge';
              badge.textContent = '作者';
              replyMeta.appendChild(badge);
            }
            const replyTime = document.createElement('span');
            replyTime.textContent = replyItem.created_at ? new Date(replyItem.created_at).toLocaleString() : '';
            replyMeta.appendChild(replyTime);
            const replyBody = document.createElement('div');
            replyBody.className = 'comment-body';
            replyBody.textContent = replyItem.message;
            row.append(replyMeta, replyBody);
            replyList.appendChild(row);
          });
          card.appendChild(replyList);
        }
        list.appendChild(card);
      });
      launchDanmaku(latestComments);
    }
    async function loadComments() {
      if (!loginValidated) return;
      try {
        const data = await apiGet('/api/comments');
        renderComments(data.comments || []);
      } catch (err) {
        const list = document.getElementById('commentList');
        list.innerHTML = '';
        const message = document.createElement('div');
        message.className = 'muted';
        message.textContent = `评论读取失败：${String(err.message || err)}`;
        list.appendChild(message);
      }
    }
    async function authorReply(parentId) {
      const message = await uiPrompt('输入作者回复内容，发布后会显示“作者”标识。');
      if (!message) return;
      const password = await uiPrompt('输入交易管理密码以验证作者身份。', '', 'password');
      if (!password) return;
      try {
        await apiGet('/api/comments/reply', { parent_id: parentId, message }, password);
        showNotice('作者回复已发布。');
        loadComments();
      } catch (err) {
        await uiAlert(err.message || String(err), '回复失败');
      }
    }
    document.getElementById('settingsClose').addEventListener('click', () => {
      document.getElementById('settingsModal').classList.remove('open');
    });
    document.getElementById('settingsReload').addEventListener('click', async () => {
      try {
        await loadSettings();
        showNotice('设置已重新读取。', 'success', 4000);
      } catch (err) {
        const message = err.message || String(err);
        setSettingsStatus(message, 'error');
        showNotice(`读取设置失败：${message}`, 'error');
      }
    });
    document.getElementById('settingsSave').addEventListener('click', async () => {
      const password = await uiPrompt('输入当前交易开关密码以保存设置', '', 'password');
      if (!password) return;
      const saveButton = document.getElementById('settingsSave');
      saveButton.disabled = true;
      saveButton.textContent = '保存中...';
      setSettingsStatus('正在保存并校验设置...');
      try {
        await apiGet('/api/settings/update', settingsPayload(password), password);
        const profitResult = await apiGet('/api/strategy/take-profit', {
          take_profit_pct: Number(document.getElementById('setTakeProfitPct').value || 0),
          apply_existing: document.getElementById('setApplyTakeProfit').value === 'true'
        }, password);
        const lots = profitResult.lots || {};
        const message = `设置保存成功。盈利比例 ${fmt((profitResult.take_profit_pct || 0) * 100, 4)}%，未平批次更新 ${lots.updated || 0} 个，跳过 ${lots.skipped || 0} 个。`;
        setSettingsStatus(message, 'success');
        showNotice(message, 'success');
        settingsLoaded = false;
        try {
          await loadSettings(false);
        } catch (reloadError) {
          showNotice(`设置已保存，但重新读取失败：${reloadError.message || reloadError}`, 'error');
        }
        refresh();
      } catch (err) {
        const message = err.message || String(err);
        setSettingsStatus(message, 'error');
        showNotice(`设置保存失败：${message}`, 'error');
      } finally {
        saveButton.disabled = false;
        saveButton.textContent = '保存设置';
      }
    });
    document.getElementById('noticeClose').addEventListener('click', () => {
      document.getElementById('noticeToast').classList.remove('show');
      if (noticeTimer) clearTimeout(noticeTimer);
    });
    document.getElementById('themeFab').addEventListener('click', () => {
      document.getElementById('themeDock').classList.toggle('open');
    });
    document.addEventListener('click', event => {
      const dock = document.getElementById('themeDock');
      if (!dock.contains(event.target)) dock.classList.remove('open');
    });
    document.getElementById('openSettingsDock').addEventListener('click', event => {
      event.stopPropagation();
      document.getElementById('themeDock').classList.remove('open');
      openSettingsModal();
    });
    document.getElementById('quickTheme').addEventListener('click', () => {
      setTheme(document.body.dataset.theme === 'night' ? 'day' : 'night');
    });
    document.body.classList.toggle('layout-wide', localStorage.getItem('dashboardLayout') === 'wide');
    document.getElementById('layoutToggle').addEventListener('click', () => {
      const wide = !document.body.classList.contains('layout-wide');
      document.body.classList.toggle('layout-wide', wide);
      localStorage.setItem('dashboardLayout', wide ? 'wide' : 'compact');
      drawChart(chartPoints, chartReference, chartTrades);
    });
    document.getElementById('scrollTop').addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => setTheme(button.dataset.themeChoice));
    });
    document.getElementById('mascotToggle').addEventListener('click', () => {
      const hidden = document.getElementById('mascot').classList.contains('hidden');
      setMascotVisible(hidden);
    });
    const mascotImage = document.getElementById('mascotImage');
    let mascotRetries = 0;
    mascotImage.addEventListener('error', () => {
      mascotRetries += 1;
      if (mascotRetries <= 3) {
        setTimeout(() => { mascotImage.src = `/static/mascot-ai.png?v=1.0.7-${Date.now()}`; }, 800 * mascotRetries);
      } else {
        mascotImage.closest('.mascot-figure').classList.add('failed');
      }
    });
    mascotImage.addEventListener('load', () => mascotImage.closest('.mascot-figure').classList.remove('failed'));
    document.getElementById('commentName').value = localStorage.getItem('dashboardCommentName') || '';
    document.getElementById('commentSubmit').addEventListener('click', async () => {
      const name = document.getElementById('commentName').value.trim();
      const message = document.getElementById('commentMessage').value.trim();
      if (!name || !message) return uiAlert('请填写昵称和评论内容。');
      const button = document.getElementById('commentSubmit');
      button.disabled = true;
      button.textContent = '发布中...';
      try {
        await apiGet('/api/comments/add', { name, message });
        localStorage.setItem('dashboardCommentName', name);
        document.getElementById('commentMessage').value = '';
        showNotice('评论已发布。');
        await loadComments();
      } catch (err) {
        await uiAlert(err.message || String(err), '评论发布失败');
      } finally {
        button.disabled = false;
        button.textContent = '发表评论';
      }
    });
    const danmakuEnabled = localStorage.getItem('dashboardDanmaku') !== 'off';
    document.getElementById('danmakuLayer').classList.toggle('hidden', !danmakuEnabled);
    document.getElementById('danmakuToggle').textContent = danmakuEnabled ? '关闭弹幕' : '开启弹幕';
    document.getElementById('danmakuSpeed').value = localStorage.getItem('dashboardDanmakuSpeed') || 'normal';
    document.getElementById('danmakuToggle').addEventListener('click', () => {
      const enabled = localStorage.getItem('dashboardDanmaku') === 'off';
      localStorage.setItem('dashboardDanmaku', enabled ? 'on' : 'off');
      document.getElementById('danmakuLayer').classList.toggle('hidden', !enabled);
      document.getElementById('danmakuToggle').textContent = enabled ? '关闭弹幕' : '开启弹幕';
      if (enabled) launchDanmaku(latestComments); else clearDanmaku();
    });
    document.getElementById('danmakuSpeed').addEventListener('change', event => {
      localStorage.setItem('dashboardDanmakuSpeed', event.target.value);
      launchDanmaku(latestComments);
    });
    document.addEventListener('mouseover', event => {
      const target = event.target.closest('[data-help], button, th, .label, .panel-title, .strat-label, .field label, .account-table th, .summary-card span, .status-item .k');
      if (!target) return;
      const idHelp = {
        execute: '控制自动策略是否允许真实下单。手动交易仍需要二次确认密码。',
        manualBuy: '人工买入会写入账本，可以选择是否交给脚本自动卖出。',
        externalLimitSell: '用于卖出脚本账本外的 BTC 持仓，不会关闭任何未平批次。',
        calibrate: '把当前资产设为新的盈亏基准，适合充值后校准。',
        runBacktest: '用当前资金和策略参数跑模拟或历史 K 线回测。',
        bulkAutoOn: '一次开启所有未平批次的自动止盈卖出。',
        bulkAutoOff: '一次暂停所有未平批次的自动卖出，持仓不会被卖掉。',
        bulkMarketSell: '按市价卖出全部未平批次，属于不可撤回的真实交易。',
        bulkLimitSell: '按每个批次当前预计卖价分别提交限价单。',
        settingsReload: '放弃页面内尚未保存的修改，重新读取服务器配置。',
        settingsSave: '校验并保存全部系统设置，完成后会显示明确结果。',
        commentSubmit: '发布评论，并在启用弹幕时显示在页面上方。',
        danmakuToggle: '只控制当前浏览器是否显示弹幕，不会删除评论。',
        danmakuSpeed: '调整弹幕从右向左移动的速度，鼠标悬停会暂停。',
        tradePrev: '查看上一页近期订单。',
        tradeNext: '查看下一页近期订单。',
        openPrev: '查看上一页未平批次。',
        openNext: '查看下一页未平批次。',
        closedPrev: '查看上一页已平批次。',
        closedNext: '查看下一页已平批次。'
      };
      const textHelp = {
        '交易对': '当前脚本正在监控和交易的现货交易对。',
        '实时价格': '从币安接口获取的最新成交参考价。',
        '当前信号': '策略当前判断：买入、卖出、持有或暂停。',
        '当前资产估值': '账户 USDT 加 BTC 按当前价格折算后的总价值。',
        '较启动基准盈亏': '相对启动或手动校准基准的收益变化。',
        '未平批次': '脚本仍持有、尚未卖出的批次。',
        '已平批次': '已经卖出并结算盈亏的批次。',
        '限价挂单': '当前仍在交易所等待成交的限价委托。',
        '最近实盘订单': '脚本近期记录到的真实订单流水。',
        '账户与策略': '账户余额、风控、策略和当前执行状态摘要。',
        '行情走势': 'K 线图展示近期价格与买卖位置；滚轮可连续切换时间周期。',
        '策略回测': '使用当前资金和策略参数，对模拟数据或指定日期真实 K 线进行回放。',
        '止盈比例': '普通批次价格达到成本价加止盈空间后，才满足自动卖出条件。',
        '买入间距': '价格相对参考位下跌到指定间距后，普通网格才考虑新增批次。',
        '仓位分档': '决定单笔金额和最大持仓是否随账户资产自动缩放。',
        '防守模式': '持仓占用、浮亏或回撤达到条件时，会放慢普通补仓节奏。',
        '浮亏保护': '未平仓浮亏达到保护金额后，普通自动买入会暂停。',
        '账户余额': '账户 BTC 总量，包含可用和被限价单锁定的部分。',
        '可用现金': '账户 USDT 总量，锁定资金会单独标注。',
        '当前持仓': '未平批次数、新单参考金额和最大持仓额度。',
        '收益概况': '已结算利润与当前未平批次浮动盈亏的摘要。',
        '交易费用': '未平批次买入费、已平批次双端费用及累计费用。',
        '自动买入': '当前风控是否允许普通网格继续新增仓位。',
        '趋势判断': '根据 24 小时与 7 日均线判断是否进入下跌趋势保护。',
        '波段抄底': '独立资金池在较深位置寻找波段机会，不与普通网格混用。',
        '防守震荡': '防守期内用单独的小资金池尝试窄幅低买高卖。',
        '账本同步': '比较脚本记录的 BTC 数量与币安账户实际余额。',
        '提示': '展示接口、价格源或订单处理中的异常信息。',
        '限价': '委托只有达到指定价格后才可能成交，成交前资金会被锁定。',
        '数量': '交易或批次对应的 BTC 数量，为避免小额显示为零会保留更多小数。',
        '手续费': '该批次已记录的交易手续费。',
        '浮盈亏': '按当前市价估算，尚未真正结算。',
        '净利润': '扣除已记录手续费后的批次实际利润。',
        '评论内容': '评论最多 300 字，请勿填写账号密钥等敏感信息。',
        '昵称': '评论展示名称，会记在当前浏览器方便下次使用。'
      };
      const rawText = String(target.textContent || '').trim();
      const help = target.dataset.help || idHelp[target.id] || textHelp[rawText]
        || (target.tagName === 'BUTTON' ? `“${rawText}”用于执行当前页面对应操作，涉及交易时仍会要求密码和二次确认。` : '')
        || (target.tagName === 'TH' || target.matches('.field label, .strat-label, .account-table th') ? `这里显示或设置“${rawText}”，修改策略参数前建议先回测。` : '');
      if (help) mascotSay(help);
    });
    setTheme(localStorage.getItem('dashboardTheme') || 'day');
    setMascotVisible(localStorage.getItem('dashboardMascot') !== 'off');
    document.querySelectorAll('.range-tabs button').forEach(btn => {
      btn.addEventListener('click', () => setActiveRange(btn.dataset.range));
    });
  </script>
</body>
</html>
"""


def _config_setting_fields(config: AgentConfig) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for spec in CONFIG_SETTING_FIELDS:
        value = getattr(config, spec["key"])
        fields.append(
            {
                "key": spec["key"],
                "env": spec["env"],
                "label": spec["label"],
                "category": spec["category"],
                "kind": spec["kind"],
                "value": value,
            }
        )
    return fields


def _config_updates_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw_updates = payload.get("config_updates", {})
    if not isinstance(raw_updates, dict):
        return {}
    specs = {spec["key"]: spec for spec in CONFIG_SETTING_FIELDS}
    updates: dict[str, str] = {}
    for key, raw_value in raw_updates.items():
        spec = specs.get(str(key))
        if not spec:
            continue
        value = str(raw_value if raw_value is not None else "").strip()
        if value == "":
            continue
        if spec["kind"] == "bool":
            parsed = _payload_bool(value)
            updates[spec["env"]] = "true" if parsed else "false"
            continue
        if spec["kind"] == "int":
            parsed_int = int(float(value))
            if parsed_int < 0:
                raise ValueError(f"{spec['env']} must be non-negative")
            updates[spec["env"]] = str(parsed_int)
            continue
        if spec["kind"] == "float":
            parsed_float = float(value)
            if parsed_float < 0:
                raise ValueError(f"{spec['env']} must be non-negative")
            updates[spec["env"]] = f"{parsed_float:.10f}".rstrip("0").rstrip(".")
            continue
        updates[spec["env"]] = value
    return updates


class Dashboard:
    def __init__(self, config: AgentConfig, baseline_path: Path, trades_path: Path, state_path: Path) -> None:
        self.config = config
        self.baseline_path = baseline_path
        self.trades_path = trades_path
        self.state_path = state_path
        self.control_path = Path("data/control.json")
        self.ledger = PositionLedger(Path(f"data/lots_{config.symbol}.json"))
        self.pending_path = Path(f"data/pending_orders_{config.symbol}.json")
        self.pending_store = SQLiteJsonListStore(self.pending_path, "orders")
        self.comments_store = SQLiteJsonListStore(Path("data/comments.json"), "comments")
        self.client = BinanceSpotClient(config.base_url, config.api_key, config.api_secret)
        self.strategy = GridStrategy(
            grid_step_pct=config.grid_step_pct,
            take_profit_pct=config.take_profit_pct,
            order_quote_size=config.order_quote_size,
            max_position_quote=config.max_position_quote,
        )

    def comments(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.comments_store.load()[-max(1, min(limit, 200)):]

    def add_comment(
        self,
        name: str,
        message: str,
        parent_id: str = "",
        is_author: bool = False,
    ) -> dict[str, Any]:
        clean_name = ("作者" if is_author else name.strip())[:20]
        clean_message = message.strip()[:300]
        clean_parent = parent_id.strip()[:80]
        if not clean_name:
            return {"error": "请输入昵称"}
        if not clean_message:
            return {"error": "请输入评论内容"}
        if clean_parent and not any(str(item.get("id")) == clean_parent for item in self.comments_store.load()):
            return {"error": "原评论不存在或已被删除"}
        row = {
            "id": f"{int(datetime.now().timestamp() * 1000)}-{secrets.token_hex(4)}",
            "parent_id": clean_parent,
            "name": clean_name,
            "message": clean_message,
            "is_author": is_author,
            "created_at": _utc_now(),
        }
        self.comments_store.update(lambda rows: rows.append(row))
        return {"ok": True, "comment": row}

    def status(self, range_key: str = "minute") -> dict[str, Any]:
        pending_orders = self.sync_pending_orders()
        price = self.client.ticker_price(self.config.symbol)
        interval, limit = self.chart_window(range_key)
        klines = self.client.klines(self.config.symbol, interval=interval, limit=limit)
        reference_klines = self.client.klines(self.config.symbol, interval="1m", limit=60) if interval != "1m" else klines
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        base_free, base_locked, base_balance = _balance_parts(balances, self.config.base_asset)
        quote_free, quote_locked, quote_balance = _balance_parts(balances, self.config.quote_asset)
        snapshot = MarketSnapshot(
            symbol=self.config.symbol,
            price=price,
            recent_closes=[float(item[4]) for item in reference_klines],
            base_balance=base_balance,
            quote_balance=quote_balance,
        )
        raw_open_lots = self.ledger.open_lots()
        raw_grid_lots, raw_swing_lots = split_lots(raw_open_lots)
        raw_scalp_lots = [lot for lot in raw_open_lots if is_scalp_lot(lot)]
        open_lots = [self._lot_with_fee(lot) for lot in self._lots_for_strategy(raw_grid_lots) + raw_swing_lots + raw_scalp_lots]
        sizing = position_sizing(
            quote_balance + base_balance * price,
            self.config.order_quote_size,
            self.config.max_position_quote,
            self.config.auto_position_sizing,
        )
        reference_closes = [float(item[4]) for item in reference_klines]
        defensive = self.defensive(price, reference_closes, raw_grid_lots, sizing.max_position_quote)
        strategy = GridStrategy(
            grid_step_pct=self.config.grid_step_pct,
            take_profit_pct=self.config.take_profit_pct,
            order_quote_size=sizing.order_quote_size,
            max_position_quote=sizing.max_position_quote,
            add_on_step_pct=defensive["add_on_step_pct"],
        )
        trend_guard = self.trend_guard(price, sizing.max_position_quote, raw_grid_lots, raw_swing_lots)
        decision = strategy.decide(snapshot, self.grid_state(), [lot for lot in open_lots if not str(lot.get("level", "")).startswith(("swing-", "scalp-"))])
        decision = self._trend_guard().apply_to_grid(decision, trend_guard)
        swing_band = self.swing_band(price, raw_swing_lots, quote_balance + base_balance * price)
        scalp_state = self.defensive_scalp(price, reference_closes, raw_scalp_lots, quote_balance + base_balance * price, defensive["active"])
        metrics = portfolio_metrics(
            self.baseline_path,
            symbol=self.config.symbol,
            base_asset=self.config.base_asset,
            quote_asset=self.config.quote_asset,
            price=price,
            base_balance=base_balance,
            quote_balance=quote_balance,
        )
        return {
            **metrics_asdict(metrics),
            "signal": decision.signal.value,
            "reason": decision.reason,
            "reference_price": decision.reference_price,
            "execute_trades": self.execute_trades_enabled(),
            "risk": self.risk(price, reference_closes),
            "defensive_mode": defensive,
            "trend_guard": trend_guard.to_dict(),
            "swing_band": swing_band,
            "defensive_scalp": scalp_state,
            "chart_range": range_key,
            "chart_interval": interval,
            "price_history": [
                {
                    "open_time": int(item[0]),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
                for item in klines
            ],
            "trades": self.trades(),
            "open_lots": open_lots,
            "pending_orders": pending_orders,
            "base_free_balance": base_free,
            "base_locked_balance": base_locked,
            "quote_free_balance": quote_free,
            "quote_locked_balance": quote_locked,
            "ledger_sync": self.ledger_sync(base_balance, open_lots),
            "closed_lots": self.closed_lots(),
            "realized_pnl": self.ledger.realized_pnl(self.config.trading_fee_rate),
            "unrealized_lot_pnl": self.ledger.unrealized_pnl(price),
            "fee_summary": self.ledger.fee_summary(self.config.trading_fee_rate),
            "manual_buy_auto_sell": self.manual_buy_auto_sell_enabled(),
            "position_sizing": {
                "total_value_quote": sizing.total_value_quote,
                "order_quote_size": sizing.order_quote_size,
                "max_position_quote": sizing.max_position_quote,
                "tier": sizing.tier,
                "enabled": sizing.enabled,
            },
            "strategy_summary": {
                "take_profit_pct": self.config.take_profit_pct,
                "grid_step_pct": self.config.grid_step_pct,
                "auto_position_sizing": self.config.auto_position_sizing,
                "defensive_mode": self.config.defensive_mode,
                "max_floating_loss_quote": self.config.max_floating_loss_quote,
            },
        }

    @staticmethod
    def chart_window(range_key: str) -> tuple[str, int]:
        windows = {
            "minute": ("1m", 60),
            "5m": ("1m", 5),
            "15m": ("1m", 15),
            "1h": ("1m", 60),
            "4h": ("5m", 48),
            "day": ("15m", 96),
            "week": ("1h", 168),
        }
        return windows.get(range_key, windows["minute"])

    def closed_lots(self, limit: int = 200) -> list[dict[str, Any]]:
        lots = [lot for lot in self.ledger.lots() if lot.get("status") == "closed"]
        return [self._lot_with_fee(lot) for lot in lots[-limit:]]

    def _lot_with_fee(self, lot: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(lot)
        if is_swing_lot(enriched):
            target_price = float(enriched.get("target_price") or 0)
            enriched["effective_target_price"] = target_price
            enriched["target_price_adjusted"] = False
            enriched["target_note"] = "swing"
            enriched["target_profit_pct_effective"] = (
                (target_price / float(enriched.get("buy_price") or 1)) - 1
                if target_price > 0 and float(enriched.get("buy_price") or 0) > 0
                else 0.0
            )
        if is_scalp_lot(enriched):
            target_price = float(enriched.get("target_price") or 0)
            enriched["effective_target_price"] = target_price
            enriched["target_price_adjusted"] = False
            enriched["target_note"] = "scalp"
            enriched["target_profit_pct_effective"] = (
                (target_price / float(enriched.get("buy_price") or 1)) - 1
                if target_price > 0 and float(enriched.get("buy_price") or 0) > 0
                else 0.0
            )
        if enriched.get("effective_target_price") is None:
            enriched = enrich_lot_with_defensive_target(
                enriched,
                enabled=self.config.defensive_mode,
                target_profit_pct=self.config.take_profit_pct,
                trading_fee_rate=self.config.trading_fee_rate,
                aged_days_1=self.config.defensive_aged_lot_days_1,
                aged_profit_pct_1=self.config.defensive_aged_lot_profit_pct_1,
                aged_days_2=self.config.defensive_aged_lot_days_2,
                aged_profit_pct_2=self.config.defensive_aged_lot_profit_pct_2,
            )
        fee_quote = self.ledger.lot_fee_quote(enriched, self.config.trading_fee_rate)
        enriched["fee_quote"] = fee_quote
        if enriched.get("status") == "closed":
            enriched["net_realized_pnl"] = self.ledger.lot_net_realized_pnl(enriched, self.config.trading_fee_rate)
        return enriched

    def _lots_for_strategy(self, open_lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return enrich_lots_with_defensive_targets(
            open_lots,
            enabled=self.config.defensive_mode,
            target_profit_pct=self.config.take_profit_pct,
            trading_fee_rate=self.config.trading_fee_rate,
            aged_days_1=self.config.defensive_aged_lot_days_1,
            aged_profit_pct_1=self.config.defensive_aged_lot_profit_pct_1,
            aged_days_2=self.config.defensive_aged_lot_days_2,
            aged_profit_pct_2=self.config.defensive_aged_lot_profit_pct_2,
        )

    def defensive(
        self,
        price: float,
        closes: list[float],
        open_lots: list[dict[str, Any]],
        max_position_quote: float,
    ) -> dict[str, Any]:
        return evaluate_defensive_mode(
            enabled=self.config.defensive_mode,
            price=price,
            recent_closes=closes,
            open_lots=open_lots,
            max_position_quote=max_position_quote,
            unrealized_pnl=self.ledger.unrealized_pnl(price),
            normal_add_on_step_pct=self.config.defensive_normal_add_on_step_pct,
            defensive_add_on_step_pct=self.config.defensive_add_on_step_pct,
            position_usage_trigger=self.config.defensive_position_usage_trigger,
            floating_loss_trigger_quote=self.config.defensive_floating_loss_quote,
            recent_drawdown_trigger_pct=self.config.defensive_recent_drawdown_pct,
        ).to_dict()

    def swing_band(
        self,
        price: float,
        swing_lots: list[dict[str, Any]],
        total_value_quote: float,
    ) -> dict[str, Any]:
        klines = self.client.klines(
            self.config.symbol,
            interval=self.config.swing_kline_interval,
            limit=self.config.swing_kline_limit,
        )
        closes = [float(item[4]) for item in klines]
        return SwingStrategy(
            enabled=self.config.swing_strategy,
            allocation_pct=self.config.swing_allocation_pct,
            min_order_quote=self.config.swing_min_order_quote,
            max_order_quote=self.config.swing_max_order_quote,
            add_step_pct=self.config.swing_add_step_pct,
            min_band_pct=self.config.swing_min_band_pct,
            max_band_pct=self.config.swing_max_band_pct,
            manual_center_price=self.config.swing_manual_center_price,
        ).band(price, closes, swing_lots, total_value_quote).to_dict()

    def defensive_scalp(
        self,
        price: float,
        recent_closes: list[float],
        scalp_lots: list[dict[str, Any]],
        total_value_quote: float,
        defensive_active: bool,
    ) -> dict[str, Any]:
        snapshot = MarketSnapshot(self.config.symbol, price, recent_closes, 0.0, 0.0)
        return DefensiveScalpStrategy(
            enabled=self.config.defensive_scalp,
            allocation_pct=self.config.defensive_scalp_allocation_pct,
            order_pct=self.config.defensive_scalp_order_pct,
            min_order_quote=self.config.defensive_scalp_min_order_quote,
            max_order_quote=self.config.defensive_scalp_max_order_quote,
            buy_drop_pct=self.config.defensive_scalp_buy_drop_pct,
            take_profit_pct=self.config.defensive_scalp_take_profit_pct,
            add_step_pct=self.config.defensive_scalp_add_step_pct,
            min_range_pct=self.config.defensive_scalp_min_range_pct,
            max_range_pct=self.config.defensive_scalp_max_range_pct,
            trading_fee_rate=self.config.trading_fee_rate,
        ).state(snapshot, recent_closes, scalp_lots, total_value_quote, defensive_active).to_dict()

    def _trend_guard(self) -> TrendGuard:
        return TrendGuard(
            enabled=self.config.trend_guard,
            normal_pool_pct=self.config.trend_normal_pool_pct,
            dip_pool_pct=self.config.trend_dip_pool_pct,
            dip_order_quote=self.config.trend_dip_order_quote,
            rebound_pct=self.config.trend_rebound_pct,
            interval_minutes=_interval_minutes(self.config.trend_kline_interval),
        )

    def trend_guard(
        self,
        price: float,
        max_position_quote: float,
        grid_lots: list[dict[str, Any]],
        swing_lots: list[dict[str, Any]],
    ):
        klines = self.client.klines(
            self.config.symbol,
            interval=self.config.trend_kline_interval,
            limit=self.config.trend_kline_limit,
        )
        closes = [float(item[4]) for item in klines]
        return self._trend_guard().evaluate(
            price,
            closes,
            max_position_quote,
            _position_quote(grid_lots, price),
            _position_quote(swing_lots, price),
        )

    def risk(self, price: float, closes: list[float]) -> dict[str, Any]:
        from .risk import evaluate_buy_risk

        decision = evaluate_buy_risk(
            price=price,
            recent_closes=closes,
            unrealized_pnl=self.ledger.unrealized_pnl(price),
            max_floating_loss_quote=self.config.max_floating_loss_quote,
            rapid_drop_pause_pct=self.config.rapid_drop_pause_pct,
            large_drop_pause_pct=self.config.large_drop_pause_pct,
            rebound_buy_pct=self.config.rebound_buy_pct,
            price_anomaly_pct=self.config.price_anomaly_pct,
        )
        return {"allow_buy": decision.allow_buy, "reason": decision.reason, "rebound": decision.rebound}

    def ledger_sync(self, base_balance: float, open_lots: list[dict[str, Any]]) -> dict[str, Any]:
        tracked_qty = sum(float(lot.get("remaining_quantity", 0) or 0) for lot in open_lots)
        difference = tracked_qty - base_balance
        return {
            "tracked_base_quantity": tracked_qty,
            "account_base_balance": base_balance,
            "difference": difference,
            "mismatch": difference > 0.00000001,
        }

    def execute_trades_enabled(self) -> bool:
        if not self.control_path.exists():
            return self.config.execute_trades
        try:
            payload = json.loads(self.control_path.read_text())
        except json.JSONDecodeError:
            return self.config.execute_trades
        return bool(payload.get("execute_trades", self.config.execute_trades))

    def set_execute_trades(self, enabled: bool) -> dict[str, bool]:
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        self.control_path.write_text(json.dumps({"execute_trades": enabled}, indent=2, sort_keys=True) + "\n")
        return {"execute_trades": enabled}

    def calibrate_baseline(self) -> dict[str, Any]:
        price = self.client.ticker_price(self.config.symbol)
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        _, _, base_balance = _balance_parts(balances, self.config.base_asset)
        _, _, quote_balance = _balance_parts(balances, self.config.quote_asset)
        return reset_baseline(
            self.baseline_path,
            symbol=self.config.symbol,
            base_asset=self.config.base_asset,
            quote_asset=self.config.quote_asset,
            price=price,
            base_balance=base_balance,
            quote_balance=quote_balance,
            note="manual dashboard calibration",
        )

    def backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode", "synthetic") or "synthetic")
        take_profits = _parse_profit_list(str(payload.get("take_profits", "") or "0.6,0.8,1.0,1.2"))
        initial_quote = self.current_total_value_quote()
        results: list[dict[str, Any]] = []
        if mode == "historical":
            from .historical_backtest import _date_ms, fetch_close_prices

            start = str(payload.get("start", "") or "").strip()
            end = str(payload.get("end", "") or "").strip()
            if not start or not end:
                return {"error": "historical backtest requires start and end dates"}
            interval = _historical_backtest_interval(start, end)
            prices = fetch_close_prices(self.config.base_url, self.config.symbol, interval, _date_ms(start), _date_ms(end))
            if len(prices) < 120:
                return {"error": f"not enough kline data: {len(prices)}"}
            for profit in take_profits:
                result = run_backtest(
                    f"{start}..{end} {interval}",
                    prices,
                    self._backtest_config(initial_quote, prices[0], len(prices), profit),
                )
                item = asdict(result)
                item["take_profit_pct"] = profit
                item["interval"] = interval
                results.append(item)
        else:
            days = max(1, min(90, int(float(payload.get("days", 30) or 30))))
            current_price = self.client.ticker_price(self.config.symbol)
            for profit in take_profits:
                config = self._backtest_config(initial_quote, current_price, days * 24 * 60, profit)
                for result in run_scenarios(config):
                    item = asdict(result)
                    item["take_profit_pct"] = profit
                    results.append(item)
        return {
            "mode": mode,
            "initial_quote": initial_quote,
            "take_profits": take_profits,
            "results": results,
            "interval": results[0].get("interval") if results else None,
            "recommendation": _backtest_recommendation(results, mode),
        }

    def update_take_profit(self, payload: dict[str, Any]) -> dict[str, Any]:
        profit = _parse_profit_value(payload.get("take_profit_pct"))
        if profit <= 0:
            return {"error": "take profit must be positive"}
        if profit > 0.05:
            return {"error": "take profit is too large; use a value such as 1.0 for 1%"}
        updates = {"TAKE_PROFIT_PCT": f"{profit:.6f}".rstrip("0").rstrip(".")}
        _update_dotenv(Path(".env"), updates)
        os.environ.update(updates)
        self._reload_config()
        applied = {"updated": 0, "skipped": 0}
        if _payload_bool(payload.get("apply_existing")):
            applied = self.ledger.retarget_open_lots(profit, self.config.trading_fee_rate)
        return {
            "take_profit_pct": profit,
            "apply_existing": bool(_payload_bool(payload.get("apply_existing"))),
            "lots": applied,
        }

    def _reload_config(self) -> None:
        latest = AgentConfig.from_env()
        if latest.symbol != self.config.symbol:
            return
        if latest.base_url != self.config.base_url or latest.api_key != self.config.api_key or latest.api_secret != self.config.api_secret:
            self.client = BinanceSpotClient(latest.base_url, latest.api_key, latest.api_secret)
        self.config = latest

    def current_total_value_quote(self) -> float:
        price = self.client.ticker_price(self.config.symbol)
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        _, _, base_balance = _balance_parts(balances, self.config.base_asset)
        _, _, quote_balance = _balance_parts(balances, self.config.quote_asset)
        return quote_balance + base_balance * price

    def _backtest_config(self, initial_quote: float, initial_price: float, minutes: int, take_profit_pct: float) -> BacktestConfig:
        return BacktestConfig(
            initial_quote=initial_quote,
            initial_price=initial_price,
            minutes=minutes,
            order_quote_size=self.config.order_quote_size,
            auto_position_sizing=self.config.auto_position_sizing,
            max_position_quote=self.config.max_position_quote,
            grid_step_pct=self.config.grid_step_pct,
            take_profit_pct=take_profit_pct,
            trading_fee_rate=self.config.trading_fee_rate,
            max_floating_loss_quote=self.config.max_floating_loss_quote,
            rapid_drop_pause_pct=self.config.rapid_drop_pause_pct,
            large_drop_pause_pct=self.config.large_drop_pause_pct,
            rebound_buy_pct=self.config.rebound_buy_pct,
            price_anomaly_pct=self.config.price_anomaly_pct,
            defensive_mode=self.config.defensive_mode,
            defensive_position_usage_trigger=self.config.defensive_position_usage_trigger,
            defensive_floating_loss_quote=self.config.defensive_floating_loss_quote,
            defensive_recent_drawdown_pct=self.config.defensive_recent_drawdown_pct,
            defensive_normal_add_on_step_pct=self.config.defensive_normal_add_on_step_pct,
            defensive_add_on_step_pct=self.config.defensive_add_on_step_pct,
            defensive_aged_lot_days_1=self.config.defensive_aged_lot_days_1,
            defensive_aged_lot_profit_pct_1=self.config.defensive_aged_lot_profit_pct_1,
            defensive_aged_lot_days_2=self.config.defensive_aged_lot_days_2,
            defensive_aged_lot_profit_pct_2=self.config.defensive_aged_lot_profit_pct_2,
            defensive_scalp=self.config.defensive_scalp,
            defensive_scalp_allocation_pct=self.config.defensive_scalp_allocation_pct,
            defensive_scalp_order_pct=self.config.defensive_scalp_order_pct,
            defensive_scalp_min_order_quote=self.config.defensive_scalp_min_order_quote,
            defensive_scalp_max_order_quote=self.config.defensive_scalp_max_order_quote,
            defensive_scalp_buy_drop_pct=self.config.defensive_scalp_buy_drop_pct,
            defensive_scalp_take_profit_pct=self.config.defensive_scalp_take_profit_pct,
            defensive_scalp_add_step_pct=self.config.defensive_scalp_add_step_pct,
            defensive_scalp_min_range_pct=self.config.defensive_scalp_min_range_pct,
            defensive_scalp_max_range_pct=self.config.defensive_scalp_max_range_pct,
        )

    def manual_buy(self, quote_size: float, target_profit_pct: float, auto_sell: bool | None = None) -> dict[str, Any]:
        if quote_size <= 0:
            return {"error": "manual buy quote size must be positive"}
        target_profit_pct = max(0.0, target_profit_pct)
        auto_sell_enabled = self.manual_buy_auto_sell_enabled() if auto_sell is None else auto_sell
        filters = self.client.symbol_filters(self.config.symbol)
        if Decimal(str(quote_size)) < filters.min_notional:
            return {"error": f"quote size below minNotional {filters.min_notional}"}
        order = self.client.market_buy_quote(self.config.symbol, quote_size)
        lot = self.ledger.add_buy(
            self.config.symbol,
            order,
            target_profit_pct,
            self.config.trading_fee_rate,
            "manual-entry",
            None,
            auto_sell_enabled,
        )
        self._record_manual_trade("MANUAL_BUY", quote_size, order, lot)
        return {"order": order, "lot": lot}

    def manual_limit_buy(
        self,
        quote_size: float,
        limit_price: float,
        target_profit_pct: float,
        auto_sell: bool | None = None,
    ) -> dict[str, Any]:
        if quote_size <= 0:
            return {"error": "manual limit buy quote size must be positive"}
        if limit_price <= 0:
            return {"error": "limit price must be positive"}
        filters = self.client.symbol_filters(self.config.symbol)
        price = self.client.round_price(Decimal(str(limit_price)), filters)
        quantity = self.client.round_quantity(Decimal(str(quote_size)) / price, filters)
        if quantity < filters.min_qty:
            return {"error": f"quantity below minQty {filters.min_qty}"}
        if quantity * price < filters.min_notional:
            return {"error": f"quote size below minNotional {filters.min_notional}"}
        auto_sell_enabled = self.manual_buy_auto_sell_enabled() if auto_sell is None else auto_sell
        order = self.client.limit_buy_qty(self.config.symbol, quantity, price)
        pending = self.add_pending_order(
            {
                "order_id": int(order["orderId"]),
                "side": "BUY",
                "level": "manual-limit-buy",
                "limit_price": float(price),
                "quantity": float(quantity),
                "quote_size": float(quantity * price),
                "target_profit_pct": max(0.0, target_profit_pct),
                "auto_sell": auto_sell_enabled,
                "status": order.get("status", "NEW"),
                "created_at": _utc_now(),
            }
        )
        self._record_manual_trade("LIMIT_BUY_PLACED", float(quantity * price), order, pending)
        return {"order": order, "pending": pending}

    def manual_sell(self, lot_id: str) -> dict[str, Any]:
        lot = next((item for item in self.ledger.open_lots() if item.get("id") == lot_id), None)
        if not lot:
            return {"error": "open lot not found"}
        quantity = Decimal(str(lot.get("remaining_quantity", 0) or 0))
        if quantity <= 0:
            return {"error": "lot has no remaining quantity"}
        filters = self.client.symbol_filters(self.config.symbol)
        rounded_qty = self.client.round_quantity(quantity, filters)
        if rounded_qty < filters.min_qty:
            return {"error": f"quantity below minQty {filters.min_qty}"}
        price = self.client.ticker_price(self.config.symbol)
        if rounded_qty * Decimal(str(price)) < filters.min_notional:
            return {"error": f"quantity below minNotional {filters.min_notional}"}
        order = self.client.market_sell_qty(self.config.symbol, rounded_qty)
        updated = self.ledger.close_lot(lot_id, order, self.config.trading_fee_rate)
        self._record_manual_trade("MANUAL_SELL", float(rounded_qty) * price, order, updated)
        return {"order": order, "lot": updated}

    def set_manual_lot_auto_sell(self, lot_id: str, enabled: bool) -> dict[str, Any]:
        lot = next((item for item in self.ledger.open_lots() if item.get("id") == lot_id), None)
        if not lot:
            return {"error": "open lot not found"}
        updated = self.ledger.set_auto_sell(lot_id, enabled)
        if not updated:
            return {"error": "failed to update auto sell"}
        self._record_manual_trade("AUTO_SELL_ENABLED" if enabled else "AUTO_SELL_DISABLED", 0, {}, updated)
        return {"lot": updated}

    def manual_limit_sell(self, lot_id: str, limit_price: float) -> dict[str, Any]:
        if limit_price <= 0:
            return {"error": "limit price must be positive"}
        lot = self.reserve_lot_pending_sell(lot_id, limit_price)
        if not lot:
            return {"error": "没有找到这个未平批次，或这个批次已经有未成交限价卖单。如果你要卖的是脚本外部持仓，请使用“外部持仓限价卖出”。"}
        quantity = Decimal(str(lot.get("remaining_quantity", 0) or 0))
        if quantity <= 0:
            self.clear_lot_pending_sell(lot_id)
            return {"error": "lot has no remaining quantity"}
        filters = self.client.symbol_filters(self.config.symbol)
        price = self.client.round_price(Decimal(str(limit_price)), filters)
        rounded_qty = self.client.round_quantity(quantity, filters)
        if rounded_qty < filters.min_qty:
            self.clear_lot_pending_sell(lot_id)
            return {"error": f"quantity below minQty {filters.min_qty}"}
        if rounded_qty * price < filters.min_notional:
            self.clear_lot_pending_sell(lot_id)
            return {"error": f"quantity below minNotional {filters.min_notional}"}
        try:
            order = self.client.limit_sell_qty(self.config.symbol, rounded_qty, price)
        except BinanceAPIError:
            self.clear_lot_pending_sell(lot_id)
            raise
        self.mark_lot_pending_sell(lot_id, int(order["orderId"]), float(price))
        pending = self.add_pending_order(
            {
                "order_id": int(order["orderId"]),
                "side": "SELL",
                "level": "manual-limit-sell",
                "lot_id": lot_id,
                "limit_price": float(price),
                "quantity": float(rounded_qty),
                "quote_size": float(rounded_qty * price),
                "status": order.get("status", "NEW"),
                "created_at": _utc_now(),
            }
        )
        self._record_manual_trade("LIMIT_SELL_PLACED", float(rounded_qty * price), order, pending)
        return {"order": order, "pending": pending}

    def manual_external_limit_sell(self, quantity: float, limit_price: float) -> dict[str, Any]:
        if quantity <= 0:
            return {"error": "sell quantity must be positive"}
        if limit_price <= 0:
            return {"error": "limit price must be positive"}
        account = self.client.account()
        balances = {item["asset"]: item for item in account.get("balances", [])}
        base_free, _, _ = _balance_parts(balances, self.config.base_asset)
        filters = self.client.symbol_filters(self.config.symbol)
        price = self.client.round_price(Decimal(str(limit_price)), filters)
        rounded_qty = self.client.round_quantity(Decimal(str(quantity)), filters)
        if rounded_qty <= 0:
            return {"error": "quantity becomes zero after exchange step-size rounding"}
        if rounded_qty > Decimal(str(base_free)):
            return {"error": f"available {self.config.base_asset} balance is only {base_free}"}
        if rounded_qty < filters.min_qty:
            return {"error": f"quantity below minQty {filters.min_qty}"}
        if rounded_qty * price < filters.min_notional:
            return {"error": f"quantity below minNotional {filters.min_notional}"}
        order = self.client.limit_sell_qty(self.config.symbol, rounded_qty, price)
        pending = self.add_pending_order(
            {
                "order_id": int(order["orderId"]),
                "side": "SELL",
                "level": "manual-external-limit-sell",
                "limit_price": float(price),
                "quantity": float(rounded_qty),
                "quote_size": float(rounded_qty * price),
                "status": order.get("status", "NEW"),
                "created_at": _utc_now(),
            }
        )
        self._record_manual_trade("EXTERNAL_LIMIT_SELL_PLACED", float(rounded_qty * price), order, pending)
        return {"order": order, "pending": pending}

    def cancel_pending_order(self, order_id: int) -> dict[str, Any]:
        order = self.client.cancel_order(self.config.symbol, order_id)
        def mutate(pending: list[dict[str, Any]]) -> None:
            for item in pending:
                if int(item.get("order_id", 0) or 0) != order_id:
                    continue
                item["status"] = order.get("status", "CANCELED")
                item["closed_at"] = _utc_now()
                if item.get("lot_id"):
                    self.clear_lot_pending_sell(str(item["lot_id"]))

        self.update_pending_orders(mutate)
        self._record_manual_trade("LIMIT_ORDER_CANCELED", 0, order, None)
        return {"order": order}

    def add_pending_order(self, order: dict[str, Any]) -> dict[str, Any]:
        def mutate(pending: list[dict[str, Any]]) -> dict[str, Any]:
            pending.append(order)
            return order

        return self.update_pending_orders(mutate)

    def pending_orders(self) -> list[dict[str, Any]]:
        return self.pending_store.load()

    def save_pending_orders(self, orders: list[dict[str, Any]]) -> None:
        self.pending_store.save(orders)

    def update_pending_orders(self, mutator: Any) -> Any:
        return self.pending_store.update(mutator)

    def reserve_lot_pending_sell(self, lot_id: str, limit_price: float) -> dict[str, Any] | None:
        def mutate(lots: list[dict[str, Any]]) -> dict[str, Any] | None:
            for lot in lots:
                if lot.get("id") != lot_id or lot.get("status") != "open":
                    continue
                if lot.get("pending_limit_sell_order_id"):
                    return None
                lot["pending_limit_sell_order_id"] = "RESERVING"
                lot["pending_limit_sell_price"] = limit_price
                return dict(lot)
            return None

        return self.ledger.update_lots(mutate)

    def mark_lot_pending_sell(self, lot_id: str, order_id: int, limit_price: float) -> None:
        def mutate(lots: list[dict[str, Any]]) -> None:
            for lot in lots:
                if lot.get("id") == lot_id and lot.get("status") == "open":
                    lot["pending_limit_sell_order_id"] = order_id
                    lot["pending_limit_sell_price"] = limit_price

        self.ledger.update_lots(mutate)

    def clear_lot_pending_sell(self, lot_id: str) -> None:
        def mutate(lots: list[dict[str, Any]]) -> None:
            for lot in lots:
                if lot.get("id") == lot_id:
                    lot.pop("pending_limit_sell_order_id", None)
                    lot.pop("pending_limit_sell_price", None)

        self.ledger.update_lots(mutate)

    def mark_lot_limit_sell_filled(self, lot_id: str, order_id: int, limit_price: float) -> None:
        def mutate(lots: list[dict[str, Any]]) -> None:
            for lot in lots:
                if lot.get("id") == lot_id:
                    lot["limit_sell_filled"] = True
                    lot["limit_sell_order_id"] = order_id
                    lot["manual_sell_price"] = limit_price
                    lot.pop("pending_limit_sell_order_id", None)
                    lot.pop("pending_limit_sell_price", None)

        self.ledger.update_lots(mutate)

    def sync_pending_orders(self) -> list[dict[str, Any]]:
        def mutate(pending: list[dict[str, Any]]) -> None:
            for item in pending:
                if item.get("processed"):
                    continue
                status = str(item.get("status", "NEW"))
                try:
                    order = self.client.order(self.config.symbol, int(item["order_id"]))
                except (BinanceAPIError, KeyError, ValueError):
                    continue
                item["status"] = order.get("status", status)
                item["updated_at"] = _utc_now()
                executed_qty = float(order.get("executedQty", 0) or 0)
                quote_qty = _order_quote_qty(order, fallback_price=float(item.get("limit_price", 0) or 0))
                if item["status"] in {"FILLED", "CANCELED", "EXPIRED"} and executed_qty > 0 and quote_qty > 0:
                    order = dict(order)
                    order["cummulativeQuoteQty"] = str(quote_qty)
                    if item.get("side") == "BUY":
                        lot = self.ledger.add_buy(
                            self.config.symbol,
                            order,
                            float(item.get("target_profit_pct", 0) or 0),
                            self.config.trading_fee_rate,
                            str(item.get("level", "manual-limit-buy")),
                            None,
                            bool(item.get("auto_sell", False)),
                        )
                        self._record_manual_trade("LIMIT_BUY_FILLED", quote_qty, order, lot)
                    elif item.get("side") == "SELL":
                        if item.get("lot_id"):
                            lot = self.ledger.close_lot(str(item["lot_id"]), order, self.config.trading_fee_rate)
                            self.mark_lot_limit_sell_filled(
                                str(item["lot_id"]),
                                int(item.get("order_id", 0) or 0),
                                float(item.get("limit_price", 0) or 0),
                            )
                            self._record_manual_trade("LIMIT_SELL_FILLED", quote_qty, order, lot)
                        else:
                            self._record_manual_trade("EXTERNAL_LIMIT_SELL_FILLED", quote_qty, order, item)
                    item["processed"] = True
                    item["closed_at"] = _utc_now()
                if item["status"] in {"CANCELED", "EXPIRED", "REJECTED"} and item.get("lot_id") and not item.get("processed"):
                    self.clear_lot_pending_sell(str(item["lot_id"]))
                    item["closed_at"] = _utc_now()

        return self.update_pending_orders(lambda pending: (mutate(pending), pending)[1])

    def external_close_lot(self, lot_id: str, sell_price: float, quantity: float | None) -> dict[str, Any]:
        lot = next((item for item in self.ledger.open_lots() if item.get("id") == lot_id), None)
        if not lot:
            return {"error": "open lot not found"}
        if sell_price <= 0:
            return {"error": "sell price must be positive"}
        if quantity is not None and quantity <= 0:
            quantity = None
        updated = self.ledger.external_close_lot(
            lot_id,
            sell_price,
            quantity,
            self.config.trading_fee_rate,
            "dashboard external sell sync",
        )
        if not updated:
            return {"error": "failed to sync external sell"}
        quote_size = float(updated.get("sell_quote") or 0)
        order = {
            "external_sync": True,
            "side": "SELL",
            "symbol": self.config.symbol,
            "executedQty": str(quantity or lot.get("remaining_quantity", 0)),
            "cummulativeQuoteQty": str(quote_size),
            "price": str(sell_price),
        }
        self._record_manual_trade("EXTERNAL_SELL_SYNC", quote_size, order, updated)
        return {"lot": updated}

    def _record_manual_trade(
        self,
        side: str,
        quote_size: float,
        order: dict[str, Any],
        lot: dict[str, Any] | None,
    ) -> None:
        from datetime import datetime, timezone

        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": self.config.symbol,
            "side": side,
            "level": str(lot.get("level") or "manual-entry") if lot else "manual-entry",
            "reason": "manual dashboard trade; auto sell disabled",
            "target_quote_size": quote_size,
            "order": order,
            "lot_id": lot.get("id") if lot else None,
        }
        with self.trades_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def trading_password_ok(self, password: str) -> bool:
        expected = os.getenv("TRADING_TOGGLE_PASSWORD", "change-me")
        return hmac.compare_digest(password, expected)

    def dashboard_password_ok(self, password: str) -> bool:
        expected = os.getenv("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_BASIC_PASSWORD", "change-me")
        return hmac.compare_digest(password, expected)

    def settings(self) -> dict[str, Any]:
        return {
            "dashboard_password_set": bool(os.getenv("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_BASIC_PASSWORD")),
            "trading_toggle_password_set": bool(os.getenv("TRADING_TOGGLE_PASSWORD")),
            "smtp_host": os.getenv("SMTP_HOST", ""),
            "smtp_port": os.getenv("SMTP_PORT", "465"),
            "smtp_username": os.getenv("SMTP_USERNAME", ""),
            "smtp_password_set": bool(os.getenv("SMTP_PASSWORD")),
            "smtp_from_name": os.getenv("SMTP_FROM_NAME", "交易报告"),
            "report_recipient": os.getenv("REPORT_RECIPIENT", ""),
            "manual_buy_auto_sell": self.manual_buy_auto_sell_enabled(),
            "take_profit_pct": float(os.getenv("TAKE_PROFIT_PCT", str(self.config.take_profit_pct))),
            "config_fields": _config_setting_fields(self.config),
        }

    def manual_buy_auto_sell_enabled(self) -> bool:
        value = os.getenv("MANUAL_BUY_AUTO_SELL")
        if value is None:
            return self.config.manual_buy_auto_sell
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, str] = {}
        mapping = {
            "dashboard_password": "DASHBOARD_PASSWORD",
            "trading_toggle_password": "TRADING_TOGGLE_PASSWORD",
            "smtp_host": "SMTP_HOST",
            "smtp_port": "SMTP_PORT",
            "smtp_username": "SMTP_USERNAME",
            "smtp_password": "SMTP_PASSWORD",
            "smtp_from_name": "SMTP_FROM_NAME",
            "report_recipient": "REPORT_RECIPIENT",
            "manual_buy_auto_sell": "MANUAL_BUY_AUTO_SELL",
        }
        for source, env_key in mapping.items():
            value = str(payload.get(source, "") or "").strip()
            if source.endswith("password") and not value:
                continue
            if source in {"smtp_host", "smtp_port", "smtp_username", "smtp_from_name", "report_recipient", "manual_buy_auto_sell"}:
                updates[env_key] = value
            elif value:
                updates[env_key] = value
        try:
            updates.update(_config_updates_from_payload(payload))
        except ValueError as exc:
            return {"error": str(exc)}
        if updates:
            _update_dotenv(Path(".env"), updates)
            os.environ.update(updates)
            self._reload_config()
        return {"updated": sorted(updates)}

    def trades(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.trades_path.exists():
            return []
        lines = self.trades_path.read_text().splitlines()[-limit:]
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def grid_state(self) -> dict[str, int]:
        if not self.state_path.exists():
            return {"last_buy_level": 0, "last_sell_level": 0}
        try:
            return json.loads(self.state_path.read_text())
        except json.JSONDecodeError:
            return {"last_buy_level": 0, "last_sell_level": 0}


def _update_dotenv(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    lines: list[str] = []
    for raw_line in existing:
        if "=" not in raw_line or raw_line.lstrip().startswith("#"):
            lines.append(raw_line)
            continue
        key = raw_line.split("=", 1)[0].strip()
        if key in remaining:
            lines.append(f"{key}={_quote_env_value(remaining.pop(key))}")
        else:
            lines.append(raw_line)
    for key, value in remaining.items():
        lines.append(f"{key}={_quote_env_value(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text("\n".join(lines).rstrip() + "\n")
    temp_path.replace(path)


def _quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() for char in value) or any(char in value for char in {'"', "'", "#"}):
        return json.dumps(value, ensure_ascii=False)
    return value


def _payload_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _manual_control_lot(lot: dict[str, Any]) -> bool:
    level = str(lot.get("level", ""))
    return level.startswith("manual-") or level.startswith("swing-")


def _parse_profit_value(value: Any) -> float:
    raw = float(value or 0)
    return raw / 100 if raw > 0.05 else raw


def _parse_profit_list(value: str) -> list[float]:
    profits = [_parse_profit_value(item.strip()) for item in value.split(",") if item.strip()]
    return [item for item in profits if item > 0] or [0.006, 0.01, 0.012]


def _historical_backtest_interval(start: str, end: str) -> str:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    days = max(1, (end_dt - start_dt).days)
    if days <= 7:
        return "1m"
    if days <= 31:
        return "5m"
    return "15m"


def _backtest_recommendation(results: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    if not results:
        return {"text": "没有足够结果生成建议。", "take_profit_pct": None}
    grouped: dict[float, list[dict[str, Any]]] = {}
    for item in results:
        grouped.setdefault(float(item["take_profit_pct"]), []).append(item)
    scores: list[tuple[float, float, float, float]] = []
    for profit, rows in grouped.items():
        avg_return = sum(float(row["total_return_pct"]) for row in rows) / len(rows)
        avg_drawdown = sum(float(row["max_drawdown_quote"]) for row in rows) / len(rows)
        avg_trades = sum(float(row["buys"]) + float(row["sells"]) for row in rows) / len(rows)
        score = avg_return - avg_drawdown * 0.05
        scores.append((score, profit, avg_return, avg_trades))
    best = max(scores, key=lambda item: item[0])
    text = (
        f"建议优先试 {best[1] * 100:.2f}%："
        f"{'真实 K 线' if mode == 'historical' else '模拟场景'}中综合收益和回撤更均衡，"
        f"平均收益约 {best[2]:+.2f}%，平均交易次数约 {best[3]:.0f}。"
    )
    return {"text": text, "take_profit_pct": best[1], "avg_return_pct": best[2], "avg_trades": best[3]}


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _balance_parts(balances: dict[str, dict[str, Any]], asset: str) -> tuple[float, float, float]:
    item = balances.get(asset, {})
    free = float(item.get("free", 0) or 0)
    locked = float(item.get("locked", 0) or 0)
    return free, locked, free + locked


def _order_quote_qty(order: dict[str, Any], fallback_price: float = 0.0) -> float:
    quote_qty = float(order.get("cummulativeQuoteQty", 0) or 0)
    if quote_qty > 0:
        return quote_qty
    orig_quote_qty = float(order.get("origQuoteOrderQty", 0) or 0)
    if orig_quote_qty > 0 and str(order.get("status", "")).upper() == "FILLED":
        return orig_quote_qty
    executed_qty = float(order.get("executedQty", 0) or 0)
    price = float(order.get("price", 0) or 0) or fallback_price
    return executed_qty * price if executed_qty > 0 and price > 0 else 0.0


def make_handler(dashboard: Dashboard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/" or path == "/index.html":
                self._send(200, HTML.encode(), "text/html; charset=utf-8")
                return
            if path == "/favicon.svg":
                self._send(200, FAVICON_SVG, "image/svg+xml")
                return
            if path == "/static/mascot-ai.png":
                asset_path = Path(__file__).with_name("static") / "mascot-ai.png"
                if asset_path.exists():
                    self._send(200, asset_path.read_bytes(), "image/png")
                else:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            if not self._authorized():
                self._send(403, json.dumps({"error": "not logged in"}).encode(), "application/json")
                return
            if path == "/api/login":
                self._send(200, json.dumps({"ok": True}).encode(), "application/json")
                return
            if path == "/api/status":
                try:
                    range_key = parse_qs(parsed.query).get("range", ["minute"])[0]
                    payload = dashboard.status(range_key)
                except Exception as exc:
                    payload = {"error": str(exc)}
                self._send(200, json.dumps(payload, sort_keys=True).encode(), "application/json")
                return
            if path == "/api/settings":
                self._send(200, json.dumps(dashboard.settings(), sort_keys=True).encode(), "application/json")
                return
            if path == "/api/comments":
                self._send(200, json.dumps({"comments": dashboard.comments()}, sort_keys=True).encode(), "application/json")
                return
            if path not in {
                "/api/trading",
                "/api/baseline/calibrate",
                "/api/backtest",
                "/api/settings/update",
                "/api/strategy/take-profit",
                "/api/manual/buy",
                "/api/manual/sell",
                "/api/manual/auto-sell",
                "/api/manual/limit-buy",
                "/api/manual/limit-sell",
                "/api/manual/external-limit-sell",
                "/api/manual/cancel-order",
                "/api/manual/external-close",
                "/api/comments/add",
                "/api/comments/reply",
            }:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            payload = self._payload()
            if path == "/api/backtest":
                try:
                    result = dashboard.backtest(payload)
                except Exception as exc:
                    result = {"error": f"backtest failed: {exc}"}
                self._send(200, json.dumps(result, sort_keys=True).encode(), "application/json")
                return
            if path == "/api/comments/add":
                result = dashboard.add_comment(
                    str(payload.get("name", "")),
                    str(payload.get("message", "")),
                )
                self._send(200, json.dumps(result, sort_keys=True).encode(), "application/json")
                return
            trading_password = self.headers.get("X-Trading-Password", "")
            if not dashboard.trading_password_ok(trading_password):
                self._send(403, json.dumps({"error": "invalid trading password"}).encode(), "application/json")
                return
            if path == "/api/baseline/calibrate":
                try:
                    result = dashboard.calibrate_baseline()
                except BinanceAPIError as exc:
                    result = {"error": str(exc)}
            elif path == "/api/settings/update":
                result = dashboard.update_settings(payload)
            elif path == "/api/strategy/take-profit":
                result = dashboard.update_take_profit(payload)
            elif path == "/api/manual/buy":
                try:
                    result = dashboard.manual_buy(
                        float(payload.get("quote_size", 0) or 0),
                        float(payload.get("target_profit_pct", 0) or 0),
                        _payload_bool(payload.get("auto_sell")),
                    )
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/manual/limit-buy":
                try:
                    result = dashboard.manual_limit_buy(
                        float(payload.get("quote_size", 0) or 0),
                        float(payload.get("limit_price", 0) or 0),
                        float(payload.get("target_profit_pct", 0) or 0),
                        _payload_bool(payload.get("auto_sell")),
                    )
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/manual/sell":
                try:
                    result = dashboard.manual_sell(str(payload.get("lot_id", "")))
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/manual/auto-sell":
                result = dashboard.set_manual_lot_auto_sell(
                    str(payload.get("lot_id", "")),
                    bool(_payload_bool(payload.get("auto_sell"))),
                )
            elif path == "/api/manual/limit-sell":
                try:
                    result = dashboard.manual_limit_sell(
                        str(payload.get("lot_id", "")),
                        float(payload.get("limit_price", 0) or 0),
                    )
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/manual/external-limit-sell":
                try:
                    result = dashboard.manual_external_limit_sell(
                        float(payload.get("quantity", 0) or 0),
                        float(payload.get("limit_price", 0) or 0),
                    )
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/manual/cancel-order":
                try:
                    result = dashboard.cancel_pending_order(int(payload.get("order_id", 0) or 0))
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/manual/external-close":
                try:
                    raw_quantity = str(payload.get("quantity", "") or "").strip()
                    quantity = float(raw_quantity) if raw_quantity else None
                    result = dashboard.external_close_lot(
                        str(payload.get("lot_id", "")),
                        float(payload.get("sell_price", 0) or 0),
                        quantity,
                    )
                except (BinanceAPIError, ValueError) as exc:
                    result = {"error": str(exc)}
            elif path == "/api/comments/reply":
                result = dashboard.add_comment(
                    "作者",
                    str(payload.get("message", "")),
                    str(payload.get("parent_id", "")),
                    is_author=True,
                )
            else:
                result = dashboard.set_execute_trades(bool(payload.get("execute_trades")))
            self._send(200, json.dumps(result, sort_keys=True).encode(), "application/json")

        def do_POST(self) -> None:
            self._send(405, json.dumps({"error": "use GET"}).encode(), "application/json")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _authorized(self) -> bool:
            return dashboard.dashboard_password_ok(self.headers.get("X-Dashboard-Password", ""))

        def _payload(self) -> dict[str, Any]:
            raw = self.headers.get("X-Action-Payload", "")
            if not raw:
                return {}
            try:
                decoded = base64.b64decode(raw).decode()
                payload = json.loads(decoded)
                return payload if isinstance(payload, dict) else {}
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return {}

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _position_quote(lots: list[dict[str, Any]], price: float) -> float:
    return sum(float(lot.get("remaining_quantity", 0) or 0) * price for lot in lots)


def _interval_minutes(interval: str) -> int:
    raw = interval.strip().lower()
    if raw.endswith("m"):
        return max(1, int(raw[:-1] or "1"))
    if raw.endswith("h"):
        return max(1, int(raw[:-1] or "1") * 60)
    if raw.endswith("d"):
        return max(1, int(raw[:-1] or "1") * 24 * 60)
    return 60


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance Spot Live Agent dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--baseline", default="data/baseline_BTCUSDT.json")
    parser.add_argument("--trades", default="data/trades_BTCUSDT.jsonl")
    parser.add_argument("--state", default="data/grid_state_BTCUSDT.json")
    args = parser.parse_args()

    config = AgentConfig.from_env()
    dashboard = Dashboard(config, Path(args.baseline), Path(args.trades), Path(args.state))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(dashboard))
    print(f"dashboard listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
