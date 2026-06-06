import unittest
from datetime import date

from binance_testnet_agent.email_report import _card, _fmt, _money, _money_plain, _report_period_for_date


class EmailReportTest(unittest.TestCase):
    def test_report_priority_prefers_month_then_week_then_day(self) -> None:
        self.assertEqual(_report_period_for_date(date(2026, 5, 31)), "month")
        self.assertEqual(_report_period_for_date(date(2026, 6, 7)), "week")
        self.assertEqual(_report_period_for_date(date(2026, 6, 6)), "day")

    def test_report_numbers_use_two_decimal_places(self) -> None:
        self.assertEqual(_fmt(61243.756), "61,243.76")
        self.assertEqual(_fmt(0.00296552), "0.00")
        self.assertEqual(_money(1.23456, "USDT"), "+1.23 USDT")
        self.assertEqual(_money_plain(394.220497, "USDT"), "394.22 USDT")

    def test_chinese_market_colors_use_red_for_profit_green_for_loss(self) -> None:
        self.assertIn("#e11d48", _card("盈利", "+1.00 USDT", True))
        self.assertIn("#059669", _card("亏损", "-1.00 USDT", False))


if __name__ == "__main__":
    unittest.main()
