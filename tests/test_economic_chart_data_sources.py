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
from sources.korea_export_intramonth import ExportSnapshot, ReleaseLink  # noqa: E402


class KoreaExportMonthlyTests(unittest.TestCase):
    def test_monthly_export_backfill_uses_official_kcs_month_end_amount_and_workdays(self):
        snapshot = ExportSnapshot(
            stage="month_end",
            reference_month=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            cumulative_export_musd=60_000.0,
            cumulative_workdays=20.0,
            published_on=date(2026, 9, 1),
            source_url="https://www.customs.go.kr/example",
        )
        self.assertEqual(export_monthly.monthly_row_from_snapshot(snapshot), {
            "series_code": "KR_EXPORT_DAILY_AVG",
            "observation_date": "2026-08-31",
            "value": 30.0,
            "frequency": "M",
            "source": "KCS:MONTHLY_EXPORT/daily_avg",
        })

    def test_monthly_export_backfill_fetches_only_month_end_releases(self):
        month_end = ReleaseLink(
            title="2026년 8월 수출입 현황",
            ntt_sn="1",
            ntt_url="",
            stage="month_end",
            reference_month=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            published_on=date(2026, 9, 1),
        )
        d20 = ReleaseLink(
            title="8월 1일~20일 수출입 현황",
            ntt_sn="2",
            ntt_url="",
            stage="d20",
            reference_month=date(2026, 8, 1),
            period_end=date(2026, 8, 20),
            published_on=date(2026, 8, 21),
        )
        snapshot = ExportSnapshot(
            stage="month_end",
            reference_month=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            cumulative_export_musd=60_000.0,
            cumulative_workdays=20.0,
            published_on=date(2026, 9, 1),
            source_url="https://www.customs.go.kr/example",
        )
        with (
            patch.object(export_monthly, "fetch_release_links", return_value=([d20, month_end], [])),
            patch.object(export_monthly, "_detail_markup", return_value="<html></html>"),
            patch.object(export_monthly, "parse_snapshot", return_value=snapshot) as parser,
        ):
            rows, errors = export_monthly.fetch_monthly_export_rows(date(2026, 8, 1), date(2026, 8, 1))
        self.assertEqual(errors, [])
        self.assertEqual([row["observation_date"] for row in rows], ["2026-08-31"])
        parser.assert_called_once()


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
