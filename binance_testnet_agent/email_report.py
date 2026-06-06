from __future__ import annotations

import json
import os
import smtplib
import argparse
import calendar
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

from .dashboard import Dashboard
from .config import AgentConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Send trading email report")
    parser.add_argument("--period", choices=["auto", "day", "week", "month"], default="auto")
    parser.add_argument("--only-last-day", action="store_true", help="Only send if today is the last day of the month in Asia/Shanghai.")
    args = parser.parse_args()

    config = AgentConfig.from_env()
    if args.only_last_day and not _is_month_end():
        return
    period = _report_period_for_date() if args.period == "auto" else args.period

    dashboard = Dashboard(
        config,
        Path(f"data/baseline_{config.symbol}.json"),
        Path(f"data/trades_{config.symbol}.jsonl"),
        Path(f"data/grid_state_{config.symbol}.json"),
    )
    status = dashboard.status("day")
    report = build_report(status, period)
    sender = os.getenv("SMTP_USERNAME", "")
    recipient = os.getenv("REPORT_RECIPIENT", "")
    if not sender or not recipient:
        raise SystemExit("Missing SMTP_USERNAME or REPORT_RECIPIENT")

    subject = f"{report['title']} - {config.symbol} - {report['date_label']}"
    message = EmailMessage()
    sender_name = os.getenv("SMTP_FROM_NAME", "交易报告")
    message["From"] = formataddr((sender_name, sender))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(_text(report))
    message.add_alternative(_html(report), subtype="html")

    host = os.getenv("SMTP_HOST", "smtp.exmail.qq.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    password = os.getenv("SMTP_PASSWORD", "")
    with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


def build_report(status: dict, period: str) -> dict:
    start, end, label, title = _period_window(period)
    trades = [item for item in status.get("trades", []) if _in_period(item.get("ts"), start, end)]
    closed_lots = [item for item in status.get("closed_lots", []) if _in_period(item.get("closed_at"), start, end)]
    period_realized = sum(float(item.get("realized_pnl") or 0) for item in closed_lots)
    return {
        "period": period,
        "title": title,
        "date_label": label,
        "start": start,
        "end": end,
        "status": status,
        "trades": trades,
        "closed_lots": closed_lots,
        "trade_count": len(trades),
        "closed_count": len(closed_lots),
        "open_count": len(status.get("open_lots", [])),
        "period_realized": period_realized,
    }


def _text(report: dict) -> str:
    status = report["status"]
    return "\n".join(
        [
            report["title"],
            f"周期: {report['date_label']}",
            f"交易对: {status.get('symbol')}",
            f"价格: {_fmt(status.get('price'))}",
            f"总资产估值: {_money_plain(float(status.get('value_quote') or 0), status.get('quote_asset', 'USDT'))}",
            f"BTC持仓: {_fmt(status.get('base_balance'))} {status.get('base_asset')}",
            f"USDT余额: {_money_plain(float(status.get('quote_balance') or 0), status.get('quote_asset', 'USDT'))}",
            f"本周期已实现利润: {_money(float(report['period_realized']), status.get('quote_asset', 'USDT'))}",
            f"累计已实现利润: {_money(float(status.get('realized_pnl') or 0), status.get('quote_asset', 'USDT'))}",
            f"未实现批次盈亏: {_money(float(status.get('unrealized_lot_pnl') or 0), status.get('quote_asset', 'USDT'))}",
            f"总估值盈亏: {_money(float(status.get('pnl_quote') or 0), status.get('quote_asset', 'USDT'))}",
            f"本周期交易次数: {report['trade_count']}",
            f"本周期已平批次: {report['closed_count']}",
            f"当前未平批次: {report['open_count']}",
        ]
    )


def _html(report: dict) -> str:
    status = report["status"]
    quote = status.get("quote_asset", "USDT")
    period_realized = float(report.get("period_realized") or 0)
    realized = float(status.get("realized_pnl") or 0)
    unrealized = float(status.get("unrealized_lot_pnl") or 0)
    pnl = float(status.get("pnl_quote") or 0)
    risk = status.get("risk") or {}
    trade_count = report["trade_count"]
    open_count = report["open_count"]
    closed_count = report["closed_count"]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""
<!doctype html>
<html>
<body style="margin:0;background:#f4f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#17212b;">
  <div style="max-width:860px;margin:0 auto;padding:28px;">
    <div style="background:#fff;border:1px solid #d9e2ec;border-radius:12px;padding:22px;box-shadow:0 12px 30px rgba(15,23,42,.08);">
      <h1 style="margin:0 0 6px;font-size:24px;">{report['title']}</h1>
      <div style="color:#607080;font-size:13px;">{report['date_label']} · {generated} · {status.get('symbol')}</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px;">
        {_card("实时价格", _fmt(status.get("price")))}
        {_card("总资产估值", _money_plain(float(status.get("value_quote") or 0), quote))}
        {_card("本周期已实现", _money(period_realized, quote), period_realized >= 0)}
        {_card("累计已实现", _money(realized, quote), realized >= 0)}
        {_card("总估值盈亏", _money(pnl, quote), pnl >= 0)}
        {_card("未实现批次", _money(unrealized, quote), unrealized >= 0)}
        {_card("BTC 持仓", f"{_fmt(status.get('base_balance'))} {status.get('base_asset', 'BTC')}")}
        {_card("USDT 余额", _money_plain(float(status.get("quote_balance") or 0), quote))}
      </div>
      <div style="margin-top:18px;padding:14px;border-radius:10px;background:#eef6ff;">
        <strong>周期概览：</strong>交易 {trade_count} 次，已平 {closed_count} 单，当前未平 {open_count} 单。<br>
        <strong>风控状态：</strong>{risk.get('reason', '--')}
      </div>
      {_table("批次摘要", ["项目", "数量"], [
          ["本周期交易次数", str(trade_count)],
          ["本周期已平批次", str(closed_count)],
          ["当前未平批次", str(open_count)],
      ])}
    </div>
  </div>
</body>
</html>
"""


def _card(label: str, value: str, positive: bool | None = None) -> str:
    color = "#17212b" if positive is None else ("#e11d48" if positive else "#059669")
    return f"""
    <div style="border:1px solid #e5ebf2;border-radius:10px;padding:14px;background:#fbfdff;">
      <div style="color:#687789;font-size:12px;margin-bottom:8px;">{label}</div>
      <div style="font-size:20px;font-weight:750;color:{color};">{value}</div>
    </div>
    """


def _table(title: str, headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        rows = [["--"] * len(headers)]
    head = "".join(f"<th style='text-align:left;padding:9px;border-bottom:1px solid #e5ebf2;color:#687789;'>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:9px;border-bottom:1px solid #edf2f7;'>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"""
    <h2 style="font-size:16px;margin:22px 0 8px;">{title}</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr>{head}</tr></thead>
      <tbody>{body}</tbody>
    </table>
    """


def _fmt(value, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _money(value: float, quote: str) -> str:
    return f"{value:+,.2f} {quote}"


def _money_plain(value: float, quote: str) -> str:
    return f"{value:,.2f} {quote}"


def _report_period_for_date(today: date | None = None) -> str:
    current = today or datetime.now(_local_tz()).date()
    if current.day == calendar.monthrange(current.year, current.month)[1]:
        return "month"
    if current.weekday() == 6:
        return "week"
    return "day"


def _period_window(period: str) -> tuple[datetime, datetime, str, str]:
    local_tz = _local_tz()
    now = datetime.now(local_tz)
    today = now.date()
    if period == "day":
        start = datetime.combine(today, time.min, tzinfo=local_tz)
        end = start + timedelta(days=1)
        return start, end, today.isoformat(), "实盘交易日报"
    if period == "week":
        start_date = today - timedelta(days=today.weekday())
        start = datetime.combine(start_date, time.min, tzinfo=local_tz)
        end = start + timedelta(days=7)
        return start, end, f"{start_date.isoformat()} ~ {(end.date() - timedelta(days=1)).isoformat()}", "实盘交易周报"
    first = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)
    start = datetime.combine(first, time.min, tzinfo=local_tz)
    end_month = today.month + 1
    end_year = today.year
    if end_month == 13:
        end_month = 1
        end_year += 1
    end = datetime.combine(first.replace(year=end_year, month=end_month), time.min, tzinfo=local_tz)
    return start, end, f"{first.isoformat()} ~ {last.isoformat()}", "实盘交易月报"


def _in_period(value: str | None, start: datetime, end: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return start <= parsed.astimezone(timezone.utc) < end


def _local_tz() -> ZoneInfo:
    return ZoneInfo("Asia/Shanghai")


def _is_month_end() -> bool:
    today = datetime.now(_local_tz()).date()
    return today.day == calendar.monthrange(today.year, today.month)[1]


if __name__ == "__main__":
    main()
