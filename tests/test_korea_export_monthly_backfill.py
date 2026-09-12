from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals import korea_export_backfill as backfill  # noqa: E402
from sources import korea_export_monthly as monthly  # noqa: E402
from sources.korea_export_intramonth import (  # noqa: E402
    ExportSnapshot,
    ReleaseLink,
    independent_segment_rows,
)


class FakeDb:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.upserts = []

    def request(self, method, table, params=None, **_kwargs):
        if method == "GET" and table == "economic_chart_points":
            return self.existing
        raise AssertionError((method, table, params))

    def upsert(self, table, rows, conflict):
        self.upserts.append((table, rows, conflict))


class KoreaExportMonthlyBackfillTests(unittest.TestCase):
    def test_monthly_backfill_creates_one_month_end_point_from_total_export_and_workdays(self):
        snapshot = ExportSnapshot(
            stage="month_end",
            reference_month=date(2018, 4, 1),
            period_end=date(2018, 4, 30),
            cumulative_export_musd=50_000.0,
            cumulative_workdays=20.0,
            published_on=date(2018, 5, 1),
            source_url="https://www.customs.go.kr/example",
        )
        row = monthly.monthly_row_from_snapshot(snapshot)
        self.assertEqual(row["observation_date"], "2018-04-30")
        self.assertAlmostEqual(row["value"], 25.0)
        self.assertEqual(row["frequency"], "M")
        self.assertEqual(row["source"], "KCS:MONTHLY_EXPORT/daily_avg")
        self.assertNotIn("2018-04-10", row.values())
        self.assertNotIn("2018-04-20", row.values())

    def test_monthly_source_preserves_successful_month_when_another_month_fails(self):
        april = ReleaseLink(
            title="2018년 4월 수출입 현황",
            ntt_sn="1",
            ntt_url="",
            stage="month_end",
            reference_month=date(2018, 4, 1),
            period_end=date(2018, 4, 30),
            published_on=date(2018, 5, 1),
        )
        may = ReleaseLink(
            title="2018년 5월 수출입 현황",
            ntt_sn="2",
            ntt_url="",
            stage="month_end",
            reference_month=date(2018, 5, 1),
            period_end=date(2018, 5, 31),
            published_on=date(2018, 6, 1),
        )
        snapshot = ExportSnapshot(
            stage="month_end",
            reference_month=date(2018, 4, 1),
            period_end=date(2018, 4, 30),
            cumulative_export_musd=50_000.0,
            cumulative_workdays=20.0,
            published_on=date(2018, 5, 1),
            source_url="https://www.customs.go.kr/example",
        )
        with (
            patch.object(monthly, "fetch_release_links", return_value=([april, may], [])),
            patch.object(monthly, "_detail_markup", return_value="<html></html>"),
            patch.object(monthly, "parse_snapshot", side_effect=[snapshot, ValueError("bad month")]),
            patch.object(monthly.time, "sleep"),
        ):
            rows, errors = monthly.fetch_monthly_export_rows(date(2018, 4, 1), date(2018, 5, 1))
        self.assertEqual([row["observation_date"] for row in rows], ["2018-04-30"])
        self.assertEqual(len(errors), 1)
        self.assertIn("2018-05 month-end", errors[0])
        self.assertIn("bad month", errors[0])

    def test_existing_intramonth_data_protects_the_entire_month_from_monthly_backfill(self):
        db = FakeDb(existing=[
            {"observation_date": "2026-07-10", "source": "KCS:INTRAMONTH_EXPORT/d10"},
            {"observation_date": "2026-06-30", "source": "KCS:MONTHLY_EXPORT/daily_avg"},
        ])
        protected = backfill.existing_intramonth_months(db, date(2026, 6, 1), date(2026, 7, 31))
        self.assertEqual(protected, {"2026-07"})
        candidates = [
            {"series_code": "KR_EXPORT_DAILY_AVG", "observation_date": "2026-06-30", "value": 1},
            {"series_code": "KR_EXPORT_DAILY_AVG", "observation_date": "2026-07-31", "value": 2},
        ]
        self.assertEqual(
            [row["observation_date"] for row in backfill.monthly_rows_without_intramonth(candidates, protected)],
            ["2026-06-30"],
        )

    def test_automatic_intramonth_three_segment_contract_is_unchanged(self):
        snapshots = [
            ExportSnapshot("d10", date(2026, 8, 1), date(2026, 8, 10), 20_000, 7.0, None, "d10"),
            ExportSnapshot("d20", date(2026, 8, 1), date(2026, 8, 20), 42_000, 14.5, None, "d20"),
            ExportSnapshot("month_end", date(2026, 8, 1), date(2026, 8, 31), 60_000, 21.0, None, "end"),
        ]
        rows = independent_segment_rows(snapshots)
        self.assertEqual([row["observation_date"] for row in rows], [
            "2026-08-10", "2026-08-20", "2026-08-31"
        ])
        self.assertEqual([row["source"] for row in rows], [
            "KCS:INTRAMONTH_EXPORT/d10",
            "KCS:INTRAMONTH_EXPORT/d11_20",
            "KCS:INTRAMONTH_EXPORT/d21_end",
        ])
        self.assertTrue(all(row["frequency"] == "T" for row in rows))

    def test_chart_keeps_sparse_points_connected_and_existing_ma_contract_unchanged(self):
        chart = (ROOT / "assets/js/charts/economic-charts.js").read_text(encoding="utf-8")
        self.assertIn("const MA_WINDOWS={D:[5,20,'5일','20일'],W:[4,26,'4주','26주'],T:[6,18,'6구간','18구간'],M:[6,24,'6개월','24개월']};", chart)
        self.assertIn("const normalize=(data,dk,vk)=>data.map", chart)
        self.assertIn("return [...merged.values()].sort((a,b)=>a.time.localeCompare(b.time));", chart)
        self.assertNotIn("WhitespaceData", chart)
        self.assertNotIn("value:null", chart)

    def test_backfill_entrypoint_does_not_import_or_fetch_intramonth_history(self):
        source = (ROOT / "backend/signals/korea_export_backfill.py").read_text(encoding="utf-8")
        automatic = (ROOT / "backend/signals/economic_chart_automatic.py").read_text(encoding="utf-8")
        self.assertNotIn("fetch_snapshots", source)
        self.assertNotIn("derive_missing_segments", source)
        self.assertNotIn("insert_missing_snapshots", source)
        self.assertIn("fetch_snapshots(export_start_month, max_pages=6)", automatic)
        self.assertIn('inserted["KR_EXPORT_RAW"] = insert_missing_snapshots', automatic)
        self.assertIn('inserted["KR_EXPORT_DAILY_AVG"] = derive_missing_segments', automatic)


if __name__ == "__main__":
    unittest.main()
