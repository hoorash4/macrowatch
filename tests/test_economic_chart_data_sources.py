from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources import korea_export_monthly as export_monthly  # noqa: E402
from sources import krx_index_fundamentals as krx  # noqa: E402


class KoreaExportMonthlyTests(unittest.TestCase):
    def test_workdays_use_weekday_one_saturday_half_and_holiday_zero(self):
        fake_holidays = {date(2026, 9, 7)}
        with patch.object(export_monthly.holidays, "KR", return_value=fake_holidays):
            value = export_monthly.monthly_workdays(2026, 9)
        expected = 0.0
        for day in range(1, 31):
            current = date(2026, 9, day)
            if current in fake_holidays or current.weekday() == 6:
                continue
            expected += 0.5 if current.weekday() == 5 else 1.0
        self.assertEqual(value, expected)

    def test_monthly_export_fallback_uses_ecos_export_amount_and_workdays(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "StatisticSearch": {"row": [{"TIME": "202608", "DATA_VALUE": "60000"}]}
        }
        with (
            patch.object(export_monthly, "require_env", return_value="ecos-key"),
            patch.object(export_monthly, "request_with_retry", side_effect=lambda fn: fn()),
            patch.object(export_monthly.requests, "get", return_value=response) as get,
            patch.object(export_monthly, "monthly_workdays", return_value=20.0),
        ):
            rows = export_monthly.fetch_monthly_export_rows(date(2026, 8, 1), date(2026, 8, 1))
        self.assertEqual(rows, [{
            "series_code": "KR_EXPORT_DAILY_AVG",
            "observation_date": "2026-08-31",
            "value": 30.0,
            "frequency": "M",
            "source": "ECOS:901Y118/T002+KR_WORKDAYS",
        }])
        self.assertIn("901Y118/M/202608/202608/T002", get.call_args.args[0])


class FakeFrame:
    def __init__(self, rows, columns=("PER", "PBR")):
        self._rows = rows
        self.columns = list(columns)
        self.empty = not rows

    def iterrows(self):
        return iter(self._rows)


class KrxIndexFundamentalTests(unittest.TestCase):
    def test_kospi_per_pbr_rows_use_pykrx_index_fundamentals(self):
        frame = FakeFrame([("2026-09-10", {"PER": "13.61", "PBR": "0.94"})])
        with patch.object(krx, "_call_frame", return_value=frame):
            rows = krx.fetch_krx_kospi_fundamental_rows(date(2026, 9, 10), date(2026, 9, 10))
        self.assertEqual(rows["KOSPI_PER"][0]["value"], 13.61)
        self.assertEqual(rows["KOSPI_PBR"][0]["value"], 0.94)
        self.assertEqual(rows["KOSPI_PER"][0]["source"], "PYKRX:KRX_INDEX_FUNDAMENTAL/KOSPI1001")

    def test_pykrx_is_called_for_kospi_index_1001(self):
        frame = FakeFrame([("2026-09-10", {"PER": 13.61, "PBR": 1.24})])
        getter = Mock(return_value=frame)
        with patch.object(krx.stock, "get_index_fundamental_by_date", getter, create=True):
            krx.fetch_krx_kospi_fundamental_rows(date(2026, 9, 1), date(2026, 9, 10))
        getter.assert_called_once_with("20260901", "20260910", "1001")


if __name__ == "__main__":
    unittest.main()
