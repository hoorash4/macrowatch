from __future__ import annotations

import os
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


class KrxIndexFundamentalTests(unittest.TestCase):
    def test_official_kospi_endpoint_uses_market_index_contract(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"output": [{
            "TRD_DD": "2026/09/10", "WT_PER": "13.61", "WT_STKPRC_NETASST_RTO": "1.24"
        }]}
        session = Mock()
        session.post.return_value = response
        with patch.object(krx, "request_with_retry", side_effect=lambda fn: fn()):
            rows = krx._payload(session, date(2026, 9, 1), date(2026, 9, 10))
        self.assertEqual(len(rows), 1)
        payload = session.post.call_args.kwargs["data"]
        self.assertEqual(payload["bld"], "dbms/MDC/STAT/standard/MDCSTAT00702")
        self.assertEqual(payload["indTpCd"], "1")
        self.assertEqual(payload["indTpCd2"], "001")

    def test_kospi_per_pbr_rows_are_built_from_official_fields(self):
        with (
            patch.object(krx, "_warm_session"),
            patch.object(krx, "_optional_login", return_value=False),
            patch.object(krx, "_payload", return_value=[{
                "TRD_DD": "2026/09/10", "WT_PER": "13.61", "WT_STKPRC_NETASST_RTO": "1.24"
            }]),
        ):
            rows = krx.fetch_krx_kospi_fundamental_rows(date(2026, 9, 1), date(2026, 9, 10))
        self.assertEqual(rows["KOSPI_PER"][0]["value"], 13.61)
        self.assertEqual(rows["KOSPI_PBR"][0]["value"], 1.24)
        self.assertEqual(rows["KOSPI_PER"][0]["source"], "KRX_DATA_MARKETPLACE:MDCSTAT00702/KOSPI1001")

    def test_optional_krx_login_handles_duplicate_session(self):
        first = Mock()
        first.raise_for_status = Mock()
        first.json.return_value = {"_error_code": "CD011"}
        second = Mock()
        second.raise_for_status = Mock()
        second.json.return_value = {"_error_code": "CD001"}
        session = Mock()
        session.post.side_effect = [first, second]
        with (
            patch.dict(os.environ, {"KRX_ID": "id", "KRX_PW": "pw"}, clear=False),
            patch.object(krx, "request_with_retry", side_effect=lambda fn: fn()),
        ):
            self.assertTrue(krx._optional_login(session))
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(session.post.call_args.kwargs["data"]["skipDup"], "Y")


if __name__ == "__main__":
    unittest.main()
