from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
from dataclasses import asdict
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any

from .binance_client import BinanceAPIError, BinanceSpotClient
from .config import AgentConfig
from .defensive import enrich_lot_with_defensive_target, enrich_lots_with_defensive_targets, evaluate_defensive_mode
from .ledger import PositionLedger
from .local_backtest import BacktestConfig, run_scenarios, run_backtest
from .portfolio import metrics_asdict, portfolio_metrics, reset_baseline
from .sizing import position_sizing
from .strategy import GridStrategy, MarketSnapshot
from .swing import SwingStrategy, is_swing_lot, split_lots


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


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Binance Spot Live Agent</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="shortcut icon" href="/favicon.svg">
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #eef4f7; color: #17212b; overflow-x: hidden; }
    body::before { content: ""; position: fixed; inset: 0; pointer-events: none; background: linear-gradient(150deg, rgba(14,165,233,.11), rgba(255,255,255,.58) 42%, rgba(16,185,129,.10)); }
    main { position: relative; max-width: 1320px; margin: 0 auto; padding: 28px; }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 30px; font-weight: 760; letter-spacing: 0; }
    .muted { color: #607080; font-size: 14px; }
    .top-board { display: grid; grid-template-columns: 1.25fr .95fr .95fr; gap: 14px; align-items: stretch; }
    .panel { background: rgba(255,255,255,.96); border: 1px solid #d6e0ea; border-radius: 8px; padding: 18px; box-shadow: 0 18px 42px rgba(15, 23, 42, .08); }
    .metric-card { position: relative; overflow: hidden; min-height: 172px; }
    .metric-card::after { content: ""; position: absolute; inset: auto -42px -64px auto; width: 160px; height: 160px; border-radius: 999px; background: rgba(14, 165, 233, .10); }
    .label { color: #687789; font-size: 13px; font-weight: 760; margin-bottom: 8px; }
    .value { font-size: 25px; font-weight: 760; overflow-wrap: anywhere; letter-spacing: 0; }
    .hero-symbol { font-size: 18px; color: #607080; font-weight: 760; margin-bottom: 12px; }
    .hero-price { font-size: 46px; line-height: 1; font-weight: 800; letter-spacing: 0; margin-bottom: 18px; }
    .signal-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .signal-pill { display: inline-flex; align-items: center; justify-content: center; min-height: 34px; padding: 0 13px; border-radius: 999px; background: #edf5f2; color: #047857; font-weight: 800; }
    .capital-stack { display: grid; gap: 14px; }
    .capital-value { font-size: 30px; font-weight: 800; margin-bottom: 10px; }
    .pnl-card .value { font-size: 26px; margin-bottom: 14px; }
    .control-panel { display: grid; align-content: start; gap: 14px; }
    .control-row { display: grid; grid-template-columns: 1fr; gap: 9px; padding-bottom: 14px; border-bottom: 1px solid #e5ebf2; }
    .control-row:last-child { padding-bottom: 0; border-bottom: 0; }
    .profit { color: #059669; }
    .loss { color: #e11d48; }
    .warn { color: #b7791f; }
    .chart-wrap { height: 460px; padding: 0; overflow: hidden; position: relative; }
    .chart-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px 0; }
    .range-tabs { display: inline-flex; gap: 6px; padding: 4px; background: #eef3f8; border-radius: 8px; }
    .range-tabs button { border: 0; border-radius: 6px; background: transparent; color: #526173; height: 30px; padding: 0 11px; font-weight: 700; cursor: pointer; }
    .range-tabs button.active { background: #ffffff; color: #0f172a; box-shadow: 0 2px 8px rgba(15,23,42,.10); }
    canvas { width: 100%; height: calc(100% - 70px); display: block; cursor: crosshair; }
    .action-button { border: 0; border-radius: 8px; height: 38px; padding: 0 14px; background: #0f172a; color: #fff; font-weight: 800; cursor: pointer; }
    .action-button.off { background: #e11d48; }
    .action-button.on { background: #059669; }
    .tooltip { position: absolute; z-index: 5; pointer-events: none; display: none; min-width: 172px; padding: 9px 10px; border-radius: 8px; background: rgba(15, 23, 42, .92); color: #f8fafc; font-size: 12px; box-shadow: 0 12px 24px rgba(15,23,42,.22); }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { text-align: left; padding: 11px 10px; border-bottom: 1px solid #e5ebf2; font-size: 14px; vertical-align: top; }
    th { color: #687789; font-weight: 650; }
    tr:last-child td, tr:last-child th { border-bottom: 0; }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; align-items: stretch; }
    .split .panel { min-height: 430px; display: flex; flex-direction: column; }
    .panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
    .panel-title { font-size: 16px; font-weight: 800; color: #263445; }
    .account-table { margin-top: 4px; }
    .account-table th { width: 150px; color: #6b7b8c; font-size: 13px; }
    .account-table td { color: #1d2937; font-weight: 650; }
    .orders-table th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .orders-table td { height: 54px; vertical-align: middle; }
    .orders-panel .table-scroll { flex: 1; }
    .table-scroll { overflow-x: auto; }
    .pager { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 10px; color: #687789; font-size: 13px; }
    .pager button { border: 1px solid #d9e2ec; background: #fff; color: #334155; border-radius: 6px; height: 30px; min-width: 34px; padding: 0 10px; font-weight: 700; cursor: pointer; }
    .pager button:disabled { opacity: .45; cursor: not-allowed; }
    .badge { display: inline-flex; align-items: center; min-height: 26px; padding: 0 9px; border-radius: 999px; background: #eef3f8; color: #526173; font-weight: 700; font-size: 13px; }
    .header-actions { display: flex; gap: 10px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
    .modal { position: fixed; inset: 0; z-index: 20; display: none; align-items: center; justify-content: center; padding: 20px; background: rgba(15, 23, 42, .38); }
    .modal.open { display: flex; }
    .modal-panel { width: min(780px, 100%); max-height: 88vh; overflow-y: auto; background: #fff; border: 1px solid #d9e2ec; border-radius: 8px; box-shadow: 0 24px 60px rgba(15,23,42,.24); padding: 18px; }
    .modal-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
    .modal-head h2 { margin: 0; font-size: 20px; letter-spacing: 0; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .field label { display: block; color: #687789; font-size: 13px; font-weight: 700; margin-bottom: 6px; }
    .field input, .field select { width: 100%; height: 38px; border: 1px solid #d9e2ec; border-radius: 7px; padding: 0 10px; font: inherit; background: #fff; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
    .secondary-button { border: 1px solid #d9e2ec; border-radius: 8px; height: 34px; padding: 0 13px; background: #fff; color: #334155; font-weight: 800; cursor: pointer; }
    .inline-controls { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; align-items: end; }
    .inline-controls .field input, .inline-controls .field select { height: 36px; }
    .result-note { margin-top: 12px; padding: 12px; border-radius: 8px; background: #eef8f4; color: #065f46; font-weight: 750; }
    @media (max-width: 980px) {
      main { width: 100%; overflow-x: hidden; }
      .top-board { grid-template-columns: 1fr; }
      .split { grid-template-columns: 1fr; }
      .inline-controls { grid-template-columns: 1fr 1fr; }
      header { display: block; }
      .hero-price { font-size: 38px; }
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
      .form-grid, .inline-controls { grid-template-columns: 1fr; }
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
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Binance Spot Live Agent</h1>
        <div class="muted">实盘实时交易看板</div>
      </div>
      <div class="header-actions">
        <button class="secondary-button" id="settingsOpen">设置</button>
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
          <button class="action-button" id="manualBuy">人工买入并记账</button>
        </div>
      </div>
    </section>
    <section class="panel chart-wrap" style="margin-top:14px">
      <div class="chart-head">
        <div>
          <div class="label">行情走势</div>
          <div class="muted" id="chartLabel">分时</div>
        </div>
        <div class="range-tabs">
          <button data-range="minute" class="active">分时</button>
          <button data-range="15m">15分</button>
          <button data-range="4h">4小时</button>
          <button data-range="day">一天</button>
          <button data-range="week">一周</button>
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
            <tr><th>基础资产余额</th><td id="base">--</td></tr>
            <tr><th>计价资产余额</th><td id="quote">--</td></tr>
            <tr><th>账本同步</th><td id="ledgerSync">--</td></tr>
            <tr><th>参考价</th><td id="reference">--</td></tr>
            <tr><th>资金档位</th><td id="sizing">--</td></tr>
            <tr><th>策略原因</th><td id="reason">--</td></tr>
            <tr><th>未平批次</th><td id="lots">--</td></tr>
            <tr><th>已实现利润</th><td id="realized">--</td></tr>
            <tr><th>未实现批次盈亏</th><td id="unrealized">--</td></tr>
            <tr><th>手续费统计</th><td id="fees">--</td></tr>
            <tr><th>风控状态</th><td id="risk">--</td></tr>
            <tr><th>防守模式</th><td id="defensive">--</td></tr>
            <tr><th>波段策略</th><td id="swing">--</td></tr>
            <tr><th>错误</th><td id="error">--</td></tr>
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
            <thead><tr><th>时间</th><th>方向</th><th>档位</th><th>金额</th></tr></thead>
            <tbody id="trades"><tr><td colspan="4" class="muted">暂无订单</td></tr></tbody>
          </table>
        </div>
        <div class="pager"><button id="tradePrev">上一页</button><span id="tradePage">1 / 1</span><button id="tradeNext">下一页</button></div>
      </div>
    </section>
    <section class="panel" style="margin-top:14px">
      <div class="label">未平批次</div>
      <table>
        <thead><tr><th>档位</th><th>状态</th><th>成本价</th><th>预计卖价</th><th>手动价格</th><th>数量</th><th>手续费</th><th>浮盈亏</th><th>操作</th></tr></thead>
        <tbody id="openLots"><tr><td colspan="9" class="muted">暂无未平批次</td></tr></tbody>
      </table>
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
        <thead><tr><th>关闭时间</th><th>档位</th><th>状态</th><th>成本价</th><th>卖出价</th><th>手动价格</th><th>数量</th><th>手续费</th><th>净利润</th></tr></thead>
        <tbody id="closedLots"><tr><td colspan="9" class="muted">暂无已平批次</td></tr></tbody>
      </table>
      <div class="pager"><button id="closedPrev">上一页</button><span id="closedPage">1 / 1</span><button id="closedNext">下一页</button></div>
    </section>
  </main>
  <div class="modal" id="settingsModal">
    <div class="modal-panel">
      <div class="modal-head">
        <h2>面板设置</h2>
        <button class="secondary-button" id="settingsClose">关闭</button>
      </div>
      <div class="form-grid">
        <div class="field"><label>页面登录密码</label><input id="setDashboardPassword" type="password" placeholder="留空不改" autocomplete="new-password"></div>
        <div class="field"><label>交易开关密码</label><input id="setTradingPassword" type="password" placeholder="留空不改" autocomplete="new-password"></div>
        <div class="field"><label>默认盈利比例 %</label><input id="setTakeProfitPct" inputmode="decimal" placeholder="例如 1.0"></div>
        <div class="field"><label>应用到未平批次</label><select id="setApplyTakeProfit"><option value="false">只影响后续</option><option value="true">重算未平批次</option></select></div>
        <div class="field"><label>人工买入默认自动卖出</label><select id="setManualBuyAutoSell"><option value="false">关闭</option><option value="true">开启</option></select></div>
        <div class="field"><label>SMTP 服务器</label><input id="setSmtpHost" placeholder="smtp.example.com"></div>
        <div class="field"><label>SMTP 端口</label><input id="setSmtpPort" inputmode="numeric" placeholder="465"></div>
        <div class="field"><label>SMTP 账号</label><input id="setSmtpUsername" autocomplete="username"></div>
        <div class="field"><label>SMTP 密码</label><input id="setSmtpPassword" type="password" placeholder="留空不改" autocomplete="new-password"></div>
        <div class="field"><label>发件人姓名</label><input id="setSmtpFromName"></div>
        <div class="field"><label>报告收件人</label><input id="setReportRecipient"></div>
      </div>
      <div class="muted" id="settingsStatus" style="margin-top:12px">密码字段不会回显；留空表示不修改。</div>
      <div class="modal-actions">
        <button class="secondary-button" id="settingsReload">重新读取</button>
        <button class="action-button" id="settingsSave">保存设置</button>
      </div>
    </div>
  </div>
  <div class="modal open" id="loginModal">
    <div class="modal-panel" style="width:min(420px,100%)">
      <div class="modal-head"><h2>登录看板</h2></div>
      <div class="field"><label>页面密码</label><input id="loginPassword" type="password" autocomplete="current-password"></div>
      <div class="muted" id="loginStatus" style="margin-top:12px">请输入页面密码后查看实盘看板。</div>
      <div class="modal-actions"><button class="action-button" id="loginButton">登录</button></div>
    </div>
  </div>
  <script>
    const fmt = (n, digits = 4) => Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
    const rangeLabels = { minute: '近 60 分钟', '15m': '近 15 分钟', '4h': '近 4 小时', day: '近 24 小时', week: '近 7 天' };
    let activeRange = 'minute';
    let chartZoom = { start: 0, end: 1 };
    let chartPoints = [];
    let chartReference = null;
    let chartLayout = null;
    let latestTrades = [];
    let latestClosedLots = [];
    let manualBuyAutoSellDefault = false;
    let tradePage = 0;
    let closedPage = 0;
    let settingsLoaded = false;
    let dashboardPassword = sessionStorage.getItem('dashboardPassword') || '';
    let loginValidated = false;
    const tradePageSize = 10;
    const closedPageSize = 8;
    const canvas = document.getElementById('chart');
    const ctx = canvas.getContext('2d');
    const chartTip = document.getElementById('chartTip');
    function authHeaders(tradingPassword, payload) {
      const headers = { 'X-Dashboard-Password': dashboardPassword };
      if (tradingPassword) headers['X-Trading-Password'] = tradingPassword;
      if (payload) headers['X-Action-Payload'] = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
      return headers;
    }
    async function apiGet(path, payload, tradingPassword) {
      const res = await fetch(path, { method: 'GET', cache: 'no-store', headers: authHeaders(tradingPassword, payload) });
      const data = await res.json();
      if (res.status === 403) {
        loginValidated = false;
        document.getElementById('loginModal').classList.add('open');
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
        document.getElementById('loginModal').classList.add('open');
        document.getElementById('loginStatus').textContent = dashboardPassword ? '密码不正确，请重新输入。' : '请输入页面密码后查看实盘看板。';
        return false;
      }
    }
    function drawChart(points, reference) {
      chartPoints = points || [];
      chartReference = reference;
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      const pad = { left: 86, right: 22, top: 18, bottom: 58 };
      const total = chartPoints.length;
      const startIndex = Math.max(0, Math.floor(chartZoom.start * Math.max(total - 1, 1)));
      const endIndex = Math.min(total, Math.max(startIndex + 2, Math.ceil(chartZoom.end * total)));
      const visible = chartPoints.slice(startIndex, endIndex);
      const values = visible.map(p => p.close);
      if (reference) values.push(reference);
      if (!values.length) return;
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(max - min, 1);
      const plotW = rect.width - pad.left - pad.right;
      const plotH = rect.height - pad.top - pad.bottom;
      const x = i => pad.left + plotW * (i / Math.max(visible.length - 1, 1));
      const y = v => pad.top + (rect.height - pad.top - pad.bottom) * (1 - (v - min) / span);
      ctx.strokeStyle = '#e5ebf2'; ctx.lineWidth = 1;
      ctx.fillStyle = '#687789'; ctx.font = '12px sans-serif';
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
      const gradient = ctx.createLinearGradient(pad.left, 0, rect.width - pad.right, 0);
      gradient.addColorStop(0, '#0ea5e9');
      gradient.addColorStop(1, '#10b981');
      ctx.strokeStyle = gradient; ctx.lineWidth = 2.5; ctx.beginPath();
      visible.forEach((p, i) => { const xx = x(i), yy = y(p.close); i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
      ctx.stroke();
      ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 1;
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
        const sideClass = t.side === 'BUY' ? 'profit' : 'warn';
        const quote = tradeQuoteAmount(t);
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${new Date(t.ts).toLocaleString()}</td><td><span class="badge ${sideClass}">${t.side}</span></td><td>${t.level}</td><td>${fmt(quote || 0, 6)}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['时间', '方向', '档位', '金额']);
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
    function renderOpenLots(lots, currentPrice) {
      const tbody = document.getElementById('openLots');
      tbody.innerHTML = '';
      if (!lots.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="muted">暂无未平批次</td></tr>';
        return;
      }
      lots.forEach(lot => {
        const qty = Number(lot.remaining_quantity || 0);
        const buy = Number(lot.buy_price || 0);
        const target = Number(lot.effective_target_price || lot.target_price || 0);
        const manualNote = lot.auto_sell === false ? ' <span class="badge">手动</span>' : '';
        const swingNote = lot.target_note === 'swing' ? ' <span class="badge">波段</span>' : '';
        const note = lot.target_price_adjusted ? ` <span class="badge">防守 ${lot.target_note}</span>` : (swingNote || manualNote);
        const pnl = (Number(currentPrice) - buy) * qty;
        const fee = Number(lot.fee_quote || lot.buy_fee_quote || 0);
        const status = lot.pending_limit_sell_order_id ? '限价卖出中' : (lot.auto_sell === false ? '手动持仓' : '自动卖出');
        const manualPrice = lot.pending_limit_sell_price ? fmt(lot.pending_limit_sell_price, 8) : '--';
        const levelText = String(lot.level || '');
        const manualControlLot = levelText.startsWith('manual-') || levelText.startsWith('swing-');
        const manualSell = manualControlLot ? `<button class="secondary-button" data-manual-sell="${lot.id}">市价卖出</button>` : '';
        const autoToggle = manualControlLot ? `<button class="secondary-button" data-auto-sell="${lot.id}" data-auto-sell-enabled="${lot.auto_sell === false ? 'true' : 'false'}">${lot.auto_sell === false ? '开启自动卖' : '取消自动卖'}</button>` : '';
        const limitSell = `<button class="secondary-button" data-limit-sell="${lot.id}" data-target-price="${target}">限价卖出</button>`;
        const externalClose = `<button class="secondary-button" data-external-close="${lot.id}">外部已卖</button>`;
        const action = `<div style="display:flex;gap:6px;flex-wrap:wrap">${manualSell}${autoToggle}${limitSell}${externalClose}</div>`;
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${lot.level || 'legacy'}</td><td><span class="badge">${status}</span></td><td>${fmt(buy, 8)}</td><td>${fmt(target, 8)}${note}</td><td>${manualPrice}</td><td>${fmt(qty, 8)}</td><td>${fmt(fee, 6)}</td><td class="${pnl >= 0 ? 'profit' : 'loss'}">${fmt(pnl, 6)}</td><td>${action}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['档位', '状态', '成本价', '预计卖价', '手动价格', '数量', '手续费', '浮盈亏', '操作']);
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
        tr.innerHTML = `<td>${order.created_at ? new Date(order.created_at).toLocaleString() : '--'}</td><td><span class="badge ${sideClass}">${order.side}</span></td><td>${fmt(order.limit_price || 0, 8)}</td><td>${fmt(order.quantity || 0, 8)}</td><td>${order.status || '--'}</td><td>${action}</td>`;
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
        const manualPrice = lot.manual_sell_price ? fmt(lot.manual_sell_price, 8) : '--';
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${lot.closed_at ? new Date(lot.closed_at).toLocaleString() : '--'}</td><td>${lot.level || 'legacy'}</td><td><span class="badge">${status}</span></td><td>${fmt(lot.buy_price || 0, 8)}</td><td>${fmt(lot.sell_price || 0, 8)}</td><td>${manualPrice}</td><td>${fmt(lot.quantity || 0, 8)}</td><td>${fmt(fee, 6)}</td><td class="${pnl >= 0 ? 'profit' : 'loss'}">${fmt(pnl, 6)}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['关闭时间', '档位', '状态', '成本价', '卖出价', '手动价格', '数量', '手续费', '净利润']);
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
    async function loadSettings() {
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
      document.getElementById('settingsStatus').textContent = '密码字段不会回显；留空表示不修改。';
      settingsLoaded = true;
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
        report_recipient: document.getElementById('setReportRecipient').value.trim()
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
        tr.innerHTML = `<td>${row.scenario || '--'}</td><td>${fmt(Number(row.take_profit_pct || 0) * 100, 3)}%</td><td>${fmt(row.final_value || 0, 4)}</td><td class="${ret >= 0 ? 'profit' : 'loss'}">${fmt(ret, 2)}%</td><td>${fmt(row.realized_net_pnl || 0, 4)}</td><td>${fmt(row.unrealized_pnl || 0, 4)}</td><td>${fmt(row.fees_paid || 0, 4)}</td><td>${row.buys || 0}/${row.sells || 0}</td><td>${row.open_lots || 0}</td><td>${fmt(row.max_drawdown_quote || 0, 4)}</td>`;
        tbody.appendChild(tr);
      });
      applyMobileLabels(tbody, ['场景', '盈利比例', '总资产', '收益率', '已实现', '未实现', '手续费', '买/卖', '未平', '最大回撤']);
      const advice = document.getElementById('backtestAdvice');
      advice.textContent = data.recommendation && data.recommendation.text ? data.recommendation.text : '';
      advice.style.display = advice.textContent ? 'block' : 'none';
    }
    async function refresh() {
      try {
        if (!(await requireLogin())) return;
        const data = await apiGet('/api/status?range=' + encodeURIComponent(activeRange));
        manualBuyAutoSellDefault = Boolean(data.manual_buy_auto_sell);
        document.getElementById('symbol').textContent = data.symbol;
        document.getElementById('price').textContent = fmt(data.price, 8);
        document.getElementById('signal').textContent = data.signal;
        const executeButton = document.getElementById('execute');
        executeButton.textContent = data.execute_trades ? '交易已开' : '交易暂停';
        executeButton.className = 'action-button ' + (data.execute_trades ? 'on' : 'off');
        document.getElementById('value').textContent = fmt(data.value_quote, 6) + ' ' + data.quote_asset;
        const pnl = document.getElementById('pnl');
        pnl.textContent = fmt(data.pnl_quote, 6) + ' ' + data.quote_asset + ' / ' + fmt(data.pnl_pct, 4) + '%';
        pnl.className = 'value ' + (data.pnl_quote >= 0 ? 'profit' : 'loss');
        const baseLocked = Number(data.base_locked_balance || 0);
        const quoteLocked = Number(data.quote_locked_balance || 0);
        document.getElementById('base').textContent = fmt(data.base_balance, 8) + ' ' + data.base_asset + (baseLocked > 0 ? `（锁定 ${fmt(baseLocked, 8)}）` : '');
        document.getElementById('quote').textContent = fmt(data.quote_balance, 8) + ' ' + data.quote_asset + (quoteLocked > 0 ? `（锁定 ${fmt(quoteLocked, 8)}）` : '');
            const ledgerSync = data.ledger_sync || {};
            const ledgerSyncText = ledgerSync.mismatch
              ? `需同步：账本 ${fmt(ledgerSync.tracked_base_quantity || 0, 8)} / 账户 ${fmt(ledgerSync.account_base_balance || 0, 8)} ${data.base_asset}`
              : `正常：账本 ${fmt(ledgerSync.tracked_base_quantity || 0, 8)} / 账户 ${fmt(ledgerSync.account_base_balance || 0, 8)} ${data.base_asset}`;
            document.getElementById('ledgerSync').textContent = ledgerSyncText;
            document.getElementById('reference').textContent = fmt(data.reference_price, 8);
            const sizing = data.position_sizing || {};
            document.getElementById('sizing').textContent = `${sizing.tier || '--'} / 新单 ${fmt(sizing.order_quote_size || 0, 4)} / 最大 ${fmt(sizing.max_position_quote || 0, 4)} ${data.quote_asset}`;
            document.getElementById('reason').textContent = data.reason;
            document.getElementById('lots').textContent = (data.open_lots || []).length + ' 批';
            document.getElementById('realized').textContent = fmt(data.realized_pnl || 0, 6) + ' ' + data.quote_asset;
            document.getElementById('unrealized').textContent = fmt(data.unrealized_lot_pnl || 0, 6) + ' ' + data.quote_asset;
            const feeSummary = data.fee_summary || {};
            document.getElementById('fees').textContent = `未平 ${fmt(feeSummary.open_fee_quote || 0, 6)} / 已平 ${fmt(feeSummary.closed_fee_quote || 0, 6)} / 合计 ${fmt(feeSummary.total_fee_quote || 0, 6)} ${data.quote_asset}`;
        document.getElementById('risk').textContent = data.risk ? data.risk.reason : '--';
        const defensive = data.defensive_mode || {};
        const defensiveReasons = defensive.reasons && defensive.reasons.length ? defensive.reasons.join(' / ') : '未触发';
        document.getElementById('defensive').textContent = `${defensive.enabled ? '开启' : '关闭'} / ${defensive.active ? '防守中' : '正常'} / 间距 ${fmt((defensive.add_on_step_pct || 0) * 100, 3)}% / ${defensiveReasons}`;
        const swing = data.swing_band || {};
        document.getElementById('swing').textContent = `${swing.enabled ? '开启' : '关闭'} / 中枢 ${fmt(swing.center_price || 0, 2)} / 买 ${fmt(swing.buy_price || 0, 2)} / 卖 ${fmt(swing.sell_price || 0, 2)} / 预算 ${fmt(swing.allocation_quote || 0, 4)} ${data.quote_asset}`;
        document.getElementById('error').textContent = '--';
        drawChart(data.price_history || [], data.reference_price);
        document.getElementById('chartLabel').textContent = rangeLabels[activeRange] || activeRange;
        renderTrades(data.trades || []);
        renderOpenLots(data.open_lots || [], data.price);
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
      sessionStorage.setItem('dashboardPassword', dashboardPassword);
      loginValidated = false;
      if (await requireLogin()) refresh();
    });
    document.getElementById('loginPassword').addEventListener('keydown', event => {
      if (event.key === 'Enter') document.getElementById('loginButton').click();
    });
    refresh();
    setInterval(refresh, 5000);
    window.addEventListener('resize', () => drawChart(chartPoints, chartReference));
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      const box = canvas.getBoundingClientRect();
      const anchor = Math.max(0, Math.min(1, (event.clientX - box.left - 66) / Math.max(box.width - 120, 1)));
      const width = chartZoom.end - chartZoom.start;
      const factor = event.deltaY < 0 ? 0.78 : 1.28;
      const nextWidth = Math.max(0.08, Math.min(1, width * factor));
      let nextStart = chartZoom.start + (width - nextWidth) * anchor;
      let nextEnd = nextStart + nextWidth;
      if (nextStart < 0) { nextEnd -= nextStart; nextStart = 0; }
      if (nextEnd > 1) { nextStart -= nextEnd - 1; nextEnd = 1; }
      chartZoom = { start: Math.max(0, nextStart), end: Math.min(1, nextEnd) };
      drawChart(chartPoints, chartReference);
    }, { passive: false });
    canvas.addEventListener('mousemove', event => {
      const hit = chartPointAt(event.clientX);
      if (!hit || !chartLayout) return;
      drawChart(chartPoints, chartReference);
      ctx.strokeStyle = '#94a3b8'; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(hit.x, chartLayout.pad.top); ctx.lineTo(hit.x, chartLayout.rect.height - chartLayout.pad.bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(chartLayout.pad.left, hit.y); ctx.lineTo(chartLayout.rect.width - chartLayout.pad.right, hit.y); ctx.stroke(); ctx.setLineDash([]);
      const box = canvas.getBoundingClientRect();
      chartTip.style.display = 'block';
      chartTip.style.left = Math.min(box.width - 190, Math.max(10, event.clientX - box.left + 14)) + 'px';
      chartTip.style.top = Math.max(54, event.clientY - box.top - 12) + 'px';
      chartTip.innerHTML = `<strong>${fmt(hit.point.close, 8)}</strong><br>${new Date(hit.point.open_time).toLocaleString()}`;
    });
    canvas.addEventListener('mouseleave', () => {
      chartTip.style.display = 'none';
      drawChart(chartPoints, chartReference);
    });
    document.getElementById('tradePrev').addEventListener('click', () => { tradePage = Math.max(0, tradePage - 1); renderTrades(); });
    document.getElementById('tradeNext').addEventListener('click', () => { tradePage += 1; renderTrades(); });
    document.getElementById('closedPrev').addEventListener('click', () => { closedPage = Math.max(0, closedPage - 1); renderClosedLots(); });
    document.getElementById('closedNext').addEventListener('click', () => { closedPage += 1; renderClosedLots(); });
    document.getElementById('runBacktest').addEventListener('click', async () => {
      const password = window.prompt('输入交易开关密码以运行回测');
      if (!password) return;
      document.getElementById('backtestStatus').textContent = '回测运行中...';
      try {
        const data = await apiGet('/api/backtest', {
          mode: document.getElementById('backtestMode').value,
          start: document.getElementById('backtestStart').value.trim(),
          end: document.getElementById('backtestEnd').value.trim(),
          days: document.getElementById('backtestDays').value.trim(),
          take_profits: document.getElementById('backtestProfits').value.trim()
        }, password);
        renderBacktest(data);
        document.getElementById('backtestStatus').textContent = `完成，初始资金 ${fmt(data.initial_quote || 0, 4)} USDT`;
      } catch (err) {
        document.getElementById('backtestStatus').textContent = err.message || String(err);
      }
    });
    document.getElementById('execute').addEventListener('click', async () => {
      const enabled = !document.getElementById('execute').classList.contains('on');
      const password = window.prompt(enabled ? '输入开关密码以开启交易' : '输入开关密码以暂停交易');
      if (!password) return;
      await apiGet('/api/trading', { execute_trades: enabled }, password);
      refresh();
    });
    document.getElementById('calibrate').addEventListener('click', async () => {
      const password = window.prompt('输入开关密码以校准当前资产为新基准');
      if (!password) return;
      const confirmed = window.confirm('确认把当前总资产设为新的盈亏基准？这会让看板的较启动基准盈亏从当前值重新计算。');
      if (!confirmed) return;
      await apiGet('/api/baseline/calibrate', {}, password);
      refresh();
    });
    document.getElementById('manualBuy').addEventListener('click', async () => {
      const orderType = window.prompt('输入买入类型：market 市价 / limit 限价', 'market');
      if (!orderType) return;
      const quoteSize = window.prompt('输入手动买入金额（USDT）。买入后会记账，但默认不自动卖出。', '10');
      if (!quoteSize) return;
      const autoSellText = window.prompt('这次人工买入是否自动卖出？输入 yes/no。默认跟随设置。', manualBuyAutoSellDefault ? 'yes' : 'no');
      if (autoSellText === null) return;
      const autoSell = ['1', 'true', 'yes', 'y', 'on', '是', '开'].includes(autoSellText.trim().toLowerCase());
      const targetProfitPct = window.prompt(autoSell ? '输入自动卖出目标利润百分比，例如 0.6 表示 0.6%。' : '输入参考目标利润百分比，例如 0.6 表示 0.6%。只作为参考卖价。', '0.6');
      if (targetProfitPct === null) return;
      let limitPrice = null;
      if (orderType.trim().toLowerCase() === 'limit') {
        limitPrice = window.prompt('输入限价买入价格。订单成交后才会记到账本。');
        if (!limitPrice) return;
      }
      const password = window.prompt('输入交易开关密码以确认手动买入');
      if (!password) return;
      const isLimit = orderType.trim().toLowerCase() === 'limit';
      const confirmed = window.confirm(isLimit ? `确认挂限价买入单：约 ${quoteSize} USDT，价格 ${limitPrice}？成交后才记账。自动卖出：${autoSell ? '是' : '否'}。` : `确认市价买入约 ${quoteSize} USDT 的 BTC，并记录为手动仓？自动卖出：${autoSell ? '是' : '否'}。`);
      if (!confirmed) return;
      try {
        await apiGet(isLimit ? '/api/manual/limit-buy' : '/api/manual/buy', { quote_size: quoteSize, limit_price: limitPrice, target_profit_pct: Number(targetProfitPct) / 100, auto_sell: autoSell }, password);
      } catch (err) { window.alert(err.message || err); return; }
      refresh();
    });
    async function manualSell(lotId) {
      const password = window.prompt('输入交易开关密码以确认手动卖出');
      if (!password) return;
      const confirmed = window.confirm('确认市价卖出这个手动批次，并关闭账本记录？');
      if (!confirmed) return;
      try { await apiGet('/api/manual/sell', { lot_id: lotId }, password); }
      catch (err) { window.alert(err.message || err); return; }
      refresh();
    }
    async function setLotAutoSell(lotId, enabled) {
      const password = window.prompt(enabled ? '输入交易开关密码以开启这个批次的自动卖出' : '输入交易开关密码以取消这个批次的自动卖出');
      if (!password) return;
      const confirmed = window.confirm(enabled ? '确认让这个人工买入批次到目标价后由脚本自动卖出？' : '确认取消这个人工买入批次的自动卖出？取消后脚本不会自动卖出它。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/auto-sell', { lot_id: lotId, auto_sell: enabled }, password); }
      catch (err) { window.alert(err.message || err); return; }
      refresh();
    }
    async function limitSell(lotId, targetPrice) {
      const limitPrice = window.prompt('输入限价卖出价格。订单成交后才会关闭账本批次。', targetPrice || '');
      if (!limitPrice) return;
      const password = window.prompt('输入交易开关密码以确认限价卖出');
      if (!password) return;
      const confirmed = window.confirm(`确认为这个批次挂限价卖出单，价格 ${limitPrice}？成交前不会关闭账本。`);
      if (!confirmed) return;
      try { await apiGet('/api/manual/limit-sell', { lot_id: lotId, limit_price: limitPrice }, password); }
      catch (err) { window.alert(err.message || err); return; }
      refresh();
    }
    async function cancelPendingOrder(orderId) {
      const password = window.prompt('输入交易开关密码以取消限价挂单');
      if (!password) return;
      const confirmed = window.confirm('确认取消这个限价挂单？如果已经成交，取消会失败或只取消未成交部分。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/cancel-order', { order_id: orderId }, password); }
      catch (err) { window.alert(err.message || err); return; }
      refresh();
    }
    async function externalClose(lotId) {
      const sellPrice = window.prompt('输入你在币安外部卖出的成交价。这个操作只同步账本，不会再次下单。');
      if (!sellPrice) return;
      const quantity = window.prompt('输入卖出数量，留空表示关闭整个批次。');
      if (quantity === null) return;
      const password = window.prompt('输入交易开关密码以确认同步账本');
      if (!password) return;
      const confirmed = window.confirm('确认只按外部成交同步这个批次？不会向币安提交新订单。');
      if (!confirmed) return;
      try { await apiGet('/api/manual/external-close', { lot_id: lotId, sell_price: sellPrice, quantity }, password); }
      catch (err) { window.alert(err.message || err); return; }
      refresh();
    }
    document.getElementById('settingsOpen').addEventListener('click', async () => {
      document.getElementById('settingsModal').classList.add('open');
      if (!settingsLoaded) {
        try { await loadSettings(); } catch (err) { document.getElementById('settingsStatus').textContent = String(err.message || err); }
      }
    });
    document.getElementById('settingsClose').addEventListener('click', () => {
      document.getElementById('settingsModal').classList.remove('open');
    });
    document.getElementById('settingsReload').addEventListener('click', async () => {
      try { await loadSettings(); } catch (err) { document.getElementById('settingsStatus').textContent = String(err.message || err); }
    });
    document.getElementById('settingsSave').addEventListener('click', async () => {
      const password = window.prompt('输入当前交易开关密码以保存设置');
      if (!password) return;
      try { await apiGet('/api/settings/update', settingsPayload(password), password); }
      catch (err) { document.getElementById('settingsStatus').textContent = err.message || String(err); return; }
      try {
        const profitResult = await apiGet('/api/strategy/take-profit', {
          take_profit_pct: Number(document.getElementById('setTakeProfitPct').value || 0),
          apply_existing: document.getElementById('setApplyTakeProfit').value === 'true'
        }, password);
        const lots = profitResult.lots || {};
        document.getElementById('settingsStatus').textContent = `已保存。盈利比例 ${fmt((profitResult.take_profit_pct || 0) * 100, 4)}%，未平批次更新 ${lots.updated || 0} 个，跳过 ${lots.skipped || 0} 个。`;
      } catch (err) {
        document.getElementById('settingsStatus').textContent = err.message || String(err);
        return;
      }
      settingsLoaded = false;
      await loadSettings();
      refresh();
    });
    document.querySelectorAll('.range-tabs button').forEach(btn => {
      btn.addEventListener('click', () => {
        activeRange = btn.dataset.range;
        chartZoom = { start: 0, end: 1 };
        document.querySelectorAll('.range-tabs button').forEach(item => item.classList.toggle('active', item === btn));
        refresh();
      });
    });
  </script>
</body>
</html>
"""


class Dashboard:
    def __init__(self, config: AgentConfig, baseline_path: Path, trades_path: Path, state_path: Path) -> None:
        self.config = config
        self.baseline_path = baseline_path
        self.trades_path = trades_path
        self.state_path = state_path
        self.control_path = Path("data/control.json")
        self.ledger = PositionLedger(Path(f"data/lots_{config.symbol}.json"))
        self.pending_path = Path(f"data/pending_orders_{config.symbol}.json")
        self.client = BinanceSpotClient(config.base_url, config.api_key, config.api_secret)
        self.strategy = GridStrategy(
            grid_step_pct=config.grid_step_pct,
            take_profit_pct=config.take_profit_pct,
            order_quote_size=config.order_quote_size,
            max_position_quote=config.max_position_quote,
        )

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
        open_lots = [self._lot_with_fee(lot) for lot in self._lots_for_strategy(raw_grid_lots) + raw_swing_lots]
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
        decision = strategy.decide(snapshot, self.grid_state(), [lot for lot in open_lots if not str(lot.get("level", "")).startswith("swing-")])
        swing_band = self.swing_band(price, raw_swing_lots, quote_balance + base_balance * price)
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
            "swing_band": swing_band,
            "chart_range": range_key,
            "chart_interval": interval,
            "price_history": [
                {"open_time": int(item[0]), "close": float(item[4])}
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
        }

    @staticmethod
    def chart_window(range_key: str) -> tuple[str, int]:
        windows = {
            "minute": ("1m", 60),
            "15m": ("1m", 15),
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
            prices = fetch_close_prices(self.config.base_url, self.config.symbol, "1m", _date_ms(start), _date_ms(end))
            if len(prices) < 120:
                return {"error": f"not enough kline data: {len(prices)}"}
            for profit in take_profits:
                result = run_backtest(
                    f"{start}..{end}",
                    prices,
                    self._backtest_config(initial_quote, prices[0], len(prices), profit),
                )
                item = asdict(result)
                item["take_profit_pct"] = profit
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
            return {"error": "manual lot not found"}
        if not _manual_control_lot(lot):
            return {"error": "only manual or swing lots can be sold from this action"}
        quantity = Decimal(str(lot.get("remaining_quantity", 0) or 0))
        if quantity <= 0:
            return {"error": "manual lot has no remaining quantity"}
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
        if not _manual_control_lot(lot):
            return {"error": "only manual or swing lots can change auto sell"}
        updated = self.ledger.set_auto_sell(lot_id, enabled)
        if not updated:
            return {"error": "failed to update auto sell"}
        self._record_manual_trade("AUTO_SELL_ENABLED" if enabled else "AUTO_SELL_DISABLED", 0, {}, updated)
        return {"lot": updated}

    def manual_limit_sell(self, lot_id: str, limit_price: float) -> dict[str, Any]:
        lot = next((item for item in self.ledger.open_lots() if item.get("id") == lot_id), None)
        if not lot:
            return {"error": "open lot not found"}
        if lot.get("pending_limit_sell_order_id"):
            return {"error": "lot already has a pending limit sell order"}
        if limit_price <= 0:
            return {"error": "limit price must be positive"}
        quantity = Decimal(str(lot.get("remaining_quantity", 0) or 0))
        if quantity <= 0:
            return {"error": "lot has no remaining quantity"}
        filters = self.client.symbol_filters(self.config.symbol)
        price = self.client.round_price(Decimal(str(limit_price)), filters)
        rounded_qty = self.client.round_quantity(quantity, filters)
        if rounded_qty < filters.min_qty:
            return {"error": f"quantity below minQty {filters.min_qty}"}
        if rounded_qty * price < filters.min_notional:
            return {"error": f"quantity below minNotional {filters.min_notional}"}
        order = self.client.limit_sell_qty(self.config.symbol, rounded_qty, price)
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

    def cancel_pending_order(self, order_id: int) -> dict[str, Any]:
        order = self.client.cancel_order(self.config.symbol, order_id)
        pending = self.pending_orders()
        for item in pending:
            if int(item.get("order_id", 0) or 0) == order_id:
                item["status"] = order.get("status", "CANCELED")
                item["closed_at"] = _utc_now()
                if item.get("lot_id"):
                    self.clear_lot_pending_sell(str(item["lot_id"]))
        self.save_pending_orders(pending)
        self._record_manual_trade("LIMIT_ORDER_CANCELED", 0, order, None)
        return {"order": order}

    def add_pending_order(self, order: dict[str, Any]) -> dict[str, Any]:
        pending = self.pending_orders()
        pending.append(order)
        self.save_pending_orders(pending)
        return order

    def pending_orders(self) -> list[dict[str, Any]]:
        if not self.pending_path.exists():
            return []
        try:
            payload = json.loads(self.pending_path.read_text())
        except json.JSONDecodeError:
            return []
        return payload.get("orders", [])

    def save_pending_orders(self, orders: list[dict[str, Any]]) -> None:
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_path.write_text(json.dumps({"orders": orders}, indent=2, sort_keys=True) + "\n")

    def mark_lot_pending_sell(self, lot_id: str, order_id: int, limit_price: float) -> None:
        lots = self.ledger.lots()
        for lot in lots:
            if lot.get("id") == lot_id and lot.get("status") == "open":
                lot["pending_limit_sell_order_id"] = order_id
                lot["pending_limit_sell_price"] = limit_price
        self.ledger.save(lots)

    def clear_lot_pending_sell(self, lot_id: str) -> None:
        lots = self.ledger.lots()
        for lot in lots:
            if lot.get("id") == lot_id:
                lot.pop("pending_limit_sell_order_id", None)
                lot.pop("pending_limit_sell_price", None)
        self.ledger.save(lots)

    def mark_lot_limit_sell_filled(self, lot_id: str, order_id: int, limit_price: float) -> None:
        lots = self.ledger.lots()
        for lot in lots:
            if lot.get("id") == lot_id:
                lot["limit_sell_filled"] = True
                lot["limit_sell_order_id"] = order_id
                lot["manual_sell_price"] = limit_price
                lot.pop("pending_limit_sell_order_id", None)
                lot.pop("pending_limit_sell_price", None)
        self.ledger.save(lots)

    def sync_pending_orders(self) -> list[dict[str, Any]]:
        pending = self.pending_orders()
        changed = False
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
                elif item.get("side") == "SELL" and item.get("lot_id"):
                    lot = self.ledger.close_lot(str(item["lot_id"]), order, self.config.trading_fee_rate)
                    self.mark_lot_limit_sell_filled(
                        str(item["lot_id"]),
                        int(item.get("order_id", 0) or 0),
                        float(item.get("limit_price", 0) or 0),
                    )
                    self._record_manual_trade("LIMIT_SELL_FILLED", quote_qty, order, lot)
                item["processed"] = True
                item["closed_at"] = _utc_now()
            if item["status"] in {"CANCELED", "EXPIRED", "REJECTED"} and item.get("lot_id") and not item.get("processed"):
                self.clear_lot_pending_sell(str(item["lot_id"]))
                item["closed_at"] = _utc_now()
            changed = True
        if changed:
            self.save_pending_orders(pending)
        return pending

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
        if updates:
            _update_dotenv(Path(".env"), updates)
            os.environ.update(updates)
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
    path.write_text("\n".join(lines).rstrip() + "\n")


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
                except BinanceAPIError as exc:
                    payload = {"error": str(exc)}
                self._send(200, json.dumps(payload, sort_keys=True).encode(), "application/json")
                return
            if path == "/api/settings":
                self._send(200, json.dumps(dashboard.settings(), sort_keys=True).encode(), "application/json")
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
                "/api/manual/cancel-order",
                "/api/manual/external-close",
            }:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            payload = self._payload()
            trading_password = self.headers.get("X-Trading-Password", "")
            if not dashboard.trading_password_ok(trading_password):
                self._send(403, json.dumps({"error": "invalid trading password"}).encode(), "application/json")
                return
            if path == "/api/baseline/calibrate":
                try:
                    result = dashboard.calibrate_baseline()
                except BinanceAPIError as exc:
                    result = {"error": str(exc)}
            elif path == "/api/backtest":
                try:
                    result = dashboard.backtest(payload)
                except (BinanceAPIError, ValueError) as exc:
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
